from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from upshift.tools.base import Tool, ToolRegistry
from upshift.tools.cache import JsonCache
from upshift.tools.tavily_extract import TavilyExtractTool
from upshift.tools.tavily_search import TavilySearchTool

MIGRATION_URL = "https://docs.pydantic.dev/latest/migration/"


class FakeTavily:
    def __init__(self) -> None:
        self.search_calls = 0
        self.extract_calls = 0

    def search(self, **params: Any) -> dict[str, Any]:
        self.search_calls += 1
        return {
            "query": params["query"],
            "results": [
                {
                    "title": "Migration Guide - Pydantic",
                    "url": MIGRATION_URL,
                    "content": "Pydantic V2 renames .dict() to .model_dump() ...",
                    "score": 0.93,
                    "raw_content": None,
                }
            ],
            "response_time": 0.4,
        }

    def extract(self, urls: list[str], **params: Any) -> dict[str, Any]:
        self.extract_calls += 1
        return {
            "results": [{"url": urls[0], "raw_content": "# Migration\n" + "x" * 200}],
            "failed_results": [{"url": "https://bad.example", "error": "timeout"}],
        }


async def test_search_normalises_and_caches(tmp_path: Path) -> None:
    fake = FakeTavily()
    tool = TavilySearchTool(client=fake, cache=JsonCache(tmp_path, ttl_hours=1))
    first = await tool.search("pydantic 2 migration", include_domains=["docs.pydantic.dev"])
    assert first["cached"] is False
    assert first["results"][0]["url"] == MIGRATION_URL
    assert "raw_content" not in first["results"][0]

    second = await tool.search("pydantic 2 migration", include_domains=["docs.pydantic.dev"])
    assert second["cached"] is True
    assert fake.search_calls == 1 and tool.cache_hits == 1


async def test_search_error_is_structured() -> None:
    class Boom:
        def search(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("credits exhausted")

    result = await TavilySearchTool(client=Boom()).search("anything")
    assert result["results"] == [] and "credits exhausted" in result["error"]


async def test_extract_truncates_and_reports_failures() -> None:
    tool = TavilyExtractTool(client=FakeTavily(), max_chars=50)
    result = await tool.extract([MIGRATION_URL, "https://bad.example"])
    page = result["results"][0]
    assert page["truncated"] is True and len(page["content"]) == 50 and page["chars"] == 212
    assert result["failed"] == [{"url": "https://bad.example", "error": "timeout"}]


async def test_extract_rejects_empty_input() -> None:
    result = await TavilyExtractTool(client=FakeTavily()).extract(["", "   "])
    assert result["results"] == [] and "error" in result


def test_tool_schemas_are_openai_shaped() -> None:
    schema = TavilySearchTool(client=FakeTavily()).as_tool().to_openai()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "web_search"
    assert schema["function"]["parameters"]["required"] == ["query"]


async def test_registry_dispatches_and_never_raises() -> None:
    async def echo(value: str) -> dict[str, str]:
        return {"echo": value}

    async def boom() -> None:
        raise ValueError("nope")

    registry = ToolRegistry(
        [
            Tool("echo", "", {"type": "object", "properties": {}}, echo),
            Tool("boom", "", {"type": "object", "properties": {}}, boom),
        ]
    )
    assert len(registry.schemas()) == 2
    assert json.loads(await registry.call("echo", '{"value": "hi"}')) == {"echo": "hi"}
    assert "error" in json.loads(await registry.call("boom", "{}"))
    assert "unknown tool" in json.loads(await registry.call("missing", None))["error"]
    assert "error" in json.loads(await registry.call("echo", "[1, 2]"))
