# Upshift architecture

Upshift is an autonomous dependency-upgrade agent. Given a repository and a package, it
reads the real changelog and migration guide, rewrites the code that breaks, runs the
project's own tests in an isolated sandbox until they pass, has a second model review the
diff adversarially (and a third fact-check that review), and opens a merge request in which
every edit links to the documentation line that justifies it.

This document explains how the pieces fit, and why they are shaped the way they are. The
reasons matter more than the shapes: most of them were learned from runs that failed.

## 1. The one-sentence design

**Everything is an append-only event, every claim is checked against something outside the
model, and the test suite is the only arbiter of success.**

- Events (`upshift/core/events.py`) are the single source of truth. Agents emit them; the
  SQLite store persists them; the UI streams them live over SSE and can replay any run by
  reducing the same list. There is no second "state" to drift.
- Claims are checked: the Historian's classifications are intersected with the code's actual
  symbol usage; the Surgeon's edits are verified by the sandbox; the Reviewer's blockers are
  audited against the diff and the installed API; the Scribe only cites URLs the Historian
  actually fetched.
- Success means the project's tests pass on the new version with no test weakened. Nothing
  else counts.

## 2. Agents and models

| Agent | Job | Model (Nemotron on Nebius Token Factory) | Why that tier |
| --- | --- | --- | --- |
| Scout | Parse manifests, query PyPI, map which symbols of each package the code uses (AST) | none (deterministic) | Facts, not judgement |
| Historian | Find and read changelogs and migration guides (Tavily search + extract), classify every documented change, intersect with usage into an **Impact Dossier** | 3.5 Lightning (1M context) | A Flask or pydantic changelog is 100k to 400k characters; only Lightning reads it whole and cheaply |
| Surgeon | Bump the pin, run tests, edit code until green | Ultra | Code edits that must be right; Super never converged in our runs |
| Verifier | Install, introspect the installed API, run pytest in a sandbox, parse JUnit | none | Ground truth must not come from a model |
| Reviewer | Adversarial review of the final diff: weakened tests, silent behaviour changes, compat shims | Ultra | Needs the strongest reader |
| Auditor | Fact-check each blocking or factual finding of the Reviewer | Ultra, fresh context | A focused question beats a long review for accuracy |
| Research planner | When the Surgeon stalls, write the web queries and a root-cause hypothesis | Lightning | Cheap, and better than templating a query from an assertion |
| Scribe | Write the merge request body with the evidence table | Super | Prose, with structure supplied by us |

Routing lives in `upshift/llm/router.py` and is driven by `config/models.yaml` plus
`config/capabilities.json`, which `scripts/probe_models.py` fills by actually testing chat,
tool calling, parallel tools, `json_object` and strict `json_schema` on each model. The
router only relies on capabilities that passed. Reasoning is on by default on every
Nemotron tier and reasoning tokens count against `max_tokens`, so `TokenFactoryClient`
turns thinking off (`chat_template_kwargs.enable_thinking=false`) unless an agent asks for
`thinking=True`; the Surgeon does so in its deep-reasoning mode.

## 3. The pipeline, as a run experiences it

```
Supervisor
  Scout ----------------------------> dep.discovered (per dependency), candidates by policy
  for each candidate, in a fresh copy of the workspace:
    Historian -----------------------> tool.call, evidence.fetched, dossier.ready
    Surgeon
      bump pin, add required packages (dossier.requires_new_dependencies)
      Verifier "after version bump" -> sandbox.started/finished, test.result
      if pip refused: co-upgrade conflicting pins, re-verify (up to 6 rounds)
      loop attempts (<= 12):
        build prompt (dossier, failures, allowed files, installed API, dead ends, research)
        model -> SEARCH/REPLACE or WRITE blocks  -> patch.proposed / patch.rejected
        Verifier -> test.result; keep or roll back
    Reviewer (ruff + model) -> Auditor per blocker -> review.verdict
    if findings: Surgeon.polish (one attempt, rolled back if anything breaks), re-review
    Scribe -> merge request body
    GitHub/GitLab: branch, commit, PR/MR (Draft if blockers remain) -> mr.opened
  run.finished (summary with per-dependency outcomes)
```

Every `llm.call` is also an event with model, tokens, latency and cost, so the UI's cost
strip and the eval table are computed, not estimated.

## 4. Evidence chain

The Impact Dossier is a list of entries `{title, category, migration, quote, evidence_url,
matched_symbols, relevant}` produced by Lightning under a strict JSON schema from the pages
Tavily fetched. `relevant` is not the model's opinion alone: an entry is relevant when its
symbols intersect the Scout's usage map for that package. The Surgeon receives relevant
entries in full and the rest as titles. Each edit block the Surgeon emits carries a
`JUSTIFICATION` and an `EVIDENCE` URL; the Scribe builds the merge request's table from
those and drops URLs that were never fetched. The MR a human reads is therefore traceable
end to end: changelog line -> dossier entry -> edit -> test result -> review -> audit.

## 5. Why the Surgeon converges (the hard part)

A model that is good at writing code is not automatically good at *finishing* a migration.
Every mechanism below exists because a real run failed without it; the day logs in
`docs/examples/` record each one.

**Edit protocol.** Plain-text `### EDIT` (SEARCH/REPLACE) and `### WRITE` (whole file) blocks
rather than JSON: code inside JSON strings gets double-escaped by models too often. Blocks
whose search text is not found verbatim are rejected and reported back; a WRITE that would
shrink a file by more than 70% is rejected as truncation.

**Allowlist.** The model may only edit files that import the package, files named in failing
tracebacks, and manifests. Everything else is rejected with a reason.

