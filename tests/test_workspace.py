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


async def test_copy_of_a_workspace_inside_the_same_base_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "requirements.txt").write_text("pydantic==1.10.13\n")
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    base_dir = tmp_path / "workspaces"

    base = await prepare_workspace(str(repo), base_dir=base_dir)
    copy = await prepare_workspace(str(base.path), base_dir=base_dir)
    try:
        assert (copy.path / "requirements.txt").read_text() == "pydantic==1.10.13\n"
        assert (copy.path / "pkg" / "a.py").exists()
        assert copy.path != base.path
    finally:
        copy.cleanup()
        base.cleanup()
