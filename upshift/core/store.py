"""SQLite-backed append-only event log plus run metadata. Thread-safe, zero ops."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from .events import Event, Run, RunStatus, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    agent TEXT NOT NULL,
    node_id TEXT,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


class EventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- runs -------------------------------------------------------------

    def create_run(self, run: Run) -> Run:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, created_at, status, data) VALUES (?, ?, ?, ?)",
                (run.id, run.created_at.isoformat(), run.status.value, run.model_dump_json()),
            )
            self._conn.commit()
        return run

    def update_run(self, run: Run) -> Run:
        run.updated_at = utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, data = ? WHERE id = ?",
                (run.status.value, run.model_dump_json(), run.id),
            )
            self._conn.commit()
        return run

    def set_status(self, run_id: str, status: RunStatus, **summary: Any) -> Run | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        run.status = status
        if summary:
            run.summary.update(summary)
        return self.update_run(run)

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run.model_validate_json(row["data"]) if row else None

    def list_runs(self, limit: int = 50) -> list[Run]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Run.model_validate_json(r["data"]) for r in rows]

    # ---- events -----------------------------------------------------------

    def append(self, event: Event) -> Event:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()
            stored = event.model_copy(update={"seq": int(row["next"])})
            self._conn.execute(
                "INSERT INTO events (run_id, seq, ts, type, agent, node_id, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    stored.run_id,
                    stored.seq,
                    stored.ts.isoformat(),
                    stored.type.value,
                    stored.agent,
                    stored.node_id,
                    stored.model_dump_json(),
                ),
            )
            self._conn.commit()
        return stored

    def events(self, run_id: str, after_seq: int = 0, limit: int | None = None) -> list[Event]:
        sql = "SELECT data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq"
        params: tuple[Any, ...] = (run_id, after_seq)
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Event.model_validate_json(r["data"]) for r in rows]

    def close(self) -> None:
        self._conn.close()
