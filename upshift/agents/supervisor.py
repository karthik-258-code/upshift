"""Supervisor: plan the run, drive one dependency at a time through the agent chain.

Scout -> (per dependency, in a fresh workspace copy) Historian -> Surgeon -> Reviewer ->
polish -> Scribe -> merge request. One budget for the whole run; everything is emitted as
events so the UI can watch live or replay later.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from upshift.agents.historian import Historian
from upshift.agents.reviewer import Reviewer
from upshift.agents.scout import DependencyAssessment, Scout, ScoutReport
from upshift.agents.scribe import Scribe
from upshift.agents.surgeon import Surgeon
from upshift.agents.verifier import Verifier
from upshift.config import Settings
from upshift.core.budget import Budget
from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType, Run, RunStatus
from upshift.core.store import EventStore
from upshift.core.workspace import prepare_workspace
from upshift.ecosystems.python import normalize
from upshift.llm.client import TokenFactoryClient
from upshift.llm.router import ModelRouter
from upshift.sandbox.base import Executor
from upshift.tools.cache import JsonCache
from upshift.tools.gitlab_mr import GitLabClient, GitLabError
from upshift.tools.pypi import PyPIClient
from upshift.tools.tavily_extract import TavilyExtractTool
from upshift.tools.tavily_search import TavilySearchTool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Policy:
    bumps: frozenset[str]
    max_dependencies: int


POLICIES: dict[str, Policy] = {
    "conservative": Policy(frozenset({"minor", "patch"}), 3),
    "balanced": Policy(frozenset({"major", "minor", "patch"}), 3),
    "aggressive": Policy(frozenset({"major", "minor", "patch"}), 8),
}


def select_candidates(
    report: ScoutReport, policy_name: str, only: list[str] | None = None
) -> list[DependencyAssessment]:
    policy = POLICIES.get(policy_name, POLICIES["balanced"])
    candidates = report.actionable  # already sorted by risk, highest first
    if only:
        wanted = {normalize(name) for name in only}
        return [d for d in candidates if d.name in wanted]
    return [d for d in candidates if d.bump in policy.bumps][: policy.max_dependencies]


@dataclass
class DependencyOutcome:
    name: str
    current: str | None
    target: str | None
    success: bool
    files_changed: list[str] = field(default_factory=list)
    attempts: int = 0
    cost_usd: float = 0.0
    review_verdict: str | None = None
    review_findings: int = 0
    merge_request_url: str | None = None
    merge_request: dict[str, Any] | None = None
    error: str | None = None
    diff: str = ""
    failures: list[dict[str, str]] = field(default_factory=list)
    attempt_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _error_line(message: str) -> str:
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    return next((ln for ln in lines if ln.startswith("E ")), lines[0] if lines else "")


class Supervisor:
    agent = "supervisor"

    def __init__(
        self,
        *,
        settings: Settings,
        store: EventStore,
        bus: EventBus,
        llm: TokenFactoryClient,
        router: ModelRouter,
        pypi: PyPIClient,
        search: TavilySearchTool,
        extract: TavilyExtractTool,
        executor: Executor,
        gitlab: GitLabClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bus = bus
        self.llm = llm
        self.router = router
        self.pypi = pypi
        self.search = search
        self.extract = extract
        self.executor = executor
        self.gitlab = gitlab

    async def _emit(self, run_id: str, type_: EventType, **payload: Any) -> None:
        node = payload.pop("_node", None)
        await self.bus.publish(
            Event(run_id=run_id, type=type_, agent=self.agent, node_id=node, payload=payload)
        )

    async def run(self, run: Run) -> dict[str, Any]:
        budget = Budget(run.budget_cap_usd)
        self.store.set_status(run.id, RunStatus.RUNNING)
        await self._emit(
            run.id, EventType.RUN_STARTED, repo_url=run.repo_url, policy=run.policy,
            budget_cap_usd=run.budget_cap_usd, packages=run.packages,
            target_project=run.target_project,
        )
        outcomes: list[DependencyOutcome] = []
        candidates: list[DependencyAssessment] = []
        workspaces_dir = self.settings.data_dir / "workspaces"
        base = await prepare_workspace(run.repo_url, workspaces_dir)
        try:
            report = await Scout(self.pypi, self.bus).assess(base.path, run.id)
            candidates = select_candidates(report, run.policy, run.packages or None)
            await self._emit(
                run.id, EventType.AGENT_MESSAGE,
                message=f"planning {len(candidates)} upgrade(s) under policy {run.policy}",
                candidates=[c.name for c in candidates],
            )
            self.store.set_status(
                run.id, RunStatus.RUNNING, scout=report.to_dict(),
                candidates=[c.name for c in candidates],
            )
            for dep in candidates:
                if budget.exhausted:
                    await self._emit(
                        run.id, EventType.BUDGET_WARNING, threshold_pct=100,
                        message=f"budget exhausted before {dep.name}",
                    )
                    break
                copy = await prepare_workspace(str(base.path), workspaces_dir)
                try:
                    outcome = await self._upgrade_one(copy.path, dep, run, budget)
                except Exception as exc:  # noqa: BLE001 - one dependency must not sink the run
                    log.exception("upgrade of %s crashed", dep.name)
                    outcome = DependencyOutcome(
                        dep.name, dep.current, dep.latest, False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    copy.cleanup()
                outcomes.append(outcome)
                self.store.set_status(
                    run.id, RunStatus.RUNNING,
                    dependencies=[o.to_dict() for o in outcomes], spent_usd=budget.spent_usd,
                )
        finally:
            base.cleanup()

        succeeded = [o for o in outcomes if o.success]
        summary = {
            "candidates": len(candidates),
            "succeeded": len(succeeded),
            "failed": len(outcomes) - len(succeeded),
            "merge_requests": [o.merge_request_url for o in succeeded if o.merge_request_url],
            "spent_usd": round(budget.spent_usd, 4),
            "budget_cap_usd": budget.cap_usd,
        }
        status = RunStatus.SUCCEEDED if (succeeded or not outcomes) else RunStatus.FAILED
        record = self.store.set_status(
            run.id, status, dependencies=[o.to_dict() for o in outcomes],
            spent_usd=budget.spent_usd, result=summary,
        )
        if record is not None:
            record.spent_usd = budget.spent_usd
            self.store.update_run(record)
        await self._emit(run.id, EventType.RUN_FINISHED, status=status.value, **summary)
        return summary

    async def _upgrade_one(
        self, workspace: Path, dep: DependencyAssessment, run: Run, budget: Budget
    ) -> DependencyOutcome:
        node = f"dep:{dep.name}"
        cost_before = self.llm.total_cost_usd
        historian = Historian(self.llm, self.router, self.search, self.extract, self.bus)
        dossier = await historian.research(dep, run.id, budget)

        verifier = Verifier(self.executor, self.bus)
        surgeon = Surgeon(
            self.llm, self.router, verifier, self.bus, self.pypi, search=self.search
        )
        surgery = await surgeon.upgrade(workspace, dep, dossier, run.id, budget)

        review = None
        if surgery.success:
            reviewer = Reviewer(self.llm, self.router, self.executor, self.bus)
            review = await reviewer.review(workspace, dep, dossier, surgery, run.id, budget)
            if review.actionable:
                polish = await surgeon.polish(
                    workspace, dep, dossier, surgery,
                    [asdict(f) for f in review.actionable], run.id, budget,
                )
                if polish is not None and not polish.rolled_back and any(
                    e.status == "applied" for e in polish.edits
                ):
                    # the diff changed: the verdict in the MR must describe what ships
                    review = await reviewer.review(
                        workspace, dep, dossier, surgery, run.id, budget
                    )

        final = surgery.final or {}
        outcome = DependencyOutcome(
            name=dep.name, current=dep.current, target=dep.latest, success=surgery.success,
            files_changed=surgery.files_changed, attempts=len(surgery.attempts),
            review_verdict=review.verdict if review else None,
            review_findings=len(review.findings) if review else 0,
            error=surgery.error, diff=surgery.diff,
            failures=[
                {"test": f["test"], "error": _error_line(f["message"])[:200]}
                for f in final.get("failures", [])[:10]
            ],
            attempt_log=[
                f"{a.number}: {a.model.split('/')[-1]} "
                f"{sum(e.status == 'applied' for e in a.edits)} applied/"
                f"{sum(e.status == 'rejected' for e in a.edits)} rejected -> "
                + (
                    f"{a.verification.get('passed', 0)} passing, "
                    f"{a.verification.get('failure_count', 0)} failing"
                    if a.verification else "not verified"
                )
                + (f" (rolled back: {a.rollback_reason})" if a.rolled_back else "")
                + "".join(
                    f" | rejected {e.path}: {e.reason}" for e in a.edits if e.status == "rejected"
                )
                + (
                    " | first error: "
                    + _error_line(a.verification["failures"][0]["message"])[:140]
                    if a.verification and a.verification.get("failures") else ""
                )
                for a in surgery.attempts
            ],
        )
        if surgery.success:
            draft = await Scribe(self.llm, self.router, self.bus).write(
                dep, dossier, surgery, review, run.id, budget
            )
            outcome.merge_request = draft.to_dict()
            target = run.target_project or GitLabClient.project_path_from_url(run.repo_url)
            if self.gitlab is not None and target:
                files: dict[str, str | None] = {}
                for rel in surgery.files_changed:
                    path = workspace / rel
                    files[rel] = (
                        path.read_text(encoding="utf-8", errors="replace")
                        if path.is_file() else None
                    )
                try:
                    info = await self.gitlab.publish(target, draft, files)
                    outcome.merge_request_url = info.web_url
                    outcome.merge_request.update(info.to_dict())
                    await self._emit(run.id, EventType.MR_OPENED, _node=node, **info.to_dict())
                except GitLabError as exc:
                    log.warning("merge request publish failed: %s", exc)
                    await self._emit(
                        run.id, EventType.AGENT_MESSAGE, _node=node,
                        message=f"merge request not published: {exc}",
                    )
            else:
                await self._emit(
                    run.id, EventType.AGENT_MESSAGE, _node=node,
                    message="merge request drafted (no GitLab target configured)",
                )
        outcome.cost_usd = round(self.llm.total_cost_usd - cost_before, 6)
        return outcome


def build_supervisor(
    settings: Settings,
    store: EventStore,
    bus: EventBus,
    llm: TokenFactoryClient,
    router: ModelRouter,
    executor: Executor,
) -> Supervisor:
    tavily_cache = JsonCache(settings.cache_dir / "tavily", settings.tavily_cache_ttl_hours)
    gitlab = (
        GitLabClient(settings.gitlab_url, settings.gitlab_token)
        if settings.gitlab_token else None
    )
    return Supervisor(
        settings=settings, store=store, bus=bus, llm=llm, router=router,
        pypi=PyPIClient(cache=JsonCache(settings.cache_dir / "pypi", ttl_hours=24)),
        search=TavilySearchTool(settings.tavily_api_key, cache=tavily_cache),
        extract=TavilyExtractTool(
            settings.tavily_api_key, cache=tavily_cache, max_chars=400_000
        ),
        executor=executor, gitlab=gitlab,
    )
