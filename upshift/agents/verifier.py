"""Verifier: install and test inside the sandbox, then turn the result into structured facts.

Deterministic. Produces pass/fail counts, each failing test with its message, and the list of
source files the failures reference, which the Surgeon uses to decide what it may edit.
"""

from __future__ import annotations

import logging
import re
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
TRACE_PATH = re.compile(r"((?:[\w.-]+[/\\])*[\w.-]+\.py)(?::\d+|::)")


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

    @property
    def failure_count(self) -> int:
        return self.failed + self.errors

    def referenced_files(self) -> list[str]:
        """Source paths mentioned in failing test ids or tracebacks, as posix relative paths."""
        found: list[str] = []
        for failure in self.failures:
            for text in (failure.test, failure.message):
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


def build_test_command(workspace: Path) -> str:
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
    lines.append(
        f"python -m pytest -q -p no:cacheprovider --maxfail=50 --junitxml={JUNIT_PATH} "
        f"> {LOG_PATH} 2>&1"
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
                failures.append(TestFailure(name, text[:1_500], child.tag))
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
    ) -> VerificationResult:
        command = build_test_command(workspace)
        log.info("verifier: %s (%s, %s)", label, self.executor.name, self.image)
        await self._emit(
            run_id, EventType.SANDBOX_STARTED, node_id, label=label,
            backend=self.executor.name, image=self.image,
        )
        result = await self.executor.run(
            ExecutionRequest(
                workspace=workspace, command=command, timeout_seconds=self.timeout_seconds,
                image=self.image, collect=[JUNIT_PATH],
            )
        )
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
