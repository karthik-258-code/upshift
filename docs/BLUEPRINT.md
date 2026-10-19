# Upshift: Blueprint

> Autonomous dependency-upgrade agent that reads the real changelogs, rewrites the code that breaks, proves it in an isolated sandbox, and opens a merge request with evidence.
>
> Nebius x NVIDIA Global AI Hackathon. Track: **Coding and Agentic Engineering**. Also targeting **Best Use of Tavily** and **Most Valuable Feedback**.

This document is the single source of truth for scope, architecture, UX, timeline and submission. `research-brief.md` holds the verified platform facts; this file holds the decisions.

---

## 0. Hard constraints (from the brief)

| Item | Value |
| --- | --- |
| Submission deadline | **Fri 30 Oct 2026, 10:00 PDT** (22:30 IST). Internal freeze: 29 Oct 18:00 IST |
| Judging window | 1 Dec 09:00 PT to 15 Dec 12:00 PT. Hosted demo must stay up until then |
| Judging weights | Tech implementation 25%, Design 25%, Potential impact 25%, Quality of idea 25% (after pass/fail viability) |
| Platform requirement | Runtime calls to Token Factory with an NVIDIA model satisfy the core requirement. Serverless Jobs/Endpoints strengthen it |
| Credits | Finite promotional credits. **No paid resource is created without explicit approval** |
| Dev machine | Windows laptop, Docker Desktop installed. All scripts ship in PowerShell and bash |
| Repo rules | Public, OSS licence at root (Apache 2.0), README with setup, explicit Nemotron/Token Factory/Nebius usage section, feedback doc |

Design is a full 25% of the score. That is why the UI is a first-class deliverable and not an afterthought.

---

## 1. The product in one paragraph

Point Upshift at a repository. It discovers outdated dependencies, maps which symbols of each package the codebase actually uses, pulls the real changelogs and migration guides from the web (Tavily), isolates the breaking changes that matter for *this* codebase, rewrites the affected code, installs and runs the test suite plus type checks in an ephemeral sandbox (Docker locally, Nebius Serverless Jobs in the cloud), escalates to a stronger model only when a patch fails, has Nemotron Ultra adversarially review the final patch set, and opens a merge request where every edit is pinned to the changelog line that justified it. Dependabot and Renovate bump versions and leave a red pipeline. Upshift finishes the job.

---

## 2. Why this wins (mapped to the rubric)

- **Quality of idea**: universally felt pain, clearly unsolved by existing bots, narrow enough to be judged in three minutes.
- **Technological implementation**: multi-agent system with a visible three-tier Nemotron routing policy, a replayable event-sourced run graph, isolated execution on Nebius Serverless Jobs, and a published evaluation table on real OSS repos.
- **Design**: a mission-control UI built around the agent's actual decisions (graph, evidence, diffs, cost) rather than a chat box.
- **Potential impact**: ships as a GitLab CI component that runs on a schedule, so the demo is also the distribution channel.
- **Tavily**: load-bearing. Changelog discovery and extraction is the Historian agent's core tool, with domain allowlists and credit accounting shown in the UI.

---

## 3. Architecture

### 3.1 Agent roster

| Agent | Job | Default model | Escalation |
| --- | --- | --- | --- |
| **Supervisor** | Plans the run, fans out per-dependency workers, enforces budget, owns the escalation ladder | Nano | none |
| **Scout** | Parses manifests and lockfiles, queries PyPI/npm JSON APIs for latest versions, AST-scans the repo to build a *symbol usage map* per package, ranks upgrades by risk | Nano (ranking only; parsing is deterministic code) | none |
| **Historian** | Tavily search + extract on release notes, CHANGELOG, migration guides. Classifies entries as breaking / deprecation / behavioural / noise and intersects them with the symbol usage map to produce an **Impact Dossier** | Nemotron 3.5 Lightning (1M context, Nano price) with Nano fallback | Super if classification confidence is low |
| **Surgeon** | Produces patches for files flagged in the dossier, using `read_file`, `search_code`, `apply_patch` tools. Receives test failures back and iterates | Super | Ultra on third attempt with whole-repo context |
| **Verifier** | Runs install, pytest, mypy/pyright, optional mutation tests (mutmut) on touched functions, in the sandbox. Emits a structured **Confidence Score** | deterministic + Nano for failure triage | none |
| **Reviewer** | Adversarial review of the final patch set: hidden behaviour changes, security regressions, missed call sites | Ultra | none |
| **Scribe** | Writes the MR description and Migration Report with changelog quotes and URLs per edit; opens the MR via GitLab API | Super | none |

All agents use the OpenAI-compatible Token Factory API with native tool calling. No heavy agent framework: a small, readable loop we fully understand is a better story for judges and easier to debug in 11 days.

