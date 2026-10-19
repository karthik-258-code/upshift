"""Publish an upgrade as a GitLab merge request: branch, single commit, MR."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from upshift.agents.scribe import MergeRequestDraft

log = logging.getLogger(__name__)


class GitLabError(RuntimeError):
    pass


@dataclass
class MergeRequestInfo:
    project: str
    iid: int
    web_url: str
    branch: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"PRIVATE-TOKEN": token}
        )

    @staticmethod
    def project_path_from_url(url: str) -> str | None:
        """https://gitlab.com/group/sub/repo.git -> group/sub/repo (None for non-GitLab)."""
        parsed = urlparse(url)
        if "gitlab" not in parsed.netloc:
            return None
        path = parsed.path.strip("/").split("/-/")[0].removesuffix(".git")
        return path or None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(
            method, f"{self.base_url}/api/v4{path}", **kwargs
        )
        if response.status_code >= 400:
            raise GitLabError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else None

    async def project(self, path: str) -> dict[str, Any]:
        return await self._request("GET", f"/projects/{quote(path, safe='')}")

    async def file_exists(self, project_id: int, path: str, ref: str) -> bool:
        response = await self._client.head(
            f"{self.base_url}/api/v4/projects/{project_id}/repository/files/"
            f"{quote(path, safe='')}",
            params={"ref": ref},
        )
        return response.status_code == 200

    async def create_branch(self, project_id: int, branch: str, ref: str) -> None:
        await self._request(
            "POST", f"/projects/{project_id}/repository/branches",
            params={"branch": branch, "ref": ref},
        )

    async def commit(
        self, project_id: int, branch: str, message: str, actions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"/projects/{project_id}/repository/commits",
            json={"branch": branch, "commit_message": message, "actions": actions},
        )

    async def create_merge_request(
        self, project_id: int, source: str, target: str, title: str, description: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"/projects/{project_id}/merge_requests",
            json={
                "source_branch": source, "target_branch": target, "title": title,
                "description": description, "remove_source_branch": True,
            },
        )

    async def publish(
        self, project_path: str, draft: MergeRequestDraft, files: dict[str, str | None]
    ) -> MergeRequestInfo:
        """Create a branch off the default branch, commit the changed files, open the MR."""
        project = await self.project(project_path)
        project_id = int(project["id"])
        default = str(project.get("default_branch") or "main")

        branch = draft.branch
        try:
            await self.create_branch(project_id, branch, default)
        except GitLabError as exc:
            if "already exists" not in str(exc):
                raise
            branch = f"{draft.branch}-{int(time.time())}"
            await self.create_branch(project_id, branch, default)

        actions: list[dict[str, Any]] = []
        for path, content in files.items():
            exists = await self.file_exists(project_id, path, default)
            if content is None:
                if exists:
                    actions.append({"action": "delete", "file_path": path})
                continue
            actions.append({
                "action": "update" if exists else "create",
                "file_path": path,
                "content": content,
            })
        if not actions:
            raise GitLabError("nothing to commit")
        await self.commit(project_id, branch, draft.commit_message, actions)
        merge_request = await self.create_merge_request(
            project_id, branch, default, draft.title, draft.description
        )
        return MergeRequestInfo(
            project=project_path, iid=int(merge_request["iid"]),
            web_url=str(merge_request["web_url"]), branch=branch,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
