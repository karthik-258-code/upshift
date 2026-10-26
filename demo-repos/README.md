# Demo repositories

Small, realistic codebases pinned to outdated dependencies. Upshift runs against these in the
demo and in the evaluation harness. Each one is designed so that its test suite is green on the
pinned versions and red after a naive version bump, for documented, real-world reasons.

| Repo | Stack | What breaks on upgrade |
| --- | --- | --- |
| `pyshop-legacy` | pydantic 1.10, SQLAlchemy 1.4, requests 2.28, python-dateutil 2.8 | pydantic 1 to 2: `BaseSettings` moved to its own package, `__root__` models removed, `root_validator` needs `skip_on_failure`, `Field(const=True)` removed, `.dict()`/`.parse_obj()`/`__fields__`/`Config` deprecations. SQLAlchemy 1.4 to 2.0: `engine.execute()` removed, raw SQL strings need `text()`, list-style `select([...])` removed, `declarative_base` moved. requests and dateutil: safe, no-op upgrades |
| `flask-notes` | Flask 2.0.3 with Werkzeug, Jinja2, click and itsdangerous pinned | pip refuses the install until the four pins are co-upgraded; then `flask.escape`, `flask.json.JSONEncoder` / `app.json_encoder`, `JSON_SORT_KEYS`, `@app.before_first_request` and `werkzeug.urls.url_quote` are gone (removed in Flask 2.3 / Werkzeug 3.0) |

Run a demo repo's tests the way the Verifier does:

```bash
cd demo-repos/pyshop-legacy
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```