### 3.2 Model routing policy

```
ModelRouter(task_class, attempt, budget_remaining, required_caps)
  classify / rank / triage            -> Nano          (nvidia/nvidia-nemotron-3-nano-30b-a3b)
  long-document ingestion (>100K tok) -> Lightning     (nvidia/Nemotron-3_5-Lightning), fallback Nano
  code generation, attempt 1-2        -> Super         (nvidia/nemotron-3-super-120b-a12b)
  code generation, attempt 3          -> Ultra         (nvidia/Nemotron-3-Ultra-550b-a55b)
  final adversarial review            -> Ultra
  any call when budget < 10% left     -> degrade one tier or stop with a clear event
```

Rules:
- Model IDs, prices and capability flags live in `config/models.yaml`, never in code.
- Day-0 capability probe (`scripts/probe_models.py`) tests tool calling, parallel tool calls, `json_object`, and JSON-Schema per model and writes `config/capabilities.json`. The router refuses to send a capability a model failed in the probe.
- Every call records model, tokens in/out, latency, cost, and the agent/node that made it. This feeds the live cost meter.
- 429/5xx: bounded retries, exponential backoff with jitter, honour `Retry-After`, per-call timeout.

### 3.3 Run graph (event-sourced core)

Everything the system does is an append-only event: `run.started`, `dep.discovered`, `evidence.fetched`, `dossier.ready`, `patch.proposed`, `sandbox.started`, `test.result`, `escalation`, `review.verdict`, `mr.opened`, `budget.warning`, `run.finished`.

- Stored in SQLite (`runs.db`) via SQLModel. Single container, zero ops.
- Projected into a graph (`Run -> Dependency -> Evidence/Patch/Verification -> Decision`) served to the UI.
- Streamed over SSE as they happen, and replayable later with a time scrubber. The demo video is literally a replay.

### 3.4 Sandbox executor

Interface `Executor.run(workspace_tar, command, timeout) -> {exit_code, stdout, stderr, artifacts}` with two backends:

- `DockerExecutor` (local dev and CI): ephemeral `python:3.12-slim` container, no network after install step, CPU/memory limits, read-only root, workspace mounted as tmpfs copy.
- `NebiusJobExecutor` (cloud): `nebius ai job create` with a pinned image, `--env`, explicit `--timeout`, logs pulled via `nebius ai job logs`. The workload writes `result.json` with exit status because the brief warns the exit-code field is not guaranteed. CPU platform preferred; GPU never requested for test runs.

Project code **never** executes on the host or in the API process.

### 3.5 Safety and trust

- Extracted web pages are data, not instructions. Historian output is a strict JSON schema; free text from pages never reaches the Surgeon prompt except as quoted evidence fields.
- Secrets only via env; redaction filter on all logs and events.
- Patch allowlist: Surgeon may only touch files the dossier flagged plus manifests. Any attempt outside is rejected and logged as an event (visible in UI).
- Per-run budget cap (default $2.00) and per-run wall-clock cap.

### 3.6 Tech stack

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | Python 3.12, FastAPI, asyncio, SQLModel/SQLite, `openai` SDK, `tavily-python`, `httpx` | Brief-aligned, fast to ship, async fan-out |
| Frontend | React 18 + Vite + TypeScript, Tailwind, React Flow (graph), Monaco diff editor, Radix primitives, cmdk palette | Real UI with no bespoke rendering risk; built to static files and served by FastAPI so there is one container |
| Streaming | Server-Sent Events | Simple, proxy-friendly, enough for one-way event flow |
| Packaging | One Dockerfile, multi-stage (node build -> python runtime) | One image for local, CI and Serverless Endpoint |
| CI | GitLab CI: lint (ruff), type (mypy), tests (pytest, mocked APIs), frontend build, image build | Deployment is manual and credential-gated |
| Distribution | `templates/upshift.gitlab-ci.yml` CI component to run Upshift on a schedule against any repo | Impact story |

---

## 4. UI/UX: Mission Control

Dark theme, dense but calm, monospace for identifiers, keyboard-first (`Cmd/Ctrl+K` palette). No chat box. The interface is organised around the questions a reviewer asks.

**Screens**

1. **Launch**: paste a repo URL (or pick a demo repo), choose policy (conservative / balanced / aggressive), set budget cap, see a pre-flight estimate (deps found, likely cost range). One primary button.
2. **Run view** (the hero screen), four regions:
   - **Dependency graph** (left): nodes per package, coloured by state (queued, researching, patching, verifying, passed, failed, skipped). Edges show which files import which packages. Nodes pulse while a worker is active. Click a node to focus the other panes.
   - **Agent timeline** (centre): streaming, per-worker lanes. Each event is a compact card: what was decided, which model, tokens, cost, elapsed. Escalations are visually distinct.
   - **Evidence and diff** (right): tabs for *Dossier* (classified changelog entries with source URL, each tagged with the symbols in this repo that it affects) and *Diff* (Monaco side-by-side; hovering an edited hunk highlights the changelog line that justified it).
   - **Cost and confidence strip** (bottom): live spend split by Nano/Lightning/Super/Ultra, Tavily credits used, time, and the Verifier confidence score building up (tests, types, mutation, review).
