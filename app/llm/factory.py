"""Build the configured LLM provider."""

from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError, LLMProvider


def get_provider(settings: Settings) -> LLMProvider:
    """Return the provider named by `settings.llm_provider`."""
    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
            timeout_s=settings.llm_timeout_seconds,
        )
    raise LLMError(f"Unknown LLM provider: {settings.llm_provider}")
