"""Reviewer: deterministic lint on the changed files, then an adversarial model review.

The Surgeon optimises for a green test suite. The Reviewer's job is to catch what a green
suite cannot: weakened tests, silent behaviour changes, leftover compatibility shims,
missed call sites, and sloppy code (unused or misplaced imports).
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from upshift.agents.historian import ImpactDossier
from upshift.agents.scout import DependencyAssessment
from upshift.agents.surgeon import SurgeryResult
from upshift.core.budget import Budget
from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType
from upshift.llm.client import TokenFactoryClient
from upshift.llm.router import ModelRouter, TaskClass
from upshift.llm.structured import complete_json
from upshift.sandbox.base import ExecutionRequest, Executor

log = logging.getLogger(__name__)

LINT_SELECT = "F401,F811,F821,F841,E402"
LINT_PATH = ".upshift/lint.json"
MAX_DIFF_CHARS = 60_000
MAX_FILE_CHARS = 14_000  # per changed file, when showing full post-edit contents
MAX_FILES_CHARS = 48_000
MAX_OUTPUT_TOKENS = 6_000
AUDITED_KINDS = ("behavior_change", "missed_migration", "weakened_test", "security")
SEVERITIES = ("blocker", "major", "minor")
KINDS = (
    "weakened_test", "behavior_change", "compat_shim", "missed_migration", "security",
    "style", "other",
)

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "request_changes"]},
        "summary": {"type": "string"},
        "confidence": {"type": "number"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "file": {"type": "string"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["severity", "kind", "file", "title", "detail", "suggestion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "summary", "confidence", "findings"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are the Reviewer in Upshift, a dependency-upgrade agent. Another agent upgraded one "
    "package and edited code until the test suite passed. You review the resulting diff "
    "adversarially. A green suite is NOT proof of correctness. Look specifically for: tests "
    "whose INPUTS, fixtures or the CALL UNDER TEST were changed so that they no longer "
    "exercise what they did before (weakened_test, a blocker); silent behaviour changes "
    "versus the original code (behavior_change); deprecated or compatibility APIs left in "
    "place when the migration guide names a replacement (compat_shim); call sites of changed "
    "APIs that were not updated and are not covered by tests (missed_migration); security "
    "regressions; and code quality problems such as unused or mid-file imports, empty config "
    "objects, or gratuitous reordering (style, minor).\n"
    "NOT findings: updating a test's EXPECTED outcome (exception type, message, value) to the "
    "new library's documented behaviour while its inputs and call are unchanged; raising the "
    "exception type a library's own validation hooks document (for example ValueError inside "
    "a validator that the framework wraps); idiomatic use of names present in the installed "
    "API list. Do not speculate about callers that do not appear in the diff or the tests.\n"
    "Compare defaults, validation bounds and signatures line by line: a default value or a "
    "constraint changed when no documented change requires it is a behavior_change (major). "
    "Re-adding a removed or deprecated name as a wrapper (for example defining parse_obj to "
    "call model_validate) is a compat_shim (major). "
    "Be concrete: name the file and quote the line. Use approve when the suite is green and "
    "every remaining concern is minor; reserve request_changes for correctness problems you "
    "can point to."
)

AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["supported", "contradicted", "unverifiable", "revised"],
        },
        "reason": {"type": "string"},
        "revised_title": {"type": "string"},
        "revised_detail": {"type": "string"},
        "revised_severity": {"type": "string", "enum": list(SEVERITIES)},
    },
    "required": ["status", "reason", "revised_title", "revised_detail", "revised_severity"],
    "additionalProperties": False,
}

AUDIT_PROMPT = (
    "You are the Auditor in Upshift. A reviewer raised ONE blocking finding on a "
    "dependency-upgrade diff. Check that single claim against the evidence and nothing else: "
    "the unified diff is authoritative for what changed, the installed public API list is "
    "authoritative for what exists in the new version, and the project's full test suite "
    "passes after the change. Answer: supported (the diff proves the claim as worded); "
    "contradicted (the diff or API list disproves it: a name said to be removed is still "
    "present, a rule the claim asserts about the library is not how the installed version "
    "behaves, or the test suite exercises exactly that path and passes); unverifiable "
    "(plausible, but nothing here proves it); or revised (the claim as worded is wrong, but "
    "the same lines show a different, real problem: restate it precisely with the right "
    "severity). The API list contains MODULE-LEVEL names only: a method or attribute of a "
    "class (for example app.errorhandler on Flask) is never in it, so its absence from the "
    "list proves nothing; a claim that such a member does not exist is unverifiable at best, "
    "and contradicted if the test suite exercises it and passes. Quote the lines you rely "
    "on. Be literal."
)


@dataclass
class Finding:
    severity: str
    kind: str
    file: str
    title: str
    detail: str = ""
    suggestion: str = ""
    source: str = "model"  # model | lint | audit
    line: int | None = None
    audit_status: str = ""  # supported | contradicted | unverifiable | revised


@dataclass
class ReviewReport:
    verdict: str  # approve | request_changes | skipped
    summary: str
    findings: list[Finding] = field(default_factory=list)
    model: str | None = None
    confidence: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocker"]

    @property
    def actionable(self) -> list[Finding]:
        return [
            f for f in self.findings
            if f.severity in ("blocker", "major") or f.source == "lint"
        ]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blocker_count"] = len(self.blockers)
        return data


class Reviewer:
    agent = "reviewer"

    def __init__(
        self,
        llm: TokenFactoryClient,
        router: ModelRouter,
        executor: Executor,
        bus: EventBus | None = None,
        image: str = "python:3.12-slim",
    ) -> None:
        self.llm = llm
        self.router = router
        self.executor = executor
        self.bus = bus
        self.image = image

    async def _emit(
        self, run_id: str | None, type_: EventType, node_id: str | None, **payload: Any
    ) -> None:
        if self.bus is None or run_id is None:
            return
        await self.bus.publish(
            Event(run_id=run_id, type=type_, agent=self.agent, node_id=node_id, payload=payload)
        )

    # ---- deterministic lint -------------------------------------------------

    async def lint(self, workspace: Path, files: list[str]) -> list[Finding]:
        python_files = [f for f in files if f.endswith(".py") and (workspace / f).is_file()]
        if not python_files:
            return []
        quoted = " ".join(shlex.quote(f) for f in python_files)
        command = "\n".join([
            "export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore",
            "mkdir -p .upshift",
            "pip install -q ruff >/dev/null 2>&1",
            f"ruff check --select {LINT_SELECT} --output-format json {quoted} "
            f"> {LINT_PATH} 2>/dev/null",
            "exit 0",
        ])
        result = await self.executor.run(
            ExecutionRequest(
                workspace=workspace, command=command, timeout_seconds=300, image=self.image,
                collect=[LINT_PATH], cpu=1.0, memory="1g",
            )
        )
        raw = result.artifacts.get(LINT_PATH, "")
        return parse_lint(raw)

    # ---- changed files, as they are now ---------------------------------------

    @staticmethod
    def changed_files(workspace: Path, surgery: SurgeryResult) -> dict[str, str]:
        """Full post-edit contents of the changed Python files, within a size budget.

        Diff hunks alone invite claims like "field X was removed" about code that simply
        sits outside the hunk; the full file settles that.
        """
        files: dict[str, str] = {}
        total = 0
        for rel in surgery.files_changed:
            path = workspace / rel
            if not rel.endswith(".py") or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_FILE_CHARS:
                text = text[:MAX_FILE_CHARS] + "\n... (truncated)\n"
            if total + len(text) > MAX_FILES_CHARS:
                break
            files[rel] = text
            total += len(text)
        return files

    @staticmethod
    def files_section(files: dict[str, str]) -> list[str]:
        if not files:
            return []
        parts = [
            "",
            "Changed files AFTER the edits, in full (authoritative for what the code now "
            "contains; never claim a name, field, decorator or method is missing if it "
            "appears here):",
        ]
        for rel, text in files.items():
            parts.append(f"=== {rel} ===\n{text}")
        return parts

    # ---- audit of findings that assert facts -------------------------------------

    async def audit(
        self,
        finding: Finding,
        surgery: SurgeryResult,
        spec: Any,
        run_id: str | None,
        budget: Budget | None,
        files: dict[str, str] | None = None,
    ) -> Finding:
        """Fact-check one blocking finding in a fresh, focused context.

        A blocker that the diff contradicts becomes a minor note; one nothing proves becomes
        major; one that is wrong as worded but points at a real problem is restated.
        """
        parts = [
            f"Finding under audit ({finding.severity}/{finding.kind}) in {finding.file}:",
            f"Title: {finding.title}",
            f"Detail: {finding.detail}",
            "",
            "The project's full test suite passes after this diff.",
        ]
        if surgery.api_surface:
            parts.append("\nInstalled public API of the upgraded package (authoritative):")
            for module, info in surgery.api_surface.items():
                names = info.get("names") or []
                parts.append(f"{module} {info.get('version') or ''}: {', '.join(names[:400])}")
        parts += self.files_section(files or {})
        parts += ["", "Unified diff:", surgery.diff[:MAX_DIFF_CHARS]]
        try:
            data = await complete_json(
                self.llm, spec,
                [{"role": "system", "content": AUDIT_PROMPT},
                 {"role": "user", "content": "\n".join(parts)}],
                schema_name="audit", schema=AUDIT_SCHEMA, max_tokens=1_500,
                agent=self.agent, run_id=run_id, budget=budget,
            )
        except Exception as exc:  # noqa: BLE001 - an unaudited blocker stays a blocker
            log.warning("reviewer: audit failed for %r: %s", finding.title, exc)
            return finding
        status = str(data.get("status", ""))
        reason = str(data.get("reason", ""))[:600]
        log.info("reviewer: audit of %r -> %s", finding.title, status)
        if status == "contradicted":
            return replace(
                finding, severity="minor", kind="style", source="audit", audit_status=status,
                title=f"{finding.title} (not supported by the diff)",
                detail=f"{finding.detail}\nAudit: {reason}",
            )
        if status == "unverifiable":
            return replace(
                finding, severity="major", source="audit", audit_status=status,
                detail=f"{finding.detail}\nAudit: unverified. {reason}",
            )
        if status == "revised":
            severity = str(data.get("revised_severity", "major"))
            return replace(
                finding, severity=severity if severity in SEVERITIES else "major",
                source="audit", audit_status=status,
                title=str(data.get("revised_title") or finding.title).strip()[:200],
                detail=f"{str(data.get('revised_detail') or finding.detail)[:1_000]}"
                       f"\nAudit: {reason}",
            )
        return replace(finding, source="audit", audit_status="supported")

    # ---- model review -------------------------------------------------------

    def build_prompt(
        self,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        surgery: SurgeryResult,
        lint: list[Finding],
        files: dict[str, str] | None = None,
    ) -> str:
        before = surgery.after_bump or {}
        after = surgery.final or {}
        parts = [
            f"Upgrade under review: {dep.name} {dep.current} -> {dep.latest}",
            f"Tests after the bare version bump: {before.get('passed', 0)} passing, "
            f"{before.get('failure_count', 0)} failing of {before.get('tests', 0)}",
            f"Tests after the agent's edits: {after.get('passed', 0)} passing, "
            f"{after.get('failure_count', 0)} failing of {after.get('tests', 0)}",
            "",
            "Documented changes the edits should reflect:",
        ]
        parts += [
            f"- [{e.category}] {e.title}: {e.migration} ({e.evidence_url})"[:400]
            for e in dossier.relevant[:25]
        ] or ["- (none)"]
        parts += ["", "Edits the agent recorded (path, justification):"]
        for attempt in surgery.attempts:
            for edit in attempt.edits:
                if edit.status == "applied":
                    parts.append(f"- {edit.path}: {edit.justification}")
        if lint:
            parts += ["", "Deterministic lint findings on changed files (already confirmed):"]
            parts += [f"- {f.file}:{f.line} {f.title}" for f in lint]
        if surgery.api_surface:
            parts += [
                "",
                "Installed public API of the upgraded package (authoritative). Do not claim an "
                "import path is wrong, or that a method requires an argument, unless this list "
                "or the diff itself proves it:",
            ]
            for module, info in surgery.api_surface.items():
                names = info.get("names") or []
                parts.append(f"{module} {info.get('version') or ''}: {', '.join(names[:400])}")
        parts += self.files_section(files or {})
        parts += ["", "Unified diff:", surgery.diff[:MAX_DIFF_CHARS]]
        if len(surgery.diff) > MAX_DIFF_CHARS:
            parts.append(f"... diff truncated ({len(surgery.diff)} chars total)")
        return "\n".join(parts)

    async def review(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        surgery: SurgeryResult,
        run_id: str | None = None,
        budget: Budget | None = None,
    ) -> ReviewReport:
        node = f"dep:{dep.name}"
        cost_before = self.llm.total_cost_usd
        lint = await self.lint(workspace, surgery.files_changed)
        code_changed = [f for f in surgery.files_changed if f.endswith(".py")]
        if not surgery.success or not code_changed:
            reason = "upgrade did not succeed" if not surgery.success else "manifest-only change"
            report = ReviewReport("skipped", reason, lint)
            await self._emit(run_id, EventType.REVIEW_VERDICT, node, **report.to_dict())
            return report

        files = self.changed_files(workspace, surgery)
        prompt = self.build_prompt(dep, dossier, surgery, lint, files)
        try:
            decision = self.router.route(
                TaskClass.REVIEW, required_caps={"json_schema"}, budget=budget,
                min_context_tokens=len(prompt) // 3 + MAX_OUTPUT_TOKENS,
            )
            data = await complete_json(
                self.llm, decision.model,
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": prompt}],
                schema_name="review", schema=REVIEW_SCHEMA, max_tokens=MAX_OUTPUT_TOKENS,
                agent=self.agent, run_id=run_id, budget=budget,
            )
        except Exception as exc:  # noqa: BLE001 - review failure must not sink the run
            log.exception("reviewer failed for %s", dep.name)
            report = ReviewReport(
                "skipped", "model review failed", lint, error=f"{type(exc).__name__}: {exc}",
                cost_usd=round(self.llm.total_cost_usd - cost_before, 6),
            )
            await self._emit(run_id, EventType.REVIEW_VERDICT, node, **report.to_dict())
            return report

        findings = list(lint)
        for item in data.get("findings", []) or []:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "minor")).lower()
            kind = str(item.get("kind", "other")).lower()
            finding = Finding(
                severity=severity if severity in SEVERITIES else "minor",
                kind=kind if kind in KINDS else "other",
                file=str(item.get("file", "")),
                title=str(item.get("title", "")).strip()[:200],
                detail=str(item.get("detail", ""))[:1_000],
                suggestion=str(item.get("suggestion", ""))[:1_000],
            )
            finding = calibrate(finding)
            factual = finding.severity == "major" and finding.kind in AUDITED_KINDS
            if finding.severity == "blocker" or factual:
                finding = await self.audit(
                    finding, surgery, decision.model, run_id, budget, files
                )
            findings.append(finding)
        audited = [f for f in findings if f.source == "audit"]
        if audited:
            await self._emit(
                run_id, EventType.AGENT_MESSAGE, node,
                message=f"audited {len(audited)} finding(s): " + ", ".join(
                    f"{f.file}: {f.audit_status}" for f in audited
                ),
            )
        verdict = str(data.get("verdict", "approve"))
        if any(f.severity in ("blocker", "major") for f in findings):
            verdict = "request_changes"
        report = ReviewReport(
            verdict=verdict if verdict in ("approve", "request_changes") else "approve",
            summary=str(data.get("summary", "")),
            findings=findings,
            model=decision.model.id,
            confidence=float(data.get("confidence", 0) or 0),
            cost_usd=round(self.llm.total_cost_usd - cost_before, 6),
        )
        await self._emit(run_id, EventType.REVIEW_VERDICT, node, **report.to_dict())
        return report


EXPECTED_OUTCOME = re.compile(
    r"expected (exception|error|outcome|value)|exception type|"
    r"from \w*(Error|Exception) to \w*(Error|Exception)|"
    r"\w+Error (instead of|rather than|to) \w+Error",
    re.IGNORECASE,
)


def calibrate(finding: Finding) -> Finding:
    """Deterministic guard against the reviewer's most common false positive.

    A test whose expected exception or value was updated is not a weakened test; the prompt
    says so, but models still report it. Such findings are kept, visibly, as minor notes.
    """
    text = f"{finding.title} {finding.detail}"
    if finding.kind == "weakened_test" and EXPECTED_OUTCOME.search(text):
        return Finding(
            severity="minor", kind="style", file=finding.file,
            title=f"{finding.title} (expected-outcome update; not a weakened test)",
            detail=finding.detail, suggestion=finding.suggestion, source=finding.source,
            line=finding.line,
        )
    return finding


def parse_lint(raw: str) -> list[Finding]:
    if not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    findings: list[Finding] = []
    for item in items if isinstance(items, list) else []:
        code = str(item.get("code", ""))
        filename = str(item.get("filename", "")).replace("\\", "/")
        if filename.startswith("/work/"):
            filename = filename[len("/work/"):]
        location = item.get("location") or {}
        findings.append(
            Finding(
                severity="major" if code == "F821" else "minor",
                kind="style",
                file=filename,
                title=f"{code} {item.get('message', '')}".strip(),
                detail="reported by ruff on the changed file",
                suggestion=str((item.get("fix") or {}).get("message") or ""),
                source="lint",
                line=int(location.get("row", 0)) or None,
            )
        )
    return findings
