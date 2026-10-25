# Upshift eval results

Generated 2026-10-25 11:48 UTC. Bare bump = tests passing after only the version pin changed (what a dependabot-style tool leaves you with). Upshift = after the agent's edits. Costs are Nebius Token Factory usage per case.

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

**17/18 upgrades landed** (94%). Median attempts on success: 0. Mean cost: $0.071. Total: $1.28 in 40 minutes.
