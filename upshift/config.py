"""Runtime settings. Everything comes from environment variables or a local .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.us-central1.nebius.com/v1/"
    tavily_api_key: str = ""
    gitlab_token: str = ""
    gitlab_url: str = "https://gitlab.com"

    models_file: Path = ROOT / "config" / "models.yaml"
    capabilities_file: Path = ROOT / "config" / "capabilities.json"
    data_dir: Path = ROOT / ".data"
    cache_dir: Path = ROOT / ".cache"

    default_budget_usd: float = 2.0
    daily_budget_usd: float = 25.0
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 4
    tavily_cache_ttl_hours: float = 72.0
    executor: str = "docker"
    log_level: str = "INFO"

    @property
    def runs_db(self) -> Path:
        return self.data_dir / "runs.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
