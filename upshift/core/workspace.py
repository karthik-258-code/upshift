"""Prepare an isolated working copy of a repository (local path or git URL)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from asyncio.subprocess import PIPE
from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache",
)


@dataclass
class Workspace:
    path: Path
    source: str
    commit: str | None = None
    _tmp: tempfile.TemporaryDirectory | None = None

    def cleanup(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


async def _git_head(path: Path) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(path), "rev-parse", "HEAD", stdout=PIPE, stderr=PIPE
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (OSError, TimeoutError):
        return None
    return out.decode().strip() or None if proc.returncode == 0 else None


async def prepare_workspace(source: str, base_dir: Path | None = None) -> Workspace:
    """Copy a local directory or shallow-clone a git URL into a fresh temporary directory.

    Patches are applied to this copy, never to the user's original checkout.
    """
    if base_dir is not None:
        base_dir.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory(
        prefix="upshift-ws-", dir=str(base_dir) if base_dir else None, ignore_cleanup_errors=True
    )
    target = Path(tmp.name) / "repo"

    local = Path(source.removeprefix("file://"))
    if local.is_dir():
        await asyncio.to_thread(shutil.copytree, local, target, ignore=IGNORE)
        return Workspace(target, source, await _git_head(local), tmp)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--quiet", source, str(target),
            stdout=PIPE, stderr=PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    except (OSError, TimeoutError) as exc:
        tmp.cleanup()
        raise WorkspaceError(f"git clone failed: {exc}") from exc
    if proc.returncode != 0:
        tmp.cleanup()
        raise WorkspaceError(f"git clone failed: {err.decode(errors='replace').strip()}")
    return Workspace(target, source, await _git_head(target), tmp)
