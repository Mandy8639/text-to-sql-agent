"""Provider-agnostic LLM interface.

The agent depends only on `LLMProvider` and `LLMResponse`, so swapping Anthropic for another
vendor (or a fake in tests) means writing one new class, not touching agent code.
"""

from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    """Raised when the provider call fails or returns no usable text."""


@dataclass(frozen=True)
class LLMResponse:
    """One completed model call, with the metadata the eval harness needs."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    stop_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# One conversation turn: {"role": "user" | "assistant", "content": "..."}
Message = dict[str, str]


class LLMProvider(Protocol):
    """Anything that turns a system prompt + conversation into text."""

    model: str

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> LLMResponse:
        """Run one single-turn completion."""
        ...

    def chat(
        self, system: str, messages: list[Message], *, max_tokens: int | None = None
    ) -> LLMResponse:
        """Run a multi-turn completion (used by the self-correction loop)."""
        ...
