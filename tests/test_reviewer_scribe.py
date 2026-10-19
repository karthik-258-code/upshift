from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from upshift.agents.historian import ChangeEntry, EvidenceSource, ImpactDossier
from upshift.agents.reviewer import LINT_PATH, Reviewer, parse_lint
from upshift.agents.scout import DependencyAssessment
from upshift.agents.scribe import Scribe
from upshift.agents.surgeon import AppliedEdit, Attempt, SurgeryResult
from upshift.llm.client import TokenFactoryClient
from upshift.llm.models import ModelCatalog
from upshift.llm.router import ModelRouter
from upshift.sandbox.base import ExecutionRequest, ExecutionResult

MIGRATION = "https://docs.pydantic.dev/latest/migration/"

LINT_JSON = json.dumps([
    {
        "code": "F401", "message": "`pydantic.types.StringConstraints` imported but unused",
        "filename": "/work/pyshop/models.py", "location": {"row": 11, "column": 1},
        "fix": {"message": "Remove unused import"},
    },
    {
        "code": "E402", "message": "Module level import not at top of file",
        "filename": "/work/pyshop/models.py", "location": {"row": 29, "column": 1},
        "fix": None,
    },
])


class FakeExecutor:
    name = "fake"

    def __init__(self, lint_json: str) -> None:
        self.lint_json = lint_json
        self.requests: list[ExecutionRequest] = []

    async def available(self) -> bool:
        return True

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return ExecutionResult(0, "", "", 10, artifacts={LINT_PATH: self.lint_json})


class FakeCompletions:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.requests.append(kwargs)
        return ChatCompletion(
            id="x", object="chat.completion", created=0, model=kwargs["model"],
            choices=[Choice(index=0, finish_reason="stop", message=ChatCompletionMessage(
                role="assistant", content=self.outputs.pop(0)))],
            usage=CompletionUsage(prompt_tokens=5000, completion_tokens=300, total_tokens=5300),
        )


