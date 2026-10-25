"""Nebius Serverless AI Jobs executor: the cloud twin of the Docker sandbox.

Built strictly against the documented surface:
- jobs API: POST /ai/v1/jobs, GET /ai/v1/jobs/{id}, POST /ai/v1/jobs/cancel, DELETE /ai/v1/jobs/{id}
  with `spec.image`, `containerCommand`, `args`, `environmentVariables`, `injectedFiles`
  (base64, 64 KiB each), `timeout`, `platform`, `preset`, `subnetId`, `disk`;
- auth: a user access token (`nebius iam get-access-token`, 12 h) or the service-account flow
  (RS256 JWT with `kid`, `iss`, `sub`, `exp`, exchanged at /oauth2/token/exchange).

Transport. Injected files are capped at 64 KiB and the only documented ways to get output
back are logs or a mounted volume, so the workspace travels through object storage instead:
the executor uploads a tarball and hands the job two presigned URLs (download input, upload a
single result JSON). The job runs a small bash + python script that exists in any Python
image; no credentials and no mount semantics inside the container.

Cost. Every job is a VM billed per second from boot to shutdown, so `available()` returns
False unless NEBIUS_ACCEPT_CHARGES=1 is set explicitly. This backend is unit-tested against
the documented request and response shapes; it has not been exercised against the live API
in this repository, to avoid compute charges.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import httpx

from .base import ExecutionRequest, ExecutionResult, SandboxError

log = logging.getLogger(__name__)

DEFAULT_JOBS_API = "https://api.nebius.cloud/ai/v1"
DEFAULT_TOKEN_EXCHANGE_URL = "https://auth.eu.nebius.com:443/oauth2/token/exchange"
TERMINAL_STATES = frozenset({
    "COMPLETED", "SUCCEEDED", "FAILED", "CANCELLED", "CANCELED", "ERROR", "TIMEOUT",
    "TIMED_OUT",
})
SUCCESS_STATES = frozenset({"COMPLETED", "SUCCEEDED"})
SCRIPT_PATH = "/upshift/job.sh"
SPEC_PATH = "/upshift/job.json"
INJECTED_LIMIT = 64 * 1024

# Runs inside the job container. Only bash + the image's python are assumed.
JOB_SCRIPT = r"""#!/bin/bash
set -u
mkdir -p /work
python3 - <<'PY'
import os, urllib.request
urllib.request.urlretrieve(os.environ["UPSHIFT_INPUT_URL"], "/tmp/in.tgz")
PY
tar xzf /tmp/in.tgz -C /work
cd /work
SPEC=/upshift/job.json
CMD=$(python3 -c "import json;print(json.load(open('$SPEC'))['command'])")
( eval "$CMD" ) > /tmp/log.txt 2>&1
echo $? > /tmp/exit
python3 - <<'PY'
import json, os, urllib.request
spec = json.load(open("/upshift/job.json"))
out = {
    "exit": int(open("/tmp/exit").read().strip() or 1),
    "log": open("/tmp/log.txt", "rb").read().decode("utf-8", "replace")[-200000:],
    "artifacts": {},
}
for rel in spec.get("collect", []):
    path = os.path.join("/work", rel)
    if os.path.isfile(path):
        out["artifacts"][rel] = open(path, "rb").read().decode("utf-8", "replace")
body = json.dumps(out).encode()
req = urllib.request.Request(os.environ["UPSHIFT_RESULT_URL"], data=body, method="PUT")
req.add_header("Content-Type", "application/json")
urllib.request.urlopen(req, timeout=300).read()
PY
"""


# ---- pluggable pieces ------------------------------------------------------------


class Storage(Protocol):
    """Object storage with presigned URLs (S3-compatible in production)."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...
    def presigned_get(self, key: str, expires_seconds: int) -> str: ...
    def presigned_put(self, key: str, expires_seconds: int) -> str: ...


class S3Storage:
    """boto3-backed Storage. boto3 is an optional dependency (`pip install upshift[nebius]`)."""

    def __init__(
        self, bucket: str, endpoint_url: str, region: str, access_key: str, secret_key: str
    ) -> None:
        import boto3  # optional dependency

        self.bucket = bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        )

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes | None:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self._client.exceptions.NoSuchKey:
            return None

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_get(self, key: str, expires_seconds: int) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_seconds
        )

    def presigned_put(self, key: str, expires_seconds: int) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": "application/json"},
            ExpiresIn=expires_seconds,
        )


class TokenProvider(Protocol):
    async def token(self) -> str: ...


class StaticToken:
    """A user access token from `nebius iam get-access-token` (valid 12 hours)."""

    def __init__(self, value: str) -> None:
        self._value = value

    async def token(self) -> str:
        if not self._value:
            raise SandboxError("NEBIUS_IAM_TOKEN is empty")
        return self._value


Signer = Callable[[dict[str, Any], str, str], str]  # (claims, private_key_pem, kid) -> JWT


