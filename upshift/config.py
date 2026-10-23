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
    executor: str = "docker"  # docker | nebius
    sandbox_image: str = "python:3.12-slim"  # python:3.12 when a project needs a compiler
    log_level: str = "INFO"

    # Nebius Serverless AI Jobs executor (EXECUTOR=nebius). Paid compute: nothing is created
    # unless NEBIUS_ACCEPT_CHARGES=1. Auth is a user token or the service-account trio.
    nebius_accept_charges: bool = False
    nebius_project_id: str = ""
    nebius_subnet_id: str = ""
    nebius_jobs_platform: str = ""
    nebius_jobs_preset: str = ""
    nebius_jobs_api: str = "https://api.nebius.cloud/ai/v1"
    nebius_iam_token: str = ""
    nebius_sa_id: str = ""
    nebius_sa_key_id: str = ""
    nebius_sa_private_key_file: str = ""
    nebius_token_exchange_url: str = "https://auth.eu.nebius.com:443/oauth2/token/exchange"
    nebius_s3_endpoint: str = ""
    nebius_s3_bucket: str = ""
    nebius_s3_region: str = "eu-west2"
    nebius_s3_access_key: str = ""
    nebius_s3_secret_key: str = ""

    @property
    def runs_db(self) -> Path:
        return self.data_dir / "runs.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