**Verification after every attempt, rollback on regression.** Progress is ordered by tests
passing, then tests *executed*, then failures. Moving from "nothing collected" to "nine tests
that all fail at runtime" is progress (the imports work); deleting tests is a regression.
Collection-error states count as zero executed, so fixing one of two broken modules is also
progress even though pytest's error count drops.

**Startup failures are failures.** pytest exit 4 (conftest import error) reports no test
cases; the Verifier synthesises a `pytest::startup` failure carrying the traceback so the
loop has something to compare and the right files become editable.

**Captured output on failures.** `-o junit_logging=all --showlocals`: a web framework that
logs the exception and answers 500 still shows the model the traceback, and the failing
frame's locals disambiguate `'NoneType' object has no attribute` errors.

**Resolver conflicts.** pip's conflict summary (quiet or verbose form) is parsed; packages
pinned in the same requirements file are co-upgraded to their latest release and the install
retried, up to six rounds. Upgrading Flask drags Werkzeug, Jinja2, click and itsdangerous.

**Installed API introspection.** The sandbox dumps the public names of the upgraded package
and of any module named in a `cannot import name X from M` error (`.upshift/api.json`). The
Surgeon and Reviewer see what actually exists, so "import url_quote from werkzeug.utils"
dies on contact with the list rather than on the next test run.

**Never retry the same input.** After an attempt that applied nothing (truncated, no blocks,
all rejected), the next call gets a higher temperature, a retry notice naming the failure
mode, and alternates out of deep mode; the output cap is sized to the editable files so a
runaway ends quickly; four consecutive no-ops abort the upgrade.

**Nudge for blocks.** A response that is analysis without edits gets one follow-up turn in
the same conversation asking for the blocks only.

**Dead-end memory.** Error lines that recur across attempts are listed as approaches that do
not work on this version. Persistent collection errors count as stalled.

**Deep mode.** Thinking on, larger output cap, when stalled or in the endgame (<= 3 failing).

**Research planner.** When stalled, Lightning writes up to three web queries and a one-line
hypothesis from the failing test, its traceback and the last edits; Tavily results and the
hypothesis go into the next prompt.

**Stuck escalation.** After two consecutive rollbacks because the edits changed nothing about
the failure, surgical mode is lifted: the model is told the approach is wrong, asked to
re-derive the implementation from what the test observes, and allowed to rewrite the module.

**Per-call timeouts scale with the output budget.** A 20k-token reasoning call on a 550B
model takes minutes; a fixed 120 s timeout only retried the same slow request.

## 6. Review that checks itself

The Reviewer sees the dossier, the edit justifications, ruff findings, the installed API,
the full post-edit files and the diff, and returns findings under a strict schema
(severity, kind, file, title, detail, suggestion). Deterministic calibration downgrades the
most common false positive (a test's expected exception updated to the library's documented
behaviour is not a weakened test). Then every blocker, and every major that asserts a fact,
goes to the Auditor in a fresh context with one question: supported, contradicted,
unverifiable, or revised (wrong as worded, but pointing at a real problem, restated). The
merge request is a Draft only if a supported blocker remains. The honest limitation: the
Auditor can be wrong too (it once agreed that `app.errorhandler` does not exist in Flask 3,
because module-level API lists say nothing about class attributes; the prompt now says so).

## 7. Sandbox

`upshift/sandbox/base.py` defines the contract: a workspace directory, a command, an image,
files to collect back. `DockerExecutor` runs it locally with dropped capabilities, no new
privileges, CPU and memory limits, and reads artifacts back through `docker cp`.
`NebiusJobsExecutor` implements the same contract on Nebius Serverless AI Jobs using only
the documented REST surface: the workspace travels as a tarball through object storage with
presigned URLs (injected files are capped at 64 KiB; output otherwise reaches only logs),
the job runs a bash+python script that exists in any Python image, and authentication is a
user token or the documented service-account JWT exchange. It refuses to create anything
unless `NEBIUS_ACCEPT_CHARGES=1`.

## 8. Mission Control

`web/` is Vite + React + TypeScript + Tailwind with no runtime dependency beyond React. It
consumes `/api/runs`, `/api/runs/{id}` and the SSE stream, and reduces the event list into
UI state in `web/src/state.ts`, a pure function, so dragging the replay slider simply reduces
a prefix of the list. When no API is reachable it reads `runs/index.json` and
`runs/<id>.json` written by `python -m upshift export`, which is how the GitHub Pages site
works: a recorded replay of real runs, at no hosting cost.

## 9. Evaluation

`python -m upshift eval` runs `evals/cases.yaml` through the real pipeline without
publishing merge requests and writes a table comparing the bare bump (what a
dependabot-style tool leaves behind) with Upshift's result, plus attempts, review verdict,
cost and time. Results live in `evals/results/`; the latest table is embedded on the site.

## 10. Decisions we would defend

- **Text edit protocol over tool calls.** Tool-call JSON with code payloads failed more often
  than it worked; SEARCH/REPLACE text with verbatim matching is boring and robust.
- **Ultra for every code edit.** Super was cheaper and never finished; the cost difference
  per upgrade is cents.
- **A reviewer that is audited.** An adversarial reviewer without a fact-check turns every
  MR into a Draft over hallucinated blockers; auditing restores signal.
- **Recorded replay instead of a paid live host.** Judges see every real event; nothing
  is simulated; it never goes down; it costs nothing.
- **Honest failure accounting.** The day logs and eval tables keep the failures. They are
  the record of how the convergence machinery was earned.
