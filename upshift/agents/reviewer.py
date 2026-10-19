"""Reviewer: deterministic lint on the changed files, then an adversarial model review.

The Surgeon optimises for a green test suite. The Reviewer's job is to catch what a green
suite cannot: weakened tests, silent behaviour changes, leftover compatibility shims,
missed call sites, and sloppy code (unused or misplaced imports).
"""

from __future__ import annotations

import json
import logging
import shlex
from dataclasses import asdict, dataclass, field
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
MAX_OUTPUT_TOKENS = 6_000
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
    "whose inputs or call under test were changed (weakened_test, always a blocker); silent "
    "behaviour changes versus the original code (behavior_change); deprecated or "
    "compatibility APIs left in place when the migration guide names a replacement "
    "(compat_shim); call sites of changed APIs that were not updated and are not covered by "
    "tests (missed_migration); security regressions; and code quality problems such as "
    "unused or mid-file imports, empty config objects, or a regex pattern where a Literal "
    "type is the idiomatic constraint (style, minor). Updating a test's EXPECTED outcome to "
    "documented new library behaviour is legitimate and must not be flagged. Be concrete: "
    "name the file and quote the line. Verdict is request_changes if any finding is a "
    "blocker or major."
)


@dataclass
class Finding:
    severity: str
    kind: str
    file: str
    title: str
    detail: str = ""
    suggestion: str = ""
    source: str = "model"  # model | lint
    line: int | None = None


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

    # ---- model review -------------------------------------------------------

    def build_prompt(
        self,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        surgery: SurgeryResult,
        lint: list[Finding],
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

        prompt = self.build_prompt(dep, dossier, surgery, lint)
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
            findings.append(
                Finding(
                    severity=severity if severity in SEVERITIES else "minor",
                    kind=kind if kind in KINDS else "other",
                    file=str(item.get("file", "")),
                    title=str(item.get("title", "")).strip()[:200],
                    detail=str(item.get("detail", ""))[:1_000],
                    suggestion=str(item.get("suggestion", ""))[:1_000],
                )
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
