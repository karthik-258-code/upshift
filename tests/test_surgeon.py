from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from upshift.agents.historian import ChangeEntry, ImpactDossier
from upshift.agents.scout import DependencyAssessment
from upshift.agents.surgeon import Surgeon, parse_edit_blocks, unescape_literal_newlines
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


def edit_block(path: str, search: str, replace: str, just: str = "j", ev: str = "none") -> str:
    return (
        f"### EDIT {path}\n<<<<<<< SEARCH\n{search}=======\n{replace}>>>>>>> REPLACE\n"
        f"JUSTIFICATION: {just}\nEVIDENCE: {ev}\n\n"
    )


def write_block(path: str, content: str, just: str = "j", ev: str = "none") -> str:
    return (
        f"### WRITE {path}\n<<<<<<< FILE\n{content}>>>>>>> END\n"
        f"JUSTIFICATION: {just}\nEVIDENCE: {ev}\n\n"
    )


def response(analysis: str, *blocks: str, deps: str = "none") -> str:
    return f"ANALYSIS:\n{analysis}\n\nNEW_DEPENDENCIES: {deps}\n\n" + "".join(blocks)


class FakeVerifier:
    def __init__(self, results: list[VerificationResult]) -> None:
        self.results = results
        self.labels: list[str] = []

    async def verify(self, workspace: Path, run_id=None, node_id=None, label="verify", **_):
        self.labels.append(label)
        return self.results.pop(0)


class FakeCompletions:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.requests.append(kwargs)
        text = self.outputs.pop(0)
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
    (tmp_path / "pyshop" / "models.py").write_text("from pydantic import BaseModel\n" * 30)
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


# ---- protocol parsing -------------------------------------------------------


def test_parse_edit_blocks() -> None:
    text = response(
        "BaseSettings moved.\nAlso Config.",
        edit_block("pyshop/settings.py", "from pydantic import BaseSettings\n",
                   "from pydantic_settings import BaseSettings\n", "moved", MIGRATION),
        write_block("pyshop/new.py", "```python\nX = 1\n```\n", "new"),
        deps="pydantic-settings, `rich`",
    )
    data = parse_edit_blocks(text)
    assert data["analysis"] == "BaseSettings moved.\nAlso Config."
    assert data["new_dependencies"] == ["pydantic-settings", "rich"]
    assert len(data["edits"]) == 2
    first, second = data["edits"]
    assert first == {
        "path": "pyshop/settings.py", "op": "replace_block",
        "search": "from pydantic import BaseSettings\n",
        "replace": "from pydantic_settings import BaseSettings\n",
        "justification": "moved", "evidence_url": MIGRATION,
    }
    assert second["op"] == "write_file" and second["replace"] == "X = 1\n"  # fences stripped
    assert second["evidence_url"] == ""


def test_parse_edit_blocks_json_fallback_and_empty() -> None:
    data = parse_edit_blocks('{"analysis": "a", "edits": [{"path": "p"}], "new_dependencies": []}')
    assert data["analysis"] == "a" and data["edits"] == [{"path": "p"}]
    assert parse_edit_blocks("I could not determine the fix.") == {
        "analysis": "", "edits": [], "new_dependencies": [],
    }


def test_unescape_literal_newlines() -> None:
    assert unescape_literal_newlines("a\\nb\\n") == "a\nb\n"
    assert unescape_literal_newlines("a\nb\\n") == "a\nb\\n"  # real newlines present: untouched
    assert unescape_literal_newlines("plain") == "plain"


