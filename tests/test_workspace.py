from __future__ import annotations

from pathlib import Path

from upshift.core.workspace import prepare_workspace


async def test_local_copy_excludes_destination_and_junk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("print('hi')\n")
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "x.py").write_text("")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "a.pyc").write_bytes(b"")

    # destination lives inside the source, the exact situation that recursed forever
    workspace = await prepare_workspace(str(repo), base_dir=repo / ".data" / "workspaces")
    try:
        copied = sorted(p.relative_to(workspace.path).as_posix() for p in workspace.path.rglob("*"))
        assert copied == ["app", "app/main.py"]
        assert workspace.path.is_dir() and workspace.source == str(repo)
    finally:
        workspace.cleanup()
