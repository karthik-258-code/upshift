# flask-notes

A deliberately legacy notes API written against **Flask 2.0.3**, with **Werkzeug 2.0.3**,
**Jinja2 3.0.3**, **click 8.0.4** and **itsdangerous 2.0.1** pinned alongside it. It is a
demo target for Upshift.

Upgrading Flask to 3.x breaks it in two layers:

1. pip refuses the install because Flask 3 requires newer Werkzeug, Jinja2, click and
   itsdangerous, all pinned here (Upshift co-upgrades them from pip's conflict report).
2. Five APIs the code relies on were removed in Flask 2.3 / Werkzeug 3.0:
   `flask.escape`, `flask.json.JSONEncoder` (and `app.json_encoder`), the `JSON_SORT_KEYS`
   config key, `@app.before_first_request`, and `werkzeug.urls.url_quote`.

`pytest.ini` promotes deprecation warnings raised from the application and the tests to
errors, so a half-migration that still works "with warnings" does not pass.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q   # 9 passed
```
