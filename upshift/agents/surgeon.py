"""Surgeon: bump the pin, run the tests, rewrite the code that breaks, prove it, repeat.

Safety properties:
- Edits are restricted to an allowlist: files that import the package, files named in the
  failing tests' tracebacks, and manifests. Anything else is rejected and recorded.
- Every attempt is verified in the sandbox. If an attempt makes things worse, or changes
  nothing about which tests fail, the files are rolled back before the next attempt.
- Attempts escalate through the router (Super, Super, Ultra) and stop at the budget cap.
- The model answers in a plain-text SEARCH/REPLACE block protocol rather than JSON: code
  inside JSON strings gets double-escaped by models far too often. Each block carries a
  justification and the evidence URL it rests on, which is what makes the MR auditable.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from upshift.agents.historian import ImpactDossier
from upshift.agents.scout import DependencyAssessment
from upshift.agents.verifier import VerificationResult, Verifier
from upshift.core.budget import Budget
from upshift.core.bus import EventBus
from upshift.core.events import Event, EventType
from upshift.ecosystems.python import add_requirement, bump_pin, normalize, parse_requirements
from upshift.ecosystems.usage import import_names_for
from upshift.llm.client import TokenFactoryClient, content_of
from upshift.llm.router import BudgetExhausted, ModelRouter, NoCapableModel, TaskClass
from upshift.llm.structured import complete_json, parse_json
from upshift.tools.pypi import PyPIClient
from upshift.tools.tavily_search import TavilySearchTool

log = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 120_000
MAX_OUTPUT_TOKENS = 24_000
DEEP_OUTPUT_TOKENS = 32_000  # reasoning tokens count against the budget
MAX_FAILURES_SHOWN = 12
NOOP_LIMIT = 4  # consecutive attempts that applied nothing before the upgrade is abandoned
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
WARNING_CONFIGS = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")


def error_line(message: str) -> str:
    """First `E ...` line of a pytest failure, cleaned for use in a search query."""
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    line = next((ln for ln in lines if ln.startswith("E ")), lines[0] if lines else "")
    line = re.sub(r"^E\s+", "", line)
    line = re.sub(r"\S*[/\\]\S+\.py:\d+", "", line)  # drop file paths
    line = re.sub(r"<class '([\w.]+)'>", r"\1", line)
    return re.sub(r"\s+", " ", line).strip()


CONFLICT_PATTERN = re.compile(
    r"depends on ([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:[<>=!~][^\s,;]*)?"
)
CANNOT_INSTALL_PATTERN = re.compile(
    r"Cannot install (.+?) because these package versions", re.IGNORECASE
)
LINE_REF_PATTERN = re.compile(r"-r\s+(\S+)\s+\(line\s+(\d+)\)")
PACKAGE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def conflicting_pins(log: str, manifest_text: str, manifest: str, upgraded: str) -> list[str]:
    """Pinned packages that pip's resolver names as incompatible with the upgraded one.

    Two shapes are understood. With `-q` pip prints only the summary line
    `Cannot install -r requirements.txt (line 1) and click==8.0.4 because ...`, where a
    `(line N)` reference points at a line of the requirements file. In verbose mode it adds
    `flask 3.1.2 depends on Werkzeug>=3.1` lines under "The conflict is caused by". Only
    packages constrained in the same requirements file (exact pins or ranges) are returned,
    in order of first mention; anything unconstrained pip can already resolve by itself.
    """
    if not manifest.endswith(".txt"):
        return []
    deps = parse_requirements(manifest_text, manifest)
    # any version constraint counts: pip refuses `Werkzeug<3.0` next to Flask 3 exactly as
    # it refuses `Werkzeug==2.3.7`, and both are ours to rewrite
    pinned = {d.name for d in deps if d.specifier}
    by_line = {d.line: d.name for d in deps if d.specifier and d.line is not None}
    candidates: list[str] = []
    for match in CANNOT_INSTALL_PATTERN.finditer(log):
        for token in re.split(r",\s*|\s+and\s+", match.group(1)):
            token = token.strip()
            ref = LINE_REF_PATTERN.match(token)
            if ref:
                same_file = PurePosixPath(ref.group(1)).name == PurePosixPath(manifest).name
                if same_file and int(ref.group(2)) in by_line:
                    candidates.append(by_line[int(ref.group(2))])
                continue
            name = PACKAGE_TOKEN.match(token)
            if name:
                candidates.append(normalize(name.group(0)))
    candidates += [normalize(m.group(1)) for m in CONFLICT_PATTERN.finditer(log)]
    found: list[str] = []
    for name in candidates:
        if name != normalize(upgraded) and name in pinned and name not in found:
            found.append(name)
    return found


IMPORT_ERROR_PATTERN = re.compile(r"cannot import name '\w+' from '([\w.]+)'")


def modules_from_import_errors(verification: VerificationResult) -> list[str]:
    """Modules named in `cannot import name X from M` errors, so their installed public
    API can be introspected and shown to the model (M is often not the upgraded package:
    Flask drags Werkzeug along)."""
    modules: list[str] = []
    for failure in verification.failures:
        for match in IMPORT_ERROR_PATTERN.finditer(failure.message):
            if match.group(1) not in modules:
                modules.append(match.group(1))
    return modules


def is_collection_error(verification: VerificationResult) -> bool:
    """True when nothing ran because a module failed to import (or pytest died at startup).

    pytest reports one `collection failure` error per module that failed to import; a
    runtime error inside a test (fixture setup, an exception in the body) is not that.
    """
    if not verification.failures or verification.passed != 0:
        return False
    return all(
        f.kind == "error" and (
            f.test == "pytest::startup"
            or f.message.startswith("collection failure")
            or "in <module>" in f.message
        )
        for f in verification.failures
    )


def tests_executed(verification: VerificationResult) -> int:
    """Tests that actually ran. In a collection-error state the JUnit "tests" are per-module
    error entries, not executions, so they count as zero."""
    return 0 if is_collection_error(verification) else verification.tests


def warnings_are_errors(workspace: Path) -> str | None:
    """Return the config file that promotes warnings to errors, if any."""
    for name in WARNING_CONFIGS:
        path = workspace / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "filterwarnings" in text and re.search(r"\berror\b", text):
            return name
    return None
SHRINK_LIMIT = 0.3  # a rewrite keeping <30% of a >20-line file is almost always truncation


def retry_notice(previous: Any, number: int, max_tokens: int) -> str:
    """Appended to the prompt after an attempt that produced nothing usable.

    Re-sending an identical prompt at temperature 0 reproduces the identical failure; the
    notice changes the input, names the failure mode, and bounds the response.
    """
    what = (getattr(previous, "analysis", "") or "")[:200].replace("\n", " ")
    return (
        f"\n\nRETRY NOTICE (attempt {number}): your previous response produced no usable "
        f"edits. It began: {what!r}. It was cut off, repeated itself, or ignored the format. "
        f"This time keep the entire response well under {max_tokens} tokens: ANALYSIS in at "
        "most three sentences, then the fewest EDIT blocks that fix the failures, or exactly "
        "one WRITE block per file that needs most of its content changed. Never repeat a "
        "line or an import. Stop immediately after the last block."
    )

FORMAT_SPEC = """Respond in exactly this plain-text format. No JSON, no markdown fences.

