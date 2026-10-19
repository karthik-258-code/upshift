from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from upshift.agents.historian import ChangeEntry, ImpactDossier
from upshift.agents.scout import DependencyAssessment
from upshift.agents.surgeon import EDIT_SCHEMA, Surgeon
from upshift.agents.verifier import TestFailure, VerificationResult
from upshift.core.budget import Budget
from upshift.core.bus import EventBus
from upshift.core.events import EventType, Run
from upshift.core.store import EventStore
from upshift.llm.client import TokenFactoryClient
from upshift.llm.models import ModelCatalog
from upshift.llm.router import ModelRouter
from upshift.tools.pypi import PackageInfo

MIGRATION = "https://docs.pydantic.dev/latest/migration/"


class FakeVerifier:
    def __init__(self, results: list[VerificationResult]) -> None:
        self.results = results
        self.labels: list[str] = []

    async def verify(self, workspace: Path, run_id=None, node_id=None, label="verify"):
        self.labels.append(label)
        return self.results.pop(0)


class FakeCompletions:
    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.requests.append(kwargs)
        text = json.dumps(self.outputs.pop(0))
        return ChatCompletion(
            id="x", object="chat.completion", created=0, model=kwargs["model"],
            choices=[Choice(index=0, finish_reason="stop",
                            message=ChatCompletionMessage(role="assistant", content=text))],
            usage=CompletionUsage(
                prompt_tokens=20_000, completion_tokens=800, total_tokens=20_800
            ),
        )


class FakePyPI:
    async def info(self, name: str) -> PackageInfo | None:
        return PackageInfo(name, "2.6.1", ["2.6.1"]) if name == "pydantic-settings" else None


def red(*failures: tuple[str, str]) -> VerificationResult:
    return VerificationResult(
        ok=False, phase="test", exit_code=1, duration_ms=100, tests=3,
        passed=3 - len(failures), failed=len(failures),
        failures=[TestFailure(t, m) for t, m in failures],
    )


def green() -> VerificationResult:
    return VerificationResult(
        ok=True, phase="test", exit_code=0, duration_ms=100, tests=3, passed=3
    )


def workspace(tmp_path: Path) -> Path:
    (tmp_path / "requirements.txt").write_text("pydantic==1.10.13\nrequests==2.28.2\n")
    (tmp_path / "pyshop").mkdir()
    (tmp_path / "pyshop" / "settings.py").write_text(
        "from pydantic import BaseSettings\n\n\nclass Settings(BaseSettings):\n    x: int = 1\n"
    )
    (tmp_path / "pyshop" / "models.py").write_text("from pydantic import BaseModel\n")
    (tmp_path / "secrets.py").write_text("TOKEN = 'x'\n")
    return tmp_path


def dep() -> DependencyAssessment:
    return DependencyAssessment(
        name="pydantic", source="requirements.txt", specifier="==1.10.13", current="1.10.13",
        latest="2.9.2", bump="major", risk=0.8,
        usage={"files": ["pyshop/settings.py", "pyshop/models.py"], "symbols": {}},
    )


def dossier() -> ImpactDossier:
    return ImpactDossier(
        package="pydantic", current="1.10.13", target="2.9.2", sources=[], search_queries=[],
        summary="v2 rewrite", requires_new_dependencies=["pydantic-settings"],
        entries=[ChangeEntry(
            "BaseSettings moved", "breaking", ["BaseSettings"], "2.0",
            "from pydantic_settings import BaseSettings", MIGRATION, "moved", True, 0.98,
            relevant=True,
        )],
    )


