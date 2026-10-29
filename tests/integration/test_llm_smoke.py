"""Smoke test: one real call to the configured LLM. Skips if no API key is set.

Costs a fraction of a cent per run.
"""

import pytest

from app.config import get_settings
from app.llm.factory import get_provider

pytestmark = pytest.mark.llm


def test_llm_round_trip():
    settings = get_settings()
    if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
        pytest.skip("ANTHROPIC_API_KEY not set")

    provider = get_provider(settings)
    result = provider.complete(
        system="Reply with exactly the word requested and nothing else.",
        user="Say the word: pong",
        max_tokens=2000,
    )

    assert "pong" in result.text.lower()
    assert result.input_tokens > 0
    assert result.output_tokens > 0
