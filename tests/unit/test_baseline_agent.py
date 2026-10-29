"""Unit tests for the baseline agent pipeline, with a fake LLM and fake executor."""

from app.agent.baseline import AGENT_VERSION, BaselineAgent, build_system_prompt
from app.db.executor import QueryExecutionError, QueryResult
from app.llm.base import LLMError, LLMResponse
from app.schema_doc import load_schema_doc


class FakeLLM:
    model = "fake-model"

    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system, user, *, max_tokens=None):
        self.prompts.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(self.reply, self.model, 1000, 50, 12.0, "end_turn")


ROWS = QueryResult(columns=["n"], rows=[(42,)], truncated=False, elapsed_ms=1.0)


def _executor(result=ROWS):
    calls = []

    def execute(sql):
        calls.append(sql)
        if isinstance(result, Exception):
            raise result
        return result

    execute.calls = calls
    return execute


def test_happy_path():
    llm, execute = FakeLLM("```sql\nSELECT 42 AS n;\n```"), _executor()
    res = BaselineAgent(llm, execute).answer("How many?")

    assert res.ok
    assert res.sql == "SELECT 42 AS n"
    assert execute.calls == ["SELECT 42 AS n"]
    assert res.result.rows == [(42,)]
    assert res.agent_version == AGENT_VERSION
    assert (res.input_tokens, res.output_tokens) == (1000, 50)
    assert llm.prompts[0][1] == "How many?"


def test_llm_error_is_reported_not_raised():
    execute = _executor()
    res = BaselineAgent(FakeLLM(LLMError("API down")), execute).answer("q")
    assert (res.ok, res.error_stage, res.error) == (False, "llm", "API down")
    assert res.sql is None
    assert execute.calls == []


def test_missing_sql_is_reported():
    execute = _executor()
    res = BaselineAgent(FakeLLM("Sorry, no idea."), execute).answer("q")
    assert res.error_stage == "extract"
    assert res.raw_reply == "Sorry, no idea."
    assert execute.calls == []


def test_execution_error_keeps_sql():
    error = QueryExecutionError('column "foo" does not exist', "42703")
    res = BaselineAgent(FakeLLM("```sql\nSELECT foo\n```"), _executor(error)).answer("q")
    assert res.error_stage == "execute"
    assert res.error == 'column "foo" does not exist'
    assert res.sql == "SELECT foo"
    assert res.result is None


def test_system_prompt_contains_full_schema():
    prompt = build_system_prompt()
    doc = load_schema_doc()
    for table, spec in doc["tables"].items():
        assert f"### {table}" in prompt
        for column in spec["columns"]:
            assert f"- {column} " in prompt
    assert "$schema" not in prompt  # placeholder was substituted
    assert "Business rules" in prompt


def test_system_prompt_is_deterministic():
    assert build_system_prompt() == build_system_prompt()
