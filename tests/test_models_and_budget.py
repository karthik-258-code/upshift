from __future__ import annotations

import pytest

from upshift.core.budget import Budget
from upshift.llm.models import ModelCatalog


def test_catalog_loads_all_tiers(catalog: ModelCatalog) -> None:
    assert {"nano", "lightning", "super", "ultra"} <= set(catalog.models)
    assert catalog.models["nano"].id == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert catalog.models["lightning"].context_tokens == 1_024_000
    assert not catalog.probed


def test_cost_uses_published_prices(catalog: ModelCatalog) -> None:
    assert catalog.models["nano"].cost(1_000_000, 1_000_000) == pytest.approx(0.30)
    assert catalog.models["ultra"].cost(500_000, 100_000) == pytest.approx(0.5 + 0.3)


def test_thinking_control_loaded_from_probe(tmp_path, catalog: ModelCatalog) -> None:
    import json

    from tests.conftest import MODELS_FILE

    data = {"models": {catalog.models["super"].id: {
        "chat": True, "thinking": {"detected": True, "control": "chat_template_kwargs"},
    }}}
    path = tmp_path / "caps.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = ModelCatalog.load(MODELS_FILE, path)
    assert loaded.models["super"].thinking_control == "chat_template_kwargs"
    assert loaded.models["ultra"].thinking_control == ""


def test_probe_results_override_documented_caps(probed_catalog: ModelCatalog) -> None:
    assert probed_catalog.probed
    assert "json_schema" in probed_catalog.capabilities("ultra")
    assert "json_schema" not in probed_catalog.capabilities("super")
    assert probed_catalog.capabilities("lightning") == set()
    assert not probed_catalog.is_available("lightning")


def test_budget_thresholds_and_exhaustion() -> None:
    budget = Budget(cap_usd=10.0)
    assert budget.spend(5.0) == [50]
    assert budget.spend(3.0) == [80]
    assert budget.spend(1.0) == [90]
    assert budget.spend(0.5) == []
    assert not budget.exhausted
    assert budget.spend(1.0) == []
    assert budget.exhausted
    assert budget.remaining_usd == 0.0
