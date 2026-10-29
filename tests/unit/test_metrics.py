"""Unit tests for per-question scoring and run-level aggregation."""

from app.agent.types import AgentResult, LLMCall
from app.db.executor import QueryResult
from evals.dataset import Question
from evals.metrics import aggregate, score_question


def _q(expected="answer", difficulty="easy", category="aggregation", qid="x"):
    return Question(
        id=qid,
        split="test",
        category=category,
        difficulty=difficulty,
        expected=expected,
        question="?",
        gold_sql="SELECT 1" if expected == "answer" else None,
        reason=None if expected == "answer" else "r",
    )


def _qr(rows, cols=("n",), truncated=False):
    return QueryResult(columns=list(cols), rows=rows, truncated=truncated, elapsed_ms=1.0)


def _res(result=None, error=None, stage=None, abstention=None, latency=1000.0):
    return AgentResult(
        question="?",
        agent_version="t",
        sql="SELECT 1" if result else None,
        result=result,
        error=error,
        error_stage=stage,
        abstention=abstention,
        llm_calls=[LLMCall("claude-opus-5-5", 6000, 200, latency)],
        total_latency_ms=latency,
    )


GOLD = _qr([(5,)])


def test_correct_answer():
    s = score_question(_q(), _res(_qr([(5,)])), GOLD)
    assert s.correct and s.outcome == "answer"
    assert s.cost_usd > 0


def test_wrong_answer():
    assert not score_question(_q(), _res(_qr([(6,)])), GOLD).correct


def test_execution_error_is_wrong():
    s = score_question(_q(), _res(error="boom", stage="execute"), GOLD)
    assert not s.correct and s.outcome == "error" and s.error_stage == "execute"


def test_truncated_result_is_wrong():
    s = score_question(_q(), _res(_qr([(5,)], truncated=True)), GOLD)
    assert not s.correct and s.reason == "result truncated"


def test_abstaining_on_answerable_is_wrong():
    s = score_question(_q(), _res(abstention="clarify"), GOLD)
    assert not s.correct and s.outcome == "clarify"


def test_refusal_expected():
    assert score_question(_q("refuse"), _res(abstention="refuse"), None).correct
    assert score_question(_q("refuse"), _res(abstention="clarify"), None).correct
    # Answering a destructive or unanswerable question is wrong, even if the SQL ran.
    assert not score_question(_q("refuse"), _res(_qr([(1,)])), None).correct


def test_aggregate():
    scores = [
        score_question(_q(qid="a", difficulty="easy"), _res(_qr([(5,)]), latency=1000), GOLD),
        score_question(_q(qid="b", difficulty="hard"), _res(_qr([(9,)]), latency=3000), GOLD),
        score_question(_q(qid="c", difficulty="hard"), _res(error="e", stage="execute"), GOLD),
        score_question(
            _q("refuse", qid="d", category="refusal", difficulty="medium"), _res(_qr([(1,)])), None
        ),
    ]
    m = aggregate(scores)
    assert m["n"] == 4 and m["n_answerable"] == 3 and m["n_abstain_expected"] == 1
    assert m["execution_accuracy"] == 33.3
    assert m["overall_accuracy"] == 25.0
    assert m["valid_sql_rate"] == 66.7
    assert m["abstention_accuracy"] == 0.0
    assert m["errors_by_stage"] == {"execute": 1}
    assert m["by_difficulty"]["easy"]["accuracy"] == 100.0
    assert m["by_difficulty"]["hard"] == {"n": 2, "correct": 0, "accuracy": 0.0}
    assert m["latency_ms_p50"] == 1000
    assert m["latency_ms_p95"] == 3000
