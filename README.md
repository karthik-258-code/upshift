# Upshift

**Dependency upgrades that finish the job.** Upshift reads the real changelog, rewrites the
code that breaks, runs your tests in an isolated sandbox until they pass, has a second model
review the diff (and a third fact-check the review), and opens a merge request where every
edit links to the documentation line that justifies it.

Built for the **Nebius x NVIDIA Global AI Hackathon**, Coding and Agentic Engineering track.
Powered by **NVIDIA Nemotron 3** (Nano, Super, Ultra, 3.5 Lightning) on **Nebius Token
Factory**, with **Tavily** for changelog research. Apache 2.0.

- **Repository:** https://github.com/karthik-258-code/upshift
- **Merge requests Upshift opened (hackathon evidence):** [pydantic 1.10 -> 2.14](https://gitlab.com/notorandyt/pyshop-legacy/-/merge_requests/1), [SQLAlchemy 1.4 -> 2.1](https://gitlab.com/notorandyt/pyshop-legacy/-/merge_requests/2), [Flask 2.0 -> 3.1](https://gitlab.com/notorandyt/flask-notes/-/merge_requests/1) (after co-upgrading Werkzeug, Jinja2, click and itsdangerous from pip's conflict report).
- **Docs:** [ARCHITECTURE](docs/ARCHITECTURE.md) · [FEEDBACK for Nebius/NVIDIA](docs/FEEDBACK.md) · [SUBMISSION](docs/SUBMISSION.md) · day logs [5](docs/examples/day5-runs.md), [6-7](docs/examples/day6-7-runs.md), [8-9](docs/examples/day8-9-runs.md) · [BLUEPRINT](docs/BLUEPRINT.md)
- **CI:** use the [GitHub Actions workflow](.github/workflows/upshift.yml) or the [GitLab CI template](templates/upshift.yml) to run Upshift as a job that opens PRs/MRs in your project.

## Results

`python -m upshift eval --repeat 3`, three repeats per case, real pipeline, no merge requests published.
"Bare bump" is what a dependabot-style tool leaves you with: tests passing after only the
version pin changed. Full tables in [`evals/results/`](evals/results/).

| Case | Upgrade | Bare bump | Upshift | Attempts | Review | Cost | Time | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pyshop-pydantic | pydantic 1.10.13 -> 2.14.1 | 0 (import error) | **17/17** | 5 | approve | $0.158 | 3m47s | pass |
| pyshop-pydantic #2 | pydantic 1.10.13 -> 2.14.1 | 0 (import error) | **17/17** | 8 | approve | $0.283 | 8m14s | pass |
| pyshop-pydantic #3 | pydantic 1.10.13 -> 2.14.1 | 0 (import error) | **17/17** | 4 | approve | $0.066 | 3m50s | pass |
| pyshop-sqlalchemy | sqlalchemy 1.4.49 -> 2.1.6 | 15/17 | **17/17** | 3 | approve | $0.046 | 2m15s | pass |
| pyshop-sqlalchemy #2 | sqlalchemy 1.4.49 -> 2.1.6 | 15/17 | **17/17** | 1 | approve | $0.028 | 1m17s | pass |
| pyshop-sqlalchemy #3 | sqlalchemy 1.4.49 -> 2.1.6 | 15/17 | **17/17** | 1 | approve | $0.037 | 1m14s | pass |
| pyshop-requests | requests 2.28.2 -> 2.34.2 | 17/17 | **17/17** | 0 | skipped | $0.001 | 0m17s | pass |
| pyshop-requests #2 | requests 2.28.2 -> 2.34.2 | 17/17 | **17/17** | 0 | skipped | $0.001 | 0m17s | pass |
| pyshop-requests #3 | requests 2.28.2 -> 2.34.2 | 17/17 | **17/17** | 0 | skipped | $0.001 | 0m20s | pass |
| pyshop-dateutil | python-dateutil 2.8.2 -> 3.0.0 | 17/17 | **17/17** | 0 | skipped | $0.003 | 0m23s | pass |
| pyshop-dateutil #2 | python-dateutil 2.8.2 -> 3.0.0 | 17/17 | **17/17** | 0 | skipped | $0.003 | 0m18s | pass |
| pyshop-dateutil #3 | python-dateutil 2.8.2 -> 3.0.0 | 17/17 | **17/17** | 0 | skipped | $0.003 | 0m18s | pass |
| pyshop-pytest | pytest 8.3.3 -> 9.1.1 | 17/17 | **17/17** | 0 | skipped | $0.004 | 0m34s | pass |
| pyshop-pytest #2 | pytest 8.3.3 -> 9.1.1 | 17/17 | **17/17** | 0 | skipped | $0.003 | 0m20s | pass |
| pyshop-pytest #3 | pytest 8.3.3 -> 9.1.1 | 17/17 | **17/17** | 0 | skipped | $0.009 | 0m57s | pass |
| flask-notes-flask | flask 2.0.3 -> 3.1.3 | 0 (import error) | **9/9** | 11 | approve | $0.205 | 5m33s | pass |
| flask-notes-flask #2 | flask 2.0.3 -> 3.1.3 | 0 (import error) | **8/9** | 12 | - | $0.402 | 8m27s | FAIL (tests still failing after 12 attempt(s)) |
| flask-notes-flask #3 | flask 2.0.3 -> 3.1.3 | 0 (import error) | **9/9** | 2 | approve | $0.028 | 1m48s | pass |

**17/18 upgrades landed (94%).** Mean cost $0.071; total $1.28 in 40 minutes (eval of
2026-10-25 11:48 UTC, [full table](evals/results/latest.md)). The previous eval
(2026-10-23, two repeats, [JSON](evals/results/20261023-014137.json)) landed 7/8; its Flask
miss led to the "stuck escalation" mechanism. Since then Flask has landed 6 of 8:
[3 of 3](evals/results/flask-stuck-check/latest.md) on 2026-10-23, then
[1 of 2](evals/results/range-opt-in-check/latest.md) and 2 of 3 in the table above on
2026-10-25. Its misses stall at 8/9 after 12 attempts, and it is the most expensive case
($0.03 to $0.40 per run). The pydantic, SQLAlchemy and Flask cases need code edits; requests,
python-dateutil and pytest land with zero edits, because the suite is already green after
the bump. The Flask bare-bump column reads "import error" because pip's install first failed
on four pinned packages, which Upshift co-upgraded before any test could run. Every number
comes from recorded `llm.call` and `test.result` events, not estimates. Failures are kept in
the logs on purpose; they are how the convergence machinery was earned.

## How it works

| Agent | Role | Nemotron model |
| --- | --- | --- |
| Scout | Parses manifests, finds outdated packages, maps which symbols your code actually uses (AST) | deterministic |
| Historian | Finds and reads changelogs and migration guides (Tavily), classifies every documented change, intersects with your usage into an **Impact Dossier** | 3.5 Lightning (1M context) |
| Surgeon | Bumps the pin, runs the tests, edits code until green: co-upgrades conflicting pins, introspects the installed API, rolls back regressions, researches when stuck | Ultra |
| Verifier | Installs, introspects, runs pytest in an ephemeral sandbox (Docker locally; Nebius Serverless Jobs executor behind a charge gate) | deterministic |
| Reviewer + Auditor | Adversarial review of the diff (weakened tests, silent behaviour changes, shims); every blocker fact-checked in a fresh context | Ultra |
| Scribe | Writes the evidence-linked merge request | Super |

Every decision, tool call, token and test result is an append-only event. Mission Control
streams them live and can replay any run. [ARCHITECTURE.md](docs/ARCHITECTURE.md) explains
the convergence machinery (progress ordering, dead-end memory, startup-failure handling,
captured tracebacks, research planner, stuck escalation) and why each piece exists.

## How Tavily is used

Upshift's research is Tavily end to end. The Historian runs two **Search** queries per
upgrade (the migration guide for the new major, and the release notes for the exact target
version), ranks the results, and **Extracts** the full pages (120k to 400k characters for
pydantic, SQLAlchemy or Flask changelogs), which Nemotron 3.5 Lightning reads whole. The
fetched URLs are the only ones the Scribe is allowed to cite, so every link in a merge
request resolves to a page that was actually read. When the Surgeon stalls, the research
planner writes targeted Search queries from the failing test and traceback (for example
`flask 3 JSON_SORT_KEYS removed app.json sort_keys`) and the snippets go into the next
prompt. Results are cached on disk (72 h) so repeated runs do not re-fetch.

## Quick start (local; model calls are the only cost, cents per upgrade)

Requirements: Python 3.11+, Docker Desktop (sandbox), Node 20+ (UI).

```powershell
# 1. credentials
copy .env.example .env      # set NEBIUS_API_KEY, TAVILY_API_KEY, optionally GITHUB_TOKEN or GITLAB_TOKEN

# 2. install
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd web; npm install; npm run build; cd ..

# 3. verify the models you have access to (one-time, a few cents)
python scripts/probe_models.py

# 4. run
python -m uvicorn upshift.api.main:create_app --factory --port 8000
```

Open http://localhost:8000, keep the pre-filled demo (pydantic on `demo-repos/pyshop-legacy`)
and click **Start upgrade run**. Or from the CLI:

```powershell
python -m upshift run demo-repos\pyshop-legacy --package pydantic
python -m upshift run https://github.com/your/repo --package flask --target-project your/repo
python -m upshift eval --repeat 3            # the results table above
python -m upshift export --succeeded 6       # recordings for the static replay site
```

macOS / Linux: same commands with `source .venv/bin/activate`, or `./scripts/run_local.sh`.

| Variable | Purpose |
| --- | --- |
| `NEBIUS_API_KEY` | Token Factory API key (required) |
| `NEBIUS_BASE_URL` | Defaults to `https://api.tokenfactory.us-central1.nebius.com/v1/` |
| `TAVILY_API_KEY` | Changelog search and extraction (required) |
| `GITHUB_TOKEN` | Opening pull requests on GitHub in `--target-project` (optional; drafts otherwise) |
| `GITLAB_TOKEN` | Opening merge requests on GitLab in `--target-project` (optional; drafts otherwise) |
| `DEFAULT_BUDGET_USD` | Per-run spend cap, default 2.00 |
| `EXECUTOR` | `docker` (default) or `nebius` (requires `NEBIUS_ACCEPT_CHARGES=1` and the `NEBIUS_*` settings in `upshift/config.py`) |
| `SANDBOX_IMAGE` | `python:3.12-slim` (default); set `python:3.12` for projects whose dependencies need a compiler |

Tests: `pytest -q` (fully mocked; the real-Docker test runs with `UPSHIFT_DOCKER_TESTS=1`) and `ruff check .`.

## API

- `GET /api/health`, `GET /api/models`
- `POST /api/runs` `{repo_url, policy, budget_cap_usd, packages, target_project}`
- `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events?after=N`
- `GET /api/runs/{id}/stream` Server-Sent Events with `Last-Event-ID` resume

## Project layout

```
upshift/
  agents/      scout, historian, surgeon, verifier, reviewer (+auditor), scribe, supervisor
  api/         FastAPI app, SSE, static UI
  core/        events, SQLite event store, bus, budget, workspace preparation
  ecosystems/  Python manifest parsing and AST symbol-usage scanner
  llm/         model catalog, routing, Token Factory client, structured output
  sandbox/     executor contract, Docker backend, Nebius Serverless Jobs backend, factory
  tools/       Tavily search/extract, PyPI client, GitHub PRs, GitLab MRs, cache
  evals.py     eval harness
  cli.py       python -m upshift scout|research|upgrade|run|eval|export|sandbox-check
web/           Mission Control (Vite + React + TS + Tailwind); web/public/runs = recordings
config/        models.yaml (IDs, prices), capabilities.json (probe output)
demo-repos/    pyshop-legacy (pydantic 1, SQLAlchemy 1.4), flask-notes (Flask 2.0 + pinned deps)
evals/         cases.yaml and results
docs/          ARCHITECTURE, FEEDBACK, BLUEPRINT, day logs
```

## Limitations, honestly

- Python only (requirements.txt and pyproject); the ecosystem layer is pluggable but only one is implemented.
- Convergence is probabilistic: the same input can take 2 or 12 attempts. The eval tables show the spread.
- "Green and approved" is not "idiomatic": the Flask MR hand-rolls a `JSONProvider` where a maintainer would subclass `DefaultJSONProvider`, and one of its edits orders a response's keys to match the test on top of the real fix (`app.json.sort_keys = False`). The Reviewer catches correctness, not taste.
- The Auditor can be wrong too; it is a second opinion with evidence, not an oracle.
- The Nebius Serverless Jobs executor is built on the documented API and unit-tested, but was not exercised against the live service (no compute credits).

## Cost and safety

- Model IDs and prices live in `config/models.yaml`; every call records tokens, cost and latency; runs stop at their budget cap.
- Project code never executes on the host, only inside ephemeral containers (capabilities dropped, no new privileges, CPU and memory limits).
- Web content fetched via Tavily is treated as data, never as instructions.
- Tests are never deleted or weakened: the Surgeon's rules, the progress ordering (fewer tests executed is a regression) and the Reviewer all enforce it.
- No paid Nebius compute is created unless `NEBIUS_ACCEPT_CHARGES=1` is set explicitly.

## License

Apache 2.0. See [LICENSE](LICENSE).
