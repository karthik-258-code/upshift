"""Python ecosystem: manifest discovery and dependency parsing (requirements.txt, pyproject)."""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

log = logging.getLogger(__name__)

SKIP_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist",
        ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".eggs",
    }
)
REQUIREMENTS_NAME = re.compile(r"requirements[-_.\w]*\.txt")


def normalize(name: str) -> str:
    """PEP 503 normalisation: `Flask_SQLAlchemy` -> `flask-sqlalchemy`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def skipped(rel_parts: tuple[str, ...]) -> bool:
    return any(part in SKIP_DIRS for part in rel_parts)


@dataclass
class Dependency:
    name: str
    raw_name: str
    specifier: str
    source: str
    line: int | None = None
    extras: list[str] = field(default_factory=list)
    marker: str | None = None
    group: str = "main"

    @property
    def pinned(self) -> str | None:
        match = re.fullmatch(r"\s*==\s*([0-9][^,\s*]*)", self.specifier)
        return match.group(1) if match else None


@dataclass
class Manifest:
    path: str
    kind: str  # requirements | pyproject
    dependencies: list[Dependency]


def _from_requirement(
    text: str, source: str, line: int | None = None, group: str = "main"
) -> Dependency | None:
    try:
        req = Requirement(text)
    except InvalidRequirement:
        log.debug("skipping unparsable requirement %r in %s", text, source)
        return None
    if req.url:
        return None
    return Dependency(
        name=normalize(req.name),
        raw_name=req.name,
        specifier=str(req.specifier),
        source=source,
        line=line,
        extras=sorted(req.extras),
        marker=str(req.marker) if req.marker else None,
        group=group,
    )


def parse_requirements(text: str, source: str) -> list[Dependency]:
    deps: list[Dependency] = []
    continued = ""
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = (continued + raw).strip()
        continued = ""
        if line.endswith("\\"):
            continued = line[:-1] + " "
            continue
        if not line or line.startswith("#") or line.startswith("-"):
            continue  # comments and pip options (-r, -e, -c, --index-url ...)
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if "://" in line or line.startswith((".", "/")):
            continue  # URLs and local paths are not registry dependencies
        dep = _from_requirement(line, source, lineno)
        if dep is not None:
            deps.append(dep)
    return deps


def _poetry_specifier(version: str) -> str:
    value = version.strip()
    if not value or value == "*":
        return ""
    return f"=={value}" if value[0].isdigit() else value


def parse_pyproject(text: str, source: str) -> list[Dependency]:
    data = tomllib.loads(text)
    deps: list[Dependency] = []

    project = data.get("project") or {}
    for item in project.get("dependencies") or []:
        dep = _from_requirement(str(item), source)
        if dep is not None:
            deps.append(dep)
    for group, items in (project.get("optional-dependencies") or {}).items():
        for item in items or []:
            dep = _from_requirement(str(item), source, group=str(group))
            if dep is not None:
                deps.append(dep)

    poetry = (data.get("tool") or {}).get("poetry") or {}
    tables: list[tuple[str, dict]] = [("main", poetry.get("dependencies") or {})]
    for group, cfg in (poetry.get("group") or {}).items():
        tables.append((str(group), (cfg or {}).get("dependencies") or {}))
    for group, table in tables:
        for name, spec in table.items():
            if normalize(name) == "python":
                continue
            if isinstance(spec, dict):
                version = str(spec.get("version", ""))
                extras = [str(e) for e in spec.get("extras") or []]
            else:
                version, extras = str(spec), []
            deps.append(
                Dependency(
                    name=normalize(name),
                    raw_name=str(name),
                    specifier=_poetry_specifier(version),
                    source=source,
                    extras=sorted(extras),
                    group=group,
                )
            )
    return deps


def discover(workspace: Path) -> list[Manifest]:
    manifests: list[Manifest] = []
    for path in sorted(workspace.rglob("*")):
        rel_parts = path.relative_to(workspace).parts
        if skipped(rel_parts) or not path.is_file():
            continue
        rel = "/".join(rel_parts)
        try:
            if path.name == "pyproject.toml":
                text = path.read_text(encoding="utf-8")
                manifests.append(Manifest(rel, "pyproject", parse_pyproject(text, rel)))
            elif REQUIREMENTS_NAME.fullmatch(path.name) or (
                path.parent.name == "requirements" and path.suffix == ".txt"
            ):
                text = path.read_text(encoding="utf-8", errors="replace")
                manifests.append(Manifest(rel, "requirements", parse_requirements(text, rel)))
        except (OSError, ValueError) as exc:
            log.warning("could not parse %s: %s", rel, exc)
    return manifests


def iter_python_files(workspace: Path) -> list[Path]:
    files = []
    for path in sorted(workspace.rglob("*.py")):
        if not skipped(path.relative_to(workspace).parts) and path.is_file():
            files.append(path)
    return files
