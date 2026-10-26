# Day 5 log: Mission Control, and making the Surgeon converge

All runs below are the same input: `demo-repos/pyshop-legacy`, policy `balanced`, one package,
Docker sandbox, Nemotron Ultra for code edits, Lightning for classification, target project
`pyshop-legacy` on GitLab. Costs are Token Factory usage per run.

## Results on the day

| # | Package | Launched from | Attempts | Result | Cost | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | pydantic | CLI | 7 | green, review approve, MR !2 | $0.12 | before the fixes |
| 2 | sqlalchemy | CLI | 2 | green, approve, MR !3 | $0.04 | |
| 3 | pydantic | browser | 3 | green, approve, MR !4 | $0.04 | first run launched from Mission Control |
| 4 | pydantic | browser | 12 | **failed** | $0.64 | 9 consecutive runaway outputs, identical prompt each time |
| 5 | pydantic | browser | 11 | **failed** | $0.57 | circled import errors, then analysis-only responses |
| 6 | pydantic | browser | 7 | green, 2 false blockers, Draft MR !5 | $0.15 | after the retry/dead-end fixes |
| 7 | pydantic | browser | 6 | green, MR !6 | $0.12 | |
| 8 | sqlalchemy | browser | 2 | green, approve, no findings, MR !7 | $0.03 | |

MRs !1 to !5 were closed: the demo project's `main` had only a README until mid-day (the
baseline push had never landed), so their diffs showed every file as new. !6 and !7 are the
showcase. (That demo project is not public; the showcase was re-run into public projects on
2026-10-25, see the README.)

## What the two failures taught, and what changed

**Failure 4: a retry with the same input is a coin flip with the same coin.** After one
truncated response the loop re-sent a byte-identical prompt at temperature 0, and Ultra
reproduced the same degenerate, repeating output nine times (`Annotated, Annotated as
AnnotatedType, Annotated as AnnotatedAlias...`). Fixes in `upshift/agents/surgeon.py`:

- after an attempt that applied nothing, the next call gets a higher temperature, a
  `RETRY NOTICE` naming the failure mode, and alternates out of deep-reasoning mode;
- the output cap is sized to the editable files (`output_budget`), so a runaway ends sooner
  and costs a third as much;
- four consecutive no-op attempts end the upgrade with a clear error (`NOOP_LIMIT`).

**Failure 5: circling looks like progress.** Five full rewrites of `models.py` each produced
a *new* import-time error, so the rollback logic (which keys on error signatures) kept them
all. Then the model answered with analysis and no edit blocks three times. Fixes:

- **dead-end memory**: error lines that recur across attempts are listed in the prompt as
  approaches that do not work on this version;
- two or more consecutive collection-error verifications count as *stalled*, which triggers
  deep mode and targeted web research;
- **nudge for blocks**: a response with analysis but no blocks gets one follow-up turn in the
  same conversation asking for the blocks only, instead of burning an attempt and a sandbox run.

Runs 6, 7 and 8 all converged after these changes.

**Run 6: the Reviewer asserted, nobody checked.** Two blockers were false (an `after`
validator returning `self` is correct in pydantic 2; the "removed" field was still there)
while the real regression at the same lines (a default URL and a validation bound changed
for no documented reason) went unnamed. Fix in `upshift/agents/reviewer.py`: an **Auditor**
step fact-checks every blocker, and every major that makes a factual claim, in a fresh
context against the diff, the installed API surface and the full post-edit files. Outcomes:
`supported`, `contradicted` (becomes a minor note), `unverifiable` (major), or `revised`
(restated precisely). The Reviewer itself now also sees the full post-edit files, not only
the hunks, which is where the "missing field" claims came from.

## Mission Control

`web/` is a Vite + React + TypeScript + Tailwind app with no runtime dependencies beyond
React. It consumes `/api/runs`, `/api/runs/{id}` and the SSE stream, reduces the event log
into UI state in `web/src/state.ts` (pure and deterministic, so the replay scrubber rebuilds
any past state by slicing the event list), and is served by FastAPI from `web/dist`. The
Dockerfile builds it in a Node stage; CI builds it as an artifact.
