"""AST scan: which symbols of each dependency does this codebase actually use, and where.

This is what lets the Historian ignore 95% of a changelog: a breaking change in a symbol the
repository never imports is noise.
"""

from __future__ import annotations

import ast
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .python import iter_python_files, normalize

log = logging.getLogger(__name__)

# Distribution name -> importable top-level names, where they differ.
IMPORT_NAME_OVERRIDES: dict[str, set[str]] = {
    "pillow": {"PIL"},
    "beautifulsoup4": {"bs4"},
    "pyyaml": {"yaml"},
    "scikit-learn": {"sklearn"},
    "python-dateutil": {"dateutil"},
    "psycopg2-binary": {"psycopg2"},
    "attrs": {"attr", "attrs"},
    "opencv-python": {"cv2"},
    "msgpack-python": {"msgpack"},
    "python-dotenv": {"dotenv"},
    "pyjwt": {"jwt"},
    "pycryptodome": {"Crypto"},
    "protobuf": {"google.protobuf"},
    "setuptools": {"setuptools", "pkg_resources"},
    "typing-extensions": {"typing_extensions"},
    "python-multipart": {"multipart"},
    "djangorestframework": {"rest_framework"},
    "gitpython": {"git"},
    "pyserial": {"serial"},
    "pymongo": {"pymongo", "bson"},
    "google-cloud-storage": {"google.cloud.storage"},
    "markdown": {"markdown"},
}


def import_names_for(dist_name: str) -> set[str]:
    norm = normalize(dist_name)
    if norm in IMPORT_NAME_OVERRIDES:
        return set(IMPORT_NAME_OVERRIDES[norm])
    return {norm.replace("-", "_")}


@dataclass
class PackageUsage:
    package: str
    files: set[str] = field(default_factory=set)
    symbols: Counter[str] = field(default_factory=Counter)
    imports: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": sorted(self.files),
            "symbols": dict(self.symbols.most_common()),
            "imports": self.imports[:50],
        }


class _FileVisitor(ast.NodeVisitor):
    def __init__(self, rel: str, lookup: dict[str, str], usages: dict[str, PackageUsage]):
        self.rel = rel
        self.lookup = lookup
        self.usages = usages
        self.aliases: dict[str, str] = {}  # local name -> qualified module or symbol

    def _package_for(self, qualified: str) -> str | None:
        parts = qualified.split(".")
        for i in range(len(parts), 0, -1):
            package = self.lookup.get(".".join(parts[:i]))
            if package:
                return package
        return None

    def _hit(self, package: str, symbol: str) -> None:
        usage = self.usages[package]
        usage.files.add(self.rel)
        usage.symbols[symbol] += 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            package = self._package_for(alias.name)
            if package:
                self._hit(package, alias.name)
                local = alias.asname or alias.name.split(".")[0]
                self.aliases[local] = alias.name if alias.asname else alias.name.split(".")[0]
                self.usages[package].imports.append(
                    {"file": self.rel, "line": node.lineno, "import": alias.name}
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            return
        package = self._package_for(node.module)
        if not package:
            return
        for alias in node.names:
            qualified = f"{node.module}.{alias.name}"
            self._hit(package, qualified)
            self.aliases[alias.asname or alias.name] = qualified
            self.usages[package].imports.append(
                {"file": self.rel, "line": node.lineno, "import": qualified}
            )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        base = node.value
        if isinstance(base, ast.Name) and base.id in self.aliases:
            qualified = self.aliases[base.id]
            package = self._package_for(qualified)
            if package:
                self._hit(package, f"{qualified}.{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.aliases:
            qualified = self.aliases[node.id]
            package = self._package_for(qualified)
            if package:
                self._hit(package, qualified)


class UsageScanner:
    def __init__(self) -> None:
        self.files_scanned = 0

    def scan(self, workspace: Path, packages: dict[str, set[str]]) -> dict[str, PackageUsage]:
        lookup = {imp: pkg for pkg, imps in packages.items() for imp in imps}
        usages = {pkg: PackageUsage(pkg) for pkg in packages}
        self.files_scanned = 0
        for path in iter_python_files(workspace):
            rel = path.relative_to(workspace).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
            except (SyntaxError, ValueError):
                log.debug("skipping unparsable file %s", rel)
                continue
            self.files_scanned += 1
            _FileVisitor(rel, lookup, usages).visit(tree)
        return usages
