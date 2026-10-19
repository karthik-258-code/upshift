from __future__ import annotations

from pathlib import Path

import httpx

from upshift.tools.cache import JsonCache
from upshift.tools.pypi import PyPIClient

PYDANTIC = {
    "info": {
        "version": "2.9.2",
        "summary": "Data validation using Python type hints",
        "requires_python": ">=3.8",
        "project_urls": {
            "Funding": "https://github.com/sponsors/samuelcolvin",
            "Homepage": "https://docs.pydantic.dev/",
            "Source": "https://github.com/pydantic/pydantic/",
            "Changelog": "https://docs.pydantic.dev/latest/changelog/",
        },
    },
    "releases": {
        "1.10.13": [{"upload_time_iso_8601": "2023-09-27T10:00:00Z", "yanked": False}],
        "2.9.2": [{"upload_time_iso_8601": "2024-09-17T10:00:00Z", "yanked": False}],
        "2.10.0b1": [{"upload_time_iso_8601": "2024-11-01T10:00:00Z", "yanked": False}],
        "2.10.0": [{"upload_time_iso_8601": "2024-11-20T10:00:00Z", "yanked": True}],
        "not-a-version": [{}],
        "0.1": [],
    },
}


def _client(calls: list[str]) -> PyPIClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/pypi/pydantic/json":
            return httpx.Response(200, json=PYDANTIC)
        return httpx.Response(404)

    return PyPIClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_parse_latest_stable_and_urls() -> None:
    calls: list[str] = []
    info = await _client(calls).info("Pydantic")
    assert info is not None
    assert info.latest == "2.9.2"
    assert info.versions == ["1.10.13", "2.9.2"]  # pre-release, yanked, empty, junk excluded
    assert info.changelog_url == "https://docs.pydantic.dev/latest/changelog/"
    assert info.repo_url == "https://github.com/pydantic/pydantic"
    assert info.release_dates["2.9.2"] == "2024-09-17"
    assert calls == ["/pypi/pydantic/json"]


async def test_missing_package_is_none() -> None:
    assert await _client([]).info("definitely-not-a-package") is None


async def test_cache_short_circuits_network(tmp_path: Path) -> None:
    calls: list[str] = []
    cache = JsonCache(tmp_path, ttl_hours=1)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=PYDANTIC)

    client = PyPIClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), cache=cache
    )
    first = await client.info("pydantic")
    second = await client.info("pydantic")
    assert first == second and len(calls) == 1
