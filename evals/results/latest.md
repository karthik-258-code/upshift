# Upshift eval results

Generated 2026-10-23 01:41 UTC. Bare bump = tests passing after only the version pin changed (what a dependabot-style tool leaves you with). Upshift = after the agent's edits. Costs are Nebius Token Factory usage per case.

| Case | Upgrade | Bare bump | Upshift | Attempts | Review | Cost | Time | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pyshop-pydantic | pydantic 1.10.13 -> 2.14.1 | 0 (import error) | **17/17** | 5 | approve | $0.081 | 2m36s | pass |
| pyshop-pydantic #2 | pydantic 1.10.13 -> 2.14.1 | 0 (import error) | **17/17** | 9 | approve | $0.225 | 5m19s | pass |
| pyshop-sqlalchemy | sqlalchemy 1.4.49 -> 2.1.6 | 15/17 | **17/17** | 1 | approve | $0.031 | 0m59s | pass |
| pyshop-sqlalchemy #2 | sqlalchemy 1.4.49 -> 2.1.6 | 15/17 | **17/17** | 1 | approve | $0.033 | 1m12s | pass |
| pyshop-requests | requests 2.28.2 -> 2.34.2 | 17/17 | **17/17** | 0 | skipped | $0.001 | 0m18s | pass |
| pyshop-requests #2 | requests 2.28.2 -> 2.34.2 | 17/17 | **17/17** | 0 | skipped | $0.001 | 0m19s | pass |
| flask-notes-flask | flask 2.0.3 -> 3.1.3 | 0 (import error) | **9/9** | 3 | approve | $0.050 | 2m03s | pass |
| flask-notes-flask #2 | flask 2.0.3 -> 3.1.3 | 0 (import error) | **8/9** | 12 | - | $0.335 | 6m59s | FAIL (tests still failing after 12 attempt(s)) |

**7/8 upgrades landed** (87%). Median attempts on success: 1. Mean cost: $0.095. Total: $0.76 in 20 minutes.
