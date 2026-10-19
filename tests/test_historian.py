from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from upshift.agents.historian import DOSSIER_SCHEMA, Historian, symbol_matches
from upshift.agents.scout import DependencyAssessment
from upshift.core.budget import Budget
from upshift.core.bus import EventBus
from upshift.core.events import EventType, Run
from upshift.core.store import EventStore
from upshift.llm.client import TokenFactoryClient
from upshift.llm.models import ModelCatalog
from upshift.llm.router import ModelRouter
from upshift.llm.structured import parse_json
from upshift.tools.tavily_extract import TavilyExtractTool
from upshift.tools.tavily_search import TavilySearchTool

MIGRATION = "https://docs.pydantic.dev/latest/migration/"
CHANGELOG = "https://docs.pydantic.dev/latest/changelog/"

MODEL_OUTPUT = {
    "summary": "Pydantic 2 is a rewrite with many renamed APIs.",
    "requires_python": "",
    "requires_new_dependencies": ["pydantic-settings"],
    "entries": [
        {
            "title": "BaseSettings moved to pydantic-settings",
            "category": "breaking",
            "affected_symbols": ["pydantic.BaseSettings"],
            "versions": "2.0",
            "migration": "pip install pydantic-settings; from pydantic_settings import BaseSettings",
            "evidence_url": MIGRATION,
            "quote": "BaseSettings has moved to the pydantic-settings package.",
            "applies_to_repo": True,
            "confidence": 0.98,
        },
        {
            "title": ".dict() deprecated in favour of .model_dump()",
            "category": "deprecation",
            "affected_symbols": ["BaseModel.dict"],
            "versions": "2.0",
            "migration": "Replace .dict() with .model_dump()",
            "evidence_url": MIGRATION,
            "quote": "dict() is deprecated, use model_dump() instead.",
            "applies_to_repo": True,
            "confidence": 0.95,
        },
        {
            "title": "validator deprecated for field_validator",
            "category": "deprecation",
            "affected_symbols": ["validator"],
            "versions": "2.0",
            "migration": "Use @field_validator",
            "evidence_url": MIGRATION,
            "quote": "validator is deprecated.",
            "applies_to_repo": False,
            "confidence": 0.9,
        },
        {
            "title": "Color type moved to pydantic-extra-types",
            "category": "breaking",
            "affected_symbols": ["pydantic.color.Color"],
            "versions": "2.0",
            "migration": "Install pydantic-extra-types",
            "evidence_url": CHANGELOG,
            "quote": "Color moved.",
            "applies_to_repo": False,
            "confidence": 0.9,
        },
        {
            "title": "Faster core in Rust",
            "category": "feature",
            "affected_symbols": [],
            "versions": "2.0",
            "migration": "",
            "evidence_url": CHANGELOG,
            "quote": "pydantic-core is written in Rust.",
            "applies_to_repo": False,
            "confidence": 0.99,
        },
    ],
}


class FakeTavily:
    def search(self, **params: Any) -> dict[str, Any]:
        return {
            "results": [
                {"title": "Stack Overflow", "url": "https://stackoverflow.com/q/1", "score": 0.9},
                {"title": "Migration Guide", "url": MIGRATION, "score": 0.8},
            ]
        }

    def extract(self, urls: list[str], **params: Any) -> dict[str, Any]:
        results = [
            {"url": u, "raw_content": f"# Page {u}\n" + "content " * 50}
            for u in urls
            if "stackoverflow" not in u
        ]
        failed = [{"url": u, "error": "blocked"} for u in urls if "stackoverflow" in u]
        return {"results": results, "failed_results": failed}


class FakeCompletions:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.requests.append(kwargs)
        text = self.outputs.pop(0)
        return ChatCompletion(
            id="x", object="chat.completion", created=0, model=kwargs["model"],
            choices=[Choice(index=0, finish_reason="stop",
                            message=ChatCompletionMessage(role="assistant", content=text))],
            usage=CompletionUsage(
                prompt_tokens=40_000, completion_tokens=1_500, total_tokens=41_500
            ),
        )


def _dep() -> DependencyAssessment:
    return DependencyAssessment(
        name="pydantic", source="requirements.txt", specifier="==1.10.13", current="1.10.13",
        latest="2.9.2", bump="major", risk=0.78, changelog_url=CHANGELOG,
        repo_url="https://github.com/pydantic/pydantic",
        usage={
            "files": ["pyshop/models.py", "pyshop/settings.py"],
            "symbols": {
                "pydantic.BaseModel": 6, "pydantic.Field": 5, "pydantic.validator": 3,
                "pydantic.BaseSettings": 2, "pydantic.root_validator": 1,
            },
        },
    )


