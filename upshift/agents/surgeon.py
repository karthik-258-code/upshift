"""Surgeon: bump the pin, run the tests, rewrite the code that breaks, prove it, repeat.

Safety properties:
- Edits are restricted to an allowlist: files that import the package, files named in the
  failing tests' tracebacks, and manifests. Anything else is rejected and recorded.
- Every attempt is verified in the sandbox. If an attempt makes things worse, the files are
  rolled back to the best state seen so far before the next attempt.
- Attempts escalate through the router (Super, Super, Ultra) and stop at the budget cap.
- Output is schema-constrained JSON of search/replace blocks, each with a justification and
  the evidence URL it rests on. That is what makes the final merge request auditable.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from upshift.agents.historian import ImpactDossier
from upshift.agents.scout import DependencyAssessment
from upshift.agents.verifier import Verifier, VerificationResult
from upshift.core.budget import Budget
from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType
from upshift.ecosystems.python import add_requirement, bump_pin, normalize, parse_requirements
from upshift.llm.client import TokenFactoryClient
from upshift.llm.router import BudgetExhausted, ModelRouter, NoCapableModel, TaskClass
from upshift.llm.structured import complete_json
from upshift.tools.pypi import PyPIClient

log = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 120_000
MAX_OUTPUT_TOKENS = 16_000
MAX_FAILURES_SHOWN = 12

EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "op": {"type": "string", "enum": ["replace_block", "write_file"]},
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                    "justification": {"type": "string"},
                    "evidence_url": {"type": "string"},
                },
                "required": ["path", "op", "search", "replace", "justification", "evidence_url"],
                "additionalProperties": False,
            },
        },
        "new_dependencies": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["analysis", "edits", "new_dependencies"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are the Surgeon in Upshift, a dependency-upgrade agent. One package has just been "
    "upgraded in a Python project and the test suite is red. You receive the evidence-backed "
    "list of changes in the new version (the Impact Dossier), the failing tests, and the full "
    "contents of the files you are allowed to edit. Produce the minimal, correct edits that "
    "make the suite pass on the NEW version while preserving the project's behaviour.\n"
    "Rules: use the modern API the migration guide recommends, not compatibility shims. Never "
    "pin the package back. Never delete tests or weaken assertions; if a test asserts the OLD "
    "library's behaviour (for example the exception type it raised), update it to the "
    "documented new behaviour and say so in the justification. Fix every failure you can see, "
    "not just the first. Prefer replace_block with a search string copied verbatim from the "
    "file that occurs exactly once; use write_file (search empty, replace = whole file) only "
    "when most of a file changes. Each edit needs a one-sentence justification and the "
    "evidence_url from the dossier that supports it (empty string if none applies)."
)


@dataclass
class AppliedEdit:
    path: str
    op: str
    justification: str
    evidence_url: str
    status: str  # applied | rejected
    reason: str = ""


@dataclass
class Attempt:
    number: int
    model: str
    analysis: str
    edits: list[AppliedEdit]
    verification: dict[str, Any] | None
    cost_usd: float
    rolled_back: bool = False


@dataclass
class SurgeryResult:
    package: str
    current: str | None
    target: str | None
    success: bool
    manifest_changes: list[str]
    attempts: list[Attempt] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    diff: str = ""
    after_bump: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    cost_usd: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_path(raw: str) -> str | None:
    path = raw.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if not path or ".." in PurePosixPath(path).parts:
        return None
    return path


def _read(workspace: Path, rel: str) -> str | None:
    target = workspace / rel
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def _write(workspace: Path, rel: str, content: str) -> None:
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def unified_diff(originals: dict[str, str | None], workspace: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    changed: list[str] = []
    for rel in sorted(originals):
        before = originals[rel] or ""
        after = _read(workspace, rel) or ""
        if before == after:
            continue
        changed.append(rel)
        chunks.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{rel}" if originals[rel] is not None else "/dev/null",
                tofile=f"b/{rel}", n=3,
            )
        )
    return "".join(chunks), changed


class Surgeon:
    agent = "surgeon"

    def __init__(
        self,
        llm: TokenFactoryClient,
        router: ModelRouter,
        verifier: Verifier,
        bus: EventBus | None = None,
        pypi: PyPIClient | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.llm = llm
        self.router = router
        self.verifier = verifier
        self.bus = bus
        self.pypi = pypi
        self.max_attempts = max_attempts

    async def _emit(
        self, run_id: str | None, type_: EventType, node_id: str | None, **payload: Any
    ) -> None:
        if self.bus is None or run_id is None:
            return
        await self.bus.publish(
            Event(run_id=run_id, type=type_, agent=self.agent, node_id=node_id, payload=payload)
        )

    # ---- manifests ----------------------------------------------------------

    async def _add_dependency(
        self, workspace: Path, manifest: str, name: str, originals: dict[str, str | None]
    ) -> str | None:
        text = _read(workspace, manifest)
        if text is None or not manifest.endswith(".txt"):
            return None
        existing = {d.name for d in parse_requirements(text, manifest)}
        if normalize(name) in existing:
            return None
        requirement = name
        if self.pypi is not None:
            info = await self.pypi.info(name)
            if info and info.latest:
                requirement = f"{name}=={info.latest}"
        originals.setdefault(manifest, text)
        _write(workspace, manifest, add_requirement(text, requirement))
        return f"added {requirement} to {manifest}"

    # ---- context ------------------------------------------------------------

    def allowed_files(
        self, workspace: Path, dep: DependencyAssessment, verification: VerificationResult | None
    ) -> list[str]:
        ordered: list[str] = []
        candidates = list(verification.referenced_files()) if verification else []
        candidates += list(dep.usage.get("files", []))
        candidates.append(dep.source)
        for raw in candidates:
            rel = _clean_path(raw)
            if rel and rel not in ordered and (workspace / rel).is_file():
                ordered.append(rel)
        return ordered

    def build_prompt(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        verification: VerificationResult,
        allowed: list[str],
        previous: Attempt | None,
    ) -> str:
        parts = [
            f"Upgrade: {dep.name} {dep.current} -> {dep.latest}",
            f"Dossier summary: {dossier.summary}",
            "",
            "Relevant documented changes (evidence):",
        ]
        for index, entry in enumerate(dossier.relevant, start=1):
            parts.append(
                f"{index}. [{entry.category}] {entry.title}\n"
                f"   migration: {entry.migration}\n"
                f"   quote: {entry.quote}\n"
                f"   url: {entry.evidence_url}"
            )
        if not dossier.relevant:
            parts.append("(none identified; rely on the test failures)")
        if dossier.requires_new_dependencies:
            parts.append(
                "\nNew packages already added to the manifest: "
                + ", ".join(dossier.requires_new_dependencies)
            )
        parts += [
            "",
            f"Test run after the version bump: phase={verification.phase} "
            f"exit={verification.exit_code} tests={verification.tests} "
            f"passed={verification.passed} failed={verification.failed} "
            f"errors={verification.errors}",
            "",
            "Failures:",
        ]
        for failure in verification.failures[:MAX_FAILURES_SHOWN]:
            parts.append(f"--- {failure.test} ({failure.kind})\n{failure.message}")
        if len(verification.failures) > MAX_FAILURES_SHOWN:
            parts.append(f"... {len(verification.failures) - MAX_FAILURES_SHOWN} more")
        if not verification.failures:
            parts.append(verification.log_tail[-4_000:] or "(no output captured)")
        if previous is not None:
            rejected = [e for e in previous.edits if e.status == "rejected"]
            parts += ["", f"Previous attempt ({previous.model}) analysis: {previous.analysis}"]
            if rejected:
                parts.append("Previous edits that were REJECTED (fix these):")
                parts += [f"- {e.path} [{e.op}]: {e.reason}" for e in rejected]
            if previous.rolled_back:
                parts.append("The previous attempt made more tests fail and was rolled back.")
        parts += ["", "Files you may edit (full contents, exact text):"]
        remaining = MAX_CONTEXT_CHARS
        omitted: list[str] = []
        for rel in allowed:
            content = _read(workspace, rel) or ""
            if len(content) > remaining:
                omitted.append(rel)
                continue
            remaining -= len(content)
            parts.append(f"=== {rel} ===\n{content}")
        if omitted:
            parts.append("Omitted for size (request smaller edits by path): " + ", ".join(omitted))
        return "\n".join(parts)

    # ---- edits --------------------------------------------------------------

    def apply_edit(
        self,
        workspace: Path,
        allowed: list[str],
        edit: dict[str, Any],
        originals: dict[str, str | None],
    ) -> AppliedEdit:
        raw_path = str(edit.get("path", ""))
        op = str(edit.get("op", ""))
        justification = str(edit.get("justification", ""))
        evidence = str(edit.get("evidence_url", ""))

        def rejected(reason: str) -> AppliedEdit:
            return AppliedEdit(raw_path, op, justification, evidence, "rejected", reason)

        rel = _clean_path(raw_path)
        if rel is None:
            return rejected("invalid path")
        allowed_dirs = {str(PurePosixPath(a).parent) for a in allowed}
        if rel not in allowed and str(PurePosixPath(rel).parent) not in allowed_dirs:
            return rejected("outside the allowed files")
        if op not in ("replace_block", "write_file"):
            return rejected(f"unknown op {op!r}")

        current = _read(workspace, rel)
        replace = str(edit.get("replace", ""))
        if op == "replace_block":
            search = str(edit.get("search", ""))
            if current is None:
                return rejected("file does not exist; use write_file to create it")
            if not search:
                return rejected("empty search block")
            count = current.count(search)
            if count == 0:
                return rejected("search block not found verbatim in file")
            if count > 1:
                return rejected(f"search block occurs {count} times; make it unique")
            originals.setdefault(rel, current)
            _write(workspace, rel, current.replace(search, replace, 1))
        else:
            if not replace.strip():
                return rejected("write_file with empty content")
            originals.setdefault(rel, current)
            _write(workspace, rel, replace)
        return AppliedEdit(rel, op, justification, evidence, "applied")

    @staticmethod
    def _snapshot(workspace: Path, paths: list[str]) -> dict[str, str | None]:
        return {rel: _read(workspace, rel) for rel in paths}

    @staticmethod
    def _restore(workspace: Path, snapshot: dict[str, str | None]) -> None:
        for rel, content in snapshot.items():
            target = workspace / rel
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                _write(workspace, rel, content)

    # ---- main loop ----------------------------------------------------------

    async def upgrade(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        run_id: str | None = None,
        budget: Budget | None = None,
    ) -> SurgeryResult:
        node = f"dep:{dep.name}"
        cost_before = self.llm.total_cost_usd
        originals: dict[str, str | None] = {}
        result = SurgeryResult(
            package=dep.name, current=dep.current, target=dep.latest, success=False,
            manifest_changes=[],
        )

        manifest_text = _read(workspace, dep.source)
        if manifest_text is None or not dep.latest:
            result.error = "manifest missing or no target version"
            return result
        bumped, changed = bump_pin(manifest_text, dep.name, dep.latest)
        if not changed:
            result.error = f"no `{dep.name}==` pin found in {dep.source}"
            return result
        originals[dep.source] = manifest_text
        _write(workspace, dep.source, bumped)
        result.manifest_changes.append(
            f"{dep.name} {dep.current} -> {dep.latest} in {dep.source}"
        )
        for extra in dossier.requires_new_dependencies:
            note = await self._add_dependency(workspace, dep.source, extra, originals)
            if note:
                result.manifest_changes.append(note)
        await self._emit(
            run_id, EventType.PATCH_PROPOSED, node, attempt=0, kind="manifest",
            changes=result.manifest_changes,
        )

        verification = await self.verifier.verify(
            workspace, run_id, node, label=f"{dep.name}: after version bump"
        )
        result.after_bump = verification.to_dict()
        if verification.ok:
            result.success = True
            result.final = result.after_bump
            result.diff, result.files_changed = unified_diff(originals, workspace)
            result.cost_usd = round(self.llm.total_cost_usd - cost_before, 6)
            return result
        if verification.phase in ("install", "timeout", "no-tests"):
            result.error = f"cannot iterate: {verification.phase} phase failed after bump"
            result.final = result.after_bump
            result.diff, result.files_changed = unified_diff(originals, workspace)
            return result

        best_failures = verification.failure_count
        best_verification = verification
        previous: Attempt | None = None
        last_model: str | None = None

        for number in range(1, self.max_attempts + 1):
            allowed = self.allowed_files(workspace, dep, verification)
            prompt = self.build_prompt(workspace, dep, dossier, verification, allowed, previous)
            log.info(
                "surgeon: %s attempt %d, %d failing, %d editable files, prompt %d chars",
                dep.name, number, verification.failure_count, len(allowed), len(prompt),
            )
            try:
                decision = self.router.route(
                    TaskClass.CODEGEN, attempt=number, required_caps={"json_schema"},
                    budget=budget, min_context_tokens=len(prompt) // 3 + MAX_OUTPUT_TOKENS,
                )
            except (BudgetExhausted, NoCapableModel) as exc:
                result.error = f"{type(exc).__name__}: {exc}"
                break
            if last_model and decision.model.id != last_model:
                await self._emit(
                    run_id, EventType.ESCALATION, node, attempt=number,
                    from_model=last_model, to_model=decision.model.id, reason=decision.reason,
                )
            last_model = decision.model.id

            attempt_cost_before = self.llm.total_cost_usd
            try:
                data = await complete_json(
                    self.llm, decision.model,
                    [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
                    schema_name="surgeon_edits", schema=EDIT_SCHEMA,
                    max_tokens=MAX_OUTPUT_TOKENS, agent=self.agent, run_id=run_id,
                    budget=budget, attempt=number,
                )
            except Exception as exc:  # noqa: BLE001 - recorded, loop continues
                log.exception("surgeon attempt %d failed", number)
                previous = Attempt(
                    number, decision.model.id, f"model call failed: {exc}", [], None,
                    round(self.llm.total_cost_usd - attempt_cost_before, 6),
                )
                result.attempts.append(previous)
                continue

            snapshot = self._snapshot(workspace, allowed)
            applied: list[AppliedEdit] = []
            for edit in data.get("edits", []) or []:
                if isinstance(edit, dict):
                    outcome = self.apply_edit(workspace, allowed, edit, originals)
                    applied.append(outcome)
                    if outcome.status == "rejected":
                        await self._emit(
                            run_id, EventType.PATCH_REJECTED, node, attempt=number,
                            **asdict(outcome),
                        )
            for extra in data.get("new_dependencies", []) or []:
                note = await self._add_dependency(workspace, dep.source, str(extra), originals)
                if note:
                    result.manifest_changes.append(note)

            attempt = Attempt(
                number=number, model=decision.model.id,
                analysis=str(data.get("analysis", ""))[:2_000], edits=applied,
                verification=None,
                cost_usd=round(self.llm.total_cost_usd - attempt_cost_before, 6),
            )
            await self._emit(
                run_id, EventType.PATCH_PROPOSED, node, attempt=number, model=decision.model.id,
                analysis=attempt.analysis, edits=[asdict(e) for e in applied],
            )

            if not any(e.status == "applied" for e in applied):
                result.attempts.append(attempt)
                previous = attempt
                continue

            verification = await self.verifier.verify(
                workspace, run_id, node, label=f"{dep.name}: attempt {number}"
            )
            attempt.verification = verification.to_dict()
            result.attempts.append(attempt)
            if verification.ok:
                result.success = True
                break
            if verification.phase == "test" and verification.failure_count > best_failures:
                self._restore(workspace, snapshot)
                attempt.rolled_back = True
                verification = best_verification
            else:
                best_failures = min(best_failures, verification.failure_count)
                best_verification = verification
            previous = attempt

        result.final = verification.to_dict()
        result.diff, result.files_changed = unified_diff(originals, workspace)
        result.cost_usd = round(self.llm.total_cost_usd - cost_before, 6)
        if not result.success and result.error is None:
            result.error = f"tests still failing after {len(result.attempts)} attempt(s)"
        return result
