"""Pick the sandbox backend from settings. Docker locally; Nebius Serverless Jobs on request."""

from __future__ import annotations

from typing import Any

from .base import Executor
from .docker import DockerExecutor


def build_executor(settings: Any, name: str | None = None) -> Executor:
    choice = (name or settings.executor or "docker").lower()
    if choice == "nebius":
        from .nebius_job import NebiusJobsExecutor

        return NebiusJobsExecutor.from_settings(settings)
    return DockerExecutor()
