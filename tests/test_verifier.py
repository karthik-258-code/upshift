from __future__ import annotations

import json
from pathlib import Path

from upshift.agents.verifier import (
    API_PATH,
    INSTALL_MARKER,
    JUNIT_PATH,
    Verifier,
    build_test_command,
    parse_junit,
)
from upshift.core.bus import EventBus
from upshift.core.events import EventType, Run
from upshift.core.store import EventStore
from upshift.ecosystems.python import add_requirement, bump_pin
from upshift.sandbox.base import ExecutionRequest, ExecutionResult

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
<testcase classname="tests.test_models" name="test_ok" time="0.01"/>
<testcase classname="tests.test_models" name="test_skip"><skipped message="no"/></testcase>
<testcase classname="tests.test_models" name="test_money_is_immutable">
<failure message="Failed: DID NOT RAISE TypeError">tests/test_models.py:27: in test_immutable
    with pytest.raises(TypeError):
E   Failed: DID NOT RAISE</failure>
<system-out>--------------------------------- Captured log call ---------------------------------
ERROR    app:app.py:838 Exception on /notes [POST]
TypeError: Object of type Decimal is not JSON serializable</system-out></testcase>
<testcase classname="tests.test_settings" name="test_settings_defaults">
<error message="collection failure">pyshop/settings.py:6: in &lt;module&gt;
    from pydantic import BaseSettings
