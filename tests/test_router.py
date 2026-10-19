from __future__ import annotations

import pytest

from upshift.core.budget import Budget
from upshift.llm.models import ModelCatalog
from upshift.llm.router import BudgetExhausted, ModelRouter, NoCapableModel, TaskClass


def test_cheap_tasks_prefer_lightning(catalog: ModelCatalog) -> None:
    router = ModelRouter(catalog)
    for task in (TaskClass.CLASSIFY, TaskClass.RANK, TaskClass.TRIAGE, TaskClass.INGEST_LONG):
        assert router.route(task).model.key == "lightning"


def test_unverified_model_is_skipped_after_probe(probed_catalog: ModelCatalog) -> None:
    router = ModelRouter(probed_catalog)
    assert router.route(TaskClass.CLASSIFY).model.key == "nano"
    assert router.route(TaskClass.INGEST_LONG).model.key == "nano"


def test_codegen_escalation_ladder(catalog: ModelCatalog) -> None:
    router = ModelRouter(catalog)
    assert router.route(TaskClass.CODEGEN, attempt=1).model.key == "super"
    assert router.route(TaskClass.CODEGEN, attempt=2).model.key == "super"
    assert router.route(TaskClass.CODEGEN, attempt=3).model.key == "ultra"
    assert router.route(TaskClass.CODEGEN, attempt=9).model.key == "ultra"


def test_required_capability_filters_candidates(probed_catalog: ModelCatalog) -> None:
    decision = ModelRouter(probed_catalog).route(TaskClass.CODEGEN, required_caps={"json_schema"})
    assert decision.model.key == "ultra"


def test_no_capable_model_raises(probed_catalog: ModelCatalog) -> None:
    with pytest.raises(NoCapableModel):
        ModelRouter(probed_catalog).route(TaskClass.CLASSIFY, required_caps={"json_schema"})


def test_context_requirement(catalog: ModelCatalog) -> None:
    decision = ModelRouter(catalog).route(TaskClass.CODEGEN, min_context_tokens=500_000)
    assert decision.model.key == "ultra"


def test_budget_degrades_to_cheapest(catalog: ModelCatalog) -> None:
    budget = Budget(cap_usd=1.0)
    budget.spend(0.95)
    decision = ModelRouter(catalog).route(TaskClass.REVIEW, budget=budget)
    assert decision.degraded
    assert decision.model.key == "super"


def test_budget_exhausted_raises(catalog: ModelCatalog) -> None:
    budget = Budget(cap_usd=1.0)
    budget.spend(1.0)
    with pytest.raises(BudgetExhausted):
        ModelRouter(catalog).route(TaskClass.CLASSIFY, budget=budget)
