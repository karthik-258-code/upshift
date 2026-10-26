# Days 6 and 7: hosting at $0, a second migration, and the long tail of convergence

## Decisions

- **No paid compute.** The Nebius project has no credits and the only VM platforms offered in
  the region are GPU ($0.89/h and up), so a live hosted instance would cost ~$1,200 through
  judging. The hosted demo is instead **Mission Control in recorded-replay mode on GitLab
  Pages**: `python -m upshift export` writes runs and their event logs to
  `web/public/runs/`, the UI detects the missing API and reads them, the `pages` CI job
  publishes `web/dist`. Nothing is simulated: every event, diff and review on the site is
  from a real run.
- **Nebius Serverless AI Jobs executor** (`upshift/sandbox/nebius_job.py`): the same
  `Executor` contract as Docker, built strictly on the documented REST surface (jobs CRUD,
  injected files, environment variables) and the documented service-account JWT exchange.
  Input and output travel through object storage via presigned URLs, because injected files
  are capped at 64 KiB and output otherwise only reaches logs. `available()` is False unless
  `NEBIUS_ACCEPT_CHARGES=1`. Unit-tested against the documented shapes; not exercised live.

## Second demo repo: `demo-repos/flask-notes`

Flask 2.0.3 with Werkzeug, Jinja2, click and itsdangerous pinned alongside. Upgrading Flask
to 3.1 fails twice over: pip refuses the install (four conflicting pins), then five removed
APIs (`flask.escape`, `flask.json.JSONEncoder` and `app.json_encoder`, `JSON_SORT_KEYS`,
`@app.before_first_request`, `werkzeug.urls.url_quote`) break import and behaviour.

| Run | Result | Cost | What it taught |
| --- | --- | --- | --- |
| 1 | install failed, 0 attempts | $0.00 | pip's quiet-mode summary names one conflict at a time |
| 2 | 12 attempts, all "rolled back: same failures" | $0.15 | pytest exit 4 (conftest import error) reported zero failures |
| 3 | 12 attempts, breakthrough rolled back | $0.25 | "0 passing, 9 failing" ranked below "0 passing, 1 failing" |
| 4 | 12 attempts, 8 blind guesses | $0.30 | all tests returned 500; the traceback lived in captured logs |
| 5 | 8/9 passing, stuck on one | $0.28 | research query templated from an assertion is useless |
| 6 | **9/9 in 3 attempts, approve** | $0.05 | |
| 7 | **9/9, approve, MR !1** (in a demo project that is not public; the public re-run is [notorandyt/flask-notes!1](https://gitlab.com/notorandyt/flask-notes/-/merge_requests/1)), Auditor contradicted one false blocker | $0.12 | |

Each failure became a general mechanism in `upshift/agents/surgeon.py` or `verifier.py`:

1. **Co-upgrade on resolver conflict.** pip's `Cannot install -r requirements.txt (line 1) and
   click==8.0.4` (quiet mode) and `flask 3.1.2 depends on Werkzeug>=3.1` (verbose) are both
   parsed; packages pinned in the same file are bumped to their latest release, up to six
   rounds, each a five-second install with no model call.
2. **Startup failures are failures.** A non-zero pytest exit with nothing collected (conftest
   import error, usage error) becomes a synthesized `pytest::startup` failure carrying the
   traceback, so the Surgeon has something to reason about and the referenced files become
   editable.
3. **Progress ordering**: more tests passing, then more tests *executed*, then fewer
   failures. Going from nothing collected to nine tests that all fail is progress; deleting
   tests is a regression.
4. **Captured output on failures.** `-o junit_logging=all` plus parsing `system-out` /
   `system-err` per test, so a framework that logs the exception and answers 500 still shows
   the model the real traceback. The demo's `conftest.py` also sets `TESTING = True`, as real
   Flask suites do.
5. **Introspect the module in the ImportError**, not only the upgraded package: `cannot
   import name 'url_quote' from 'werkzeug.urls'` adds `werkzeug.urls` to the API listing the
   model sees.
6. **Research planner.** When stalled, Nemotron 3.5 Lightning writes the web queries (and a
   one-line hypothesis) from the failing test, its traceback and the last edits, instead of a
   template built from the error line.

## Reviewer and Auditor, live

In run 7 the Reviewer raised a blocker claiming the custom JSON provider's `response()`
returned the wrong type. The Auditor checked it against the diff, the installed API and the
full post-edit file and marked it **contradicted**; it became a minor note and the MR was
published ready for review rather than as a Draft. Code-quality note for the honest record:
the approved code works but hand-rolls a `JSONProvider` where a Flask maintainer would
subclass `DefaultJSONProvider`, and it leans on `werkzeug.urls.quote`, a re-export rather
than public API. Green and approved is not the same as idiomatic; that gap is what the eval
harness on Day 8 should measure.
