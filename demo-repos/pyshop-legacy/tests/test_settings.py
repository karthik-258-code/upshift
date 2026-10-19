from pyshop.settings import get_settings


def test_settings_defaults(monkeypatch):
    for key in ("PYSHOP_DATABASE_URL", "PYSHOP_CATALOG_API_URL", "PYSHOP_REQUEST_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    settings = get_settings()
    assert settings.database_url == "sqlite://"
    assert settings.catalog_api_url.startswith("https://")
    assert settings.request_timeout == 5.0


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("PYSHOP_REQUEST_TIMEOUT", "2.5")
    monkeypatch.setenv("PYSHOP_CATALOG_API_URL", "https://staging.example.com")
    settings = get_settings()
    assert settings.request_timeout == 2.5
    assert settings.catalog_api_url == "https://staging.example.com"
