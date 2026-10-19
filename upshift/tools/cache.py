"""Tiny on-disk JSON cache with TTL. Used to avoid re-spending Tavily credits."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, directory: str | Path, ttl_hours: float = 72.0) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_hours * 3600

    @staticmethod
    def key(namespace: str, payload: Any) -> str:
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(f"{namespace}:{blob}".encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def set(self, key: str, value: Any) -> None:
        path = self.directory / f"{key}.json"
        path.write_text(json.dumps(value, default=str), encoding="utf-8")
