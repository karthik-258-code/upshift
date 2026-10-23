# Upshift

**Dependency upgrades that finish the job.** Upshift reads the real changelog, rewrites the
code that breaks, runs your tests in an isolated sandbox until they pass, has a second model
review the diff (and a third fact-check the review), and opens a merge request where every
edit links to the documentation line that justifies it.

Built for the **Nebius x NVIDIA Global AI Hackathon**, Coding and Agentic Engineering track.
Powered by **NVIDIA Nemotron 3** (Nano, Super, Ultra, 3.5 Lightning) on **Nebius Token
Factory**, with **Tavily** for changelog research. Apache 2.0.

- **Live replay (Mission Control):** https://gitlab-ai-hackathon.gitlab.io/transcend-october-2026/43491832/nvidxnebi/ : real runs, every event, diff and review; drag the slider.
- **Merge requests Upshift opened:** [pydantic 1.10 -> 2.14](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43491832/pyshop-legacy/-/merge_requests/6), [SQLAlchemy 1.4 -> 2.1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43491832/pyshop-legacy/-/merge_requests/7), [Flask 2.0 -> 3.1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43491832/flask-notes/-/merge_requests/1) (after co-upgrading Werkzeug, Jinja2, click and itsdangerous from pip's conflict report).
- **Docs:** [ARCHITECTURE](docs/ARCHITECTURE.md) · [FEEDBACK for Nebius/NVIDIA](docs/FEEDBACK.md) · day logs [5](docs/examples/day5-runs.md), [6-7](docs/examples/day6-7-runs.md) · [BLUEPRINT](docs/BLUEPRINT.md)

## Results

`python -m upshift eval`, two repeats per case, real pipeline, no merge requests published.
"Bare bump" is what a dependabot-style tool leaves you with: tests passing after only the
version pin changed. Full tables in [`evals/results/`](evals/results/).

| Upgrade | Bare bump | Upshift | Attempts | Review | Cost |
| --- | --- | --- | --- | --- | --- |
| pydantic 1.10.13 -> 2.14.1 | 0 (import error) | **17/17** | 5, 3 | approve | $0.09, $0.06 |
| SQLAlchemy 1.4.49 -> 2.1.6 | 15/17 | **17/17** | 1, 1 | approve | $0.04, $0.04 |
| requests 2.28.2 -> 2.34.2 | 17/17 | **17/17** | 0 (no edits needed) | skipped | $0.001 |
| Flask 2.0.3 -> 3.1.3 (4 pins co-upgraded) | 0 (install failed, then import error) | **9/9** | 3, 12 (one miss at 8/9) | approve | $0.05, $0.34 |

7 of 8 upgrades landed; a follow-up check of the Flask case after the "stuck escalation"
fix landed 3 of 3 (9, 9 and 12 attempts, $0.14 to $0.28). Every number comes from recorded
`llm.call` and `test.result` events, not estimates. Failures are kept in the logs on
purpose; they are how the convergence machinery was earned.

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

## Quick start (local; model calls are the only cost, cents per upgrade)

Requirements: Python 3.11+, Docker Desktop (sandbox), Node 20+ (UI).

```powershell
# 1. credentials
copy .env.example .env      # set NEBIUS_API_KEY, TAVILY_API_KEY, optionally GITLAB_TOKEN

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
python -m upshift run https://gitlab.com/your/repo --package flask --target-project your/repo
python -m upshift eval --repeat 2            # the results table above
python -m upshift export --succeeded 6       # recordings for the static replay site
```

macOS / Linux: same commands with `source .venv/bin/activate`, or `./scripts/run_local.sh`.

| Variable | Purpose |
| --- | --- |
| `NEBIUS_API_KEY` | Token Factory API key (required) |
| `NEBIUS_BASE_URL` | Defaults to `https://api.tokenfactory.us-central1.nebius.com/v1/` |
| `TAVILY_API_KEY` | Changelog search and extraction (required) |
| `GITLAB_TOKEN` | Opening merge requests in `--target-project` (optional; drafts otherwise) |
| `DEFAULT_BUDGET_USD` | Per-run spend cap, default 2.00 |
| `EXECUTOR` | `docker` (default) or `nebius` (requires `NEBIUS_ACCEPT_CHARGES=1` and the `NEBIUS_*` settings in `upshift/config.py`) |

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
  tools/       Tavily search/extract, PyPI client, GitLab merge requests, cache
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
- Convergence is probabilistic: the same input can take 3 or 12 attempts. The eval tables show the spread.
- "Green and approved" is not "idiomatic": the Flask MR hand-rolls a `JSONProvider` where a maintainer would subclass `DefaultJSONProvider`. The Reviewer catches correctness, not taste.
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
