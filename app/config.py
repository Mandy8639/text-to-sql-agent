"""Application settings, loaded from environment variables (and `.env` if present)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database (admin connection, used by scripts; the agent gets a read-only role in Phase 1)
    database_url: str = "postgresql+psycopg://olist_admin:change-me@localhost:5432/olist"

    # LLM
    llm_provider: Literal["anthropic"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: Effort = "medium"
    llm_max_tokens: int = 16000
    llm_timeout_seconds: float = 120.0


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached so `.env` is read once)."""
    return Settings()
