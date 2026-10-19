from __future__ import annotations

from pathlib import Path

from upshift.agents.scout import Scout, bump_type
from upshift.core.bus import EventBus
from upshift.core.events import EventType, Run
from upshift.core.store import EventStore
from upshift.tools.pypi import PackageInfo


class FakePyPI:
    def __init__(self) -> None:
        self.infos = {
            "pydantic": PackageInfo(
                "pydantic", "2.9.2", ["1.10.13", "2.9.2"],
                changelog_url="https://docs.pydantic.dev/latest/changelog/",
                release_dates={"2.9.2": "2024-09-17"},
            ),
            "sqlalchemy": PackageInfo("sqlalchemy", "2.0.35", ["1.4.49", "2.0.35"]),
            "requests": PackageInfo("requests", "2.32.3", ["2.32.3"]),
            "python-dateutil": PackageInfo("python-dateutil", "2.9.0", ["2.9.0"]),
            "httpx": PackageInfo("httpx", "0.28.0", ["0.27.0", "0.28.0"]),
        }

    async def info(self, name: str) -> PackageInfo | None:
        return self.infos.get(name)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "requirements.txt").write_text(
        "pydantic==1.10.13\nsqlalchemy==1.4.49\nrequests>=2.28\n"
        "python-dateutil==2.9.0\nhttpx==0.27.0\nghost-package==1.0\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text(
        "from pydantic import BaseModel, validator, Field\n"
        "from sqlalchemy.orm import Session\n"
        "import dateutil.parser\n"
        "class U(BaseModel):\n    x: int = Field(1)\n"
    )
    return tmp_path


def test_bump_type() -> None:
    assert bump_type("1.10.13", "2.9.2") == "major"
    assert bump_type("1.4.49", "1.5.0") == "minor"
    assert bump_type("2.9.0", "2.9.1") == "patch"
    assert bump_type("2.9.0", "2.9.0") == "none"
    assert bump_type(None, "1.0") == "unknown"
    assert bump_type("weird", "1.0") == "unknown"


async def test_scout_assesses_and_ranks(tmp_path: Path) -> None:
    report = await Scout(FakePyPI()).assess(_workspace(tmp_path))  # type: ignore[arg-type]

    by_name = {d.name: d for d in report.dependencies}
    assert report.manifests == ["requirements.txt"] and report.python_files == 1
    assert set(by_name) == {
        "pydantic", "sqlalchemy", "requests", "python-dateutil", "httpx", "ghost-package",
    }

    pyd = by_name["pydantic"]
    assert pyd.bump == "major" and pyd.skip_reason is None
    assert {"pydantic.BaseModel", "pydantic.validator", "pydantic.Field"} <= set(
        pyd.usage["symbols"]
    )
    assert pyd.changelog_url and pyd.release_date_latest == "2024-09-17"

    assert by_name["httpx"].bump == "minor"
    assert any("0.x" in f for f in by_name["httpx"].risk_factors)
    assert by_name["requests"].skip_reason and "unpinned" in by_name["requests"].skip_reason
    assert by_name["python-dateutil"].skip_reason == "already at latest"
    assert by_name["ghost-package"].skip_reason == "not found on PyPI"

    actionable = [d.name for d in report.actionable]
    assert actionable[0] == "pydantic"  # major bump with the most used symbols
    assert set(actionable) == {"pydantic", "sqlalchemy", "httpx"}
    assert [d.skip_reason is None for d in report.dependencies][: len(actionable)] == [True] * 3


async def test_scout_emits_events(tmp_path: Path) -> None:
    store = EventStore()
    bus = EventBus(store)
    run = store.create_run(Run(repo_url=str(tmp_path)))

    await Scout(FakePyPI(), bus=bus).assess(_workspace(tmp_path), run_id=run.id)  # type: ignore[arg-type]

    events = store.events(run.id)
    discovered = [e for e in events if e.type is EventType.DEP_DISCOVERED]
    assert len(discovered) == 6
    assert all(e.agent == "scout" and e.node_id == f"dep:{e.payload['name']}" for e in discovered)
    assert events[0].type is EventType.AGENT_MESSAGE and events[-1].type is EventType.AGENT_MESSAGE
