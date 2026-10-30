"""The product layer: one call turns a question into SQL, rows, a chart spec and an explanation.

Used by both the API and the UI. One trace per question: the agent's own trace nests inside,
followed by `chart` and `explain` steps, so the whole request is visible in one place.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.agent.pipeline import VERSIONS, PipelineAgent
from app.agent.types import AgentResult
from app.charts import ChartSpec, choose_chart
from app.config import Settings
from app.db.connection import make_engine
from app.explain import Explanation, explain, json_safe
from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.llm.pricing import estimate_cost_usd
from app.observability.logs import log_event, request_scope
from app.observability.tracing import NoopTracer, Tracer, get_tracer
from app.retrieval.embedder import get_embedder
from app.retrieval.store import VectorStore

# What the user sees when the agent fails. Details stay in the steps and the trace.
ERROR_TEXT = {
    "llm": "The language model is unavailable right now. Please try again in a moment.",
    "extract": "I couldn't turn that question into a query. Try rephrasing it.",
    "validate": "The query I wrote was blocked by the safety checks, so it was not run.",
    "execute": "I couldn't write a query that runs correctly for that question. Try rephrasing it.",
}


@dataclass
class QueryResponse:
    request_id: str
    trace_id: str | None
    question: str
    outcome: str  # answer | clarify | refuse | error
    explanation: str | None = None
    explanation_grounded: bool | None = None
    ungrounded_numbers: list[str] = field(default_factory=list)
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart: dict = field(default_factory=lambda: ChartSpec("none").to_dict())
    note: str | None = None
    steps: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0


def reasoning_steps(res: AgentResult) -> list[dict]:
    """The agent's steps, for the UI's 'Show reasoning steps' panel."""
    steps: list[dict] = []
    if res.tables:
        steps.append({"step": "schema retrieval", "tables": res.tables})
    if res.example_ids:
        steps.append({"step": "similar solved examples", "examples": res.example_ids})
    for i, a in enumerate(res.attempts, 1):
        steps.append({"step": f"attempt {i}", **a})
    return steps


def _llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    return estimate_cost_usd(model, input_tokens, output_tokens) or 0.0


class QueryService:
    def __init__(
        self,
        agent: PipelineAgent,
        explainer: LLMProvider | None,
        tracer: Tracer | None = None,
        *,
        max_rows_returned: int = 200,
    ) -> None:
        self.agent = agent
        self.explainer = explainer
        self.tracer = tracer or NoopTracer()
        self.max_rows_returned = max_rows_returned

    @classmethod
    def from_settings(cls, settings: Settings) -> "QueryService":
        tracer = get_tracer(settings)
        store = VectorStore(make_engine(settings.rag_database_url), get_embedder(settings))
        agent = PipelineAgent.from_engine(
            VERSIONS[settings.agent_version],
            get_provider(settings),
            make_engine(settings.agent_database_url),
            store,
            tracer=tracer,
        )
        explain_settings = settings.model_copy(
            update={"llm_model": settings.explain_model, "llm_effort": settings.explain_effort}
        )
        return cls(
            agent,
            get_provider(explain_settings),
            tracer,
            max_rows_returned=settings.api_max_rows_returned,
        )

    # --- one question ---------------------------------------------------------------------------

    def ask(self, question: str) -> QueryResponse:
        start = time.perf_counter()
        meta = {"request_id": None, "agent": self.agent.version}
        with request_scope() as rid:
            meta["request_id"] = rid
            with self.tracer.trace("query", input=question, metadata=meta) as root:
                response = QueryResponse(rid, self.tracer.current_trace_id(), question, "error")
                res = self.agent.answer(question)  # the agent's trace nests under this one
                self._fill(response, res)
                response.latency_ms = round((time.perf_counter() - start) * 1000, 1)
                root.update(
                    output={
                        "outcome": response.outcome,
                        "chart": response.chart["type"],
                        "grounded": response.explanation_grounded,
                    }
                )
            log_event(
                "query.done",
                outcome=response.outcome,
                ms=response.latency_ms,
                grounded=response.explanation_grounded,
            )
        return response

    def _fill(self, out: QueryResponse, res: AgentResult) -> None:
        out.outcome, out.note, out.steps = res.outcome, res.note, reasoning_steps(res)
        out.cost_usd = sum(
            _llm_cost(c.model, c.input_tokens, c.output_tokens) for c in res.llm_calls
        )

        if res.outcome in ("refuse", "clarify"):
            out.explanation = res.note  # the refusal reason or the clarifying question
            return
        if res.outcome == "error":
            out.explanation = ERROR_TEXT.get(res.error_stage or "", "Something went wrong.")
            return

        r = res.result
        out.sql, out.columns, out.row_count = res.sql, list(r.columns), r.row_count
        out.rows = [[json_safe(v) for v in row] for row in r.rows[: self.max_rows_returned]]
        out.truncated = r.truncated or r.row_count > self.max_rows_returned

        with self.tracer.step("chart", input={"columns": out.columns, "rows": r.row_count}) as obs:
            chart = choose_chart(out.columns, r.rows)
            obs.update(output=chart.to_dict())
        out.chart = chart.to_dict()

        out.explanation = res.note
        if self.explainer is not None:
            exp = self._explain(res)
            if exp.text:
                out.explanation = exp.text
                out.explanation_grounded = exp.grounded
                out.ungrounded_numbers = exp.ungrounded_numbers
            if exp.response is not None:
                e = exp.response
                out.cost_usd += _llm_cost(e.model, e.input_tokens, e.output_tokens)
        out.cost_usd = round(out.cost_usd, 6)

    def _explain(self, res: AgentResult) -> Explanation:
        r = res.result
        with self.tracer.step(
            "explain", kind="generation", model=self.explainer.model, input=res.question
        ) as gen:
            exp = explain(
                self.explainer,
                res.question,
                res.sql,
                list(r.columns),
                r.rows,
                truncated=r.truncated,
                note=res.note,
            )
            if exp.response is not None:
                e = exp.response
                cost = estimate_cost_usd(e.model, e.input_tokens, e.output_tokens)
                gen.update(
                    output=exp.text,
                    usage_details={"input": e.input_tokens, "output": e.output_tokens},
                    cost_details={"total": cost} if cost is not None else None,
                    metadata={"grounded": exp.grounded, "ungrounded": exp.ungrounded_numbers},
                )
        return exp

    # --- health --------------------------------------------------------------------------------

    def health(self) -> dict[str, str]:
        """Database (agent role) and retrieval index reachable? Never raises."""
        checks = {}
        try:
            self.agent.execute("SELECT 1 AS ok")
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - report it, don't crash the health check
            checks["database"] = f"error: {type(exc).__name__}"
        try:
            with self.agent.store.engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM rag.index_meta LIMIT 1"))
            checks["retrieval_index"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["retrieval_index"] = f"error: {type(exc).__name__}"
        return checks

    # --- feedback -------------------------------------------------------------------------------

    def feedback(self, trace_id: str, positive: bool, comment: str | None = None) -> None:
        self.tracer.score(trace_id, "user_feedback", 1.0 if positive else 0.0, comment)
        self.tracer.flush()
        log_event("feedback", trace_id=trace_id, positive=positive)
