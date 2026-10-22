"""Verifier: install and test inside the sandbox, then turn the result into structured facts.

Deterministic. Produces pass/fail counts, each failing test with its message, and the list of
source files the failures reference, which the Surgeon uses to decide what it may edit.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType
from upshift.ecosystems.python import REQUIREMENTS_NAME, skipped
from upshift.sandbox.base import ExecutionRequest, Executor

log = logging.getLogger(__name__)

INSTALL_MARKER = "__UPSHIFT_INSTALL_OK__"
JUNIT_PATH = ".upshift/junit.xml"
LOG_PATH = ".upshift/pytest.log"
API_PATH = ".upshift/api.json"

# Dumps the public top-level names of the installed packages, so agents never have to
# guess import paths for the new version.
INTROSPECT_SCRIPT = """import importlib, json, sys
out = {}
for name in sys.argv[1:]:
    try:
        module = importlib.import_module(name)
        names = sorted(n for n in dir(module) if not n.startswith('_'))
        out[name] = {'version': getattr(module, '__version__', None), 'names': names}
    except Exception as exc:
        out[name] = {'error': f'{type(exc).__name__}: {exc}'}
print(json.dumps(out))
"""
TRACE_PATH = re.compile(r"((?:[\w.-]+[/\\])*[\w.-]+\.py)(?::\d+|::)")
STARTUP_FAILURE = "pytest::startup"
CAPTURED_MARKER = "--- captured output ---"


def startup_failure(log: str, exit_code: int) -> TestFailure:
    """pytest died before collecting anything: conftest import error (exit 4), internal error
    (exit 3), or an interrupted session (exit 2). Nothing reaches the JUnit report, so the
    traceback in the log becomes the single failure the Surgeon reasons about."""
    lines = [ln.rstrip() for ln in log.splitlines() if ln.strip()]
    start = 0
    for index, line in enumerate(lines):
        if "ImportError while loading conftest" in line or "Traceback" in line:
            start = index
            break
    body = "\n".join(lines[start:][-60:])
    error = next(
        (ln.strip() for ln in reversed(lines) if re.match(r"E?\s*\w*(Error|Exception)\b", ln)),
        "",
    )
    message = f"pytest exited with code {exit_code} before collecting tests\n{body}"
    if error and not body.rstrip().endswith(error):
        message += f"\nE {error}"
    return TestFailure(STARTUP_FAILURE, message[:4_000], "error")


@dataclass
class TestFailure:
    test: str
    message: str
    kind: str = "failure"  # failure | error


@dataclass
class VerificationResult:
    ok: bool
    phase: str  # install | test | no-tests | timeout
    exit_code: int
    duration_ms: int
    tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    log_tail: str = ""
    timed_out: bool = False
    backend: str = ""
    api_surface: dict[str, Any] = field(default_factory=dict)

    @property
    def failure_count(self) -> int:
        return self.failed + self.errors

    def referenced_files(self) -> list[str]:
        """Source paths mentioned in failing test ids or tracebacks, as posix relative paths."""
        found: list[str] = []
        for failure in self.failures:
            # captured logs carry the framework's own file:line (e.g. flask.app:app.py:838)
            traceback_part = failure.message.split(CAPTURED_MARKER, 1)[0]
            for text in (failure.test, traceback_part):
                for match in TRACE_PATH.finditer(text):
                    path = match.group(1).replace("\\", "/").lstrip("./")
                    if path.startswith(("usr/", "opt/", "tmp/")) or "site-packages" in path:
                        continue
                    if path not in found:
                        found.append(path)
        return found

    def to_dict(self, log_chars: int = 2_000) -> dict[str, Any]:
        data = asdict(self)
        data["failure_count"] = self.failure_count
        data["log_tail"] = self.log_tail[-log_chars:]
        data["api_surface"] = {
            module: {"version": info.get("version"), "name_count": len(info.get("names", []))}
            for module, info in self.api_surface.items()
        }
        return data


def discover_requirements(workspace: Path) -> list[str]:
    files = []
    for path in sorted(workspace.rglob("*.txt")):
        rel = path.relative_to(workspace)
        if skipped(rel.parts) or len(rel.parts) > 2:
            continue
        if REQUIREMENTS_NAME.fullmatch(path.name) or rel.parts[0] == "requirements":
            files.append(rel.as_posix())
    return files


def has_installable_project(workspace: Path) -> bool:
    pyproject = workspace / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if "[project]" in text:
            return True
    return (workspace / "setup.py").exists()


def build_test_command(workspace: Path, introspect: list[str] | None = None) -> str:
    requirements = discover_requirements(workspace)
    lines = [
        "set -e",
        "mkdir -p .upshift",
        "export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore "
        "PYTHONDONTWRITEBYTECODE=1",
    ]
    if requirements:
        lines.append("pip install -q " + " ".join(f"-r {r}" for r in requirements))
    if has_installable_project(workspace):
        lines.append("pip install -q -e . || pip install -q .")
    lines.append("pip install -q pytest")
    lines.append(f"echo {INSTALL_MARKER}")
    lines.append("set +e")
    if introspect:
        modules = " ".join(shlex.quote(m) for m in introspect)
        lines.append("cat > .upshift/introspect.py <<'UPSHIFT_PY'")
        lines.append(INTROSPECT_SCRIPT.rstrip("\n"))
        lines.append("UPSHIFT_PY")
        lines.append(f"python .upshift/introspect.py {modules} > {API_PATH} 2>/dev/null || true")
    lines.append(
        f"python -m pytest -q -p no:cacheprovider --maxfail=50 --junitxml={JUNIT_PATH} "
        f"-o junit_logging=all > {LOG_PATH} 2>&1"
    )
    lines.append("code=$?")
    lines.append(f"tail -n 200 {LOG_PATH}")
    lines.append("exit $code")
    return "\n".join(lines)


def parse_junit(xml_text: str) -> dict[str, Any]:
    tests = failed = errors = skipped = 0
    failures: list[TestFailure] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return {"tests": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
    for case in root.iter("testcase"):
        tests += 1
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        # with `-o junit_logging=all` pytest attaches captured stdout/stderr/logs per test;
        # a web framework that logs the exception and answers 500 hides the traceback there
        captured = "\n".join(
            (case.findtext(tag) or "").strip() for tag in ("system-out", "system-err")
        ).strip()
        for child in case:
            if child.tag == "skipped":
                skipped += 1
            elif child.tag in ("failure", "error"):
                if child.tag == "failure":
                    failed += 1
                else:
                    errors += 1
                message = (child.get("message") or "").strip()
                body = (child.text or "").strip()
                text = f"{message}\n{body}".strip() if body and body != message else message
                if captured:
                    text = f"{text}\n{CAPTURED_MARKER}\n{captured[-2_000:]}"
                failures.append(TestFailure(name, text[:6_000], child.tag))
    return {
        "tests": tests, "failed": failed, "errors": errors, "skipped": skipped,
        "failures": failures,
    }


class Verifier:
    agent = "verifier"

    def __init__(
        self,
        executor: Executor,
        bus: EventBus | None = None,
        image: str = "python:3.12-slim",
        timeout_seconds: int = 900,
    ) -> None:
        self.executor = executor
        self.bus = bus
        self.image = image
        self.timeout_seconds = timeout_seconds

    async def _emit(
        self, run_id: str | None, type_: EventType, node_id: str | None, **payload: Any
    ) -> None:
        if self.bus is None or run_id is None:
            return
        await self.bus.publish(
            Event(run_id=run_id, type=type_, agent=self.agent, node_id=node_id, payload=payload)
        )

    async def verify(
        self,
        workspace: Path,
        run_id: str | None = None,
        node_id: str | None = None,
        label: str = "verify",
        introspect: list[str] | None = None,
    ) -> VerificationResult:
        command = build_test_command(workspace, introspect)
        log.info("verifier: %s (%s, %s)", label, self.executor.name, self.image)
        await self._emit(
            run_id, EventType.SANDBOX_STARTED, node_id, label=label,
            backend=self.executor.name, image=self.image,
        )
        result = await self.executor.run(
            ExecutionRequest(
                workspace=workspace, command=command, timeout_seconds=self.timeout_seconds,
                image=self.image, collect=[JUNIT_PATH, API_PATH],
            )
        )
        api_surface: dict[str, Any] = {}
        if API_PATH in result.artifacts:
            try:
                parsed = json.loads(result.artifacts[API_PATH])
                if isinstance(parsed, dict):
                    api_surface = parsed
            except json.JSONDecodeError:
                pass
        if result.timed_out:
            phase = "timeout"
        elif INSTALL_MARKER not in result.stdout:
            phase = "install"
        elif result.exit_code == 5:
            phase = "no-tests"
        else:
            phase = "test"

        stats = parse_junit(result.artifacts[JUNIT_PATH]) if JUNIT_PATH in result.artifacts else {}
        tests = int(stats.get("tests", 0))
        failed = int(stats.get("failed", 0))
        errors = int(stats.get("errors", 0))
        skipped_count = int(stats.get("skipped", 0))
        if phase == "test" and result.exit_code != 0 and tests == 0 and not stats.get("failures"):
            log_text = result.stdout.split(INSTALL_MARKER, 1)[-1]
            stats = {**stats, "failures": [startup_failure(log_text, result.exit_code)]}
            tests, errors = 1, 1
        verification = VerificationResult(
            ok=phase == "test" and result.exit_code == 0,
            phase=phase,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            tests=tests,
            passed=max(tests - failed - errors - skipped_count, 0),
            failed=failed,
            errors=errors,
            skipped=skipped_count,
            failures=list(stats.get("failures", [])),
            log_tail=result.tail(6_000),
            timed_out=result.timed_out,
            backend=result.backend,
            api_surface=api_surface,
        )
        log.info(
            "verifier: %s -> phase=%s exit=%d tests=%d failed=%d errors=%d in %.1fs",
            label, phase, result.exit_code, tests, failed, errors, result.duration_ms / 1000,
        )
        await self._emit(
            run_id, EventType.SANDBOX_FINISHED, node_id, label=label,
            exit_code=result.exit_code, duration_ms=result.duration_ms, phase=phase,
        )
        await self._emit(
            run_id, EventType.TEST_RESULT, node_id, label=label, **verification.to_dict()
        )
        return verification
