from __future__ import annotations

from pathlib import Path

from upshift.ecosystems.python import discover, normalize, parse_pyproject, parse_requirements
from upshift.ecosystems.usage import UsageScanner, import_names_for

REQUIREMENTS = """
# core
pydantic==1.10.13
SQLAlchemy==1.4.49  # database
requests>=2.28,<3
uvicorn[standard]==0.30.0 ; python_version >= "3.8"
-r other.txt
-e .
git+https://github.com/example/pkg.git#egg=pkg
./local/path
not a valid requirement !!!
httpx \\
    ==0.27.0
"""

PYPROJECT = """
[project]
name = "demo"
dependencies = ["fastapi>=0.100", "pydantic==2.0.0"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.poetry.dependencies]
python = "^3.11"
rich = "13.7.0"
click = "^8.1"
numpy = { version = "1.26.4", extras = ["all"] }

[tool.poetry.group.dev.dependencies]
black = "*"
"""


def test_normalize() -> None:
    assert normalize("Flask_SQLAlchemy") == "flask-sqlalchemy"
    assert normalize("zope.interface") == "zope-interface"


def test_parse_requirements() -> None:
    deps = {d.name: d for d in parse_requirements(REQUIREMENTS, "requirements.txt")}
    assert set(deps) == {"pydantic", "sqlalchemy", "requests", "uvicorn", "httpx"}
    assert deps["pydantic"].pinned == "1.10.13" and deps["pydantic"].line == 3
    assert deps["sqlalchemy"].raw_name == "SQLAlchemy" and deps["sqlalchemy"].pinned == "1.4.49"
    assert deps["requests"].pinned is None and deps["requests"].specifier == "<3,>=2.28"
    assert deps["uvicorn"].extras == ["standard"] and deps["uvicorn"].marker
    assert deps["httpx"].pinned == "0.27.0"


def test_parse_pyproject() -> None:
    deps = {(d.name, d.group): d for d in parse_pyproject(PYPROJECT, "pyproject.toml")}
    assert deps[("fastapi", "main")].pinned is None
    assert deps[("pydantic", "main")].pinned == "2.0.0"
    assert deps[("pytest", "dev")].specifier == ">=8"
    assert deps[("rich", "main")].pinned == "13.7.0"
    assert deps[("click", "main")].specifier == "^8.1"
    assert deps[("numpy", "main")].extras == ["all"]
    assert deps[("black", "dev")].specifier == ""
    assert ("python", "main") not in deps


def test_discover_skips_virtualenvs(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pydantic==1.10.13\n")
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "dev.txt").write_text("pytest==8.0.0\n")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "requirements.txt").write_text("junk==1\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["rich"]\n')

    manifests = discover(tmp_path)
    assert sorted(m.path for m in manifests) == [
        "pyproject.toml", "requirements.txt", "requirements/dev.txt",
    ]


def test_import_name_overrides() -> None:
    assert import_names_for("Pillow") == {"PIL"}
    assert import_names_for("python-dateutil") == {"dateutil"}
    assert import_names_for("typing_extensions") == {"typing_extensions"}
    assert import_names_for("my-fancy-lib") == {"my_fancy_lib"}


def test_usage_scanner_tracks_symbols(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text(
        "import pydantic\n"
        "from pydantic import BaseModel, validator\n"
        "from sqlalchemy.orm import Session\n"
        "import dateutil.parser\n"
        "import os\n\n"
        "class User(BaseModel):\n"
        "    name: str\n\n"
        "    @validator('name')\n"
        "    def check(cls, v):\n"
        "        return v\n\n"
        "def build():\n"
        "    return pydantic.Field(default=1), dateutil.parser.parse('2024-01-01')\n"
    )
    (tmp_path / "broken.py").write_text("def (:\n")

    scanner = UsageScanner()
    usages = scanner.scan(
        tmp_path,
        {
            "pydantic": {"pydantic"},
            "sqlalchemy": {"sqlalchemy"},
            "python-dateutil": {"dateutil"},
            "requests": {"requests"},
        },
    )
    assert scanner.files_scanned == 1
    pyd = usages["pydantic"]
    assert pyd.files == {"app/models.py"}
    assert {"pydantic.BaseModel", "pydantic.validator", "pydantic.Field"} <= set(pyd.symbols)
    assert pyd.symbols["pydantic.BaseModel"] >= 2  # import + base class reference
    assert set(usages["sqlalchemy"].symbols) == {"sqlalchemy.orm.Session"}
    assert "dateutil.parser" in usages["python-dateutil"].symbols
    assert usages["requests"].files == set() and not usages["requests"].symbols
