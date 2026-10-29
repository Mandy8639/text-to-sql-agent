"""Anthropic (Claude) implementation of `LLMProvider`."""

import time
from typing import Any

import anthropic

from app.config import Effort
from app.llm.base import LLMError, LLMResponse

# Server-side refusal fallback: if Claude's safety classifiers decline a request, the API
# re-runs it on Anthropic's recommended fallback model inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    """Calls the Claude Messages API.

    Thinking is adaptive (always on for Claude Opus 5.5); `effort` is the cost/quality knob.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        effort: Effort = "medium",
        max_tokens: int = 16000,
        timeout_s: float = 120.0,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        # `client` is injectable so unit tests can pass a fake without network access.
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout_s)

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> LLMResponse:
        """Send one system + user message and return the text answer with usage stats."""
        start = time.perf_counter()
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens or self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API call failed: {type(exc).__name__}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        if response.stop_reason == "refusal":
            raise LLMError("Model declined the request (stop_reason=refusal).")

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMError(f"Model returned no text (stop_reason={response.stop_reason}).")

        return LLMResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            stop_reason=response.stop_reason,
        )