3. **Verdict**: summary of what changed, confidence breakdown, Reviewer findings, and a single **Open merge request** action (or auto-open if policy allows). Link to the MR.
4. **Replay**: any past run with a scrubber; the UI reconstructs state from events. Used for the video and for judges without credentials.
5. **Evals**: the published benchmark table (repo, deps upgraded, result, cost, time) rendered from `evals/results.json`.

**Details that signal craft**: empty states that explain what will happen, skeleton loading, reduced-motion support, copy-to-clipboard on every ID and URL, status in the browser tab title, a public read-only mode for judges, and a `?demo=1` entry that loads a completed replay instantly.

---

## 5. Repository layout

```
.
├── README.md                    # product README with usage, architecture, Nemotron/Nebius/Tavily section, evals
├── LICENSE                      # Apache 2.0
├── docs/
│   ├── BLUEPRINT.md             # this file
│   ├── ARCHITECTURE.md          # diagrams, event schema, routing policy
│   ├── FEEDBACK.md              # Nebius / NVIDIA / Tavily feedback for the prize
│   └── SUBMISSION.md            # form answers, video script, checklist
├── research-brief.md
├── pyproject.toml  .env.example  .gitignore  Dockerfile  .gitlab-ci.yml
├── config/
│   ├── models.yaml              # IDs, prices, context, allowed tasks
│   └── capabilities.json        # written by probe script
├── upshift/                     # backend package
│   ├── api/                     # FastAPI app, SSE, routes
│   ├── core/                    # events, run graph, store, budget
│   ├── llm/                     # token factory client, router, retries, cost
│   ├── agents/                  # supervisor, scout, historian, surgeon, verifier, reviewer, scribe
│   ├── tools/                   # tavily_search, tavily_extract, registry (pypi/npm), fs, patch, git, gitlab_mr
│   ├── sandbox/                 # executor interface, docker, nebius_job
│   └── ecosystems/              # python (first), npm (stretch)
├── web/                         # React app
├── templates/upshift.gitlab-ci.yml
├── demo-repos/
│   └── pyshop-legacy/           # pydantic 1 + SQLAlchemy 1.3 + Flask 1 + requests, with tests
├── evals/                       # harness, repo list, results.json
├── scripts/                     # probe_models.py, run_local.ps1/.sh, deploy_endpoint.ps1/.sh, teardown.sh
└── tests/                       # unit + integration with recorded fixtures
```

Deviation from the brief's suggested layout: richer than `app/agent.py` because there are seven agents, two executors and a frontend. No abstraction exists without a second concrete user.

---

## 6. Demo repository design

`demo-repos/pyshop-legacy` is a small but believable Flask service with ~25 tests, pinned to:

- `pydantic==1.10.x` -> 2.x: `.dict()`/`.parse_obj()`/`@validator`/`class Config` all break. The best-documented migration guide on the internet, perfect for the Historian.
- `sqlalchemy==1.3.x` -> 2.x: `Query` to `select()`, `engine.execute` removal.
- `requests` and `flask` minor bumps that are safe no-ops (shows the agent correctly does nothing).
- One dependency with a changelog that *mentions* a breaking change in a symbol we do not use (shows the usage map filtering noise).

The demo flow: red pipeline -> run Upshift -> graph lights up -> evidence appears -> patches -> green -> MR opened. Under three minutes on replay.

---

## 7. Evaluation harness (published numbers)

`evals/run.py` takes a list of 10 to 15 public Python repos pinned to older dependency sets, runs Upshift under a fixed budget, and records: outcome (upgraded and green / partially upgraded / correctly skipped / failed), cost per tier, wall time, escalations. Results land in `evals/results.json` and render in the README and the Evals screen. Target claim: a concrete success rate on real repos, honestly reported including failures.

---

## 8. Deployment and cost plan

- **Local**: `scripts/run_local.ps1` starts the API with Docker executor. Zero Nebius compute cost; only Token Factory tokens.
- **Cloud demo**: one Serverless Endpoint (`cpu-d3`, smallest viable preset), token-auth plus a public read-only route for the replay and evals screens. Image pushed to a registry Nebius can pull from. Created once, after explicit approval, on Day 6.
- **Sandbox**: Serverless Jobs on CPU presets, only for runs started from the hosted demo; local runs use Docker.
- **Availability until 15 Dec**: verify endpoint billing behaviour and credit burn rate on Day 6. Decide between keeping the endpoint warm or a scale-to-zero style configuration if available. Keep a `teardown.sh` to kill test resources immediately.
- **Budget guardrails**: per-run cap, global daily cap in config, demo mode uses recorded Tavily evidence to save credits (model calls stay live so nothing is faked).