def pyjwt_signer(claims: dict[str, Any], private_key_pem: str, kid: str) -> str:
    import jwt  # optional dependency (PyJWT + cryptography)

    return jwt.encode(claims, private_key_pem, algorithm="RS256", headers={"kid": kid})


class ServiceAccountToken:
    """Documented service-account flow: short-lived RS256 JWT exchanged for an access token."""

    def __init__(
        self,
        service_account_id: str,
        key_id: str,
        private_key_pem: str,
        http: httpx.AsyncClient,
        exchange_url: str = DEFAULT_TOKEN_EXCHANGE_URL,
        signer: Signer = pyjwt_signer,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.service_account_id = service_account_id
        self.key_id = key_id
        self.private_key_pem = private_key_pem
        self.http = http
        self.exchange_url = exchange_url
        self.signer = signer
        self.clock = clock
        self._cached: tuple[str, float] | None = None  # token, expires_at

    async def token(self) -> str:
        now = self.clock()
        if self._cached and self._cached[1] - 300 > now:
            return self._cached[0]
        claims = {
            "iss": self.service_account_id,
            "sub": self.service_account_id,
            "exp": int(now) + 300,
        }
        assertion = self.signer(claims, self.private_key_pem, self.key_id)
        response = await self.http.post(
            self.exchange_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "subject_token": assertion,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            },
        )
        if response.status_code != 200:
            raise SandboxError(
                f"token exchange failed: {response.status_code} {response.text[:200]}"
            )
        data = response.json()
        token = str(data["access_token"])
        self._cached = (token, now + float(data.get("expires_in", 43200)))
        return token


# ---- the executor ------------------------------------------------------------------


@dataclass
class JobsConfig:
    project_id: str
    subnet_id: str
    platform: str
    preset: str
    api_base: str = DEFAULT_JOBS_API
    disk_bytes: int = 20 * 1024**3
    boot_allowance_seconds: int = 600  # VM provisioning before the command even starts
    poll_interval_seconds: float = 10.0
    keep_jobs: bool = False  # leave finished jobs in place for inspection
    key_prefix: str = "upshift"

    def missing(self) -> list[str]:
        return [
            name for name in ("project_id", "subnet_id", "platform", "preset")
            if not getattr(self, name)
        ]


@dataclass
class JobHandle:
    id: str
    state: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def job_id_of(data: dict[str, Any]) -> str:
    """The docs say to save the returned resource id; tolerate the two shapes it may take."""
    raw_metadata = data.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for candidate in (data.get("resourceId"), metadata.get("id"), data.get("id")):
        if candidate:
            return str(candidate)
    raise SandboxError(f"job creation returned no id: {json.dumps(data)[:300]}")


def job_state_of(data: dict[str, Any]) -> str:
    raw_status = data.get("status")
    status: dict[str, Any] = raw_status if isinstance(raw_status, dict) else {}
    return str(status.get("state") or data.get("state") or "").upper()


