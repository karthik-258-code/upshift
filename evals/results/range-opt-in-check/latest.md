# Upshift eval results

Generated 2026-10-25 10:08 UTC. Bare bump = tests passing after only the version pin changed (what a dependabot-style tool leaves you with). Upshift = after the agent's edits. Costs are Nebius Token Factory usage per case.

| Case | Upgrade | Bare bump | Upshift | Attempts | Review | Cost | Time | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flask-notes-flask | flask 2.0.3 -> 3.1.3 | 0 (import error) | **8/9** | 12 | - | $0.193 | 6m26s | FAIL (tests still failing after 12 attempt(s)) |
| flask-notes-flask #2 | flask 2.0.3 -> 3.1.3 | 0 (import error) | **9/9** | 2 | approve | $0.027 | 1m58s | pass |

**1/2 upgrades landed** (50%). Median attempts on success: 2. Mean cost: $0.110. Total: $0.22 in 8 minutes.
