from __future__ import annotations

import json

import httpx
import pytest

from upshift.agents.scout import DependencyAssessment, ScoutReport
from upshift.agents.scribe import MergeRequestDraft
from upshift.agents.supervisor import select_candidates
from upshift.tools.gitlab_mr import GitLabClient, GitLabError

MR_URL = "https://gitlab.com/group/repo/-/merge_requests/7"


def _client(calls: list[tuple[str, str, dict]], branch_taken: bool = False) -> GitLabClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body or dict(request.url.params)))
        path = request.url.path  # httpx returns this percent-decoded
        if path.endswith("/projects/group/repo"):
            assert "group%2Frepo" in request.url.raw_path.decode()
            return httpx.Response(200, json={"id": 42, "default_branch": "main"})
        if path.endswith("/repository/branches"):
            if branch_taken and request.url.params["branch"] == "upshift/pydantic-2.9.2":
                return httpx.Response(400, json={"message": "Branch already exists"})
            return httpx.Response(201, json={"name": request.url.params["branch"]})
        if "/repository/files/" in path:
            return httpx.Response(200 if path.endswith("requirements.txt") else 404)
        if path.endswith("/repository/commits"):
            return httpx.Response(201, json={"id": "abc"})
        if path.endswith("/merge_requests"):
            return httpx.Response(201, json={"iid": 7, "web_url": MR_URL})
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    return GitLabClient(
        "https://gitlab.com", "tok", client=httpx.AsyncClient(transport=transport)
    )


def _draft() -> MergeRequestDraft:
    return MergeRequestDraft("title", "desc", "upshift/pydantic-2.9.2", "msg")


def test_project_path_from_url() -> None:
    assert GitLabClient.project_path_from_url("https://gitlab.com/a/b/c.git") == "a/b/c"
    assert GitLabClient.project_path_from_url("https://gitlab.com/a/b/-/tree/main") == "a/b"
    assert GitLabClient.project_path_from_url("https://github.com/a/b") is None
    assert GitLabClient.project_path_from_url("C:/local/path") is None


async def test_publish_creates_branch_commit_and_mr() -> None:
    calls: list[tuple[str, str, dict]] = []
    info = await _client(calls).publish(
        "group/repo", _draft(),
        {"requirements.txt": "pydantic==2.9.2\n", "pyshop/new.py": "X = 1\n", "gone.py": None},
    )
    assert info.web_url == MR_URL and info.iid == 7
    assert info.branch == "upshift/pydantic-2.9.2"
    commit = next(c for c in calls if c[1].endswith("/repository/commits"))[2]
    assert commit["branch"] == "upshift/pydantic-2.9.2"
    assert commit["actions"] == [
        {"action": "update", "file_path": "requirements.txt", "content": "pydantic==2.9.2\n"},
        {"action": "create", "file_path": "pyshop/new.py", "content": "X = 1\n"},
    ]  # gone.py did not exist upstream, so no delete action
    mr = next(c for c in calls if c[1].endswith("/merge_requests"))[2]
    assert mr["source_branch"] == "upshift/pydantic-2.9.2" and mr["target_branch"] == "main"
    assert mr["remove_source_branch"] is True


async def test_publish_renames_branch_when_taken() -> None:
    calls: list[tuple[str, str, dict]] = []
    info = await _client(calls, branch_taken=True).publish(
        "group/repo", _draft(), {"requirements.txt": "x\n"}
    )
    assert info.branch.startswith("upshift/pydantic-2.9.2-") and info.branch != _draft().branch


async def test_publish_errors_surface() -> None:
    calls: list[tuple[str, str, dict]] = []
    with pytest.raises(GitLabError):
        await _client(calls).publish("missing/project", _draft(), {"a": "b"})
    with pytest.raises(GitLabError, match="nothing to commit"):
        await _client(calls).publish("group/repo", _draft(), {"gone.py": None})


def _report() -> ScoutReport:
    def dep(name: str, bump: str, risk: float, skip: str | None = None) -> DependencyAssessment:
        return DependencyAssessment(
            name, "requirements.txt", "==1", "1", "2", bump, risk, skip_reason=skip
        )

    deps = [
        dep("sqlalchemy", "major", 0.81), dep("pydantic", "major", 0.78),
        dep("pytest", "major", 0.66), dep("python-dateutil", "minor", 0.39),
        dep("requests", "minor", 0.39), dep("rich", "patch", 0.1),
        dep("done", "none", 0.0, skip="already at latest"),
    ]
    return ScoutReport("python", ["requirements.txt"], 11, deps, "now")


def test_select_candidates_policies() -> None:
    report = _report()
    assert [d.name for d in select_candidates(report, "balanced")] == [
        "sqlalchemy", "pydantic", "pytest",
    ]
    assert [d.name for d in select_candidates(report, "conservative")] == [
        "python-dateutil", "requests", "rich",
    ]
    assert len(select_candidates(report, "aggressive")) == 6
    assert [d.name for d in select_candidates(report, "balanced", ["Pydantic", "done"])] == [
        "pydantic",
    ]
