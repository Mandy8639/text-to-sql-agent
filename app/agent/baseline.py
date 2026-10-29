"""Baseline agent: question -> full-schema prompt -> LLM -> SQL -> read-only execution.

Deliberately naive (no retrieval, no examples, no retries). It sets the "before" score that
every later agent version is measured against.
"""

import time
from collections.abc import Callable

from sqlalchemy import Engine

from app.agent.prompts import load_prompt
from app.agent.schema_render import render_bare_schema, render_schema
from app.agent.sql_extract import SQLExtractionError, extract_note, extract_sql
from app.agent.types import AgentResult, LLMCall
from app.db.executor import QueryExecutionError, QueryResult, execute_readonly
from app.llm.base import LLMError, LLMProvider
from app.schema_doc import load_schema_doc

PROMPT_NAME = "baseline"
PROMPT_VERSION = 1
AGENT_VERSION = f"baseline-v{PROMPT_VERSION}"

Executor = Callable[[str], QueryResult]


def build_system_prompt() -> str:
    """The full system prompt: instructions plus the entire rendered schema doc."""
    template = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    return template.substitute(schema=render_schema(load_schema_doc()))


def build_bare_system_prompt() -> str:
    """Ablation prompt: same instructions, but only table/column/key names and types."""
    template = load_prompt("baseline_raw", 1)
    return template.substitute(schema=render_bare_schema(load_schema_doc()))


class BaselineAgent:
    """Answers a question with one LLM call and one query, and reports where any failure was."""

    version = AGENT_VERSION

    def __init__(self, llm: LLMProvider, execute: Executor) -> None:
        # `execute` is injected so tests can run the agent without a database.
        self.llm = llm
        self.execute = execute
        self.system_prompt = self._build_prompt()

    def _build_prompt(self) -> str:
        return build_system_prompt()

    @classmethod
    def from_engine(cls, llm: LLMProvider, agent_engine: Engine, **exec_kwargs) -> "BaselineAgent":
        """Build an agent that runs queries on `agent_engine` (must be the read-only role)."""
        return cls(llm, lambda sql: execute_readonly(agent_engine, sql, **exec_kwargs))

    def answer(self, question: str) -> AgentResult:
        """Run the pipeline. Expected failures are returned in the result, never raised."""
        start = time.perf_counter()
        out = AgentResult(question=question, agent_version=self.version)
        try:
            reply = self.llm.complete(self.system_prompt, question)
            out.raw_reply = reply.text
            out.llm_calls.append(
                LLMCall(reply.model, reply.input_tokens, reply.output_tokens, reply.latency_ms)
            )
            out.note = extract_note(reply.text)
            out.sql = extract_sql(reply.text)
            out.result = self.execute(out.sql)
        except LLMError as exc:
            out.error, out.error_stage = str(exc), "llm"
        except SQLExtractionError as exc:
            out.error, out.error_stage = str(exc), "extract"
        except QueryExecutionError as exc:
            out.error, out.error_stage = exc.message, "execute"
        out.total_latency_ms = (time.perf_counter() - start) * 1000
        return out


class BareSchemaBaselineAgent(BaselineAgent):
    """Documentation ablation: the baseline pipeline with an undocumented schema.

    Measures how much of the baseline's accuracy comes from schema.yaml's descriptions and
    business rules rather than from the model.
    """

    version = "baseline-raw-v1"

    def _build_prompt(self) -> str:
        return build_bare_system_prompt()
