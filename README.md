# Upshift

**Autonomous dependency upgrades that actually finish the job.**

Upshift points at a repository, finds outdated dependencies, reads the real changelogs and migration guides from the web, rewrites the code that breaks, proves the result in an isolated sandbox, and opens a merge request where every edit is pinned to the changelog line that justified it. Dependabot and Renovate bump versions and leave you a red pipeline. Upshift turns it green.

Built for the **Nebius x NVIDIA Global AI Hackathon**, Coding and Agentic Engineering track. Powered by **NVIDIA Nemotron 3** (Nano, Super, Ultra, and 3.5 Lightning) served on **Nebius Token Factory**, with **Nebius Serverless Jobs** for sandboxed test execution and **Tavily** for changelog research.

> Status: Day 5 of 11. **The whole chain runs from the browser and ends in a real merge request.** Scout, Historian, Surgeon, Verifier, Reviewer (with an Auditor that fact-checks its own blockers), Scribe and GitLab publishing are in place, and Mission Control streams every event live with replay. Latest showcase MRs on the demo repo: [pydantic 1.10 -> 2.14](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43491832/pyshop-legacy/-/merge_requests/6) and [SQLAlchemy 1.4 -> 2.1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43491832/pyshop-legacy/-/merge_requests/7), 17/17 green, $0.03 to $0.15 per upgrade. Day log with the reliability work: [docs/examples/day5-runs.md](docs/examples/day5-runs.md). Next: Nebius Serverless Jobs executor and hosted deployment. Plan: [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

## How it works

| Agent | Role | Nemotron model |
| --- | --- | --- |
| Scout | Parses manifests, finds outdated packages, maps which symbols your code actually uses | Nano |
| Historian | Finds and reads changelogs and migration guides (Tavily), classifies breaking changes, intersects them with your usage | 3.5 Lightning (1M context) with Nano fallback |
| Surgeon | Rewrites affected code, iterates on test failures | Super, escalating to Ultra |
| Verifier | Installs, runs tests and type checks in an ephemeral sandbox (Docker locally, Nebius Serverless Jobs in the cloud) | deterministic + Nano triage |
| Reviewer | Adversarial review of the final patch set | Ultra |
| Scribe | Writes the evidence-linked merge request | Super |

Every decision, tool call, token and test result is an append-only event. The UI streams them live and can replay any run.

## Quick start (local, no cloud compute cost)

Requirements: Python 3.11+, Docker Desktop (for the sandbox, from Day 1), Node 20+ (for the UI, from Day 5).

```powershell
# Windows
.\scripts\run_local.ps1
```

```bash
# macOS / Linux
./scripts/run_local.sh
```

Then open http://localhost:8000 for Mission Control (build it once with `cd web && npm install && npm run build`; the API serves `web/dist`), or http://localhost:8000/docs for the raw API. For UI development with hot reload use `.\scripts\dev_ui.ps1` and open http://localhost:5173. Fill in `.env` first:

| Variable | Purpose |
| --- | --- |
| `NEBIUS_API_KEY` | Token Factory API key (required for any model call) |
| `NEBIUS_BASE_URL` | Defaults to `https://api.tokenfactory.us-central1.nebius.com/v1/` |
| `TAVILY_API_KEY` | Changelog search and extraction |
| `GITLAB_TOKEN` | Opening merge requests in the target project |
| `DEFAULT_BUDGET_USD` | Per-run spend cap, default 2.00 |
| `EXECUTOR` | `docker` (default) or `nebius` |

### Verify model capabilities (one-time, a few cents)

```bash
python scripts/probe_models.py
```

This tests chat, tool calling, parallel tool calls, JSON mode and JSON Schema on each Nemotron model and writes `config/capabilities.json`. The router only uses capabilities that passed.

### Run the tests (fully mocked, no API calls)

```bash
pytest -q
ruff check .
```

## Developer CLI

```bash
python -m upshift sandbox-check                      # run a trivial job in the Docker sandbox
python -m upshift scout demo-repos/pyshop-legacy     # dependency assessment table
python -m upshift scout https://gitlab.com/org/repo --json
```

## API

- `GET /api/health`, `GET /api/models`
- `POST /api/runs` `{repo_url, policy, budget_cap_usd}`
- `GET /api/runs/{id}`, `GET /api/runs/{id}/events?after=N`
- `GET /api/runs/{id}/stream` Server-Sent Events, supports `Last-Event-ID` resume

## Project layout

```
upshift/
  agents/      Scout (more to come: Historian, Surgeon, Verifier, Reviewer, Scribe)
  api/         FastAPI app, SSE
  core/        events, SQLite event store, bus, budget, workspace preparation
  ecosystems/  Python manifest parsing and AST symbol-usage scanner
  llm/         model catalog, routing policy, Token Factory client
  sandbox/     executor contract, Docker backend (Nebius Serverless Jobs backend on Day 6)
  tools/       Tavily search/extract, PyPI client, tool registry, cache
  cli.py       python -m upshift ...
config/        models.yaml (IDs, prices), capabilities.json (probe output)
demo-repos/    pyshop-legacy: realistic outdated project used for demos and evals
scripts/       run_local, probe_models
tests/         mocked unit and API tests (real Docker test opt-in via UPSHIFT_DOCKER_TESTS=1)
docs/          BLUEPRINT.md and (soon) ARCHITECTURE, FEEDBACK, SUBMISSION
```

## Cost and safety

- Model IDs and prices live in `config/models.yaml`; nothing is hardcoded.
- Every call records tokens, cost and latency; runs stop at their budget cap.
- Project code never executes on the host; only inside ephemeral containers or Serverless Jobs.
- Web content fetched via Tavily is treated as data, never as instructions.
- No paid Nebius compute is created automatically.

## License

Apache 2.0. See [LICENSE](LICENSE).
