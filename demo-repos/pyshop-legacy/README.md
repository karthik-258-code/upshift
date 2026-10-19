# pyshop-legacy

A small catalogue and ordering library for a fictional shop. It was written in 2023 against
pydantic 1.x and SQLAlchemy 1.4 and has not been touched since. Seventeen tests, all green on
the pinned versions in `requirements.txt`.

The test suite treats deprecation warnings raised from this package's own code as errors
(see `pytest.ini`), which is a common setting in well-maintained projects and is exactly
what turns a "soft" upgrade into a red pipeline.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```
