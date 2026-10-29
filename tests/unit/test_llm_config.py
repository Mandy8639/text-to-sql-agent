"""Unit tests for settings, provider factory, and pricing."""

import pytest

from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError
from app.llm.factory import get_provider
from app.llm.pricing import estimate_cost_usd


def _settings(**overrides) -> Settings:
    # `_env_file=None` keeps a developer's real .env from leaking into tests.
    return Settings(_env_file=None, **overrides)


def test_factory_builds_anthropic_provider():
    provider = get_provider(_settings(anthropic_api_key="sk-test", llm_model="claude-sonnet-5-5"))
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5-5"


def test_factory_requires_api_key():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        get_provider(_settings(anthropic_api_key=None))


def test_api_key_is_not_printed():
    settings = _settings(anthropic_api_key="sk-secret-value")
    assert "sk-secret-value" not in repr(settings)


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("LLM_EFFORT", "high")
    settings = _settings()
    assert settings.llm_model == "claude-haiku-5-5"
    assert settings.llm_effort == "high"


def test_cost_estimate():
    # 1M input + 1M output on Opus 5.5 = $4 + $20
    assert estimate_cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)


def test_cost_unknown_model_is_none():
    assert estimate_cost_usd("some-other-model", 100, 100) is None
