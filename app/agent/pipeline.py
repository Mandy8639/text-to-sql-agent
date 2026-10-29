"""Configurable agent pipeline. Each Phase 4 version is one `AgentConfig`.

    question
      -> (retrieval) embed question; top-k tables + join-path tables; top-k dev examples
      -> prompt: instructions + examples + schema (full | retrieved | bare)
      -> LLM -> abstain? (REFUSE/CLARIFY) | extract SQL
      -> execute read-only -> error / empty / all-NULL? -> feedback and retry (max_retries)
      -> AgentResult (every attempt logged)

Versions are cumulative (see `VERSIONS`), so each one adds exactly one technique.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine

from app.agent.prompts import load_prompt
from app.agent.schema_render import render_bare_schema, render_retrieved_schema, render_schema
from app.agent.sql_extract import (
    SQLExtractionError,
    extract_abstention,
    extract_note,
    extract_sql,
)
from app.agent.types import AgentResult, LLMCall
from app.db.executor import QueryExecutionError, QueryResult, execute_readonly
from app.llm.base import LLMError, LLMProvider, Message
from app.llm.pricing import estimate_cost_usd
from app.observability.logs import log_event, request_scope
from app.observability.tracing import NoopTracer, Tracer
from app.retrieval.schema_graph import connect
from app.retrieval.store import Example, VectorStore
from app.safety.guard import SafeExecutor
from app.safety.injection import REFUSAL_TEXT as INJECTION_REFUSAL
from app.safety.injection import screen_question
from app.safety.sql_validator import UnsafeSQLError
from app.schema_doc import load_schema_doc

Executor = Callable[[str], QueryResult]
SchemaMode = Literal["full", "retrieved", "bare"]


@dataclass(frozen=True)
class AgentConfig:
    version: str
    prompt: str = "agent"  # prompts/<prompt>_v<prompt_version>.md
    prompt_version: int = 1
    schema_mode: SchemaMode = "full"
    table_k: int = 6  # chosen on dev retrieval recall (100% at k=6, see D29)
    few_shot_k: int = 0
    max_retries: int = 0
    allow_abstain: bool = False
    safety: bool = False  # injection screen + SQL validator + LIMIT (Phase 5)


VERSIONS: dict[str, AgentConfig] = {
    "v2": AgentConfig("v2-retrieval", schema_mode="retrieved"),
    "v3": AgentConfig("v3-fewshot", schema_mode="retrieved", few_shot_k=3),
    "v4": AgentConfig("v4-selfcorrect", schema_mode="retrieved", few_shot_k=3, max_retries=2),
    "v5": AgentConfig(
        "v5-abstain",
        prompt="agent_abstain",
        schema_mode="retrieved",
        few_shot_k=3,
        max_retries=2,
        allow_abstain=True,
    ),
    "v6": AgentConfig(
        "v6-safe",
        prompt="agent_abstain",
        schema_mode="retrieved",
        few_shot_k=3,
        max_retries=2,
        allow_abstain=True,
        safety=True,
    ),
    # Sparse-documentation track (D25): only table/column names, plus dev examples.
    "raw-fewshot": AgentConfig("raw-fewshot-v1", schema_mode="bare", few_shot_k=3),
}


def render_examples(examples: list[Example]) -> str:
    if not examples:
        return ""
    blocks = [f"Question: {e.question}\n```sql\n{e.sql.strip()}\n```" for e in examples]
    return (
        "\n# Examples\nSolved questions from the same database, showing its conventions in "
        "practice. Use them as patterns, not as answers.\n\n" + "\n\n".join(blocks) + "\n"
    )


def _suspicious(result: QueryResult) -> str | None:
    """An observation worth a second look, or None. Kept deliberately simple."""
    if result.row_count == 0:
        return "It ran but returned no rows. Check the filters, joins and date ranges."
    if all(v is None for row in result.rows for v in row):
        return "It ran but every value in the result is NULL."
    return None


class PipelineAgent:
    def __init__(
        self,
        config: AgentConfig,
        llm: LLMProvider,
        execute: Executor,
        store: VectorStore | None = None,
        doc: dict[str, Any] | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        needs_store = config.schema_mode == "retrieved" or config.few_shot_k > 0
        if needs_store and store is None:
            raise ValueError(f"{config.version} needs a VectorStore")
        self.config = config
        self.version = config.version
        self.llm = llm
        self.execute = execute
        self.store = store
        self.tracer: Tracer = tracer or NoopTracer()
        self.doc = doc or load_schema_doc()
        self.template = load_prompt(config.prompt, config.prompt_version)
        self.retry_template = load_prompt("retry", 1)
        # Static view for the harness's leakage check (examples are checked per question).
        self.system_prompt = self.template.substitute(schema=render_schema(self.doc), examples="")

    @classmethod
    def from_engine(
        cls,
        config: AgentConfig,
        llm: LLMProvider,
        agent_engine: Engine,
        store: VectorStore | None,
        tracer: Tracer | None = None,
    ) -> "PipelineAgent":
        if config.safety:
            execute: Executor = SafeExecutor.from_doc(agent_engine, load_schema_doc())
        else:
            execute = lambda sql: execute_readonly(agent_engine, sql)  # noqa: E731
        return cls(config, llm, execute, store, tracer=tracer)

    # --- prompt construction -----------------------------------------------------------------

    def build_prompt(self, question: str, out: AgentResult) -> str:
        if self.store:
            with self.tracer.step("retrieval", kind="retriever", input=question) as obs:
                prompt, trace = self._build_prompt(question, out)
                obs.update(output=trace)
            log_event("retrieval.done", tables=out.tables, examples=out.example_ids)
            return prompt
        return self._build_prompt(question, out)[0]

    def _build_prompt(self, question: str, out: AgentResult) -> tuple[str, dict]:
        cfg = self.config
        trace: dict[str, Any] = {}
        vec = self.store.embed(question) if self.store else None
        if cfg.schema_mode == "retrieved":
            scored = self.store.search_tables(vec, cfg.table_k)
            top = [t.table for t in scored]
            out.tables = connect(self.doc, top)
            trace["tables"] = {t.table: round(t.score, 3) for t in scored}
            trace["join_path_tables"] = [t for t in out.tables if t not in top]
            schema = render_retrieved_schema(self.doc, out.tables)
        elif cfg.schema_mode == "bare":
            out.tables = list(self.doc["tables"])
            schema = render_bare_schema(self.doc)
        else:
            out.tables = list(self.doc["tables"])
            schema = render_schema(self.doc)
        examples: list[Example] = []
        if cfg.few_shot_k:
            examples = self.store.search_examples(vec, cfg.few_shot_k, exclude_question=question)
            out.example_ids = [e.question_id for e in examples]
            trace["examples"] = {e.question_id: round(e.score, 3) for e in examples}
        prompt = self.template.substitute(schema=schema, examples=render_examples(examples))
        return prompt, trace

    # --- the loop ------------------------------------------------------------------------------

    def answer(self, question: str) -> AgentResult:
        """Run the pipeline. Expected failures are returned in the result, never raised.

        Each call gets a request id (in every JSON log line) and one trace (when configured).
        """
        start = time.perf_counter()
        out = AgentResult(question=question, agent_version=self.version)
        with (
            request_scope() as rid,
            self.tracer.trace(
                "text-to-sql", input=question, metadata={"agent": self.version, "request_id": rid}
            ) as root,
        ):
            out.request_id, out.trace_id = rid, self.tracer.current_trace_id()
            log_event("request.start", agent=self.version, question_chars=len(question))
            self._run(question, out)
            out.total_latency_ms = (time.perf_counter() - start) * 1000
            summary = {
                "outcome": out.outcome,
                "sql": out.sql,
                "rows": out.result.row_count if out.result else None,
                "error_stage": out.error_stage,
                "error": out.error,
                "attempts": len(out.attempts),
                "abstention_note": out.note if out.abstention else None,
            }
            root.update(output=summary)
            log_event("request.end", ms=round(out.total_latency_ms, 1),
                      **{k: v for k, v in summary.items() if k != "sql"})  # fmt: skip
        return out

    def _run(self, question: str, out: AgentResult) -> None:
        if self.config.safety:
            with self.tracer.step("injection_screen", kind="guardrail", input=question) as obs:
                verdict = screen_question(question)
                obs.update(output={"flagged": verdict.flagged, "rules": list(verdict.rules)})
            if verdict.flagged:
                # Refuse before retrieval or any LLM call: nothing in the request reaches the model.
                out.abstention, out.note = "refuse", INJECTION_REFUSAL
                out.attempts.append({"sql": None, "status": "blocked_injection",
                                     "detail": ", ".join(verdict.rules)})  # fmt: skip
                log_event("injection.blocked", level=logging.WARNING, rules=list(verdict.rules))
                return
        try:
            system = self.build_prompt(question, out)
            messages: list[Message] = [{"role": "user", "content": question}]
            for attempt in range(1 + self.config.max_retries):
                last = attempt == self.config.max_retries
                with self.tracer.step(
                    f"llm attempt {attempt + 1}",
                    kind="generation",
                    input=[{"role": "system", "content": system}, *messages],
                    model=getattr(self.llm, "model", None),
                ) as gen:
                    reply = self.llm.chat(system, messages)
                    cost = estimate_cost_usd(reply.model, reply.input_tokens, reply.output_tokens)
                    gen.update(
                        output=reply.text,
                        usage_details={"input": reply.input_tokens, "output": reply.output_tokens},
                        cost_details={"total": cost} if cost is not None else None,
                    )
                log_event("llm.call", attempt=attempt + 1, model=reply.model,
                          input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                          ms=round(reply.latency_ms, 1))  # fmt: skip
                out.raw_reply = reply.text
                out.llm_calls.append(
                    LLMCall(reply.model, reply.input_tokens, reply.output_tokens, reply.latency_ms)
                )
                problem = self._handle_reply(reply.text, out, last)
                if problem is None:
                    break
                messages += [
                    {"role": "assistant", "content": reply.text},
                    {"role": "user", "content": self.retry_template.substitute(problem=problem)},
                ]
        except LLMError as exc:
            out.error, out.error_stage = str(exc), "llm"
            log_event("llm.error", level=logging.ERROR, error=str(exc))

    def _handle_reply(self, text: str, out: AgentResult, last: bool) -> str | None:
        """Process one reply. Returns feedback for a retry, or None when the run is finished."""
        if self.config.allow_abstain and (abstain := extract_abstention(text)):
            out.abstention, out.note = abstain
            out.sql = out.result = out.error = out.error_stage = None
            out.attempts.append({"sql": None, "status": abstain[0], "detail": abstain[1]})
            return None

        out.note = extract_note(text)
        try:
            sql = extract_sql(text)
        except SQLExtractionError as exc:
            out.attempts.append({"sql": None, "status": "no_sql", "detail": str(exc)})
            out.error, out.error_stage = str(exc), "extract"
            return None if last else "Your reply did not contain a ```sql code block."

        repeated = bool(out.sql) and sql.strip() == out.sql.strip()
        out.sql = sql
        try:
            out.result = self._execute(sql)
        except UnsafeSQLError as exc:
            log_event("sql.blocked", level=logging.WARNING, reason=exc.message)
            out.attempts.append({"sql": sql, "status": "blocked", "detail": exc.message})
            out.result, out.error, out.error_stage = None, exc.message, "validate"
            if last:
                return None
            return (
                f"The query was rejected by the safety check: {exc.message} Only a single "
                "read-only SELECT on the documented tables is allowed."
            )
        except QueryExecutionError as exc:
            log_event("sql.error", level=logging.WARNING, sqlstate=exc.sqlstate, error=exc.message)
            out.attempts.append({"sql": sql, "status": "error", "detail": exc.message})
            out.result, out.error, out.error_stage = None, exc.message, "execute"
            return None if last else f"PostgreSQL returned an error: {exc.message}"

        out.error = out.error_stage = None
        observation = _suspicious(out.result)
        out.attempts.append({"sql": sql, "status": "suspicious" if observation else "ok",
                             "detail": observation})  # fmt: skip
        # Accept if fine, out of retries, or the model re-sent the same query (it is confident).
        if observation is None or last or repeated:
            return None
        return observation

    def _execute(self, sql: str) -> QueryResult:
        """Validate (when the executor is a SafeExecutor) and run, as separate traced steps."""
        if isinstance(self.execute, SafeExecutor):
            with self.tracer.step("validate", kind="guardrail", input=sql) as obs:
                try:
                    checked = self.execute.check(sql)
                except UnsafeSQLError as exc:
                    obs.update(output={"allowed": False, "reason": exc.message}, level="WARNING")
                    raise
                obs.update(output={"allowed": True, "limit_added": checked.limit_added})
            run = lambda: self.execute.run(checked)  # noqa: E731
            to_run = checked.sql
        else:
            run = lambda: self.execute(sql)  # noqa: E731
            to_run = sql
        with self.tracer.step("execute", input=to_run) as obs:
            try:
                result = run()
            except QueryExecutionError as exc:
                obs.update(output={"error": exc.message, "sqlstate": exc.sqlstate}, level="ERROR")
                raise
            obs.update(
                output={
                    "rows": result.row_count,
                    "columns": result.columns,
                    "truncated": result.truncated,
                    "ms": round(result.elapsed_ms, 1),
                }
            )
        log_event("sql.executed", rows=result.row_count, ms=round(result.elapsed_ms, 1))
        return result
