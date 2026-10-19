from __future__ import annotations

import json
from pathlib import Path

import pytest

from upshift.llm.models import ModelCatalog

ROOT = Path(__file__).resolve().parent.parent
MODELS_FILE = ROOT / "config" / "models.yaml"


@pytest.fixture
def catalog() -> ModelCatalog:
    """Catalog without probe results: documented capabilities apply."""
    return ModelCatalog.load(MODELS_FILE, capabilities_file=None)


@pytest.fixture
def probed_catalog(tmp_path: Path, catalog: ModelCatalog) -> ModelCatalog:
    """Catalog with a synthetic probe file. Lightning is deliberately absent (unverified)."""
    caps = {
        "probed_at": "test",
        "models": {
            catalog.models["nano"].id: {
                "chat": True,
                "tool_calling": True,
                "parallel_tool_calls": False,
                "json_object": True,
                "json_schema": False,
            },
            catalog.models["super"].id: {
                "chat": True,
                "tool_calling": True,
                "parallel_tool_calls": True,
                "json_object": True,
                "json_schema": False,
            },
            catalog.models["ultra"].id: {
                "chat": True,
                "tool_calling": True,
                "parallel_tool_calls": True,
                "json_object": True,
                "json_schema": True,
            },
        },
    }
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(caps), encoding="utf-8")
    return ModelCatalog.load(MODELS_FILE, path)
