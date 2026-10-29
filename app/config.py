"""Application settings, loaded from environment variables (and `.env` if present)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

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

    # Product layer (Phase 6). Explanations are a short second call; a small model is enough.
    explain_model: str = "claude-haiku-5-5"
    explain_effort: Effort = "low"
    agent_version: str = "v6"  # which pipeline config the API/UI serve
    api_rate_limit_per_minute: int = 10  # per client IP
    api_global_rate_limit_per_minute: int = 30  # all clients together
    api_max_question_chars: int = 500
    api_max_rows_returned: int = 200
    # Trust X-Forwarded-For / X-Client-IP from these peers (the UI in the same container,
    # or the host's reverse proxy) when identifying a client for rate limiting.
    api_trusted_proxies: list[str] = ["127.0.0.1", "::1"]

    # Cost protection (Phase 7): LLM spend per UTC day, across all users. 0 disables the cap.
    daily_budget_usd: float = 1.0
    # Writable connection for the spend counter only (role olist_app, schema app).
    # Unset: an in-memory counter (fine locally; resets on restart).
    app_database_url: str | None = None

    # Inside docker-compose the database is reachable as "db", not "localhost". Setting this
    # rewrites the host in every database URL, so one .env works on the host and in containers.
    db_host_override: str | None = None

    @model_validator(mode="after")
    def _override_db_host(self) -> "Settings":
        if self.db_host_override:
            for name in (
                "database_url",
                "agent_database_url",
                "rag_database_url",
                "app_database_url",
            ):
                url = getattr(self, name)
                if url:
                    new = make_url(url).set(host=self.db_host_override, port=5432)
                    setattr(self, name, new.render_as_string(hide_password=False))
        return self

    # Observability (Phase 5). Tracing is off unless both keys are set.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

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
