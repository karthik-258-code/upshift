"""HTTP client for the upstream catalogue API."""

from __future__ import annotations

import requests


class CatalogClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_product(self, sku: str) -> dict:
        response = requests.get(f"{self.base_url}/products/{sku}", timeout=self.timeout)
        response.raise_for_status()
        return response.json()
