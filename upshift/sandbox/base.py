"""Executor contract shared by the Docker (local) and Nebius Serverless Jobs (cloud) backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class SandboxError(RuntimeError):
    pass


@dataclass
class ExecutionRequest:
    workspace: Path
    command: str
    timeout_seconds: int = 600
    image: str = "python:3.12-slim"
    env: dict[str, str] = field(default_factory=dict)
    collect: list[str] = field(default_factory=list)  # files to read back, relative to /work
    network: bool = True
    cpu: float = 2.0
    memory: str = "2g"


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    artifacts: dict[str, str] = field(default_factory=dict)
    backend: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def tail(self, chars: int = 4000) -> str:
        combined = self.stdout
        if self.stderr:
            combined = f"{combined}\n--- stderr ---\n{self.stderr}"
        return combined[-chars:]


class Executor(Protocol):
    name: str

    async def available(self) -> bool: ...

    async def run(self, request: ExecutionRequest) -> ExecutionResult: ...