def make_surgeon(catalog, outputs, verifier, bus=None):
    completions = FakeCompletions(outputs)
    llm = TokenFactoryClient(
        "k", "https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    surgeon = Surgeon(llm, ModelRouter(catalog), verifier, bus=bus, pypi=FakePyPI())
    return surgeon, completions


def test_schema_is_strict() -> None:
    item = EDIT_SCHEMA["properties"]["edits"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert EDIT_SCHEMA["additionalProperties"] is False


async def test_bump_alone_is_enough(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    surgeon, completions = make_surgeon(catalog, [], FakeVerifier([green()]))
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert result.success and not result.attempts and not completions.requests
    manifest = (ws / "requirements.txt").read_text()
    assert "pydantic==2.9.2" in manifest and "pydantic-settings==2.6.1" in manifest
    assert result.manifest_changes == [
        "pydantic 1.10.13 -> 2.9.2 in requirements.txt",
        "added pydantic-settings==2.6.1 to requirements.txt",
    ]
    assert result.files_changed == ["requirements.txt"] and "+pydantic==2.9.2" in result.diff


async def test_edit_loop_applies_rejects_succeeds(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    failure = (
        "tests.test_settings::test_defaults",
        "pyshop/settings.py:1: in <module>\nE PydanticImportError: BaseSettings moved",
    )
    verifier = FakeVerifier([red(failure), green()])
    outputs = [{
        "analysis": "BaseSettings now lives in pydantic_settings.",
        "edits": [
            {"path": "pyshop/settings.py", "op": "replace_block",
             "search": "from pydantic import BaseSettings\n",
             "replace": "from pydantic_settings import BaseSettings\n",
             "justification": "BaseSettings moved", "evidence_url": MIGRATION},
            {"path": "secrets.py", "op": "write_file", "search": "", "replace": "TOKEN='y'\n",
             "justification": "nope", "evidence_url": ""},
            {"path": "../etc/passwd", "op": "write_file", "search": "", "replace": "x",
             "justification": "nope", "evidence_url": ""},
            {"path": "pyshop/models.py", "op": "replace_block", "search": "not in file",
             "replace": "x", "justification": "typo", "evidence_url": ""},
        ],
        "new_dependencies": [],
    }]
    surgeon, completions = make_surgeon(catalog, outputs, verifier, bus)
    budget = Budget(cap_usd=2.0)

    result = await surgeon.upgrade(ws, dep(), dossier(), run_id=run.id, budget=budget)

    assert result.success and len(result.attempts) == 1
    assert result.attempts[0].model == catalog.models["super"].id
    statuses = {e.path: (e.status, e.reason) for e in result.attempts[0].edits}
    assert statuses["pyshop/settings.py"] == ("applied", "")
    assert statuses["secrets.py"][0] == "rejected" and "allowed" in statuses["secrets.py"][1]
    assert statuses["../etc/passwd"] == ("rejected", "invalid path")
    assert "not found" in statuses["pyshop/models.py"][1]
    assert (ws / "pyshop" / "settings.py").read_text().startswith("from pydantic_settings")
    assert (ws / "secrets.py").read_text() == "TOKEN = 'x'\n"
    assert sorted(result.files_changed) == ["pyshop/settings.py", "requirements.txt"]
    assert "-from pydantic import BaseSettings" in result.diff
    assert budget.spent_usd > 0 and result.cost_usd == budget.spent_usd
    assert verifier.labels == ["pydantic: after version bump", "pydantic: attempt 1"]

    prompt = completions.requests[0]["messages"][1]["content"]
    assert "=== pyshop/settings.py ===" in prompt and "=== secrets.py ===" not in prompt
    assert "BaseSettings moved" in prompt and "PydanticImportError" in prompt
    assert completions.requests[0]["response_format"]["type"] == "json_schema"

    types = [e.type for e in store.events(run.id)]
    assert types.count(EventType.PATCH_PROPOSED) == 2  # manifest + attempt 1
    assert types.count(EventType.PATCH_REJECTED) == 3


async def test_escalates_and_rolls_back(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    f1 = ("t::a", "pyshop/settings.py:1: boom")
    verifier = FakeVerifier([red(f1), red(f1, ("t::b", "worse")), red(f1), green()])

    def edit(replace: str) -> dict[str, Any]:
        return {"analysis": "try", "new_dependencies": [], "edits": [{
            "path": "pyshop/settings.py", "op": "write_file", "search": "",
            "replace": replace, "justification": "j", "evidence_url": "",
        }]}

    outputs = [edit("BAD = 1\n"), edit("STILL_BAD = 1\n"), edit("GOOD = 1\n")]
    surgeon, _ = make_surgeon(catalog, outputs, verifier, bus)
    result = await surgeon.upgrade(ws, dep(), dossier(), run_id=run.id)

    assert result.success and len(result.attempts) == 3
    assert result.attempts[0].rolled_back  # 2 failures > 1 baseline failure
    assert not result.attempts[1].rolled_back
    models = [a.model for a in result.attempts]
    assert models == [catalog.models["super"].id] * 2 + [catalog.models["ultra"].id]
    assert (ws / "pyshop" / "settings.py").read_text() == "GOOD = 1\n"
    escalations = [e for e in store.events(run.id) if e.type is EventType.ESCALATION]
    assert len(escalations) == 1 and escalations[0].payload["attempt"] == 3


async def test_gives_up_after_max_attempts(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    f1 = ("t::a", "pyshop/settings.py:1: boom")
    verifier = FakeVerifier([red(f1), red(f1), red(f1)])
    outputs = [{"analysis": "a", "new_dependencies": [], "edits": [{
        "path": "pyshop/settings.py", "op": "write_file", "search": "", "replace": f"V = {i}\n",
        "justification": "j", "evidence_url": ""}]} for i in range(2)]
    surgeon, _ = make_surgeon(catalog, outputs, verifier)
    surgeon.max_attempts = 2
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert not result.success and len(result.attempts) == 2
    assert result.error and "still failing" in result.error


async def test_install_failure_stops_early(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    install_failed = VerificationResult(ok=False, phase="install", exit_code=1, duration_ms=5)
    surgeon, completions = make_surgeon(catalog, [], FakeVerifier([install_failed]))
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert not result.success and "install" in (result.error or "")
    assert not completions.requests


async def test_missing_pin_is_an_error(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    (ws / "requirements.txt").write_text("pydantic>=1\n")
    surgeon, _ = make_surgeon(catalog, [], FakeVerifier([]))
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert not result.success and "pin" in (result.error or "")