ANALYSIS:
<a few sentences: what broke and why>

NEW_DEPENDENCIES: <comma-separated package names to add to the manifest, or none>

Then one block per edit. To change part of a file:

### EDIT <relative/path.py>
<<<<<<< SEARCH
<existing lines copied verbatim, enough to be unique in the file>
=======
<replacement lines>
>>>>>>> REPLACE
JUSTIFICATION: <one sentence>
EVIDENCE: <url from the dossier, or none>

To rewrite most of a file or create a new one:

### WRITE <relative/path.py>
<<<<<<< FILE
<entire new file content>
>>>>>>> END
JUSTIFICATION: <one sentence>
EVIDENCE: <url from the dossier, or none>
"""

SYSTEM_PROMPT = (
    "You are the Surgeon in Upshift, a dependency-upgrade agent. One package has just been "
    "upgraded in a Python project and the test suite is red. You receive the evidence-backed "
    "list of changes in the new version (the Impact Dossier), the failing tests, and the full "
    "contents of the files you are allowed to edit. Produce the minimal, correct edits that "
    "make the suite pass on the NEW version while preserving the project's behaviour.\n"
    "Rules: use the modern API the migration guide recommends, not compatibility shims. Never "
    "pin the package back. Never delete tests or weaken them: a test's inputs and the call "
    "under test must stay exactly as they are. The ONLY permitted change to a test is its "
    "expected outcome (for example the exception type) when the library's documented "
    "behaviour changed, and the justification must cite that change. If a test fails because "
    "the application code no longer enforces something (a constant field, a constraint), fix "
    "the application code so the test passes as written. Fix every failure you can see, "
    "not just the first. Prefer EDIT blocks; use WRITE only when most of a file changes, and "
    "then include the complete file. Write real newlines, never the characters backslash-n. "
    "Do not edit version pins in requirements or pyproject files; to add a package, list it "
    "under NEW_DEPENDENCIES and it will be pinned for you.\n\n"
    + FORMAT_SPEC
)

RESEARCH_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hypothesis", "queries"],
    "additionalProperties": False,
}

RESEARCH_PLANNER_PROMPT = (
    "You plan web research for an agent that is stuck fixing a test suite after a dependency "
    "upgrade. From the failing tests (ids, assertion or exception text, captured output), the "
    "relevant code, and what the last attempt changed, state in one sentence the most likely "
    "root cause in terms of the LIBRARY's behaviour change (a removed or renamed API, a "
    "config key that no longer exists, a changed default), then write up to three web search "
    "queries that would find the documentation or changelog entry explaining it. Name the "
    "library, the major version and the specific API or config key. Never paste assertion "
    "values or file paths into a query."
)

BLOCK_HEADER = re.compile(r"^###\s+(EDIT|WRITE)\s+(\S+)\s*$")
FENCE = re.compile(r"^```[\w+-]*\s*$")


def unescape_literal_newlines(text: str) -> str:
    """Repair content the model emitted with literal backslash-n instead of newlines.

    Only fires when the body is a single line holding two or more literal sequences, which
    is the double-escaping signature and essentially never legitimate file content.
    """
    body = text.rstrip("\n")
    if "\n" in body or body.count("\\n") < 2:
        return text
    repaired = body.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return repaired + ("\n" if text.endswith("\n") else "")


def _strip_fences(lines: list[str]) -> list[str]:
    while lines and FENCE.match(lines[0]):
        lines = lines[1:]
    while lines and FENCE.match(lines[-1]):
        lines = lines[:-1]
    return lines


def parse_edit_blocks(text: str) -> dict[str, Any]:
    """Parse the SEARCH/REPLACE protocol into the edit dicts the applier understands.

    Falls back to JSON of the same shape if the model ignored the format.
    """
    text = THINK_BLOCK.sub("", text)
    lines = text.replace("\r\n", "\n").split("\n")
    analysis: list[str] = []
    deps: list[str] = []
    edits: list[dict[str, Any]] = []
    in_analysis = False
    i = 0
    while i < len(lines):
        line = lines[i]
        header = BLOCK_HEADER.match(line.strip())
        if header:
            in_analysis = False
            kind, path = header.group(1), header.group(2)
            i += 1
            if i < len(lines) and lines[i].startswith("<<<<<<<"):
                i += 1
            first: list[str] = []
            second: list[str] = []
            in_second = False
            while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                if kind == "EDIT" and not in_second and lines[i].startswith("======="):
                    in_second = True
                else:
                    (second if in_second else first).append(lines[i])
                i += 1
            if i >= len(lines):
                break  # unterminated block (truncated output): discard it
            i += 1  # closing marker
            justification = evidence = ""
            while i < len(lines):
                probe = lines[i].strip()
                if probe.startswith("JUSTIFICATION:"):
                    justification = probe.split(":", 1)[1].strip()
                elif probe.startswith("EVIDENCE:"):
                    evidence = probe.split(":", 1)[1].strip()
                elif probe:
                    break
                i += 1
            first, second = _strip_fences(first), _strip_fences(second)
            if evidence.lower() == "none":
                evidence = ""
            if kind == "EDIT":
                edits.append({
                    "path": path, "op": "replace_block",
                    "search": "\n".join(first) + "\n", "replace": "\n".join(second) + "\n",
                    "justification": justification, "evidence_url": evidence,
                })
            else:
                edits.append({
                    "path": path, "op": "write_file", "search": "",
                    "replace": "\n".join(first) + "\n",
                    "justification": justification, "evidence_url": evidence,
                })
            continue
        stripped = line.strip()
        if stripped.startswith("ANALYSIS:"):
            in_analysis = True
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                analysis.append(rest)
        elif stripped.startswith("NEW_DEPENDENCIES:"):
            in_analysis = False
            value = stripped.split(":", 1)[1].strip()
            if value and value.lower() != "none":
                deps = [d.strip().strip("`") for d in value.split(",") if d.strip()]
        elif in_analysis:
            analysis.append(line)
        i += 1

    if not edits and text.lstrip().startswith("{"):
        try:
            data = parse_json(text)
            if isinstance(data, dict):
                return {
                    "analysis": str(data.get("analysis", "")),
                    "edits": [e for e in data.get("edits", []) or [] if isinstance(e, dict)],
                    "new_dependencies": [str(d) for d in data.get("new_dependencies", []) or []],
                }
        except ValueError:
            pass
    return {"analysis": "\n".join(analysis).strip(), "edits": edits, "new_dependencies": deps}


@dataclass
class AppliedEdit:
    path: str
    op: str
    justification: str
    evidence_url: str
    status: str  # applied | rejected
    reason: str = ""


@dataclass
class Attempt:
    number: int
    model: str
    analysis: str
    edits: list[AppliedEdit]
    verification: dict[str, Any] | None
    cost_usd: float
    rolled_back: bool = False
    rollback_reason: str = ""


@dataclass
class SurgeryResult:
    package: str
    current: str | None
    target: str | None
    success: bool
    manifest_changes: list[str]
    attempts: list[Attempt] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    diff: str = ""
    after_bump: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    cost_usd: float = 0.0
    error: str | None = None
    # public API of the upgraded package as actually installed (module -> version, names)
    api_surface: dict[str, Any] = field(default_factory=dict)
    # original contents of every touched file, kept so later passes can extend the diff
    originals: dict[str, str | None] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("originals", None)
        data["api_surface"] = {
            m: {"version": i.get("version"), "name_count": len(i.get("names", []))}
            for m, i in self.api_surface.items()
        }
        return data


def api_surface_text(api_surface: dict[str, Any], limit: int = 400) -> list[str]:
    """Prompt lines describing the installed package API, or [] when unknown."""
    if not api_surface:
        return []
    lines = [
        "",
        "Installed public API of the upgraded package (authoritative, from the sandbox). "
        "Import ONLY these top-level names; never guess submodule paths:",
    ]
    for module, info in api_surface.items():
        names = info.get("names") or []
        if names:
            version = info.get("version") or ""
            lines.append(f"{module} {version}: {', '.join(names[:limit])}")
        else:
            lines.append(f"{module}: {info.get('error', 'not importable')}")
    return lines


def _outcome(edit: dict[str, Any], path: str, status: str, reason: str = "") -> AppliedEdit:
    return AppliedEdit(
        path, str(edit.get("op", "")), str(edit.get("justification", "")),
        str(edit.get("evidence_url", "")), status, reason,
    )


def _clean_path(raw: str) -> str | None:
    path = raw.strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if not path or ".." in PurePosixPath(path).parts:
        return None
    return path


def _read(workspace: Path, rel: str) -> str | None:
    target = workspace / rel
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def _write(workspace: Path, rel: str, content: str) -> None:
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def unified_diff(originals: dict[str, str | None], workspace: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    changed: list[str] = []
    for rel in sorted(originals):
        before = originals[rel] or ""
        after = _read(workspace, rel) or ""
        if before == after:
            continue
        changed.append(rel)
        chunks.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{rel}" if originals[rel] is not None else "/dev/null",
                tofile=f"b/{rel}", n=3,
            )
        )
    return "".join(chunks), changed


def _find_block(content: str, search: str) -> str | None:
    """Return the exact substring of content that the search block denotes, or None."""
    candidates = [search, search.rstrip("\n"), unescape_literal_newlines(search)]
    for candidate in candidates:
        if candidate and content.count(candidate) == 1:
            return candidate
    # tolerate trailing-whitespace drift line by line
    wanted = [ln.rstrip() for ln in search.rstrip("\n").split("\n")]
    lines = content.split("\n")
    hits = [
        i for i in range(len(lines) - len(wanted) + 1)
        if [ln.rstrip() for ln in lines[i : i + len(wanted)]] == wanted
    ]
    if len(hits) == 1:
        start = hits[0]
        return "\n".join(lines[start : start + len(wanted)])
    return None


class Surgeon:
    agent = "surgeon"

    def __init__(
        self,
        llm: TokenFactoryClient,
        router: ModelRouter,
        verifier: Verifier,
        bus: EventBus | None = None,
        pypi: PyPIClient | None = None,
        max_attempts: int = 12,
        search: TavilySearchTool | None = None,
    ) -> None:
        self.llm = llm
        self.router = router
        self.verifier = verifier
        self.bus = bus
        self.pypi = pypi
        self.max_attempts = max_attempts
        self.search = search
        self.research_planner = True  # let a cheap model write the search queries

    # ---- targeted research when stuck -----------------------------------------

    async def plan_queries(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        verification: VerificationResult,
        previous: Attempt | None,
        run_id: str | None,
        budget: Budget | None,
    ) -> tuple[list[str], str]:
        """Ask the classification-tier model what to search for. An assertion like
        `['id', 'price', 'title'] == ['id', 'title', 'price']` says nothing about the library;
        a model reading the test and the code can name the config key behind it."""
        lines = [
            f"Package upgraded: {dep.name} {dep.current} -> {dep.latest}", "", "Failing tests:",
        ]
        for failure in verification.failures[:3]:
            lines.append(f"--- {failure.test}\n{failure.message[:1_500]}")
        if previous is not None:
            lines.append(f"\nLast attempt's reasoning: {previous.analysis[:800]}")
            for edit in previous.edits:
                if edit.status == "applied":
                    lines.append(f"- edited {edit.path}: {edit.justification}")
        for rel in verification.referenced_files()[:2]:
            text = _read(workspace, rel)
            if text:
                lines.append(f"\n=== {rel} (current) ===\n{text[:3_000]}")
        try:
            decision = self.router.route(
                TaskClass.CLASSIFY, required_caps={"json_schema"}, budget=budget
            )
            data = await complete_json(
                self.llm, decision.model,
                [{"role": "system", "content": RESEARCH_PLANNER_PROMPT},
                 {"role": "user", "content": "\n".join(lines)}],
                schema_name="research_plan", schema=RESEARCH_PLAN_SCHEMA, max_tokens=600,
                agent=self.agent, run_id=run_id, budget=budget,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to templated queries
            log.warning("surgeon: research planner failed: %s", exc)
            return [], ""
        queries = [str(q).strip() for q in data.get("queries") or [] if str(q).strip()][:3]
        return queries, str(data.get("hypothesis", "")).strip()[:500]

    async def targeted_research(
        self,
        dep: DependencyAssessment,
        verification: VerificationResult,
        previous: Attempt | None,
        run_id: str | None,
        workspace: Path | None = None,
        budget: Budget | None = None,
    ) -> list[str]:
        """Search the web for the specific errors blocking progress (after a stalled attempt)."""
        if self.search is None:
            return []
        major = (dep.latest or "").split(".")[0]
        seeds: list[tuple[str, str]] = []
        if previous is not None and previous.verification:
            for failure in previous.verification.get("failures", [])[:1]:
                seeds.append((str(failure.get("test", "")), str(failure.get("message", ""))))
        for failure in verification.failures[:2]:
            seeds.append((failure.test, failure.message))
        queries: list[str] = []
        for test, message in seeds:
            error = error_line(message)[:110]
            name = test.rsplit("::", 1)[-1].removeprefix("test_").replace("_", " ")
            query = f"{dep.name} {major} {error}" if error else f"{dep.name} {major} {name}"
            if query not in queries:
                queries.append(query)
        notes: list[str] = []
        if self.research_planner and workspace is not None:
            planned, hypothesis = await self.plan_queries(
                workspace, dep, verification, previous, run_id, budget
            )
            if planned:
                queries = planned
            if hypothesis:
                notes.append(f"- Research planner hypothesis: {hypothesis}")
        for query in queries[:3]:
            result = await self.search.search(query, max_results=3)
            await self._emit(
                run_id, EventType.TOOL_CALL, f"dep:{dep.name}", tool="web_search",
                query=query, results=len(result.get("results", [])),
                cached=result.get("cached", False), error=result.get("error"),
            )
            for item in result.get("results", [])[:3]:
                snippet = str(item.get("content", "")).replace("\n", " ")[:500]
                notes.append(f"- {item.get('title', '')} ({item.get('url', '')}): {snippet}")
        return notes

    async def _nudge_for_blocks(
        self,
        spec: Any,
        prompt: str,
        text: str,
        temperature: float,
        max_tokens: int,
        run_id: str | None,
        budget: Budget | None,
        number: int,
    ) -> tuple[dict[str, Any], str]:
        """One follow-up turn when the model analysed but emitted no edit blocks."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": text[:12_000]},
            {"role": "user", "content": (
                "Your analysis is noted. Now output ONLY the ### EDIT / ### WRITE blocks that "
                "implement it, in the exact format from the instructions. No further analysis, "
                "no markdown fences."
            )},
        ]
        fallback = parse_edit_blocks(text)
        try:
            response = await self.llm.complete(
                spec, messages, temperature=temperature, max_tokens=max_tokens,
                agent=self.agent, run_id=run_id, budget=budget, attempt=number,
            )
        except Exception as exc:  # noqa: BLE001 - the attempt simply stays block-less
            log.warning("surgeon: nudge for blocks failed: %s", exc)
            return fallback, text
        follow = content_of(response)
        finish = response.choices[0].finish_reason if response.choices else None
        if finish == "length":
            log.warning("surgeon: nudge output truncated (%d chars); discarded", len(follow))
            return fallback, text
        data = parse_edit_blocks(follow)
        if data["edits"] and not data["analysis"]:
            # blocks came back without an ANALYSIS header: keep the first turn's reasoning
            data["analysis"] = fallback["analysis"] or text[:600]
        log.info("surgeon: nudge produced %d edit block(s)", len(data["edits"]))
        return data, follow

    async def _emit(
        self, run_id: str | None, type_: EventType, node_id: str | None, **payload: Any
    ) -> None:
        if self.bus is None or run_id is None:
            return
        await self.bus.publish(
            Event(run_id=run_id, type=type_, agent=self.agent, node_id=node_id, payload=payload)
        )

    # ---- manifests ----------------------------------------------------------

    async def _add_dependency(
        self, workspace: Path, manifest: str, name: str, originals: dict[str, str | None]
    ) -> str | None:
        text = _read(workspace, manifest)
        if text is None or not manifest.endswith(".txt"):
            return None
        existing = {d.name for d in parse_requirements(text, manifest)}
        if normalize(name) in existing:
            return None
        requirement = name
        if self.pypi is not None:
            info = await self.pypi.info(name)
            if info and info.latest:
                requirement = f"{name}=={info.latest}"
        originals.setdefault(manifest, text)
        _write(workspace, manifest, add_requirement(text, requirement))
        return f"added {requirement} to {manifest}"

    async def _co_upgrade_conflicts(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        verification: VerificationResult,
        result: SurgeryResult,
        run_id: str | None,
        node: str,
        introspect: list[str],
    ) -> VerificationResult:
        """Upgrading Flask drags Werkzeug and Jinja2 along: when pip refuses the install
        because of other pins in the same file, bump those pins to their latest release and
        try the install once more."""
        assert self.pypi is not None
        text = _read(workspace, dep.source) or ""
        names = conflicting_pins(verification.log_tail, text, dep.source, dep.name)
        if not names:
            return verification
        changes: list[str] = []
        for name in names:
            info = await self.pypi.info(name)
            if not info or not info.latest:
                continue
            text = _read(workspace, dep.source) or ""
            updated, changed = bump_pin(text, name, info.latest, relax_ranges=True)
            if changed:
                _write(workspace, dep.source, updated)
                changes.append(
                    f"{name} co-upgraded to {info.latest} in {dep.source} "
                    f"(pip: incompatible with {dep.name} {dep.latest})"
                )
        if not changes:
            return verification
        result.manifest_changes.extend(changes)
        log.info("surgeon: %s: co-upgrading %s after pip conflict", dep.name, ", ".join(names))
        await self._emit(
            run_id, EventType.PATCH_PROPOSED, node, attempt=0, kind="manifest", changes=changes,
        )
        return await self.verifier.verify(
            workspace, run_id, node,
            label=f"{dep.name}: after co-upgrading {', '.join(names)}", introspect=introspect,
        )

    # ---- context ------------------------------------------------------------

    def allowed_files(
        self, workspace: Path, dep: DependencyAssessment, verification: VerificationResult | None
    ) -> list[str]:
        ordered: list[str] = []
        candidates = list(verification.referenced_files()) if verification else []
        candidates += list(dep.usage.get("files", []))
        candidates.append(dep.source)
        for raw in candidates:
            rel = _clean_path(raw)
            if rel and rel not in ordered and (workspace / rel).is_file():
                ordered.append(rel)
        return ordered

    def build_prompt(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        verification: VerificationResult,
        allowed: list[str],
        previous: Attempt | None,
        research: list[str] | None = None,
        dead_ends: list[str] | None = None,
        stuck: int = 0,
    ) -> str:
        parts = [
            f"Upgrade: {dep.name} {dep.current} -> {dep.latest}",
            f"Dossier summary: {dossier.summary}",
            "",
            "Relevant documented changes (evidence):",
        ]
        for index, entry in enumerate(dossier.relevant, start=1):
            parts.append(
                f"{index}. [{entry.category}] {entry.title}\n"
                f"   migration: {entry.migration}\n"
                f"   quote: {entry.quote}\n"
                f"   url: {entry.evidence_url}"
            )
        if not dossier.relevant:
            parts.append("(none identified; rely on the test failures)")
        others = [e for e in dossier.entries if not e.relevant and e.category != "noise"]
        if others:
            parts.append("\nOther documented changes in this version range (titles only):")
            parts += [f"- [{e.category}] {e.title}: {e.migration}"[:300] for e in others[:25]]
        if dossier.requires_new_dependencies:
            parts.append(
                "\nNew packages already added to the manifest: "
                + ", ".join(dossier.requires_new_dependencies)
            )
        parts += [
            "",
            f"Test run after the version bump: phase={verification.phase} "
            f"exit={verification.exit_code} tests={verification.tests} "
            f"passed={verification.passed} failed={verification.failed} "
            f"errors={verification.errors}",
            "",
            "Failures:",
        ]
        for failure in verification.failures[:MAX_FAILURES_SHOWN]:
            parts.append(f"--- {failure.test} ({failure.kind})\n{failure.message}")
        if verification.failures and verification.passed == 0 and all(
            f.kind == "error" for f in verification.failures
        ):
            parts.append(
                "\nNOTE: these are collection (import-time) errors. The modules fail to import, "
                "so NO tests have run yet and the single error shown hides every other "
                "incompatibility behind it. Patching one line per attempt will not converge. "
                "For each module named in a traceback, produce ONE complete ### WRITE block "
                "with the whole file rewritten for the new version: migrate every import, "
                "base class, decorator, config object, field constraint and method name that "
                "the documented changes above cover, in one pass, so the module imports "
                "cleanly and the tests actually execute."
            )
        if verification.passed > 0 and stuck < 2:
            parts.append(
                f"\nSURGICAL MODE: the suite runs and {verification.passed} tests already pass. "
                f"Only {verification.failure_count} fail. Make the SMALLEST possible EDIT "
                "blocks that address exactly the failing tests: touch only the lines their "
                "tracebacks and assertions implicate. Do NOT use WRITE, do NOT restructure or "
                "'clean up' code, and do NOT change anything a passing test exercises. One "
                "regression costs more than the fix is worth."
            )
        elif verification.passed > 0:
            parts.append(
                f"\nSTUCK: {stuck} consecutive attempts changed code without changing this "
                "failure at all, so the approach itself is wrong, not its details. Stop "
                "patching the previous idea. Re-read the failing test and state exactly what "
                "it observes (which call, which value). Then re-derive the implementation "
                "that produces that observation from the library's documented behaviour, and "
                "rewrite the implicated module in full with ONE WRITE block if that is what "
                "it takes. Keep every currently passing test passing."
            )
        if dead_ends:
            parts.append(
                "\nDEAD ENDS: the errors below have appeared after two or more different "
                "attempts. Whatever approach produced them does not work on this version; do "
                "NOT reproduce it. Pick a different, documented replacement, and make sure the "
                "module imports cleanly before anything else:"
            )
            parts += [f"- {line}" for line in dead_ends]
        config = warnings_are_errors(workspace)
        if config:
            parts.append(
                f"\nNOTE: this project promotes warnings to errors ({config}: filterwarnings). "
                "Deprecated APIs that still 'work' in the new version are FAILURES here: every "
                "deprecated usage listed above must be migrated to its replacement, not left in "
                "place."
            )
        if len(verification.failures) > MAX_FAILURES_SHOWN:
            parts.append(f"... {len(verification.failures) - MAX_FAILURES_SHOWN} more")
        if not verification.failures:
            parts.append(verification.log_tail[-4_000:] or "(no output captured)")
        if previous is not None:
            rejected = [e for e in previous.edits if e.status == "rejected"]
            parts += ["", f"Previous attempt ({previous.model}) analysis: {previous.analysis}"]
            if previous.analysis.startswith("(truncated output"):
                parts.append(
                    "Your previous response exceeded the output limit and was DISCARDED in "
                    "full. Be concise: only the blocks that are needed, each file at most once, "
                    "no repetition, no commentary inside blocks."
                )
            elif not previous.edits:
                parts.append(
                    "Your previous response contained NO parsable edit blocks. Follow the "
                    "### EDIT / ### WRITE format exactly."
                )
            if rejected:
                parts.append("Previous edits that were REJECTED (fix these):")
                parts += [f"- {e.path} [{e.op}]: {e.reason}" for e in rejected]
            if previous.rolled_back:
                parts.append(
                    f"The previous attempt was ROLLED BACK: {previous.rollback_reason}. "
                    "The files below are back to their pre-attempt state."
                )
                parts.append(
                    "DEEP ANALYSIS MODE: in ANALYSIS, trace each failing test's execution path "
                    "through the project code to the exact line that raises or returns the "
                    "wrong value, state why the previous edit could not have changed that "
                    "outcome, and only then propose a DIFFERENT change that targets that line."
                )
                touched = [
                    f"- {e.path}: {e.justification}" for e in previous.edits
                    if e.status == "applied"
                ]
                if touched:
                    parts.append("What that attempt changed:")
                    parts += touched
                failures = (previous.verification or {}).get("failures", [])
                if failures:
                    parts.append("What it caused (do NOT repeat this):")
                    for failure in failures[:3]:
                        message = str(failure.get("message", ""))[:1200]
                        parts.append(f"--- {failure.get('test', '')}\n{message}")
        if research:
            parts += [
                "",
                "Targeted web research on the remaining failures (untrusted; check against the "
                "installed API list):",
            ]
            parts += research
        parts += api_surface_text(verification.api_surface)
        parts += ["", "Files you may edit (full contents, exact text):"]
        remaining = MAX_CONTEXT_CHARS
        omitted: list[str] = []
        for rel in allowed:
            content = _read(workspace, rel) or ""
            if len(content) > remaining:
                omitted.append(rel)
                continue
            remaining -= len(content)
            parts.append(f"=== {rel} ===\n{content}")
        if omitted:
            parts.append("Omitted for size (request smaller edits by path): " + ", ".join(omitted))
        return "\n".join(parts)

    # ---- edits --------------------------------------------------------------

    def apply_edits(
        self,
        workspace: Path,
        allowed: list[str],
        edits: list[dict[str, Any]],
        originals: dict[str, str | None],
    ) -> list[AppliedEdit]:
        """Apply a whole response at once.

        WRITE blocks go first. EDIT blocks are then all located against the content as the
        model saw it (after any WRITE) and applied bottom-up, so earlier edits never
        invalidate the search text of later ones. Overlapping blocks are rejected.
        """
        outcomes: list[AppliedEdit | None] = [None] * len(edits)
        allowed_dirs = {str(PurePosixPath(a).parent) for a in allowed}
        blocks: dict[str, list[int]] = {}

        for index, edit in enumerate(edits):
            raw_path = str(edit.get("path", ""))
            op = str(edit.get("op", ""))
            rel = _clean_path(raw_path)
            if rel is None:
                outcomes[index] = _outcome(edit, raw_path, "rejected", "invalid path")
                continue
            if op not in ("replace_block", "write_file"):
                outcomes[index] = _outcome(edit, rel, "rejected", f"unknown op {op!r}")
                continue
            current = _read(workspace, rel)
            if rel not in allowed:
                creating = current is None and op == "write_file"
                if not (creating and str(PurePosixPath(rel).parent) in allowed_dirs):
                    outcomes[index] = _outcome(edit, rel, "rejected", "outside the allowed files")
                    continue
            if op == "replace_block":
                blocks.setdefault(rel, []).append(index)
                continue

            replace = unescape_literal_newlines(str(edit.get("replace", "")))
            if not replace.strip():
                outcomes[index] = _outcome(edit, rel, "rejected", "WRITE with empty content")
                continue
            baseline = originals.get(rel) if originals.get(rel) is not None else current
            if baseline is not None:
                # compare with the ORIGINAL file: a corrupted current version must not block
                # the rewrite that repairs it
                old_lines = baseline.count("\n") + 1
                new_lines = replace.count("\n") + 1
                if old_lines > 20 and new_lines < old_lines * SHRINK_LIMIT:
                    outcomes[index] = _outcome(
                        edit, rel, "rejected",
                        f"WRITE would shrink {old_lines} lines to {new_lines}; looks "
                        "truncated. Use EDIT blocks or include the complete file",
                    )
                    continue
            originals.setdefault(rel, current)
            _write(workspace, rel, replace)
            outcomes[index] = _outcome(edit, rel, "applied")

        for rel, indexes in blocks.items():
            content = _read(workspace, rel)
            located: list[tuple[int, int, str, int]] = []  # start, end, replacement, index
            for index in indexes:
                edit = edits[index]
                search = str(edit.get("search", ""))
                if content is None:
                    reason = "file does not exist; use WRITE to create it"
                    outcomes[index] = _outcome(edit, rel, "rejected", reason)
                    continue
                if not search.strip():
                    outcomes[index] = _outcome(edit, rel, "rejected", "empty search block")
                    continue
                found = _find_block(content, search)
                if found is None:
                    count = content.count(search.rstrip("\n"))
                    reason = (
                        f"search block occurs {count} times; make it unique"
                        if count > 1 else "search block not found verbatim in file"
                    )
                    outcomes[index] = _outcome(edit, rel, "rejected", reason)
                    continue
                start = content.index(found)
                end = start + len(found)
                if any(s < end and start < e for s, e, _, _ in located):
                    reason = "overlaps another edit in the same response"
                    outcomes[index] = _outcome(edit, rel, "rejected", reason)
                    continue
                replace = unescape_literal_newlines(str(edit.get("replace", "")))
                replacement = replace if found.endswith("\n") else replace.rstrip("\n")
                located.append((start, end, replacement, index))
            if content is None or not located:
                continue
            originals.setdefault(rel, content)
            updated = content
            for start, end, replacement, index in sorted(located, reverse=True):
                updated = updated[:start] + replacement + updated[end:]
                outcomes[index] = _outcome(edits[index], rel, "applied")
            _write(workspace, rel, updated)

        return [o for o in outcomes if o is not None]

    @staticmethod
    def _snapshot(workspace: Path, paths: list[str]) -> dict[str, str | None]:
        return {rel: _read(workspace, rel) for rel in paths}

    @staticmethod
    def output_budget(workspace: Path, allowed: list[str], deep: bool) -> int:
        """Output token cap sized to the files the model may edit.

        A complete rewrite of every editable file plus commentary, with headroom; deep mode
        adds room for the reasoning trace. Bounding the cap this way makes a degenerate,
        repeating response end sooner and cost a fraction of the unbounded one.
        """
        chars = sum(len(_read(workspace, rel) or "") for rel in allowed)
        estimate = int(chars / 3 * 1.6) + 4_000
        if deep:
            return min(DEEP_OUTPUT_TOKENS, max(12_000, estimate + 12_000))
        return min(MAX_OUTPUT_TOKENS, max(6_000, estimate))

    @staticmethod
    def _restore(workspace: Path, snapshot: dict[str, str | None]) -> None:
        for rel, content in snapshot.items():
            target = workspace / rel
            if content is None:
                if target.exists():
                    target.unlink()
            else:
                _write(workspace, rel, content)

    @staticmethod
    def _signatures(verification: VerificationResult) -> set[str]:
        """Failing test plus its final error line, so a changed error counts as movement.

        Collection errors keep the same test id while the underlying exception changes;
        comparing ids alone would call real progress "no effect".
        """
        signatures = set()
        for failure in verification.failures:
            lines = [ln.strip() for ln in failure.message.splitlines() if ln.strip()]
            # first E line = exception type and message; later E lines are often a generic
            # docs URL that would make different errors look identical
            error = next((ln for ln in lines if ln.startswith("E ")), "")
            signatures.add(f"{failure.test}|{error or (lines[0] if lines else '')}")
        return signatures

    @staticmethod
    def _is_worse(new: VerificationResult, best: VerificationResult) -> bool:
        """Order of progress: more tests passing, then more tests executed, then fewer
        failures.

        Going from a startup error (nothing collected) to nine tests that all fail at runtime
        is a step forward, not a regression; the imports work and the failures are now
        specific. The same ordering makes deleting tests a regression.
        """
        if new.passed != best.passed:
            return new.passed < best.passed
        if tests_executed(new) != tests_executed(best):
            return tests_executed(new) < tests_executed(best)
        return new.failure_count > best.failure_count

    # ---- main loop ----------------------------------------------------------

    async def upgrade(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        run_id: str | None = None,
        budget: Budget | None = None,
    ) -> SurgeryResult:
        node = f"dep:{dep.name}"
        cost_before = self.llm.total_cost_usd
        originals: dict[str, str | None] = {}
        result = SurgeryResult(
            package=dep.name, current=dep.current, target=dep.latest, success=False,
            manifest_changes=[],
        )

        manifest_text = _read(workspace, dep.source)
        if manifest_text is None or not dep.latest:
            result.error = "manifest missing or no target version"
            return result
        bumped, changed = bump_pin(manifest_text, dep.name, dep.latest)
        if not changed:
            result.error = f"no `{dep.name}==` pin found in {dep.source}"
            return result
        originals[dep.source] = manifest_text
        _write(workspace, dep.source, bumped)
        result.manifest_changes.append(
            f"{dep.name} {dep.current} -> {dep.latest} in {dep.source}"
        )
        for extra in dossier.requires_new_dependencies:
            note = await self._add_dependency(workspace, dep.source, extra, originals)
            if note:
                result.manifest_changes.append(note)
        await self._emit(
            run_id, EventType.PATCH_PROPOSED, node, attempt=0, kind="manifest",
            changes=result.manifest_changes,
        )

        introspect = sorted(import_names_for(dep.name))
        verification = await self.verifier.verify(
            workspace, run_id, node, label=f"{dep.name}: after version bump",
            introspect=introspect,
        )
        # pip reports conflicts one package at a time in quiet mode: allow several rounds
        for _ in range(6):
            if verification.phase != "install" or self.pypi is None:
                break
            retried = await self._co_upgrade_conflicts(
                workspace, dep, verification, result, run_id, node, introspect
            )
            if retried is verification:
                break
            verification = retried
        result.after_bump = verification.to_dict()
        result.api_surface = verification.api_surface
        if verification.ok:
            result.success = True
            result.final = result.after_bump
            result.diff, result.files_changed = unified_diff(originals, workspace)
            result.cost_usd = round(self.llm.total_cost_usd - cost_before, 6)
            return result
        if verification.phase in ("install", "timeout", "no-tests"):
            tail = " | ".join(
                ln.strip() for ln in verification.log_tail.splitlines()[-12:] if ln.strip()
            )
            result.error = (
                f"cannot iterate: {verification.phase} phase failed after bump. "
                f"Sandbox output: {tail[-900:]}"
            )
            result.final = result.after_bump
            result.diff, result.files_changed = unified_diff(originals, workspace)
            return result

        best = verification
        previous: Attempt | None = None
        last_model: str | None = None
        noop_streak = 0  # consecutive attempts that applied nothing
        last_deep = False
        stuck = 0  # consecutive rollbacks because the edits changed nothing about the failure
        error_history: Counter[str] = Counter()  # error line -> attempts it appeared after
        for failure in verification.failures:
            error_history[error_line(failure.message)] += 1
        collection_streak = 1 if is_collection_error(verification) else 0

        for number in range(1, self.max_attempts + 1):
            allowed = self.allowed_files(workspace, dep, verification)
            # import errors that keep changing but never clear are circling, not progressing
            circling = collection_streak >= 2
            research: list[str] = []
            if previous is not None and (previous.rolled_back or circling):
                research = await self.targeted_research(
                    dep, verification, previous, run_id, workspace=workspace, budget=budget
                )
            dead_ends = [line for line, n in error_history.most_common(8) if n >= 2 and line]
            prompt = self.build_prompt(
                workspace, dep, dossier, verification, allowed, previous, research, dead_ends,
                stuck=stuck,
            )
            if stuck >= 2:
                log.info("surgeon: attempt %d in stuck escalation (%d no-effect rollbacks)",
                         number, stuck)
            log.info(
                "surgeon: %s attempt %d, %d failing, %d editable files, prompt %d chars",
                dep.name, number, verification.failure_count, len(allowed), len(prompt),
            )
            try:
                decision = self.router.route(
                    TaskClass.CODEGEN, attempt=number, budget=budget,
                    min_context_tokens=len(prompt) // 3 + MAX_OUTPUT_TOKENS,
                )
            except (BudgetExhausted, NoCapableModel) as exc:
                result.error = f"{type(exc).__name__}: {exc}"
                break
            if last_model and decision.model.id != last_model:
                await self._emit(
                    run_id, EventType.ESCALATION, node, attempt=number,
                    from_model=last_model, to_model=decision.model.id, reason=decision.reason,
                )
            last_model = decision.model.id

            if noop_streak >= NOOP_LIMIT:
                result.error = (
                    f"model produced no usable edits in {noop_streak} consecutive attempts"
                )
                break
            stalled = previous is not None and (previous.rolled_back or circling)
            endgame = verification.passed > 0 and verification.failure_count <= 3
            # never repeat a deep attempt that produced nothing: alternate modes so one
            # runaway reasoning trace cannot be reproduced attempt after attempt
            deep = (stalled or endgame) and not (noop_streak and last_deep)
            temperature = 0.0 if not noop_streak else min(0.3 + 0.2 * noop_streak, 0.9)
            max_tokens = self.output_budget(workspace, allowed, deep)
            if noop_streak:
                prompt += retry_notice(previous, number, max_tokens)
            if deep:
                log.info(
                    "surgeon: attempt %d in deep-reasoning mode (%s)", number,
                    "stalled" if stalled else "endgame",
                )
            if noop_streak:
                log.info(
                    "surgeon: attempt %d retry after %d no-op attempt(s): temperature %.1f, "
                    "max_tokens %d, deep=%s", number, noop_streak, temperature, max_tokens, deep,
                )
            last_deep = deep
            attempt_cost_before = self.llm.total_cost_usd
            try:
                response = await self.llm.complete(
                    decision.model,
                    [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking=deep, agent=self.agent, run_id=run_id, budget=budget,
                    attempt=number,
                )
            except Exception as exc:  # noqa: BLE001 - recorded, loop continues
                log.exception("surgeon attempt %d failed", number)
                previous = Attempt(
                    number, decision.model.id, f"model call failed: {exc}", [], None,
                    round(self.llm.total_cost_usd - attempt_cost_before, 6),
                )
                result.attempts.append(previous)
                noop_streak += 1
                continue
            text = content_of(response)
            finish = response.choices[0].finish_reason if response.choices else None
            if finish == "length":
                # a truncated response is never safe to apply: a cut-off WRITE is garbage
                log.warning("surgeon: output truncated (%d chars); discarding", len(text))
                previous = Attempt(
                    number, decision.model.id,
                    f"(truncated output: {len(text)} chars, nothing applied) {text[:300]}",
                    [], None, round(self.llm.total_cost_usd - attempt_cost_before, 6),
                )
                result.attempts.append(previous)
                await self._emit(
                    run_id, EventType.PATCH_PROPOSED, node, attempt=number,
                    model=decision.model.id, analysis=previous.analysis, edits=[],
                )
                noop_streak += 1
                continue
            data = parse_edit_blocks(text)
            if not data["edits"] and text.strip():
                # analysis without blocks: ask the same model to finish rather than spend a
                # whole attempt (and a sandbox run) on prose
                data, text = await self._nudge_for_blocks(
                    decision.model, prompt, text, temperature, max_tokens, run_id, budget, number
                )

            snapshot = self._snapshot(workspace, allowed)
            applied = self.apply_edits(workspace, allowed, data["edits"], originals)
            for outcome in applied:
                if outcome.status == "rejected":
                    await self._emit(
                        run_id, EventType.PATCH_REJECTED, node, attempt=number,
                        **asdict(outcome),
                    )
            for extra in data["new_dependencies"]:
                note = await self._add_dependency(workspace, dep.source, str(extra), originals)
                if note:
                    result.manifest_changes.append(note)

            analysis = data["analysis"] or (
                f"(no edit blocks parsed; finish_reason={finish}) {text[:600]}"
            )
            attempt = Attempt(
                number=number, model=decision.model.id, analysis=analysis[:2_000],
                edits=applied, verification=None,
                cost_usd=round(self.llm.total_cost_usd - attempt_cost_before, 6),
            )
            live_diff, _ = unified_diff(originals, workspace)
            await self._emit(
                run_id, EventType.PATCH_PROPOSED, node, attempt=number, model=decision.model.id,
                analysis=attempt.analysis, edits=[asdict(e) for e in applied],
                diff=live_diff[:60_000],
            )

            if not any(e.status == "applied" for e in applied):
                result.attempts.append(attempt)
                previous = attempt
                noop_streak += 1
                continue
            noop_streak = 0

            extra_modules = modules_from_import_errors(verification)
            introspect = sorted(set(introspect) | set(extra_modules))
            verification = await self.verifier.verify(
                workspace, run_id, node, label=f"{dep.name}: attempt {number}",
                introspect=introspect,
            )
            if verification.api_surface:
                result.api_surface = {**result.api_surface, **verification.api_surface}
            attempt.verification = verification.to_dict()
            for failure in verification.failures:
                error_history[error_line(failure.message)] += 1
            collection_streak = (
                collection_streak + 1 if is_collection_error(verification) else 0
            )
            result.attempts.append(attempt)
            if verification.ok:
                result.success = True
                break

            reason = ""
            if verification.phase != "test":
                reason = f"{verification.phase} phase failed"
            elif self._is_worse(verification, best):
                reason = (
                    f"worse: {verification.passed} passing, {verification.failure_count} "
                    f"failing (was {best.passed} passing, {best.failure_count} failing)"
                )
            elif verification.failures and (
                self._signatures(verification) == self._signatures(best)
            ):
                reason = "exactly the same failures with the same errors; the edits had no effect"
            if reason:
                self._restore(workspace, snapshot)
                attempt.rolled_back = True
                attempt.rollback_reason = reason
                stuck = stuck + 1 if reason.startswith("exactly the same failures") else 0
                await self._emit(
                    run_id, EventType.AGENT_MESSAGE, node, attempt=number, rolled_back=True,
                    message=f"attempt {number} rolled back: {reason}",
                )
                verification = best
            else:
                stuck = 0
                best = verification
            previous = attempt

        result.final = verification.to_dict()
        result.diff, result.files_changed = unified_diff(originals, workspace)
        result.cost_usd = round(self.llm.total_cost_usd - cost_before, 6)
        result.originals = originals
        if not result.success and result.error is None:
            result.error = f"tests still failing after {len(result.attempts)} attempt(s)"
        return result

    # ---- polish pass --------------------------------------------------------

    async def polish(
        self,
        workspace: Path,
        dep: DependencyAssessment,
        dossier: ImpactDossier,
        surgery: SurgeryResult,
        findings: list[dict[str, Any]],
        run_id: str | None = None,
        budget: Budget | None = None,
    ) -> Attempt | None:
        """One extra attempt that addresses reviewer findings while keeping the suite green.

        Rolled back entirely if any test stops passing. Extends surgery.diff in place.
        """
        if not surgery.success or not findings:
            return None
        node = f"dep:{dep.name}"
        allowed = self.allowed_files(workspace, dep, None)
        for finding in findings:
            rel = _clean_path(str(finding.get("file", "")))
            if rel and rel not in allowed and (workspace / rel).is_file():
                allowed.append(rel)
        lines = [
            f"Upgrade: {dep.name} {dep.current} -> {dep.latest}. The upgrade is complete and "
            "the test suite is GREEN. A reviewer raised the findings below. Address them with "
            "minimal edits. Every test must still pass; do not change behaviour, do not touch "
            "tests unless a finding is about a test, and do not revisit the upgrade itself.",
            "",
            "Findings:",
        ]
        for finding in findings:
            where = finding.get("file", "")
            if finding.get("line"):
                where += f":{finding['line']}"
            lines.append(
                f"- [{finding.get('severity', '')}/{finding.get('kind', '')}] {where}: "
                f"{finding.get('title', '')}. {finding.get('detail', '')} "
                f"Suggestion: {finding.get('suggestion', '') or 'none'}"
            )
        lines += api_surface_text(surgery.api_surface)
        lines += ["", "Files you may edit (full contents, exact text):"]
        for rel in allowed:
            lines.append(f"=== {rel} ===\n{_read(workspace, rel) or ''}")
        prompt = "\n".join(lines)

        # polish is a precision task: route like a second attempt (Ultra)
        decision = self.router.route(
            TaskClass.CODEGEN, attempt=2, budget=budget,
            min_context_tokens=len(prompt) // 3 + MAX_OUTPUT_TOKENS,
        )
        cost_before = self.llm.total_cost_usd
        response = await self.llm.complete(
            decision.model,
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=MAX_OUTPUT_TOKENS, agent=self.agent,
            run_id=run_id, budget=budget,
        )
        data = parse_edit_blocks(content_of(response))
        snapshot = self._snapshot(workspace, allowed)
        applied = self.apply_edits(workspace, allowed, data["edits"], surgery.originals)
        attempt = Attempt(
            number=len(surgery.attempts) + 1, model=decision.model.id,
            analysis=("polish: " + data["analysis"])[:2_000], edits=applied, verification=None,
            cost_usd=round(self.llm.total_cost_usd - cost_before, 6),
        )
        await self._emit(
            run_id, EventType.PATCH_PROPOSED, node, attempt=attempt.number, kind="polish",
            model=decision.model.id, analysis=attempt.analysis,
            edits=[asdict(e) for e in applied],
        )
        if any(e.status == "applied" for e in applied):
            verification = await self.verifier.verify(
                workspace, run_id, node, label=f"{dep.name}: polish"
            )
            attempt.verification = verification.to_dict()
            if not verification.ok:
                self._restore(workspace, snapshot)
                attempt.rolled_back = True
                attempt.rollback_reason = "polish broke the test suite"
                await self._emit(
                    run_id, EventType.AGENT_MESSAGE, node, attempt=attempt.number,
                    rolled_back=True, message="polish rolled back: it broke the test suite",
                )
            else:
                surgery.final = attempt.verification
        surgery.attempts.append(attempt)
        surgery.diff, surgery.files_changed = unified_diff(surgery.originals, workspace)
        surgery.cost_usd = round(surgery.cost_usd + attempt.cost_usd, 6)
        return attempt
