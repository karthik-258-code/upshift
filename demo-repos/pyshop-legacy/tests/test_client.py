import pytest
import requests

from pyshop.client import CatalogClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self.payload


def test_fetch_product(monkeypatch):
    calls = {}

    def fake_get(url, timeout):
        calls["url"] = url
        calls["timeout"] = timeout
        return FakeResponse({"sku": "AB-1", "name": "Widget"})

    monkeypatch.setattr(requests, "get", fake_get)
    client = CatalogClient("https://shop.example.com/api/", timeout=3)
    assert client.fetch_product("AB-1") == {"sku": "AB-1", "name": "Widget"}
    assert calls == {"url": "https://shop.example.com/api/products/AB-1", "timeout": 3}


def test_fetch_product_http_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse({}, status=404))
    with pytest.raises(requests.HTTPError):
        CatalogClient("https://shop.example.com").fetch_product("missing")