def _historian(outputs: list[str], catalog: ModelCatalog, bus: EventBus | None = None):
    completions = FakeCompletions(outputs)
    fake_openai = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm = TokenFactoryClient("k", "https://example.test/v1/", client=fake_openai)
    tavily = FakeTavily()
    historian = Historian(
        llm, ModelRouter(catalog), TavilySearchTool(client=tavily),
        TavilyExtractTool(client=tavily), bus=bus,
    )
    return historian, completions


def test_symbol_matches() -> None:
    used = {"pydantic.BaseModel", "pydantic.validator", "pydantic.BaseSettings"}
    assert symbol_matches("pydantic.BaseSettings", used) == ["pydantic.BaseSettings"]
    assert symbol_matches("BaseSettings", used) == ["pydantic.BaseSettings"]
    assert symbol_matches("`validator()`", used) == ["pydantic.validator"]
    assert symbol_matches("pydantic.color.Color", used) == []
    assert symbol_matches("", used) == []


def test_parse_json_tolerates_fences_and_prose() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": [1, 2]} thanks') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_schema_is_strict_everywhere() -> None:
    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(DOSSIER_SCHEMA)


async def test_research_builds_dossier(catalog: ModelCatalog) -> None:
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    historian, completions = _historian([json.dumps(MODEL_OUTPUT)], catalog, bus)
    budget = Budget(cap_usd=2.0)

    dossier = await historian.research(_dep(), run_id=run.id, budget=budget)

    urls = [s.url for s in dossier.sources]
    assert urls[:2] == [CHANGELOG, "https://github.com/pydantic/pydantic/releases"]
    assert MIGRATION in urls and len(urls) <= 5
    fetched = {s.url: s.fetched for s in dossier.sources}
    assert fetched[CHANGELOG] and not fetched["https://stackoverflow.com/q/1"]
    assert len(dossier.search_queries) == 2 and "migration guide" in dossier.search_queries[0]

    assert dossier.error is None
    assert dossier.requires_new_dependencies == ["pydantic-settings"]
    assert dossier.cost_usd > 0 and budget.spent_usd == pytest.approx(dossier.cost_usd)
    assert dossier.model == catalog.models["lightning"].id  # long-ingestion tier

    titles = [e.title for e in dossier.entries]
    relevant = [e.title for e in dossier.relevant]
    assert titles[0].startswith("BaseSettings")  # relevant breaking first
    assert "validator deprecated for field_validator" in relevant  # matched by symbol
    assert ".dict() deprecated in favour of .model_dump()" in relevant  # model flagged
    assert "Color type moved to pydantic-extra-types" not in relevant
    assert "Faster core in Rust" not in relevant
    by_title = {e.title: e for e in dossier.entries}
    assert by_title["validator deprecated for field_validator"].matched_symbols == [
        "pydantic.validator"
    ]

    request = completions.requests[0]
    assert request["response_format"]["type"] == "json_schema"
    prompt = request["messages"][1]["content"]
    assert "pydantic.BaseSettings (x2)" in prompt and "=== SOURCE 1:" in prompt
    assert "stackoverflow" not in prompt

    types = [e.type for e in store.events(run.id)]
    assert types.count(EventType.TOOL_CALL) == 2
    assert types.count(EventType.EVIDENCE_FETCHED) == len(dossier.sources)
    assert EventType.LLM_CALL not in types  # no hook wired in this test
    assert types[-1] is EventType.DOSSIER_READY


async def test_research_retries_on_bad_json(catalog: ModelCatalog) -> None:
    historian, completions = _historian(
        ["Sure! Here is some text without JSON", json.dumps(MODEL_OUTPUT)], catalog
    )
    dossier = await historian.research(_dep())
    assert dossier.error is None and len(dossier.entries) == 5
    assert completions.requests[1]["response_format"] == {"type": "json_object"}


async def test_research_without_evidence(catalog: ModelCatalog) -> None:
    class Nothing:
        def search(self, **_: Any) -> dict[str, Any]:
            return {"results": []}

        def extract(self, urls: list[str], **_: Any) -> dict[str, Any]:
            return {"results": [], "failed_results": [{"url": u, "error": "x"} for u in urls]}

    completions = FakeCompletions([])
    llm = TokenFactoryClient(
        "k", "https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    historian = Historian(
        llm, ModelRouter(catalog), TavilySearchTool(client=Nothing()),
        TavilyExtractTool(client=Nothing()),
    )
    dossier = await historian.research(_dep())
    assert dossier.error == "no evidence could be fetched" and not completions.requests
