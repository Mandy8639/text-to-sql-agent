"""Tracing behind a small interface: Langfuse when configured, a no-op otherwise.

One trace per question, with a child observation per step:

    text-to-sql (agent)            input: question            output: outcome, sql, rows
      injection_screen (guardrail)
      retrieval (retriever)        tables + scores, example ids + scores
      llm attempt N (generation)   model, prompt, reply, tokens, cost
      validate (guardrail)         allowed / blocked + reason, LIMIT added
      execute (span)               row count, ms, error

Result rows are never sent, only counts and column names: traces leave the machine.

Trace-wide tags and metadata (e.g. an eval run id and question id) are set by the caller with
`trace_context(...)` around `agent.answer(...)`, so the agent API doesn't change.
"""

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings

_TAGS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("trace_tags", default=())
_META: contextvars.ContextVar[dict | None] = contextvars.ContextVar("trace_meta", default=None)


@contextmanager
def trace_context(*, tags: list[str] | None = None, metadata: dict | None = None) -> Iterator[None]:
    """Attach tags/metadata to every trace started inside this block (this thread only)."""
    t1 = _TAGS.set(_TAGS.get() + tuple(tags or ()))
    t2 = _META.set({**(_META.get() or {}), **(metadata or {})})
    try:
        yield
    finally:
        _TAGS.reset(t1)
        _META.reset(t2)


class Observation(Protocol):
    def update(self, **fields: Any) -> None: ...


class Tracer(Protocol):
    def trace(self, name: str, *, input: Any, metadata: dict) -> Any: ...  # ctx -> Observation

    def step(self, name: str, *, kind: str = "span", input: Any = None, **kw: Any) -> Any: ...

    def current_trace_id(self) -> str | None: ...

    def flush(self) -> None: ...


# --- no-op ------------------------------------------------------------------------------------


class _NoopObservation:
    def update(self, **fields: Any) -> None:
        pass


class NoopTracer:
    """Used when Langfuse is not configured. Costs nothing, records nothing."""

    @contextmanager
    def trace(self, name: str, *, input: Any, metadata: dict) -> Iterator[_NoopObservation]:
        yield _NoopObservation()

    @contextmanager
    def step(self, name: str, *, kind: str = "span", input: Any = None, **kw: Any):
        yield _NoopObservation()

    def current_trace_id(self) -> str | None:
        return None

    def flush(self) -> None:
        pass


# --- recording (tests) ----------------------------------------------------------------------


@dataclass
class RecordedObservation:
    name: str
    kind: str
    input: Any = None
    fields: dict = field(default_factory=dict)
    children: list["RecordedObservation"] = field(default_factory=list)
    tags: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)

    def update(self, **fields: Any) -> None:
        self.fields.update(fields)


class RecordingTracer:
    """Keeps the observation tree in memory, for unit tests."""

    def __init__(self) -> None:
        self.traces: list[RecordedObservation] = []
        self._stack: list[RecordedObservation] = []

    @contextmanager
    def trace(self, name: str, *, input: Any, metadata: dict):
        obs = RecordedObservation(name, "agent", input, tags=_TAGS.get(),
                                  metadata={**(_META.get() or {}), **metadata})  # fmt: skip
        self.traces.append(obs)
        self._stack.append(obs)
        try:
            yield obs
        finally:
            self._stack.pop()

    @contextmanager
    def step(self, name: str, *, kind: str = "span", input: Any = None, **kw: Any):
        obs = RecordedObservation(name, kind, input, fields=dict(kw))
        self._stack[-1].children.append(obs)
        self._stack.append(obs)
        try:
            yield obs
        finally:
            self._stack.pop()

    def current_trace_id(self) -> str | None:
        return f"rec-{len(self.traces)}" if self._stack else None

    def flush(self) -> None:
        pass


# --- Langfuse ---------------------------------------------------------------------------------


class LangfuseTracer:
    """Langfuse v5 (OpenTelemetry-based) client wrapper."""

    def __init__(self, public_key: str, secret_key: str, base_url: str) -> None:
        from langfuse import Langfuse, is_langfuse_span, propagate_attributes

        # Export only our own observations. Langfuse v5 also captures the Anthropic SDK's
        # OpenTelemetry spans, which would duplicate every LLM call (and double its cost).
        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            should_export_span=is_langfuse_span,
        )
        self._propagate = propagate_attributes

    @contextmanager
    def trace(self, name: str, *, input: Any, metadata: dict):
        meta = {k: str(v) for k, v in {**(_META.get() or {}), **metadata}.items()}
        with (
            self._client.start_as_current_observation(
                name=name, as_type="agent", input=input
            ) as root,
            self._propagate(trace_name=name, tags=list(_TAGS.get()), metadata=meta),
        ):
            yield root

    @contextmanager
    def step(self, name: str, *, kind: str = "span", input: Any = None, **kw: Any):
        with self._client.start_as_current_observation(
            name=name, as_type=kind, input=input, **kw
        ) as obs:
            yield obs

    def current_trace_id(self) -> str | None:
        return self._client.get_current_trace_id()

    def flush(self) -> None:
        self._client.flush()

    def auth_check(self) -> bool:
        return self._client.auth_check()


def get_tracer(settings: Settings) -> Tracer:
    """Langfuse if both keys are set, otherwise the no-op tracer."""
    pk, sk = settings.langfuse_public_key, settings.langfuse_secret_key
    if pk and sk and sk.get_secret_value():
        return LangfuseTracer(pk, sk.get_secret_value(), settings.langfuse_base_url)
    return NoopTracer()
