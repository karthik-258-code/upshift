# Days 8 and 9: measure it, write it down

## Eval harness

`python -m upshift eval` (`upshift/evals.py`, `evals/cases.yaml`) runs each case through
the real pipeline without publishing merge requests and writes a JSON record plus a Markdown
table after every case, so a long eval can be aborted and still leave results. The table
compares the bare bump (tests passing after only the pin changed) with Upshift's result,
attempts, review verdict, cost from recorded `llm.call` events, and wall time.

### Main eval, 2 repeats per case (2026-10-23 01:41 UTC)

See [`evals/results/20261023-014137.json`](../../evals/results/20261023-014137.json)
(`latest.md` now holds the final eval of 2026-10-25): 7/8 landed. pydantic 5 and 9
attempts ($0.08, $0.23); SQLAlchemy 1 and 1 ($0.03 each); requests 0 and 0 ($0.001); Flask 3
attempts ($0.05) and one miss stuck at 8/9 after 12 attempts ($0.34).

### The miss, and the fix

Flask #2 reached 8/9 in three attempts and then spent nine attempts on one test, every edit
rolled back as "exactly the same failures". In surgical mode the model is told to make the
smallest possible edit; when the smallest edit is the wrong idea, it keeps making variations
of it. **Stuck escalation** (`surgeon.py`): after two consecutive no-effect rollbacks, the
prompt names the count, lifts the no-WRITE rule for the implicated module, and asks the
model to re-derive the implementation from what the failing test observes. Re-check of the
Flask case alone: [3/3](../../evals/results/flask-stuck-check/latest.md), 12, 9 and 9
attempts, $0.28, $0.16, $0.14. Reliable, and visibly more expensive than the easy cases;
both facts are in the README.

### Restraint cases (2026-10-23 06:09 UTC)

[`evals/results/new-cases/latest.md`](../../evals/results/new-cases/latest.md):
python-dateutil 2.8 -> 3.0 and pytest 8 -> 9, two repeats each, all landed with **zero code
edits** at $0.003 to $0.017. The verifier found the suite green after the bump, the Surgeon
made no attempt, the Reviewer was skipped as manifest-only. Not editing is a result too.

## Other work

- Auditor prompt: module-level API lists say nothing about class attributes (it had agreed
  that `app.errorhandler` does not exist in Flask 3).
- Eval table rendered on the Launch screen of Mission Control, live and on the Pages site.
- `docs/ARCHITECTURE.md`, `docs/FEEDBACK.md`, `docs/SUBMISSION.md` (checklist, video
  script, shot list, freeze plan); README rewritten with the results table copied verbatim
  from `latest.md`.
- `templates/upshift.yml`: reusable CI template (spec inputs, Docker-in-Docker sandbox,
  opens the MR in the calling project); a manual dogfood job in this repo's pipeline proves
  it parses.
- Clean-clone check: README quick start followed in a fresh folder; venv, UI build, 116
  tests, `scout` table all good.

## Numbers to date (all recorded, nothing estimated)

| | |
| --- | --- |
| Distinct upgrades landed | 6 (pydantic, SQLAlchemy, requests, Flask, python-dateutil, pytest) |
| Eval runs | 16, 15 landed |
| Typical cost per landed upgrade | $0.001 (no edits) to $0.09 (pydantic); hard cases up to $0.34 |
| Merge requests opened by the agent on GitLab | pyshop-legacy !6, !7; flask-notes !1 (not public; re-run publicly on 2026-10-25 as notorandyt/pyshop-legacy !1, !2 and notorandyt/flask-notes !1) |
