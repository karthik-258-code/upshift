from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from upshift.api.main import create_app
from upshift.config import Settings
from upshift.core.store import EventStore


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        nebius_api_key="",
        tavily_api_key="",
        data_dir=tmp_path,
        cache_dir=tmp_path,
        # keep tests independent of a locally generated config/capabilities.json
        capabilities_file=tmp_path / "no-capabilities.json",
    )
    app = create_app(settings, store=EventStore(), background=False)
    return TestClient(app)


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["nebius_configured"] is False
    assert body["capabilities_probed"] is False


def test_models_endpoint(client: TestClient) -> None:
    body = client.get("/api/models").json()
    keys = {m["key"] for m in body["models"]}
    assert {"lightning", "nano", "super", "ultra"} <= keys
    super_ = next(m for m in body["models"] if m["key"] == "super")
    assert super_["input_price_per_m"] == 0.30 and "json_schema" in super_["capabilities"]


def test_create_run_records_lifecycle(client: TestClient) -> None:
    response = client.post("/api/runs", json={"repo_url": "https://gitlab.com/example/repo"})
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "succeeded" and run["budget_cap_usd"] == 2.0

    events = client.get(f"/api/runs/{run['id']}/events").json()["events"]
    types = [e["type"] for e in events]
    assert types[0] == "run.started" and types[-1] == "run.finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    listed = client.get("/api/runs").json()["runs"]
    assert listed[0]["id"] == run["id"]


def test_sse_stream_replays_run(client: TestClient) -> None:
    run = client.post("/api/runs", json={"repo_url": "https://gitlab.com/example/repo"}).json()
    with client.stream("GET", f"/api/runs/{run['id']}/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert "event: run.started" in body and "event: run.finished" in body
    assert body.startswith("id: 1\n")


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/events").status_code == 404
    assert client.get("/api/runs/nope/stream").status_code == 404


def test_validation(client: TestClient) -> None:
    assert client.post("/api/runs", json={"repo_url": ""}).status_code == 422
    assert client.post("/api/runs", json={"repo_url": "x", "budget_cap_usd": -1}).status_code == 422
    assert client.post("/api/runs", json={"repo_url": "x", "policy": "yolo"}).status_code == 422
