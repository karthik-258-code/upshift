"""Model routing policy: task class + attempt + budget + required capabilities -> model."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from upshift.core.budget import Budget

from .models import ModelCatalog, ModelSpec


class TaskClass(StrEnum):
    CLASSIFY = "classify"
    RANK = "rank"
    TRIAGE = "triage"
    INGEST_LONG = "ingest_long"
    CODEGEN = "codegen"
    REVIEW = "review"
    WRITE = "write"


# Ordered candidate lists per attempt (1-based). The last list repeats for later attempts.
# Tier 0 prefers Nemotron 3.5 Lightning: Nano pricing, 1M context, and it passed every
# capability probe. Nano remains a fallback until its Token Factory ID is confirmed.
POLICY: dict[TaskClass, list[list[str]]] = {
    TaskClass.CLASSIFY: [["lightning", "nano", "super"]],
    TaskClass.RANK: [["lightning", "nano", "super"]],
    TaskClass.TRIAGE: [["lightning", "nano", "super"]],
    TaskClass.INGEST_LONG: [["lightning", "nano", "super"]],
    TaskClass.CODEGEN: [["super", "ultra"], ["super", "ultra"], ["ultra", "super"]],
    TaskClass.REVIEW: [["ultra", "super"]],
    TaskClass.WRITE: [["super", "lightning", "nano"]],
}


class BudgetExhausted(RuntimeError):
    pass


class NoCapableModel(RuntimeError):
    pass


@dataclass(frozen=True)
class RoutingDecision:
    model: ModelSpec
    task: TaskClass
    attempt: int
    reason: str
    degraded: bool = False


class ModelRouter:
    def __init__(self, catalog: ModelCatalog, degrade_threshold: float = 0.10) -> None:
        self.catalog = catalog
        self.degrade_threshold = degrade_threshold

    def route(
        self,
        task: TaskClass,
        *,
        attempt: int = 1,
        required_caps: Iterable[str] = (),
        budget: Budget | None = None,
        min_context_tokens: int = 0,
    ) -> RoutingDecision:
        ladder = POLICY[task]
        candidates = ladder[min(max(attempt, 1), len(ladder)) - 1]
        needed = set(required_caps) | {"chat"}
        capable = [
            key
            for key in candidates
            if key in self.catalog.models
            and needed <= self.catalog.capabilities(key)
            and self.catalog.models[key].context_tokens >= min_context_tokens
        ]
        if not capable:
            raise NoCapableModel(
                f"no model for task={task.value} attempt={attempt} caps={sorted(needed)}"
            )
        if budget is not None:
            if budget.exhausted:
                raise BudgetExhausted(f"budget cap ${budget.cap_usd:.2f} reached")
            if budget.remaining_fraction < self.degrade_threshold:
                cheapest = min(capable, key=lambda k: self.catalog.models[k].output_price_per_m)
                return RoutingDecision(
                    model=self.catalog.models[cheapest],
                    task=task,
                    attempt=attempt,
                    reason="budget below threshold; degraded to cheapest capable model",
                    degraded=True,
                )
        return RoutingDecision(
            model=self.catalog.models[capable[0]], task=task, attempt=attempt, reason="policy"
        )
