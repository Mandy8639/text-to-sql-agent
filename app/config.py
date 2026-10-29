"""Application settings, loaded from environment variables (and `.env` if present)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Admin connection: scripts only (data loading, indexing).
    database_url: str = "postgresql+psycopg://olist_admin:change-me@localhost:5432/olist"
    # Read-only connection: the ONLY one the agent may use.
    agent_database_url: str = "postgresql+psycopg://olist_agent:change-me-too@localhost:5432/olist"
    # Read-only connection to the retrieval index (rag schema). Separate from the agent role,
    # so generated SQL can never read the index and retrieval can never read business data.
    rag_database_url: str = "postgresql+psycopg://olist_rag:change-me-three@localhost:5432/olist"

    # Embeddings (Phase 4 retrieval)
    embedding_provider: Literal["fastembed"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = ".cache/fastembed"

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