# ---- loop behaviour ---------------------------------------------------------


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
    outputs = [response(
        "BaseSettings now lives in pydantic_settings.",
        edit_block("pyshop/settings.py", "from pydantic import BaseSettings\n",
                   "from pydantic_settings import BaseSettings\n", "BaseSettings moved",
                   MIGRATION),
        write_block("secrets.py", "TOKEN='y'\n"),
        write_block("../etc/passwd", "x\n"),
        edit_block("pyshop/models.py", "not in file\n", "x\n"),
        write_block("pyshop/models.py", "tiny\n"),
        write_block("pyshop/helpers.py", "HELPER = True\n"),
    )]
    surgeon, completions = make_surgeon(catalog, outputs, verifier, bus)
    budget = Budget(cap_usd=2.0)

    result = await surgeon.upgrade(ws, dep(), dossier(), run_id=run.id, budget=budget)

    assert result.success and len(result.attempts) == 1
    assert result.attempts[0].model == catalog.models["super"].id
    edits = result.attempts[0].edits
    status = {(e.path, e.op): (e.status, e.reason) for e in edits}
    assert status[("pyshop/settings.py", "replace_block")] == ("applied", "")
    assert status[("secrets.py", "write_file")][1] == "outside the allowed files"
    assert status[("../etc/passwd", "write_file")] == ("rejected", "invalid path")
    assert "not found" in status[("pyshop/models.py", "replace_block")][1]
    assert "shrink" in status[("pyshop/models.py", "write_file")][1]
    assert status[("pyshop/helpers.py", "write_file")] == ("applied", "")  # new file, allowed dir
    assert (ws / "pyshop" / "settings.py").read_text().startswith("from pydantic_settings")
    assert (ws / "secrets.py").read_text() == "TOKEN = 'x'\n"
    assert (ws / "pyshop" / "helpers.py").read_text() == "HELPER = True\n"
    assert sorted(result.files_changed) == [
        "pyshop/helpers.py", "pyshop/settings.py", "requirements.txt",
    ]
    assert "-from pydantic import BaseSettings" in result.diff and "/dev/null" in result.diff
    assert budget.spent_usd > 0 and result.cost_usd == budget.spent_usd
    assert verifier.labels == ["pydantic: after version bump", "pydantic: attempt 1"]

    request = completions.requests[0]
    assert "response_format" not in request and request["max_tokens"] == 24_000
    assert "### EDIT" in request["messages"][0]["content"]
    prompt = request["messages"][1]["content"]
    assert "=== pyshop/settings.py ===" in prompt and "=== secrets.py ===" not in prompt
    assert "BaseSettings moved" in prompt and "PydanticImportError" in prompt

    types = [e.type for e in store.events(run.id)]
    assert types.count(EventType.PATCH_PROPOSED) == 2  # manifest + attempt 1
    assert types.count(EventType.PATCH_REJECTED) == 4