---

## 9. Timeline (19 Oct to 30 Oct)

| Day | Date | Deliverable | Exit criterion |
| --- | --- | --- | --- |
| 0 | 19 Oct | Scaffold, licence, config, Token Factory client, router, retry layer, `probe_models.py`, Tavily tools, event store, SSE, UI shell | `pytest` green with mocked APIs; probe results committed |
| 1 | 20 Oct | Docker executor, Scout (manifest parse, registry lookups, symbol usage map), demo repo | Scout produces a correct dependency graph for `pyshop-legacy` |
| 2 | 21 Oct | Historian end to end: search, extract, classify, intersect with usage map -> Dossier | Dossier for pydantic 1->2 lists the exact symbols used in the demo repo with source URLs |
| 3 | 22 Oct | Surgeon + Verifier loop with escalation ladder | Demo repo goes from red to green autonomously at least once |
| 4 | 23 Oct | Reviewer (Ultra), Scribe, GitLab MR creation, budget enforcement | MR opened on demo repo with evidence-linked description |
| 5 | 24 Oct | UI: graph, timeline lanes, evidence/diff panes, cost strip | Live run watchable end to end in the browser |
| 6 | 25 Oct | Nebius: Job executor, image push, Serverless Endpoint (approval gate) | Hosted URL works; cost per run measured and recorded |
| 7 | 26 Oct | Replay, Verdict, Evals screens; UX polish; keyboard palette; public read-only mode | Judge can use the hosted demo with no credentials |
| 8 | 27 Oct | Eval harness over 10+ repos; fix top failure modes; npm support only if ahead of schedule | `evals/results.json` populated |
| 9 | 28 Oct | README, ARCHITECTURE, FEEDBACK, CI component, hardening, teardown of stray resources | Fresh clone follows README successfully on another machine |
| 10 | 29 Oct | Demo video (record replay + live snippet), SUBMISSION.md, final hosted check. **Freeze 18:00 IST** | Video uploaded, all form fields drafted |
| 11 | 30 Oct | Submit before 10:00 PDT / 22:30 IST | Confirmation received |

Every day ends with a tagged commit so there is always a submittable state.

---

## 10. Risk register

| Risk | Mitigation |
| --- | --- |
| A Nemotron model does not honour JSON mode or tool calls as expected | Day-0 probe; router only uses verified capabilities; structured outputs validated with Pydantic and repaired by a Nano retry |
| Serverless Jobs has no cheap CPU preset or slow cold starts | Docker executor is the default everywhere; Nebius Jobs is additive. Verify presets before Day 6 |
| Endpoint cost over two months of judging | Smallest preset, measure burn on Day 6, decide keep-warm vs scale-down; replay mode needs almost no compute |
| Tavily free credits exhausted during evals | Evidence cache keyed by (package, from, to); demo mode replays cached evidence; usage shown in UI |
| Patch loop thrashes and burns budget | Hard attempt cap (3), per-run cost cap, escalation only on new failure signatures |
| Scope creep (npm, more languages) | Python only until Day 8 exit criteria are met |
| Windows path and line-ending issues | All execution inside containers; `.gitattributes` enforces LF |

---

## 11. Submission checklist

- [ ] Public repo, Apache 2.0 `LICENSE` visible at root
- [ ] README: what, why, how; setup; env vars; architecture; explicit section on Nemotron models used per agent, Token Factory, Serverless Jobs/Endpoints, Tavily; eval table
- [ ] Hosted demo URL (Serverless Endpoint) with judge instructions and read-only mode, verified to stay up through 15 Dec
- [ ] Demo video under 3 minutes, public on YouTube
- [ ] Track selected: Coding and Agentic Engineering
- [ ] `docs/FEEDBACK.md` on Token Factory, Serverless, Nemotron, Tavily (specific, reproducible observations)
- [ ] Project description (long and short form) in `docs/SUBMISSION.md`
- [ ] City selection if a Builders & Brews event was attended

---

## 12. Open items requiring the team

1. Nebius CLI installed and authenticated; project ID and region confirmed.
2. Tavily API key created (free tier) and stored locally as `TAVILY_API_KEY`.
3. A GitLab personal access token with `api` scope for opening MRs on the demo repo.
4. Node 20 and Python 3.12 installed on the dev laptop.
5. Explicit approval before Day 6 to create the first paid Nebius resources (one endpoint, CPU job test runs).
6. Who records the video and who owns the submission form.
