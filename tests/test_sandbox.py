from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from upshift.sandbox.base import ExecutionRequest, ExecutionResult
from upshift.sandbox.docker import DockerExecutor


def test_create_args_locks_down_container(tmp_path: Path) -> None:
    request = ExecutionRequest(
        workspace=tmp_path, command="pytest -q", env={"B": "2", "A": "1"}, network=False,
        cpu=1.5, memory="1g",
    )
    args = DockerExecutor().create_args(request, "upshift-test")
    assert args[:3] == ["create", "--name", "upshift-test"]
    assert "--network" in args and args[args.index("--network") + 1] == "none"
    assert args[args.index("--cpus") + 1] == "1.5"
    assert args[args.index("--memory") + 1] == "1g"
    assert "--cap-drop" in args and "no-new-privileges" in args
    env_values = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
    assert env_values == ["A=1", "B=2"]
    assert args[-4:] == ["python:3.12-slim", "sh", "-c", "pytest -q"]


def test_network_enabled_by_default(tmp_path: Path) -> None:
    args = DockerExecutor().create_args(ExecutionRequest(tmp_path, "true"), "n")
    assert "--network" not in args


def test_result_helpers() -> None:
    ok = ExecutionResult(0, "out", "", 10)
    bad = ExecutionResult(1, "x" * 10, "boom", 10)
    assert ok.ok and ok.tail() == "out"
    assert not bad.ok and bad.tail().endswith("--- stderr ---\nboom")
    assert not ExecutionResult(0, "", "", 10, timed_out=True).ok


@pytest.mark.skipif(
    not (os.environ.get("UPSHIFT_DOCKER_TESTS") and shutil.which("docker")),
    reason="set UPSHIFT_DOCKER_TESTS=1 with Docker running to exercise the real executor",
)
async def test_docker_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("open('out.txt','w').write('hi'); print('done')\n")
    result = await DockerExecutor().run(
        ExecutionRequest(
            workspace=tmp_path, command="python hello.py", timeout_seconds=120,
            collect=["out.txt", "missing.txt"], network=False,
        )
    )
    assert result.ok and "done" in result.stdout
    assert result.artifacts == {"out.txt": "hi"}
