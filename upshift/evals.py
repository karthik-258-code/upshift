"""Eval harness: run upgrade cases through the real pipeline and tabulate the outcome.

For every case the table shows what a dependabot-style bare bump would have left behind
(tests passing after only the pin changed) next to what Upshift delivered, plus attempts,
review verdict, cost and wall time. Results are written as JSON (one file per eval run) and
as a Markdown table; `evals/results/latest.md` is the one the README links to.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from upshift.core.events import EventType, Run

log = logging.getLogger(__name__)


@dataclass
class EvalCase:
    id: str
    repo: str
    package: str
    expect: str = "upgraded"  # upgraded | not-upgraded
    note: str = ""


@dataclass
class CaseResult:
    case: str
    package: str
    current: str | None
    target: str | None
    repeat: int
    success: bool
    expected: bool
    bare_passed: int
    bare_total: int
    final_passed: int
    final_total: int
    attempts: int
    review: str | None
    cost_usd: float
    duration_s: float
    run_id: str
    error: str | None = None
    manifest_changes: list[str] = field(default_factory=list)


def load_cases(path: Path) -> list[EvalCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = []
    for item in data.get("cases") or []:
        cases.append(EvalCase(
            id=str(item["id"]), repo=str(item["repo"]), package=str(item["package"]),
            expect=str(item.get("expect", "upgraded")), note=str(item.get("note", "")).strip(),
        ))
    return cases


def first_test_result(events: list[Any]) -> dict[str, Any]:
    """The verifier's first `test.result` on a run: the state right after the bare bump
    (or after co-upgrading pins when pip refused the install)."""
    for event in events:
        if event.type is EventType.TEST_RESULT:
            return dict(event.payload)
    return {}


def case_result(
    case: EvalCase, repeat: int, run: Run, events: list[Any], duration_s: float
) -> CaseResult:
    deps: list[dict[str, Any]] = run.summary.get("dependencies", [])
    dep = next((d for d in deps if d.get("name") == case.package), deps[0] if deps else {})
    bare = first_test_result(events)
    final = dep.get("final") or {}
    success = bool(dep.get("success"))
    final_passed = int(final.get("passed", 0)) if final else (
        int(bare.get("tests", 0)) if success and not dep.get("attempts") else 0
    )
    final_total = int(final.get("tests", 0)) if final else int(bare.get("tests", 0))
    return CaseResult(
        case=case.id, package=case.package, current=dep.get("current"), target=dep.get("target"),
        repeat=repeat, success=success, expected=(success == (case.expect == "upgraded")),
        bare_passed=int(bare.get("passed", 0)), bare_total=int(bare.get("tests", 0)),
        final_passed=final_passed, final_total=final_total,
        attempts=int(dep.get("attempts", 0)), review=dep.get("review_verdict"),
        cost_usd=float(dep.get("cost_usd", run.spent_usd) or 0.0),
        duration_s=round(duration_s, 1), run_id=run.id, error=dep.get("error"),
        manifest_changes=list(dep.get("manifest_changes") or []),
    )


def markdown_table(results: list[CaseResult], generated_at: str) -> str:
    lines = [
        "# Upshift eval results",
        "",
        f"Generated {generated_at}. Bare bump = tests passing after only the version pin "
        "changed (what a dependabot-style tool leaves you with). Upshift = after the agent's "
        "edits. Costs are Nebius Token Factory usage per case.",
        "",
        "| Case | Upgrade | Bare bump | Upshift | Attempts | Review | Cost | Time | Outcome |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        outcome = "pass" if r.expected else "FAIL"
        if not r.success and r.error:
            outcome += f" ({r.error[:60]})"
        lines.append(
            f"| {r.case}{f' #{r.repeat}' if r.repeat > 1 else ''} | {r.package} "
            f"{r.current or '?'} -> {r.target or '?'} | {r.bare_passed}/{r.bare_total} | "
            f"**{r.final_passed}/{r.final_total}** | {r.attempts} | {r.review or '-'} | "
            f"${r.cost_usd:.3f} | {int(r.duration_s // 60)}m{int(r.duration_s % 60):02d}s | "
            f"{outcome} |"
        )
    if results:
        successes = sum(r.success for r in results)
        attempts = [r.attempts for r in results if r.success]
        lines += [
            "",
            f"**{successes}/{len(results)} upgrades landed** "
            f"({100 * successes // len(results)}%). "
            + (
                f"Median attempts on success: {statistics.median(attempts):.0f}. "
                if attempts else ""
            )
            + f"Mean cost: ${statistics.mean(r.cost_usd for r in results):.3f}. "
            f"Total: ${sum(r.cost_usd for r in results):.2f} in "
            f"{sum(r.duration_s for r in results) / 60:.0f} minutes.",
        ]
    return "\n".join(lines) + "\n"


async def run_evals(
    cases: list[EvalCase],
    repeat: int,
    out_dir: Path,
    *,
    only: list[str] | None = None,
) -> list[CaseResult]:
    from upshift.agents.supervisor import build_supervisor
    from upshift.config import get_settings
    from upshift.core.bus import EventBus, usage_publisher
    from upshift.core.store import EventStore
    from upshift.llm.client import TokenFactoryClient
    from upshift.llm.models import ModelCatalog
    from upshift.llm.router import ModelRouter
    from upshift.sandbox.factory import build_executor

    settings = get_settings()
    store = EventStore(settings.runs_db)
    bus = EventBus(store)
    catalog = ModelCatalog.load(settings.models_file, settings.capabilities_file)
    llm = TokenFactoryClient(
        settings.nebius_api_key, settings.nebius_base_url,
        timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries,
        usage_hook=usage_publisher(bus),
    )
    executor = build_executor(settings)
    if not await executor.available():
        raise RuntimeError(f"sandbox executor {executor.name!r} is not available")
    supervisor = build_supervisor(settings, store, bus, llm, ModelRouter(catalog), executor)

    selected = [c for c in cases if not only or c.id in only]
    results: list[CaseResult] = []
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    for case in selected:
        for index in range(1, repeat + 1):
            run = store.create_run(Run(
                repo_url=case.repo, policy="balanced", packages=[case.package],
                budget_cap_usd=settings.default_budget_usd, target_project=None,
            ))
            log.info("eval %s #%d: run %s", case.id, index, run.id)
            started = time.perf_counter()
            try:
                await supervisor.run(run)
            except Exception as exc:  # noqa: BLE001 - one crash must not sink the eval
                log.exception("eval %s #%d crashed", case.id, index)
                record = store.get_run(run.id) or run
                record.summary.setdefault("dependencies", [])
                result = case_result(case, index, record, [], time.perf_counter() - started)
                result.error = f"{type(exc).__name__}: {exc}"[:200]
                results.append(result)
                continue
            record = store.get_run(run.id) or run
            result = case_result(
                case, index, record, store.events(run.id), time.perf_counter() - started
            )
            results.append(result)
            print(
                f"{case.id} #{index}: {'OK ' if result.success else 'FAIL'} "
                f"{result.bare_passed}/{result.bare_total} -> "
                f"{result.final_passed}/{result.final_total}  attempts={result.attempts} "
                f"review={result.review} ${result.cost_usd:.3f} {result.duration_s:.0f}s"
            )
            # write after every case so a long eval can be inspected (or aborted) midway
            payload = {
                "generated_at": generated_at, "repeat": repeat,
                "results": [asdict(r) for r in results],
            }
            (out_dir / f"{stamp}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            (out_dir / "latest.md").write_text(
                markdown_table(results, generated_at), encoding="utf-8"
            )
    return results