def llm_with(outputs: list[str]) -> tuple[TokenFactoryClient, FakeCompletions]:
    completions = FakeCompletions(outputs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return TokenFactoryClient("k", "https://example.test/v1/", client=client), completions


def dep() -> DependencyAssessment:
    return DependencyAssessment(
        "pydantic", "requirements.txt", "==1.10.13", "1.10.13", "2.9.2", "major", 0.8,
        usage={"files": ["pyshop/models.py"], "symbols": {"pydantic.BaseModel": 3}},
    )


def dossier() -> ImpactDossier:
    return ImpactDossier(
        "pydantic", "1.10.13", "2.9.2",
        sources=[EvidenceSource(MIGRATION, "search", fetched=True, chars=100)],
        search_queries=["q"], summary="v2 rewrite", model="nvidia/Nemotron-3_5-Lightning",
        cost_usd=0.004,
        entries=[ChangeEntry(
            "BaseSettings moved", "breaking", ["BaseSettings"], "2.0", "use pydantic_settings",
            MIGRATION, "BaseSettings has moved.", True, 0.98, relevant=True,
            matched_symbols=["pydantic.BaseSettings"],
        )],
    )


def surgery(success: bool = True) -> SurgeryResult:
    edit = AppliedEdit("pyshop/models.py", "replace_block", "moved import", MIGRATION, "applied")
    return SurgeryResult(
        "pydantic", "1.10.13", "2.9.2", success,
        manifest_changes=["pydantic 1.10.13 -> 2.9.2 in requirements.txt"],
        attempts=[Attempt(
            1, "nvidia/nemotron-3-super-120b-a12b", "fixed imports", [edit],
            {"tests": 17, "passed": 17, "failure_count": 0}, 0.004,
        )],
        files_changed=["pyshop/models.py", "requirements.txt"],
        diff="--- a/pyshop/models.py\n+++ b/pyshop/models.py\n-old\n+new\n",
        after_bump={"tests": 2, "passed": 0, "failure_count": 2},
        final={"tests": 17, "passed": 17, "failure_count": 0}, cost_usd=0.015,
    )


def test_parse_lint() -> None:
    findings = parse_lint(LINT_JSON)
    assert [f.title.split()[0] for f in findings] == ["F401", "E402"]
    assert findings[0].file == "pyshop/models.py" and findings[0].line == 11
    assert findings[0].source == "lint" and findings[0].suggestion == "Remove unused import"
    assert parse_lint("") == [] and parse_lint("garbage") == []


async def test_review_combines_lint_and_model(tmp_path: Path, catalog: ModelCatalog) -> None:
    (tmp_path / "pyshop").mkdir()
    (tmp_path / "pyshop" / "models.py").write_text("x = 1\n")
    model_output = json.dumps({
        "verdict": "approve", "summary": "Looks right.", "confidence": 0.9,
        "findings": [{
            "severity": "blocker", "kind": "weakened_test", "file": "tests/test_models.py",
            "title": "test input changed", "detail": "price replaced", "suggestion": "restore",
        }],
    })
    llm, completions = llm_with([model_output])
    executor = FakeExecutor(LINT_JSON)
    report = await Reviewer(llm, ModelRouter(catalog), executor).review(
        tmp_path, dep(), dossier(), surgery()
    )
    assert report.verdict == "request_changes"  # blocker overrides the model's approve
    assert len(report.findings) == 3 and len(report.blockers) == 1
    assert len(report.actionable) == 3  # 2 lint + 1 blocker
    assert report.model == catalog.models["ultra"].id and report.cost_usd > 0
    assert "ruff check --select" in executor.requests[0].command
    prompt = completions.requests[0]["messages"][1]["content"]
    assert "F401" in prompt and "Unified diff:" in prompt and "BaseSettings moved" in prompt


async def test_review_skipped_when_upgrade_failed(tmp_path: Path, catalog: ModelCatalog) -> None:
    llm, completions = llm_with([])
    report = await Reviewer(llm, ModelRouter(catalog), FakeExecutor("")).review(
        tmp_path, dep(), dossier(), surgery(success=False)
    )
    assert report.verdict == "skipped" and not completions.requests


async def test_scribe_renders_auditable_description(catalog: ModelCatalog) -> None:
    llm, _ = llm_with(["Upgrades pydantic to v2 with the documented API changes; suite green."])
    draft = await Scribe(llm, ModelRouter(catalog)).write(dep(), dossier(), surgery(), None)
    assert draft.title == "chore(deps): upgrade pydantic 1.10.13 -> 2.9.2"
    assert draft.branch == "upshift/pydantic-2.9.2"
    body = draft.description
    assert body.startswith("Upgrades pydantic to v2")
    assert f"| `pyshop/models.py` | moved import | [source]({MIGRATION}) |" in body
    assert "**breaking**: BaseSettings moved (pydantic.BaseSettings)" in body
    assert "After Upshift's edits: **17 passing, 0 failing of 17 collected**" in body
    assert "nemotron-3-super-120b-a12b" in body and "$0.0190" in body
    assert "Nebius Token Factory" in body


async def test_scribe_falls_back_without_llm() -> None:
    draft = await Scribe().write(dep(), dossier(), surgery(), None)
    assert draft.description.startswith("Upgrades `pydantic` from 1.10.13 to 2.9.2.")


async def test_scribe_marks_draft_when_blockers_remain() -> None:
    from upshift.agents.reviewer import Finding, ReviewReport

    review = ReviewReport(
        "request_changes", "weakened test",
        [Finding("blocker", "weakened_test", "tests/test_models.py", "input changed")],
    )
    draft = await Scribe().write(dep(), dossier(), surgery(), review)
    assert draft.title.startswith("Draft: chore(deps)")
    assert draft.description.startswith("> **Draft: the reviewer found 1 blocking issue(s)")
    assert "- [blocker] `tests/test_models.py` input changed" in draft.description
