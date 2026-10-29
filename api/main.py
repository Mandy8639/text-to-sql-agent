"""HTTP API: `uvicorn api.main:app` (or `make run-api`).

    POST /query     question -> SQL, rows, explanation, chart spec, trace id
    POST /feedback  thumbs up/down on a trace (sent to Langfuse; keys stay server-side)
    GET  /health    database + index reachability
    GET  /schema    the documented tables and columns (what the agent can answer about)

Errors never expose internals: unexpected exceptions return a generic message and the
request id, and the details go to the JSON logs.
"""

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.config import Settings, get_settings
from app.observability.logs import configure_logging, log_event, new_request_id
from app.schema_doc import load_schema_doc
from app.service import QueryService

# --- request / response models ---------------------------------------------------------------


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, description="Business question in plain English")

    @field_validator("question")
    @classmethod
    def clean(cls, v: str) -> str:
        v = " ".join(v.split())  # collapse whitespace and newlines
        limit = get_settings().api_max_question_chars
        if len(v) < 3:
            raise ValueError("question is too short")
        if len(v) > limit:
            raise ValueError(f"question is longer than {limit} characters")
        if any(ord(c) < 32 for c in v):
            raise ValueError("question contains control characters")
        return v


class ChartModel(BaseModel):
    type: Literal["line", "bar", "none"]
    x: str | None = None
    y: list[str] = []
    reason: str = ""


class QueryResponseModel(BaseModel):
    request_id: str
    trace_id: str | None
    question: str
    outcome: Literal["answer", "clarify", "refuse", "error"]
    explanation: str | None
    explanation_grounded: bool | None
    ungrounded_numbers: list[str]
    sql: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    chart: ChartModel
    note: str | None
    steps: list[dict[str, Any]]
    latency_ms: float
    cost_usd: float


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    positive: bool
    comment: str | None = Field(default=None, max_length=1000)


# --- rate limiting -----------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limit per client IP, in memory (one process). Phase 7 adds a spend cap."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """None if allowed, else seconds until the next request is allowed."""
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return round(60 - (now - hits[0]), 1)
            hits.append(now)
            return None


# --- app ---------------------------------------------------------------------------------------


def create_app(
    service_factory: Callable[[], QueryService] | None = None,
    settings: Settings | None = None,
    health_check: Callable[[QueryService], dict] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging()
        # Build once at startup: loads the embedding model and opens DB pools.
        state["service"] = (service_factory or (lambda: QueryService.from_settings(settings)))()
        yield

    app = FastAPI(
        title="Text-to-SQL Analytics Agent",
        version="1.0",
        lifespan=lifespan,
        description="Ask the Olist e-commerce database questions in plain English.",
    )
    limiter = RateLimiter(settings.api_rate_limit_per_minute)

    def service() -> QueryService:
        return state["service"]

    def rate_limited(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        retry_after = limiter.check(client)
        if retry_after is not None:
            raise HTTPException(
                429,
                detail=f"Rate limit: {limiter.per_minute} questions per minute.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = new_request_id()
        log_event("api.error", level=logging.ERROR, error_id=rid, path=request.url.path,
                  error=type(exc).__name__, detail=str(exc)[:500])  # fmt: skip
        return JSONResponse(status_code=500, content={"error": "Internal error.", "error_id": rid})

    @app.post("/query", response_model=QueryResponseModel, dependencies=[Depends(rate_limited)])
    def query(body: QueryRequest, svc: QueryService = Depends(service)) -> dict:
        return asdict(svc.ask(body.question))

    @app.post("/feedback", status_code=204)
    def feedback(body: FeedbackRequest, svc: QueryService = Depends(service)) -> None:
        svc.feedback(body.trace_id, body.positive, body.comment)

    @app.get("/health")
    def health(svc: QueryService = Depends(service)) -> JSONResponse:
        checks = (health_check or _default_health)(svc)
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ok" if ok else "degraded", "agent": svc.agent.version, **checks},
        )

    @app.get("/schema")
    def schema() -> dict:
        doc = load_schema_doc()
        return {
            "schema_version": doc["version"],
            "tables": {
                name: {
                    "description": spec["description"].strip(),
                    "columns": {c: s["description"] for c, s in spec["columns"].items()},
                }
                for name, spec in doc["tables"].items()
            },
        }

    return app


def _default_health(svc: QueryService) -> dict:
    """Database (agent role) and retrieval index reachable? Never raises."""
    checks = {}
    try:
        svc.agent.execute("SELECT 1 AS ok")
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't crash the health endpoint
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        with svc.agent.store.engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM rag.index_meta LIMIT 1"))
        checks["retrieval_index"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["retrieval_index"] = f"error: {type(exc).__name__}"
    return checks


app = create_app()
