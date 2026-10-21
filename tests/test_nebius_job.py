"""Nebius Serverless Jobs executor, exercised against fakes shaped like the documented API."""

from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from upshift.sandbox.base import ExecutionRequest, SandboxError
from upshift.sandbox.nebius_job import (
    JOB_SCRIPT,
    SCRIPT_PATH,
    SPEC_PATH,
    JobsConfig,
    NebiusJobsExecutor,
    ServiceAccountToken,
    StaticToken,
    job_id_of,
    job_state_of,
)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.history: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data
        self.history[key] = data

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def presigned_get(self, key: str, expires_seconds: int) -> str:
        return f"https://storage.test/{key}?get&exp={expires_seconds}"

    def presigned_put(self, key: str, expires_seconds: int) -> str:
        return f"https://storage.test/{key}?put&exp={expires_seconds}"


class FakeJobsApi:
    """httpx.MockTransport handler for POST/GET/DELETE /ai/v1/jobs and /jobs/cancel."""

    def __init__(
        self,
        storage: FakeStorage,
        states: tuple[str, ...] = ("PENDING", "RUNNING", "COMPLETED"),
        result: dict[str, Any] | None = None,
        create_status: int = 200,
    ) -> None:
        self.storage = storage
        self.states = list(states)
        self.result = result
        self.create_status = create_status
        self.requests: list[httpx.Request] = []
        self.created: dict[str, Any] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/ai/v1/jobs":
            if self.create_status != 200:
                return httpx.Response(self.create_status, json={"message": "quota exceeded"})
            self.created = json.loads(request.content)
            return httpx.Response(200, json={"metadata": {"id": "job-123"}})
        if request.method == "GET" and path == "/ai/v1/jobs/job-123":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            if state in ("COMPLETED", "FAILED") and self.result is not None:
                # the container uploads its result through the presigned PUT URL
                env = {e["name"]: e["value"] for e in self.created["spec"]["environmentVariables"]}
                key = env["UPSHIFT_RESULT_URL"].split("storage.test/")[1].split("?")[0]
                self.storage.put(key, json.dumps(self.result).encode())
            return httpx.Response(200, json={"metadata": {"id": "job-123"}, "status": {"state": state}})
        if request.method == "GET" and path == "/ai/v1/jobs":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and path == "/ai/v1/jobs/cancel":
            return httpx.Response(200, json={})
        if request.method == "DELETE" and path == "/ai/v1/jobs/job-123":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def make_executor(api: FakeJobsApi, *, accept: bool = True, boot: int = 600) -> NebiusJobsExecutor:
    config = JobsConfig(
        project_id="project-e00test", subnet_id="vpcsubnet-e00test", platform="cpu-test",
        preset="4vcpu-16gb", poll_interval_seconds=0, boot_allowance_seconds=boot,
    )

    async def no_sleep(_: float) -> None:
        return None

    return NebiusJobsExecutor(
        config, api.storage, StaticToken("user-token"), accept_charges=accept,
        http=httpx.AsyncClient(transport=httpx.MockTransport(api)), sleep=no_sleep,
    )