async def test_blocks_located_against_original(tmp_path: Path, catalog: ModelCatalog) -> None:
    """Two edits whose search text comes from the same original file both apply."""
    ws = workspace(tmp_path)
    (ws / "pyshop" / "settings.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    f1 = ("t::a", "pyshop/settings.py:1: boom")
    outputs = [response(
        "two edits",
        edit_block("pyshop/settings.py", "a = 1\n", "a = 10\nextra = True\n"),
        edit_block("pyshop/settings.py", "c = 3\n", "c = 30\n"),
        edit_block("pyshop/settings.py", "c = 3\nd = 4\n", "never\n"),  # overlaps the previous
    )]
    surgeon, _ = make_surgeon(catalog, outputs, FakeVerifier([red(f1), green()]))
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert result.success
    expected = "a = 10\nextra = True\nb = 2\nc = 30\nd = 4\n"
    assert (ws / "pyshop" / "settings.py").read_text() == expected
    statuses = [(e.status, e.reason) for e in result.attempts[0].edits]
    assert statuses[:2] == [("applied", ""), ("applied", "")]
    assert "overlaps" in statuses[2][1]


async def test_literal_newlines_are_repaired(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    (ws / "pyshop" / "models.py").write_text("A = 1\n")
    f1 = ("t::a", "pyshop/models.py:1: boom")
    outputs = [response("fix", write_block("pyshop/models.py", "A = 1\\nB = 2\\nC = 3\n"))]
    surgeon, _ = make_surgeon(catalog, outputs, FakeVerifier([red(f1), green()]))
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert result.success
    assert (ws / "pyshop" / "models.py").read_text() == "A = 1\nB = 2\nC = 3\n"


async def test_escalates_and_rolls_back(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url="x"))
    f1 = ("t::a", "pyshop/settings.py:1: boom")
    f2 = ("t::b", "pyshop/settings.py:2: other")
    verifier = FakeVerifier([red(f1), red(f1, f2), red(f1), red(f2), green()])

    def rewrite(content: str) -> str:
        return response("try", write_block("pyshop/settings.py", content))

    outputs = [rewrite("WORSE = 1\n"), rewrite("SAME = 1\n"), rewrite("PROGRESS = 1\n")]
    surgeon, completions = make_surgeon(catalog, outputs, verifier, bus)
    surgeon.max_attempts = 4
    outputs.append(rewrite("GOOD = 1\n"))
    result = await surgeon.upgrade(ws, dep(), dossier(), run_id=run.id)

    assert result.success and len(result.attempts) == 4
    reasons = [a.rollback_reason for a in result.attempts]
    assert reasons[0].startswith("worse: 1 passing, 2 failing")
    assert "same failures" in reasons[1]
    # after the rollback the next prompt explains what was changed and what it caused
    second_prompt = completions.requests[1]["messages"][1]["content"]
    assert "ROLLED BACK" in second_prompt
    assert "What that attempt changed:" in second_prompt
    assert "pyshop/settings.py: j" in second_prompt
    assert "What it caused (do NOT repeat this):" in second_prompt
    assert "t::b" in second_prompt
    assert reasons[2] == "" and not result.attempts[2].rolled_back  # different failure: progress
    assert (ws / "pyshop" / "settings.py").read_text() == "GOOD = 1\n"
    models = [a.model for a in result.attempts]
    assert models[0] == catalog.models["super"].id
    assert models[1:] == [catalog.models["ultra"].id] * 3
    escalations = [e for e in store.events(run.id) if e.type is EventType.ESCALATION]
    assert len(escalations) == 1 and escalations[0].payload["attempt"] == 2


async def test_collection_fix_counts_as_progress(tmp_path: Path, catalog: ModelCatalog) -> None:
    """One collection error hiding 17 tests -> 17 collected with 4 failing is progress."""
    ws = workspace(tmp_path)
    def collection_error(message: str) -> VerificationResult:
        return VerificationResult(
            ok=False, phase="test", exit_code=2, duration_ms=1, tests=1, passed=0, errors=1,
            failures=[TestFailure("tests.test_models", message, "error")],
        )

    masked = collection_error("pyshop/models.py:9: in <module>\nE ImportError")
    changed_error = collection_error("pyshop/models.py:30: in <module>\nE TypeError")
    partial = VerificationResult(
        ok=False, phase="test", exit_code=1, duration_ms=1, tests=17, passed=13, failed=4,
        failures=[TestFailure(f"t::{i}", "tests/test_models.py:1: E x") for i in range(4)],
    )
    verifier = FakeVerifier([masked, changed_error, partial, green()])
    outputs = [response("a", write_block("pyshop/settings.py", f"V = {i}\n")) for i in range(3)]
    surgeon, _ = make_surgeon(catalog, outputs, verifier)
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert result.success
    assert [a.rolled_back for a in result.attempts] == [False, False, False]


def test_error_line_cleaning() -> None:
    from upshift.agents.surgeon import error_line

    message = (
        "/work/pyshop/models.py:41: in <module>\n    class Product(BaseModel):\n"
        "E   pydantic.warnings.PydanticDeprecatedSince211: Annotation 'kind' is marked as "
        "final and has a default value.\nE   For further information visit https://x"
    )
    assert error_line(message).startswith(
        "pydantic.warnings.PydanticDeprecatedSince211: Annotation 'kind' is marked as final"
    )
    assert error_line("E       Failed: DID NOT RAISE <class 'pydantic.ValidationError'>") == (
        "Failed: DID NOT RAISE pydantic.ValidationError"
    )
    assert error_line("") == ""


async def test_targeted_research_after_rollback(tmp_path: Path, catalog: ModelCatalog) -> None:
    from upshift.tools.tavily_search import TavilySearchTool

    class FakeTavily:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, **params: Any) -> dict[str, Any]:
            self.queries.append(params["query"])
            return {"results": [{
                "title": "Migration guide", "url": "https://docs.example/migration",
                "content": "const was removed; use Literal instead.", "score": 0.9,
            }]}

    ws = workspace(tmp_path)
    f1 = ("t::const_kind_rejected", "tests/test_models.py:40: E Failed: DID NOT RAISE X")
    f2 = ("t::import", "pyshop/models.py:41: in <module>\nE TypeError: final with default")
    verifier = FakeVerifier([red(f1), red(f2, ("t::other", "E boom")), green()])
    outputs = [
        response("a", write_block("pyshop/settings.py", "BAD = 1\n")),
        response("b", write_block("pyshop/settings.py", "GOOD = 1\n")),
    ]
    surgeon, completions = make_surgeon(catalog, outputs, verifier)
    fake = FakeTavily()
    surgeon.search = TavilySearchTool(client=fake)
    result = await surgeon.upgrade(ws, dep(), dossier())

    assert result.success and result.attempts[0].rolled_back
    assert fake.queries[0].startswith("pydantic 2 TypeError: final with default")
    assert any("DID NOT RAISE X" in q for q in fake.queries)
    second_prompt = completions.requests[1]["messages"][1]["content"]
    assert "Targeted web research" in second_prompt
    assert "const was removed; use Literal instead." in second_prompt
    assert "Targeted web research" not in completions.requests[0]["messages"][1]["content"]


