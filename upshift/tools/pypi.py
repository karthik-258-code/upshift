"""PyPI JSON API client: latest stable version, release dates, changelog and repo URLs."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from upshift.ecosystems.python import normalize

from .cache import JsonCache

log = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/{name}/json"
CHANGELOG_HINT = re.compile(r"change|release|history|news|whatsnew|migration", re.IGNORECASE)
REPO_HINT = re.compile(
    r"https?://(?:github|gitlab)\.com/(?!sponsors/|orgs/|features/)[^/#?]+/[^/#?]+", re.IGNORECASE
)
REPO_KEY_HINT = re.compile(r"source|repo|code|github|gitlab", re.IGNORECASE)


@dataclass
class PackageInfo:
    name: str
    latest: str | None
    versions: list[str]
    summary: str = ""
    requires_python: str | None = None
    project_urls: dict[str, str] = field(default_factory=dict)
    changelog_url: str | None = None
    repo_url: str | None = None
    release_dates: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PyPIClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        cache: JsonCache | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "upshift/0.1 (dependency upgrade agent)"},
        )
        self._cache = cache

    async def info(self, name: str) -> PackageInfo | None:
        norm = normalize(name)
        key = JsonCache.key("pypi.info.v2", norm) if self._cache else None
        if key and self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return PackageInfo(**hit)
        try:
            response = await self._client.get(PYPI_URL.format(name=norm))
        except httpx.HTTPError as exc:
            log.warning("pypi lookup failed for %s: %s", norm, exc)
            return None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            log.warning("pypi returned %s for %s", response.status_code, norm)
            return None
        info = self.parse(norm, response.json())
        if key and self._cache is not None:
            self._cache.set(key, info.to_dict())
        return info

    @staticmethod
    def parse(name: str, data: dict[str, Any]) -> PackageInfo:
        meta = data.get("info") or {}
        stable: list[Version] = []
        dates: dict[str, str] = {}
        for version, files in (data.get("releases") or {}).items():
            try:
                parsed = Version(version)
            except InvalidVersion:
                continue
            if not files or all(f.get("yanked") for f in files):
                continue
            if parsed.is_prerelease or parsed.is_devrelease:
                continue
            stable.append(parsed)
            uploads = [f.get("upload_time_iso_8601") or f.get("upload_time") or "" for f in files]
            first = min((u for u in uploads if u), default="")
            if first:
                dates[str(parsed)] = first[:10]
        stable.sort()
        versions = [str(v) for v in stable]
        latest = versions[-1] if versions else (meta.get("version") or None)

        urls = {
            str(k): v for k, v in (meta.get("project_urls") or {}).items() if isinstance(v, str)
        }
        if meta.get("home_page") and "Homepage" not in urls:
            urls["Homepage"] = meta["home_page"]
        changelog = next((v for k, v in urls.items() if CHANGELOG_HINT.search(k)), None)
        if changelog is None:
            changelog = next((v for v in urls.values() if CHANGELOG_HINT.search(v)), None)
        ranked = sorted(urls.items(), key=lambda kv: 0 if REPO_KEY_HINT.search(kv[0]) else 1)
        repo = None
        for _, value in ranked:
            match = REPO_HINT.match(value)
            if match:
                repo = match.group(0).removesuffix(".git")
                break

        return PackageInfo(
            name=name,
            latest=latest,
            versions=versions,
            summary=meta.get("summary") or "",
            requires_python=meta.get("requires_python"),
            project_urls=urls,
            changelog_url=changelog,
            repo_url=repo,
            release_dates=dates,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