def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyshop").mkdir()
    (tmp_path / "pyshop" / "models.py").write_text("x = 1\n")
    (tmp_path / "requirements.txt").write_text("pydantic==2.0\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return tmp_path


async def test_refuses_to_spend_without_explicit_acceptance(tmp_path: Path) -> None:
    api = FakeJobsApi(FakeStorage())
    executor = make_executor(api, accept=False)
    assert await executor.available() is False
    with pytest.raises(SandboxError, match="NEBIUS_ACCEPT_CHARGES"):
        await executor.run(ExecutionRequest(workspace=workspace(tmp_path), command="true"))
    assert api.requests == []  # not a single call reached the API


async def test_available_checks_config_then_lists_jobs() -> None:
    api = FakeJobsApi(FakeStorage())
    executor = make_executor(api)
    assert await executor.available() is True
    assert api.requests[0].method == "GET" and api.requests[0].url.path == "/ai/v1/jobs"
    assert api.requests[0].url.params["parentId"] == "project-e00test"
    assert api.requests[0].headers["authorization"] == "Bearer user-token"
    executor.config.preset = ""
    assert await executor.available() is False


async def test_round_trip_through_presigned_urls(tmp_path: Path) -> None:
    storage = FakeStorage()
    api = FakeJobsApi(
        storage,
        result={"exit": 0, "log": "17 passed in 2.1s\n", "artifacts": {".upshift/result.json": "{}"}},
    )
    executor = make_executor(api)
    request = ExecutionRequest(
        workspace=workspace(tmp_path), command="pip install -r requirements.txt && pytest -q",
        timeout_seconds=600, collect=[".upshift/result.json", ".upshift/api.json"],
        env={"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    result = await executor.run(request)

    assert result.ok and result.backend == "nebius"
    assert result.stdout == "17 passed in 2.1s\n"
    assert result.artifacts == {".upshift/result.json": "{}"}

    # the documented request body
    body = api.created
    assert body["metadata"]["parentId"] == "project-e00test"
    spec = body["spec"]
    assert spec["image"] == "python:3.12-slim"
    assert spec["containerCommand"] == "bash" and spec["args"] == SCRIPT_PATH
    assert spec["platform"] == "cpu-test" and spec["preset"] == "4vcpu-16gb"
    assert spec["subnetId"] == "vpcsubnet-e00test"
    assert spec["timeout"] == "1200s"  # command timeout + boot allowance
    assert spec["disk"] == {"type": "NETWORK_SSD", "sizeBytes": str(20 * 1024**3)}
    injected = {f["containerPath"]: base64.b64decode(f["content"]).decode() for f in spec["injectedFiles"]}
    assert injected[SCRIPT_PATH] == JOB_SCRIPT
    assert json.loads(injected[SPEC_PATH]) == {
        "command": request.command, "collect": [".upshift/result.json", ".upshift/api.json"],
    }
    env = {e["name"]: e["value"] for e in spec["environmentVariables"]}
    assert env["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"
    assert env["UPSHIFT_INPUT_URL"].endswith("/in.tgz?get&exp=4800")
    assert "/result.json?put" in env["UPSHIFT_RESULT_URL"]

    # the workspace tarball excluded .git and is cleaned up afterwards, as is the job
    input_key = next(k for k in storage.history if k.endswith("in.tgz"))
    with tarfile.open(fileobj=io.BytesIO(storage.history[input_key])) as tar:
        names = tar.getnames()
    assert "pyshop/models.py" in names and "requirements.txt" in names
    assert not any(n.startswith(".git") for n in names)
    assert storage.objects == {} and len(storage.deleted) == 2
    methods = [(r.method, r.url.path) for r in api.requests]
    assert methods[0] == ("POST", "/ai/v1/jobs")
    assert methods.count(("GET", "/ai/v1/jobs/job-123")) == 3  # PENDING, RUNNING, COMPLETED
    assert methods[-1] == ("DELETE", "/ai/v1/jobs/job-123")


async def test_timeout_cancels_the_job(tmp_path: Path) -> None:
    api = FakeJobsApi(FakeStorage(), states=("RUNNING",))
    executor = make_executor(api, boot=0)
    result = await executor.run(
        ExecutionRequest(workspace=workspace(tmp_path), command="sleep 999", timeout_seconds=0)
    )
    assert result.timed_out and result.exit_code == 124 and not result.ok
    assert "timed out" in result.stderr
    assert any(r.method == "POST" and r.url.path == "/ai/v1/jobs/cancel" for r in api.requests)
    assert api.requests[-1].method == "DELETE"


async def test_failed_job_without_result(tmp_path: Path) -> None:
    api = FakeJobsApi(FakeStorage(), states=("RUNNING", "FAILED"))
    result = await make_executor(api).run(
        ExecutionRequest(workspace=workspace(tmp_path), command="pytest")
    )
    assert result.exit_code == 125 and not result.ok
    assert "FAILED" in result.stderr and "no result" in result.stderr


async def test_creation_error_is_surfaced(tmp_path: Path) -> None:
    api = FakeJobsApi(FakeStorage(), create_status=429)
    with pytest.raises(SandboxError, match="job creation failed: 429"):
        await make_executor(api).run(ExecutionRequest(workspace=workspace(tmp_path), command="true"))


async def test_service_account_token_exchange_and_caching() -> None:
    calls: list[httpx.Request] = []
    signed: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "sa-token", "token_type": "Bearer", "expires_in": 43200})

    def signer(claims: dict[str, Any], pem: str, kid: str) -> str:
        signed.append({**claims, "kid": kid, "pem": pem})
        return "signed.jwt"

    now = [1_700_000_000.0]
    provider = ServiceAccountToken(
        "serviceaccount-e00abc", "publickey-e00key", "-----BEGIN RSA PRIVATE KEY-----",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        exchange_url="https://auth.test/oauth2/token/exchange", signer=signer,
        clock=lambda: now[0],
    )
    assert await provider.token() == "sa-token"
    assert signed[0]["iss"] == signed[0]["sub"] == "serviceaccount-e00abc"
    assert signed[0]["kid"] == "publickey-e00key" and signed[0]["exp"] == 1_700_000_300
    form = dict(httpx.QueryParams(calls[0].content.decode()))
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert form["subject_token"] == "signed.jwt"
    assert form["subject_token_type"] == "urn:ietf:params:oauth:token-type:jwt"
    # cached until shortly before expiry, then refreshed
    assert await provider.token() == "sa-token" and len(calls) == 1
    now[0] += 43200
    assert await provider.token() == "sa-token" and len(calls) == 2


def test_response_shape_tolerance() -> None:
    assert job_id_of({"resourceId": "job-1"}) == "job-1"
    assert job_id_of({"metadata": {"id": "job-2"}}) == "job-2"
    assert job_id_of({"id": "job-3"}) == "job-3"
    with pytest.raises(SandboxError):
        job_id_of({"metadata": {}})
    assert job_state_of({"status": {"state": "completed"}}) == "COMPLETED"
    assert job_state_of({"state": "Running"}) == "RUNNING"
    assert job_state_of({}) == ""