def test_warnings_as_errors_detection(tmp_path: Path) -> None:
    from upshift.agents.surgeon import warnings_are_errors

    assert warnings_are_errors(tmp_path) is None
    (tmp_path / "pytest.ini").write_text("[pytest]\nfilterwarnings =\n    ignore::UserWarning\n")
    assert warnings_are_errors(tmp_path) is None
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nfilterwarnings =\n    error::DeprecationWarning:pyshop\n"
    )
    assert warnings_are_errors(tmp_path) == "pytest.ini"


async def test_unparsable_response_is_recorded(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    f1 = ("t::a", "pyshop/settings.py:1: boom")
    surgeon, completions = make_surgeon(
        catalog, ["I think the fix is obvious.", response("ok", write_block(
            "pyshop/settings.py", "GOOD = 1\n"))],
        FakeVerifier([red(f1), green()]),
    )
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert result.success and len(result.attempts) == 2
    assert result.attempts[0].edits == [] and "no edit blocks" in result.attempts[0].analysis
    assert "NO parsable edit blocks" in completions.requests[1]["messages"][1]["content"]


async def test_gives_up_after_max_attempts(tmp_path: Path, catalog: ModelCatalog) -> None:
    ws = workspace(tmp_path)
    f1 = ("t::a", "pyshop/settings.py:1: boom")
    verifier = FakeVerifier([red(f1), red(f1), red(f1)])
    outputs = [response("a", write_block("pyshop/settings.py", f"V = {i}\n")) for i in range(2)]
    surgeon, _ = make_surgeon(catalog, outputs, verifier)
    surgeon.max_attempts = 2
    result = await surgeon.upgrade(ws, dep(), dossier())
    assert not result.success and len(result.attempts) == 2
    assert result.error and "still failing" in result.error
    assert (ws / "pyshop" / "settings.py").read_text().startswith("from pydantic")  # restored


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
