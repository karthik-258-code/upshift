from __future__ import annotations

import base64
import json

import httpx
import pytest

from upshift.agents.scribe import MergeRequestDraft
from upshift.tools.github_pr import GitHubClient, GitHubError

PR_URL = "https://github.com/owner/repo/pull/3"


def _client(
    calls: list[tuple[str, str, dict]], branch_taken: bool = False
) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        path = request.url.path

        if path == "/repos/owner/repo" and request.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/owner/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "abc123"}})
        if path == "/repos/owner/repo/git/refs" and request.method == "POST":
            ref = body.get("ref", "")
            if branch_taken and ref == "refs/heads/upshift/pydantic-2.9.2":
                return httpx.Response(
                    422, json={"message": "Reference already exists"}
                )
            return httpx.Response(201, json={"ref": ref})
        if path == "/repos/owner/repo/git/blobs":
            content = base64.b64decode(body["content"]).decode()
            sha = f"blob_{hash(content) % 10000}"
            return httpx.Response(201, json={"sha": sha})
        if path == "/repos/owner/repo/git/trees":
            return httpx.Response(201, json={"sha": "tree_sha_001"})
        if path == "/repos/owner/repo/git/commits":
            return httpx.Response(201, json={"sha": "commit_sha_001"})
        if "/git/refs/heads/" in path and request.method == "PATCH":
            return httpx.Response(200, json={"ref": path})
        if path == "/repos/owner/repo/pulls":
            return httpx.Response(
                201, json={"number": 3, "html_url": PR_URL}
            )
        if path == "/repos/missing/project":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    return GitHubClient(
        "tok",
        base_url="https://api.github.com",
        client=httpx.AsyncClient(transport=transport),
    )


def _draft() -> MergeRequestDraft:
    return MergeRequestDraft("title", "desc", "upshift/pydantic-2.9.2", "msg")


def test_repo_from_url() -> None:
    assert GitHubClient.repo_from_url("https://github.com/owner/repo.git") == "owner/repo"
    assert GitHubClient.repo_from_url("https://github.com/owner/repo") == "owner/repo"
    assert GitHubClient.repo_from_url("https://gitlab.com/a/b") is None
    assert GitHubClient.repo_from_url("C:/local/path") is None


async def test_publish_creates_branch_commit_and_pr() -> None:
    calls: list[tuple[str, str, dict]] = []
    info = await _client(calls).publish(
        "owner/repo",
        _draft(),
        {"requirements.txt": "pydantic==2.9.2\n", "app/new.py": "X = 1\n"},
    )
    assert info.web_url == PR_URL and info.number == 3
    assert info.branch == "upshift/pydantic-2.9.2"

    branch_call = next(c for c in calls if c[1] == "/repos/owner/repo/git/refs" and c[0] == "POST")
    assert branch_call[2]["ref"] == "refs/heads/upshift/pydantic-2.9.2"
    assert branch_call[2]["sha"] == "abc123"

    tree_call = next(c for c in calls if c[1] == "/repos/owner/repo/git/trees")
    assert len(tree_call[2]["tree"]) == 2
    assert tree_call[2]["base_tree"] == "abc123"

    pr_call = next(c for c in calls if c[1] == "/repos/owner/repo/pulls")
    assert pr_call[2]["head"] == "upshift/pydantic-2.9.2"
    assert pr_call[2]["base"] == "main"


async def test_publish_renames_branch_when_taken() -> None:
    calls: list[tuple[str, str, dict]] = []
    info = await _client(calls, branch_taken=True).publish(
        "owner/repo", _draft(), {"requirements.txt": "x\n"}
    )
    assert info.branch.startswith("upshift/pydantic-2.9.2-")
    assert info.branch != _draft().branch


async def test_publish_handles_file_deletion() -> None:
    calls: list[tuple[str, str, dict]] = []
    await _client(calls).publish(
        "owner/repo",
        _draft(),
        {"keep.py": "content\n", "remove.py": None},
    )
    tree_call = next(c for c in calls if c[1] == "/repos/owner/repo/git/trees")
    items = tree_call[2]["tree"]
    assert len(items) == 2
    delete_item = next(i for i in items if i["path"] == "remove.py")
    assert delete_item["sha"] is None


async def test_publish_errors_surface() -> None:
    calls: list[tuple[str, str, dict]] = []
    with pytest.raises(GitHubError):
        await _client(calls).publish("missing/project", _draft(), {"a": "b"})
    with pytest.raises(GitHubError, match="nothing to commit"):
        await _client(calls).publish("owner/repo", _draft(), {})
