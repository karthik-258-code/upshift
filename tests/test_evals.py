from __future__ import annotations

from pathlib import Path

from upshift.config import ROOT
from upshift.core.events import Event, EventType, Run
from upshift.evals import EvalCase, case_result, load_cases, markdown_table


def test_case_result_and_markdown_table() -> None:
    run = Run(repo_url="demo-repos/flask-notes", packages=["flask"], spent_usd=0.12)
    run.summary = {"dependencies": [{
        "name": "flask", "current": "2.0.3", "target": "3.1.3", "success": True,
        "attempts": 3, "review_verdict": "approve", "cost_usd": 0.1,
        "final": {"tests": 9, "passed": 9, "failure_count": 0},
        "manifest_changes": ["werkzeug co-upgraded to 3.1.9 in requirements.txt"],
    }]}
    events = [
        Event(  # pip refused the install: not a test run, skipped as the bare-bump state
            run_id=run.id, type=EventType.TEST_RESULT, agent="verifier",
            payload={"label": "flask: after version bump", "phase": "install", "tests": 0,
                     "passed": 0, "failure_count": 0},
        ),
        Event(
            run_id=run.id, type=EventType.TEST_RESULT, agent="verifier",
            payload={"label": "flask: after co-upgrading werkzeug", "phase": "test",
                     "tests": 1, "passed": 0, "errors": 1, "failure_count": 1},
        ),
    ]
    result = case_result(EvalCase("flask-notes-flask", "demo-repos/flask-notes", "flask"),
                         1, run, events, 95.0)
    assert result.success and result.expected
    assert (result.bare_passed, result.bare_total) == (0, 1)
    assert result.bare_phase == "collection"
    assert (result.final_passed, result.final_total) == (9, 9)
    assert result.manifest_changes == ["werkzeug co-upgraded to 3.1.9 in requirements.txt"]

    table = markdown_table([result], "2026-10-22 18:00 UTC")
    assert (
        "| flask-notes-flask | flask 2.0.3 -> 3.1.3 | 0 (import error) | **9/9** | 3 | approve "
        "| $0.100 | 1m35s | pass |"
    ) in table
    assert "**1/1 upgrades landed** (100%)" in table

    failed = case_result(
        EvalCase("x", "r", "pkg", expect="upgraded"), 2,
        Run(repo_url="r", summary={"dependencies": [{
            "name": "pkg", "success": False, "attempts": 12,
            "error": "tests still failing after 12 attempt(s)",
            "final": {"tests": 9, "passed": 8, "failure_count": 1},
        }]}),
        [], 600.0,
    )
    assert not failed.success and not failed.expected
    assert "| x #2 |" in markdown_table([failed], "now")
    assert "FAIL (tests still failing" in markdown_table([failed], "now")


def test_load_cases(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text("cases:\n  - id: a\n    repo: r\n    package: p\n")
    cases = load_cases(tmp_path / "c.yaml")
    assert cases[0].id == "a" and cases[0].expect == "upgraded" and cases[0].note == ""
    real = load_cases(ROOT / "evals" / "cases.yaml")
    assert {c.id for c in real} >= {"pyshop-pydantic", "pyshop-sqlalchemy", "flask-notes-flask"}
    assert all(c.note for c in real)
