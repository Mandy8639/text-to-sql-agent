"""Shared result types for all agent versions (the eval harness consumes these)."""

from dataclasses import dataclass, field
from typing import Literal

from app.db.executor import QueryResult

# Where a failed run stopped. None means success.
ErrorStage = Literal["llm", "extract", "execute"]


@dataclass
class LLMCall:
    """Usage stats for one model call within a run."""

    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass
class AgentResult:
    """Everything one question produced: the SQL, rows or error, and cost/latency data."""

    question: str
    agent_version: str
    sql: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    error_stage: ErrorStage | None = None
    note: str | None = None  # the model's prose outside the SQL (assumptions, caveats)
    raw_reply: str | None = None  # the model's full text, for debugging
    llm_calls: list[LLMCall] = field(default_factory=list)
    total_latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.llm_calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.llm_calls)
