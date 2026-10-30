"""HTTP API: `uvicorn api.main:app` (or `make run-api`).

    POST /query     question -> SQL, rows, explanation, chart spec, trace id
    POST /feedback  thumbs up/down on a trace (sent to Langfuse; keys stay server-side)
    GET  /health    database + index reachability
    GET  /schema    the documented tables and columns (what the agent can answer about)

Errors never expose internals: unexpected exceptions return a generic message and the
request id, and the details go to the JSON logs.
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.config import Settings, get_settings
from app.limits import Admission, RateLimited, RateLimiter, clean_question
from app.observability.logs import configure_logging, log_event, new_request_id
from app.schema_doc import load_schema_doc
from app.service import QueryService
from app.spend import BudgetExceeded, SpendGuard

__all__ = ["RateLimiter", "app", "client_key", "create_app"]

# --- request / response models ---------------------------------------------------------------


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, description="Business question in plain English")

    @field_validator("question")
    @classmethod
    def clean(cls, v: str) -> str:
        return clean_question(v, get_settings().api_max_question_chars)


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


# --- app ---------------------------------------------------------------------------------------


def client_key(request: Request, trusted_proxies: list[str]) -> str:
    """The client to rate-limit. Proxy headers are believed only from trusted peers: the UI
    (same container) forwards the visitor's IP as X-Client-IP; a reverse proxy sets
    X-Forwarded-For. A direct caller can't spoof its key by sending these headers."""
    peer = request.client.host if request.client else "unknown"
    if peer in trusted_proxies:
        forwarded = request.headers.get("x-client-ip") or request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def create_app(
    service_factory: Callable[[], QueryService] | None = None,
    settings: Settings | None = None,
    health_check: Callable[[QueryService], dict] | None = None,
    spend_guard: SpendGuard | None = None,
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
    admission = Admission(settings, spend_guard)

    def service() -> QueryService:
        return state["service"]

    def rate_limited(request: Request) -> None:
        try:
            admission.check_rate(client_key(request, settings.api_trusted_proxies))
        except RateLimited as exc:
            raise HTTPException(
                429, detail=str(exc), headers={"Retry-After": str(int(exc.retry_after) + 1)}
            ) from exc

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = new_request_id()
        log_event("api.error", level=logging.ERROR, error_id=rid, path=request.url.path,
                  error=type(exc).__name__, detail=str(exc)[:500])  # fmt: skip
        return JSONResponse(status_code=500, content={"error": "Internal error.", "error_id": rid})

    @app.post("/query", response_model=QueryResponseModel, dependencies=[Depends(rate_limited)])
    def query(body: QueryRequest, svc: QueryService = Depends(service)) -> dict:
        try:
            admission.check_budget()
        except BudgetExceeded as exc:
            raise HTTPException(503, detail=str(exc)) from exc
        response = svc.ask(body.question)
        admission.record(response.cost_usd)
        return asdict(response)

    @app.post("/feedback", status_code=204)
    def feedback(body: FeedbackRequest, svc: QueryService = Depends(service)) -> None:
        svc.feedback(body.trace_id, body.positive, body.comment)

    @app.get("/health")
    def health(svc: QueryService = Depends(service)) -> JSONResponse:
        checks = health_check(svc) if health_check else svc.health()
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


app = create_app()
