"""Runtime configuration from environment variables."""

from __future__ import annotations

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    database_url: str = "sqlite://"
    catalog_api_url: str = "https://api.example.com/catalog"
    request_timeout: float = Field(5.0, gt=0)

    class Config:
        env_prefix = "PYSHOP_"


def get_settings() -> Settings:
    return Settings()
