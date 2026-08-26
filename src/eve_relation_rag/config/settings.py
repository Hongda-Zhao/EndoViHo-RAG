from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EVE_RAG_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "EVE Relation RAG"
    app_version: str = "V0"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = (
        "postgresql+psycopg://eve:eve_dev_password@localhost:5432/eve_relation_rag"
    )


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""
    return Settings()
