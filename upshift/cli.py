"""Developer CLI: `python -m upshift scout <path|url>` and `python -m upshift sandbox-check`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from upshift.agents.historian import Historian
from upshift.agents.scout import Scout
from upshift.config import get_settings
from upshift.core.budget import Budget
from upshift.core.workspace import prepare_workspace
from upshift.llm.client import TokenFactoryClient
from upshift.llm.models import ModelCatalog
from upshift.llm.router import ModelRouter
from upshift.sandbox.base import ExecutionRequest
from upshift.sandbox.docker import DockerExecutor
from upshift.tools.cache import JsonCache
from upshift.tools.pypi import PyPIClient
from upshift.tools.tavily_extract import TavilyExtractTool
from upshift.tools.tavily_search import TavilySearchTool


async def _scout(source: str, as_json: bool) -> int:
    settings = get_settings()
    workspace = await prepare_workspace(source, settings.data_dir / "workspaces")
    pypi = PyPIClient(cache=JsonCache(settings.cache_dir / "pypi", ttl_hours=24))
    try:
        report = await Scout(pypi).assess(workspace.path)
    finally:
        await pypi.aclose()
        workspace.cleanup()

    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    manifests = ", ".join(report.manifests) or "none"
    print(f"manifests: {manifests}   python files: {report.python_files}")
    header = (
        f"{'package':<24}{'current':<12}{'latest':<12}{'bump':<8}"
        f"{'syms':>5}{'files':>6}{'risk':>6}  note"
    )
    print(header)
    print("-" * len(header))
    for d in report.dependencies:
        print(
            f"{d.name:<24}{d.current or '-':<12}{d.latest or '-':<12}{d.bump:<8}"
            f"{len(d.usage.get('symbols', {})):>5}{len(d.usage.get('files', [])):>6}"
            f"{d.risk:>6.2f}  {d.skip_reason or ''}"
        )
    return 0


async def _research(source: str, package: str, as_json: bool) -> int:
    settings = get_settings()
    if not settings.nebius_api_key or not settings.tavily_api_key:
        print("NEBIUS_API_KEY and TAVILY_API_KEY must be set in .env", file=sys.stderr)
        return 2
    workspace = await prepare_workspace(source, settings.data_dir / "workspaces")
    pypi = PyPIClient(cache=JsonCache(settings.cache_dir / "pypi", ttl_hours=24))
    try:
        report = await Scout(pypi).assess(workspace.path)
    finally:
        await pypi.aclose()
        workspace.cleanup()
    dep = next((d for d in report.dependencies if d.name == package.lower()), None)
    if dep is None:
        print(f"{package} is not a dependency of {source}", file=sys.stderr)
        return 1

    catalog = ModelCatalog.load(settings.models_file, settings.capabilities_file)
    llm = TokenFactoryClient(
        settings.nebius_api_key, settings.nebius_base_url,
        timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries,
    )
    tavily_cache = JsonCache(
        settings.cache_dir / "tavily", ttl_hours=settings.tavily_cache_ttl_hours
    )
    historian = Historian(
        llm, ModelRouter(catalog),
        TavilySearchTool(settings.tavily_api_key, cache=tavily_cache),
        TavilyExtractTool(settings.tavily_api_key, cache=tavily_cache),
    )
    dossier = await historian.research(dep, budget=Budget(settings.default_budget_usd))

    if as_json:
        print(json.dumps(dossier.to_dict(), indent=2))
        return 0
    print(f"{dossier.package}: {dossier.current} -> {dossier.target}")
    print(f"model: {dossier.model}   cost: ${dossier.cost_usd:.4f}")
    for s in dossier.sources:
        state = f"{s.chars} chars" if s.fetched else f"FAILED ({s.error})"
        print(f"  [{s.via}] {s.url}  {state}")
    if dossier.error:
        print(f"error: {dossier.error}")
    print(f"\nsummary: {dossier.summary}")
    if dossier.requires_new_dependencies:
        print(f"new dependencies: {', '.join(dossier.requires_new_dependencies)}")
    print(f"\n{len(dossier.relevant)} relevant of {len(dossier.entries)} entries\n")
    for e in dossier.entries:
        flag = "*" if e.relevant else " "
        matched = f"  <- {', '.join(e.matched_symbols)}" if e.matched_symbols else ""
        print(f"{flag} [{e.category:<11}] {e.title}{matched}")
        if e.relevant and e.migration:
            print(f"      fix: {e.migration}")
    return 0


async def _sandbox_check(image: str) -> int:
    executor = DockerExecutor()
    if not await executor.available():
        print("Docker is not available (is Docker Desktop running?)", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="upshift-check-") as tmp:
        Path(tmp, "hello.py").write_text(
            "import json, platform, sys\n"
            "payload = {'python': platform.python_version(), 'ok': True}\n"
            "json.dump(payload, open('result.json', 'w'))\n"
            "print('hello from the sandbox', sys.version)\n",
            encoding="utf-8",
        )
        result = await executor.run(
            ExecutionRequest(
                workspace=Path(tmp), command="python hello.py", image=image,
                timeout_seconds=120, collect=["result.json"], network=False,
            )
        )
    print(f"exit={result.exit_code} timed_out={result.timed_out} {result.duration_ms} ms")
    print(result.tail(2000).strip())
    print("artifacts:", result.artifacts)
    return 0 if result.ok and "result.json" in result.artifacts else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upshift")
    sub = parser.add_subparsers(dest="command", required=True)

    scout = sub.add_parser("scout", help="analyse a repository's dependencies")
    scout.add_argument("source", help="local path or git URL")
    scout.add_argument("--json", action="store_true", help="print the full report as JSON")

    research = sub.add_parser("research", help="build the Impact Dossier for one dependency")
    research.add_argument("source", help="local path or git URL")
    research.add_argument("package", help="dependency name, e.g. pydantic")
    research.add_argument("--json", action="store_true")

    check = sub.add_parser("sandbox-check", help="run a trivial job in the Docker sandbox")
    check.add_argument("--image", default="python:3.12-slim")

    args = parser.parse_args(argv)
    if args.command == "scout":
        return asyncio.run(_scout(args.source, args.json))
    if args.command == "research":
        return asyncio.run(_research(args.source, args.package, args.json))
    return asyncio.run(_sandbox_check(args.image))