def tar_workspace(workspace: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(workspace.rglob("*")):
            rel = path.relative_to(workspace)
            if rel.parts and rel.parts[0] == ".git":
                continue
            tar.add(path, arcname=str(rel), recursive=False)
    return buffer.getvalue()


def b64(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) > INJECTED_LIMIT:
        raise SandboxError(f"injected file exceeds {INJECTED_LIMIT} bytes")
    return base64.b64encode(data).decode("ascii")


class NebiusJobsExecutor:
    name = "nebius"

    def __init__(
        self,
        config: JobsConfig,
        storage: Storage,
        token: TokenProvider,
        *,
        accept_charges: bool = False,
        http: httpx.AsyncClient | None = None,
        output_limit: int = 200_000,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.storage = storage
        self.token_provider = token
        self.accept_charges = accept_charges
        self.http = http or httpx.AsyncClient(timeout=60)
        self.output_limit = output_limit
        self._sleep = sleep

    # ---- lifecycle -----------------------------------------------------------

    async def available(self) -> bool:
        if not self.accept_charges:
            log.warning(
                "nebius executor disabled: set NEBIUS_ACCEPT_CHARGES=1 to allow paid jobs"
            )
            return False
        missing = self.config.missing()
        if missing:
            log.warning("nebius executor misconfigured: missing %s", ", ".join(missing))
            return False
        try:
            response = await self._request(
                "GET", "/jobs", params={"parentId": self.config.project_id}
            )
        except (SandboxError, httpx.HTTPError) as exc:
            log.warning("nebius executor unavailable: %s", exc)
            return False
        return response.status_code == 200

    def build_spec(
        self, request: ExecutionRequest, name: str, input_url: str, result_url: str
    ) -> dict[str, Any]:
        env = {**request.env, "UPSHIFT_INPUT_URL": input_url, "UPSHIFT_RESULT_URL": result_url}
        spec = {"command": request.command, "collect": list(request.collect)}
        return {
            "metadata": {"parentId": self.config.project_id, "name": name},
            "spec": {
                "image": request.image,
                "containerCommand": "bash",
                "args": SCRIPT_PATH,
                "environmentVariables": [
                    {"name": key, "value": value} for key, value in sorted(env.items())
                ],
                "workingDir": "/work",
                "timeout": f"{request.timeout_seconds + self.config.boot_allowance_seconds}s",
                "platform": self.config.platform,
                "preset": self.config.preset,
                "disk": {"type": "NETWORK_SSD", "sizeBytes": str(self.config.disk_bytes)},
                "injectedFiles": [
                    {"containerPath": SCRIPT_PATH, "content": b64(JOB_SCRIPT)},
                    {"containerPath": SPEC_PATH, "content": b64(json.dumps(spec))},
                ],
                "subnetId": self.config.subnet_id,
            },
        }

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.accept_charges:
            raise SandboxError("refusing to create a paid job: NEBIUS_ACCEPT_CHARGES is not set")
        if not request.network:
            log.info("nebius jobs always have network access; network=False is not enforced")
        name = f"upshift-{uuid4().hex[:10]}"
        prefix = f"{self.config.key_prefix}/{name}"
        input_key, result_key = f"{prefix}/in.tgz", f"{prefix}/result.json"
        started = time.perf_counter()
        handle: JobHandle | None = None
        timed_out = False
        try:
            self.storage.put(input_key, tar_workspace(request.workspace))
            budget = request.timeout_seconds + self.config.boot_allowance_seconds
            body = self.build_spec(
                request, name,
                self.storage.presigned_get(input_key, budget + 3600),
                self.storage.presigned_put(result_key, budget + 3600),
            )
            response = await self._request("POST", "/jobs", json=body)
            if response.status_code >= 300:
                raise SandboxError(
                    f"job creation failed: {response.status_code} {response.text[:300]}"
                )
            handle = JobHandle(id=job_id_of(response.json()))
            log.info("nebius job %s created as %s", name, handle.id)

            deadline = time.perf_counter() + budget
            while True:
                status = await self._request("GET", f"/jobs/{handle.id}")
                handle.raw = status.json() if status.status_code == 200 else {}
                handle.state = job_state_of(handle.raw)
                if handle.state in TERMINAL_STATES:
                    break
                if time.perf_counter() > deadline:
                    timed_out = True
                    await self._request("POST", "/jobs/cancel", json={"id": handle.id})
                    break
                await self._sleep(self.config.poll_interval_seconds)

            raw = self.storage.get(result_key)
            if raw is None:
                detail = "timed out" if timed_out else f"ended in state {handle.state}"
                return ExecutionResult(
                    exit_code=124 if timed_out else 125, stdout="",
                    stderr=f"nebius job {handle.id} {detail} and produced no result",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    timed_out=timed_out, backend=self.name,
                )
            result = json.loads(raw.decode("utf-8", "replace"))
            return ExecutionResult(
                exit_code=int(result.get("exit", 1)),
                stdout=str(result.get("log", ""))[-self.output_limit:],
                stderr="" if handle.state in SUCCESS_STATES else f"job state: {handle.state}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                timed_out=timed_out,
                artifacts={str(k): str(v) for k, v in (result.get("artifacts") or {}).items()},
                backend=self.name,
            )
        finally:
            for key in (input_key, result_key):
                try:
                    self.storage.delete(key)
                except Exception as exc:  # noqa: BLE001 - cleanup is best effort
                    log.debug("could not delete %s: %s", key, exc)
            if handle is not None and not self.config.keep_jobs:
                try:
                    await self._request("DELETE", f"/jobs/{handle.id}")
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not delete nebius job %s: %s", handle.id, exc)

    # ---- http -----------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = await self.token_provider.token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        return await self.http.request(
            method, f"{self.config.api_base.rstrip('/')}{path}", headers=headers, **kwargs
        )

    # ---- construction from settings -----------------------------------------------

    @classmethod
    def from_settings(cls, settings: Any) -> NebiusJobsExecutor:
        http = httpx.AsyncClient(timeout=60)
        token: TokenProvider
        if (
            settings.nebius_sa_id
            and settings.nebius_sa_key_id
            and settings.nebius_sa_private_key_file
        ):
            pem = Path(settings.nebius_sa_private_key_file).read_text(encoding="utf-8")
            token = ServiceAccountToken(
                settings.nebius_sa_id, settings.nebius_sa_key_id, pem, http,
                exchange_url=settings.nebius_token_exchange_url,
            )
        else:
            token = StaticToken(settings.nebius_iam_token)
        storage = S3Storage(
            settings.nebius_s3_bucket, settings.nebius_s3_endpoint, settings.nebius_s3_region,
            settings.nebius_s3_access_key, settings.nebius_s3_secret_key,
        )
        config = JobsConfig(
            project_id=settings.nebius_project_id, subnet_id=settings.nebius_subnet_id,
            platform=settings.nebius_jobs_platform, preset=settings.nebius_jobs_preset,
            api_base=settings.nebius_jobs_api,
        )
        return cls(
            config, storage, token, accept_charges=settings.nebius_accept_charges, http=http
        )
