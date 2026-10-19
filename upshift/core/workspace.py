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


IGNORE_PATTERNS = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".data", ".cache",
)


def _ignore_factory(exclude: Path | None):  # type: ignore[no-untyped-def]
    """Pattern-based ignores plus a hard exclusion of the destination tree itself.

    Without this, scanning a repo whose .data/ directory holds the workspaces would copy
    the copy of the copy until the recursion limit.
    """
    resolved = exclude.resolve() if exclude is not None else None

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(IGNORE_PATTERNS(directory, names))
        if resolved is not None:
            for name in names:
                candidate = (Path(directory) / name).resolve()
                if candidate == resolved or resolved in candidate.parents:
                    ignored.add(name)
        return ignored

    return ignore


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
        # exclude only this copy's own destination: the source may itself be a workspace
        # living under base_dir (the Supervisor copies the base checkout per dependency)
        await asyncio.to_thread(
            shutil.copytree, local, target, ignore=_ignore_factory(Path(tmp.name))
        )
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