E   pydantic.errors.PydanticImportError</error></testcase>
</testsuite>
</testsuites>
"""


class FakeExecutor:
    name = "fake"

    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.requests: list[ExecutionRequest] = []

    async def available(self) -> bool:
        return True

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return self.result


def test_parse_junit_counts_and_failures() -> None:
    stats = parse_junit(JUNIT)
    assert (stats["tests"], stats["failed"], stats["errors"], stats["skipped"]) == (4, 1, 1, 1)
    kinds = {f.test: f.kind for f in stats["failures"]}
    assert kinds == {
        "tests.test_models::test_money_is_immutable": "failure",
        "tests.test_settings::test_settings_defaults": "error",
    }
    assert parse_junit("not xml")["tests"] == 0
    immutable = next(f for f in stats["failures"] if f.test.endswith("test_money_is_immutable"))
    assert "--- captured output ---" in immutable.message
    assert "Object of type Decimal is not JSON serializable" in immutable.message
    settings = next(f for f in stats["failures"] if f.test.endswith("test_settings_defaults"))
    assert "captured output" not in settings.message


def test_build_test_command(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("x\n")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "requirements.txt").write_text("junk\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    command = build_test_command(tmp_path)
    assert "pip install -q -r requirements-dev.txt -r requirements.txt" in command
    assert "pip install -q -e ." in command
    assert INSTALL_MARKER in command and "--junitxml=.upshift/junit.xml" in command
    assert command.rstrip().endswith("exit $code")
    assert "introspect.py" not in command

    with_api = build_test_command(tmp_path, ["pydantic", "pydantic_settings"])
    assert "introspect.py pydantic pydantic_settings > .upshift/api.json" in with_api
    assert "importlib.import_module(name)" in with_api


async def test_verify_parses_api_surface(tmp_path: Path) -> None:
    api = {"pydantic": {"version": "2.14.1", "names": ["BaseModel", "ConfigDict", "RootModel"]}}
    executor = FakeExecutor(
        ExecutionResult(
            0, f"{INSTALL_MARKER}\n17 passed", "", 5,
            artifacts={API_PATH: json.dumps(api), JUNIT_PATH: "<testsuites/>"},
        )
    )
    result = await Verifier(executor).verify(tmp_path, introspect=["pydantic"])
    assert result.ok and result.api_surface == api
    assert result.to_dict()["api_surface"] == {
        "pydantic": {"version": "2.14.1", "name_count": 3},
    }
    assert executor.requests[0].collect == [JUNIT_PATH, API_PATH]


async def test_verify_builds_structured_result(tmp_path: Path) -> None:
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    executor = FakeExecutor(
        ExecutionResult(
            exit_code=1, stdout=f"{INSTALL_MARKER}\n2 failed, 1 passed", stderr="",
            duration_ms=1234, artifacts={JUNIT_PATH: JUNIT}, backend="fake",
        )
    )
    result = await Verifier(executor, bus=bus).verify(tmp_path, run.id, "dep:pydantic")

    assert not result.ok and result.phase == "test"
    assert (result.tests, result.passed, result.failure_count) == (4, 1, 2)
    assert result.referenced_files() == ["tests/test_models.py", "pyshop/settings.py"]
    assert executor.requests[0].collect == [JUNIT_PATH, API_PATH]
    types = [e.type for e in store.events(run.id)]
    assert types == [EventType.SANDBOX_STARTED, EventType.SANDBOX_FINISHED, EventType.TEST_RESULT]
    payload = store.events(run.id)[-1].payload
    assert payload["failure_count"] == 2 and payload["label"] == "verify"


async def test_verify_detects_install_phase_and_timeout(tmp_path: Path) -> None:
    install = await Verifier(
        FakeExecutor(ExecutionResult(1, "ERROR: No matching distribution", "", 5))
    ).verify(tmp_path)
    assert install.phase == "install" and not install.ok

    timeout = await Verifier(
        FakeExecutor(ExecutionResult(124, "", "timed out", 5, timed_out=True))
    ).verify(tmp_path)
    assert timeout.phase == "timeout" and timeout.timed_out

    green = await Verifier(
        FakeExecutor(ExecutionResult(0, f"{INSTALL_MARKER}\n17 passed", "", 5))
    ).verify(tmp_path)
    assert green.ok and green.phase == "test"


async def test_conftest_import_error_becomes_startup_failure(tmp_path: Path) -> None:
    """pytest exit 4 with nothing collected: the traceback is the failure."""
    log = (
        f"{INSTALL_MARKER}\n"
        "ImportError while loading conftest '/work/tests/conftest.py'.\n"
        "tests/conftest.py:3: in <module>\n"
        "    from notes import create_app\n"
        "notes/__init__.py:7: in <module>\n"
        "    from flask import Flask, abort, escape, jsonify, request\n"
        "E   ImportError: cannot import name 'escape' from 'flask' "
        "(/usr/local/lib/python3.12/site-packages/flask/__init__.py)\n"
    )
    result = await Verifier(FakeExecutor(ExecutionResult(4, log, "", 5))).verify(tmp_path)
    assert result.phase == "test" and not result.ok
    assert result.tests == 1 and result.errors == 1 and result.failure_count == 1
    failure = result.failures[0]
    assert failure.test == "pytest::startup" and failure.kind == "error"
    assert "exited with code 4" in failure.message
    assert "cannot import name 'escape'" in failure.message
    assert result.referenced_files() == ["tests/conftest.py", "notes/__init__.py"]


def test_bump_pin_and_add_requirement() -> None:
    text = (
        "pydantic==1.10.13\nSQLAlchemy==1.4.49  # db\nrequests>=2.28\n"
        "uvicorn[standard]==0.30.0\n"
    )
    bumped, changed = bump_pin(text, "sqlalchemy", "2.0.35")
    assert changed and "SQLAlchemy==2.0.35  # db" in bumped and "pydantic==1.10.13" in bumped
    bumped, changed = bump_pin(bumped, "Uvicorn", "0.31.0")
    assert changed and "uvicorn[standard]==0.31.0" in bumped
    _, changed = bump_pin(text, "requests", "2.32.3")
    assert not changed  # not pinned with ==

    toml = 'dependencies = ["fastapi>=0.100", "pydantic==2.0.0", "pydantic-core==2.1"]'
    bumped, changed = bump_pin(toml, "pydantic", "2.9.2")
    assert changed and '"pydantic==2.9.2"' in bumped and '"pydantic-core==2.1"' in bumped

    assert add_requirement("a==1", "b==2") == "a==1\nb==2\n"
    assert add_requirement("", "b==2") == "b==2\n"
