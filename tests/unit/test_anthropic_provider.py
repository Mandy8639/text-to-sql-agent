"""Unit tests for AnthropicProvider, using a fake client (no network)."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError


def _response(blocks, stop_reason="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=120, output_tokens=30),
    )


class FakeClient:
    """Mimics `client.beta.messages.create` and records the kwargs it was called with."""

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _provider(client, **kwargs):
    return AnthropicProvider(api_key="test", model="claude-opus-5-5", client=client, **kwargs)


def test_returns_text_and_usage():
    blocks = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="SELECT 1;"),
    ]
    provider = _provider(FakeClient(_response(blocks)))

    result = provider.complete("system", "question")

    assert result.text == "SELECT 1;"
    assert result.input_tokens == 120
    assert result.output_tokens == 30
    assert result.total_tokens == 150
    assert result.latency_ms >= 0


def test_sends_expected_request():
    client = FakeClient(_response([SimpleNamespace(type="text", text="ok")]))
    provider = _provider(client, effort="low", max_tokens=500)

    provider.complete("be terse", "hi")

    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == "be terse"
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert call["max_tokens"] == 500
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    # Sampling params now 400 on the API; make sure we never send them.
    assert "temperature" not in call


def test_per_call_max_tokens_override():
    client = FakeClient(_response([SimpleNamespace(type="text", text="ok")]))
    _provider(client).complete("s", "u", max_tokens=64)
    assert client.calls[0]["max_tokens"] == 64


def test_refusal_raises():
    provider = _provider(FakeClient(_response([], stop_reason="refusal")))
    with pytest.raises(LLMError, match="declined"):
        provider.complete("s", "u")


def test_empty_text_raises():
    provider = _provider(FakeClient(_response([], stop_reason="max_tokens")))
    with pytest.raises(LLMError, match="no text"):
        provider.complete("s", "u")


def test_api_error_is_wrapped():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    provider = _provider(FakeClient(error))
    with pytest.raises(LLMError, match="APIConnectionError"):
        provider.complete("s", "u")
