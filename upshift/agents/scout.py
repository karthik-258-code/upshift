"""Scout: discover dependencies, their latest versions, how the codebase uses them, and risk.

Deterministic by design. The model-driven ranking hook comes later; everything here is
reproducible and cheap, which is what the rest of the pipeline builds on.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType, utcnow
from upshift.ecosystems.python import Dependency, discover
from upshift.ecosystems.usage import PackageUsage, UsageScanner, import_names_for
from upshift.tools.pypi import PackageInfo, PyPIClient

log = logging.getLogger(__name__)

BUMP_WEIGHT = {"major": 0.6, "minor": 0.3, "patch": 0.1, "none": 0.0, "unknown": 0.2}


def bump_type(current: str | None, latest: str | None) -> str:
    if not current or not latest:
        return "unknown"
    try:
        cur, new = Version(current), Version(latest)
    except InvalidVersion:
        return "unknown"
    if new <= cur:
        return "none"
    if new.major != cur.major:
        return "major"
    if new.minor != cur.minor:
        return "minor"
    return "patch"


@dataclass
class DependencyAssessment:
    name: str
    source: str
    specifier: str
    current: str | None
    latest: str | None
    bump: str
    risk: float
    risk_factors: list[str] = field(default_factory=list)
    skip_reason: str | None = None
    summary: str = ""
    changelog_url: str | None = None
    repo_url: str | None = None
    project_urls: dict[str, str] = field(default_factory=dict)
    release_date_latest: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoutReport:
    ecosystem: str
    manifests: list[str]
    python_files: int
    dependencies: list[DependencyAssessment]
    generated_at: str

    @property
    def actionable(self) -> list[DependencyAssessment]:
        return [d for d in self.dependencies if d.skip_reason is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "manifests": self.manifests,
            "python_files": self.python_files,
            "generated_at": self.generated_at,
            "dependencies": [d.to_dict() for d in self.dependencies],
        }


def score(dep: Dependency, info: PackageInfo | None, usage: PackageUsage) -> DependencyAssessment:
    current = dep.pinned
    latest = info.latest if info else None
    bump = bump_type(current, latest)
    factors: list[str] = []
    skip: str | None = None

    if info is None:
        skip = "not found on PyPI"
    elif current is None:
        skip = "unpinned: no current version to compare"
    elif bump == "none":
        skip = "already at latest"

    risk = BUMP_WEIGHT[bump]
    if bump != "none" and bump != "unknown":
        factors.append(f"{bump} bump {current} -> {latest}")
    if current and current.startswith("0.") and bump == "minor":
        risk += 0.2
        factors.append("0.x minor bump; semver-major in practice")

    symbols = len(usage.symbols)
    files = len(usage.files)
    if symbols:
        risk += min(0.25, 0.03 * symbols)
        factors.append(f"{symbols} distinct symbols used across {files} files")
    elif bump not in ("none", "unknown"):
        risk *= 0.5
        factors.append("not imported anywhere (tooling or transitive)")
    if files > 3:
        risk += min(0.15, 0.03 * files)

    return DependencyAssessment(
        name=dep.name,
        source=dep.source,
        specifier=dep.specifier,
        current=current,
        latest=latest,
        bump=bump,
        risk=round(min(risk, 1.0), 2),
        risk_factors=factors,
        skip_reason=skip,
        summary=info.summary if info else "",
        changelog_url=info.changelog_url if info else None,
        repo_url=info.repo_url if info else None,
        project_urls=info.project_urls if info else {},
        release_date_latest=(info.release_dates.get(latest or "") if info else None),
        usage=usage.to_dict(),
    )


class Scout:
    agent = "scout"

    def __init__(
        self, pypi: PyPIClient, bus: EventBus | None = None, concurrency: int = 8
    ) -> None:
        self.pypi = pypi
        self.bus = bus
        self.concurrency = concurrency

    async def _emit(self, run_id: str | None, type_: EventType, **payload: Any) -> None:
        if self.bus is None or run_id is None:
            return
        node_id = payload.pop("_node", None)
        await self.bus.publish(
            Event(run_id=run_id, type=type_, agent=self.agent, node_id=node_id, payload=payload)
        )

    async def assess(self, workspace: Path, run_id: str | None = None) -> ScoutReport:
        manifests = discover(workspace)
        deps: dict[str, Dependency] = {}
        for manifest in manifests:
            for dep in manifest.dependencies:
                existing = deps.get(dep.name)
                if existing is None or (existing.pinned is None and dep.pinned is not None):
                    deps[dep.name] = dep
        await self._emit(
            run_id,
            EventType.AGENT_MESSAGE,
            message=f"Found {len(deps)} dependencies in {len(manifests)} manifest(s)",
            manifests=[m.path for m in manifests],
        )

        semaphore = asyncio.Semaphore(self.concurrency)

        async def lookup(name: str) -> PackageInfo | None:
            async with semaphore:
                return await self.pypi.info(name)

        names = sorted(deps)
        infos = dict(zip(names, await asyncio.gather(*(lookup(n) for n in names)), strict=True))

        scanner = UsageScanner()
        usages = scanner.scan(workspace, {n: import_names_for(n) for n in names})

        assessments = [score(deps[n], infos[n], usages[n]) for n in names]
        assessments.sort(key=lambda a: (a.skip_reason is not None, -a.risk, a.name))

        for assessment in assessments:
            await self._emit(
                run_id, EventType.DEP_DISCOVERED, _node=f"dep:{assessment.name}",
                **assessment.to_dict(),
            )

        report = ScoutReport(
            ecosystem="python",
            manifests=[m.path for m in manifests],
            python_files=scanner.files_scanned,
            dependencies=assessments,
            generated_at=utcnow().isoformat(),
        )
        await self._emit(
            run_id,
            EventType.AGENT_MESSAGE,
            message=(
                f"{len(report.actionable)} upgrade candidates, "
                f"{len(assessments) - len(report.actionable)} skipped"
            ),
        )
        return report
