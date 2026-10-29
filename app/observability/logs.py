"""Structured (JSON) logging with a request ID on every line.

    {"ts": "...", "level": "INFO", "event": "sql.executed", "request_id": "3f9c2a1b7d0e",
     "agent": "v6-safe", "rows": 27, "ms": 41.2}

The request ID is set once per question (`request_scope`) and carried by a context variable,
so every log line from retrieval, the LLM and the database for that question shares it, and
it is also stored on the result and the Langfuse trace for cross-referencing.
"""

import contextvars
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

log = logging.getLogger("text2sql")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


@contextmanager
def request_scope(request_id: str | None = None) -> Iterator[str]:
    rid = request_id or new_request_id()
    token = _REQUEST_ID.set(rid)
    try:
        yield rid
    finally:
        _REQUEST_ID.reset(token)


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    """Log one structured event; the request ID is added automatically."""
    log.log(
        level, event, extra={"event": event, "fields": fields, "request_id": current_request_id()}
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "request_id": getattr(record, "request_id", None) or current_request_id(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO, *, json_output: bool = True) -> None:
    """Send `text2sql` events to stderr as JSON lines (call once at process start)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(level)
    log.propagate = False
