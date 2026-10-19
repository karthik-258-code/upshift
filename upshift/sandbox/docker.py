"""Docker-backed executor driven through the docker CLI (works with Docker Desktop on Windows).

Each run: create a locked-down container, copy the workspace in, start it attached with a
timeout, read the exit code, pull back requested artifact files, remove the container.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import tarfile
import time
from asyncio.subprocess import PIPE
from uuid import uuid4

from .base import ExecutionRequest, ExecutionResult, SandboxError

log = logging.getLogger(__name__)


class DockerExecutor:
    name = "docker"

    def __init__(self, docker_bin: str = "docker", output_limit: int = 200_000) -> None:
        self.docker_bin = docker_bin
        self.output_limit = output_limit

    async def available(self) -> bool:
        try:
            code, _, _ = await self._docker("version", "--format", "{{.Server.Version}}", timeout=20)
        except (OSError, TimeoutError):
            return False
        return code == 0

    def create_args(self, request: ExecutionRequest, name: str) -> list[str]:
        args = [
            "create",
            "--name", name,
            "--workdir", "/work",
            "--cpus", str(request.cpu),
            "--memory", request.memory,
            "--pids-limit", "512",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
        ]
        if not request.network:
            args += ["--network", "none"]
        for key, value in sorted(request.env.items()):
            args += ["--env", f"{key}={value}"]
        args += [request.image, "sh", "-c", request.command]
        return args

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        name = f"upshift-{uuid4().hex[:10]}"
        started = time.perf_counter()
        timed_out = False
        try:
            code, _, err = await self._docker(*self.create_args(request, name), timeout=120)
            if code != 0:
                raise SandboxError(f"docker create failed: {err.strip()}")

            source = os.path.join(str(request.workspace), ".")
            code, _, err = await self._docker("cp", source, f"{name}:/work", timeout=300)
            if code != 0:
                raise SandboxError(f"docker cp failed: {err.strip()}")

            try:
                code, out, err = await self._docker(
                    "start", "--attach", name, timeout=request.timeout_seconds
                )
            except TimeoutError:
                timed_out = True
                with contextlib.suppress(Exception):
                    await self._docker("kill", name, timeout=30)
                code, out, err = 124, "", f"sandbox timed out after {request.timeout_seconds}s"

            if not timed_out:
                icode, iout, _ = await self._docker(
                    "inspect", "-f", "{{.State.ExitCode}}", name, timeout=30
                )
                if icode == 0 and iout.strip().lstrip("-").isdigit():
                    code = int(iout.strip())

            artifacts: dict[str, str] = {}
            for rel in request.collect:
                content = await self._read_file(name, rel)
                if content is not None:
                    artifacts[rel] = content

            return ExecutionResult(
                exit_code=code,
                stdout=out[-self.output_limit :],
                stderr=err[-self.output_limit :],
                duration_ms=int((time.perf_counter() - started) * 1000),
                timed_out=timed_out,
                artifacts=artifacts,
                backend=self.name,
            )
        finally:
            with contextlib.suppress(Exception):
                await self._docker("rm", "-f", name, timeout=60)

    async def _read_file(self, name: str, rel: str) -> str | None:
        code, raw, _ = await self._docker_bytes("cp", f"{name}:/work/{rel}", "-", timeout=60)
        if code != 0 or not raw:
            return None
        try:
            with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        handle = tar.extractfile(member)
                        return handle.read().decode("utf-8", "replace") if handle else None
        except tarfile.TarError:
            return None
        return None

    async def _docker_bytes(self, *args: str, timeout: float) -> tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            self.docker_bin, *args, stdout=PIPE, stderr=PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise
        return proc.returncode or 0, out, err

    async def _docker(self, *args: str, timeout: float) -> tuple[int, str, str]:
        code, out, err = await self._docker_bytes(*args, timeout=timeout)
        return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
