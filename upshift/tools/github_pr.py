"""Publish an upgrade as a GitHub pull request: branch, single commit, PR."""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from upshift.agents.scribe import MergeRequestDraft

log = logging.getLogger(__name__)


class GitHubError(RuntimeError):
    pass


@dataclass
class PullRequestInfo:
    repo: str
    number: int
    web_url: str
    branch: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitHubClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    @staticmethod
    def repo_from_url(url: str) -> str | None:
        """https://github.com/owner/repo.git -> owner/repo (None for non-GitHub)."""
        parsed = urlparse(url)
        if "github" not in parsed.netloc:
            return None
        parts = parsed.path.strip("/").removesuffix(".git").split("/")
        if len(parts) >= 2:
            return "/".join(parts[:2])
        return None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(
            method, f"{self.base_url}{path}", **kwargs
        )
        if response.status_code >= 400:
            raise GitHubError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else None

    async def get_repo(self, repo: str) -> dict[str, Any]:
        return await self._request("GET", f"/repos/{repo}")

    async def get_ref(self, repo: str, ref: str) -> str:
        data = await self._request("GET", f"/repos/{repo}/git/ref/heads/{ref}")
        return data["object"]["sha"]

    async def create_branch(self, repo: str, branch: str, sha: str) -> None:
        await self._request(
            "POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    async def create_blob(self, repo: str, content: str) -> str:
        data = await self._request(
            "POST", f"/repos/{repo}/git/blobs",
            json={
                "content": base64.b64encode(content.encode()).decode(),
                "encoding": "base64",
            },
        )
        return data["sha"]

    async def create_tree(
        self, repo: str, base_tree: str, tree_items: list[dict[str, Any]]
    ) -> str:
        data = await self._request(
            "POST", f"/repos/{repo}/git/trees",
            json={"base_tree": base_tree, "tree": tree_items},
        )
        return data["sha"]

    async def create_commit(
        self, repo: str, message: str, tree: str, parents: list[str]
    ) -> str:
        data = await self._request(
            "POST", f"/repos/{repo}/git/commits",
            json={"message": message, "tree": tree, "parents": parents},
        )
        return data["sha"]

    async def update_ref(self, repo: str, branch: str, sha: str) -> None:
        await self._request(
            "PATCH", f"/repos/{repo}/git/refs/heads/{branch}",
            json={"sha": sha},
        )

    async def create_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"/repos/{repo}/pulls",
            json={"head": head, "base": base, "title": title, "body": body},
        )

    async def publish(
        self, repo: str, draft: MergeRequestDraft, files: dict[str, str | None]
    ) -> PullRequestInfo:
        """Create branch, commit changed files in a single commit, open PR."""
        repo_data = await self.get_repo(repo)
        default = repo_data.get("default_branch", "main")
        base_sha = await self.get_ref(repo, default)

        branch = draft.branch
        try:
            await self.create_branch(repo, branch, base_sha)
        except GitHubError as exc:
            if "Reference already exists" not in str(exc):
                raise
            branch = f"{draft.branch}-{int(time.time())}"
            await self.create_branch(repo, branch, base_sha)

        tree_items: list[dict[str, Any]] = []
        for path, content in files.items():
            if content is None:
                tree_items.append({
                    "path": path, "mode": "100644", "type": "blob", "sha": None,
                })
            else:
                blob_sha = await self.create_blob(repo, content)
                tree_items.append({
                    "path": path, "mode": "100644", "type": "blob", "sha": blob_sha,
                })
        if not tree_items:
            raise GitHubError("nothing to commit")

        tree_sha = await self.create_tree(repo, base_sha, tree_items)
        commit_sha = await self.create_commit(
            repo, draft.commit_message, tree_sha, [base_sha]
        )
        await self.update_ref(repo, branch, commit_sha)

        pr = await self.create_pull_request(
            repo, branch, default, draft.title, draft.description
        )
        return PullRequestInfo(
            repo=repo, number=int(pr["number"]),
            web_url=str(pr["html_url"]), branch=branch,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
