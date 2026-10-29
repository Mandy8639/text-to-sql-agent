"""Score one question, then aggregate scores into the metrics reported per run."""

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.agent.types import AgentResult
from app.db.executor import QueryResult
from app.llm.pricing import estimate_cost_usd
from evals.compare import compare_results
from evals.dataset import Question


@dataclass
class QuestionScore:
    id: str
    question: str
    category: str
    difficulty: str
    expected: str
    outcome: str  # answer | refuse | clarify | error
    correct: bool
    reason: str
    sql: str | None
    error: str | None
    error_stage: str | None
    note: str | None
    pred_rows: int | None
    gold_rows: int | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: float
    # The agent's result, stored so a run can be re-scored after gold SQL changes
    # (evals/rescore.py) without calling the LLM again. Capped at PRED_ROWS_STORED rows.
    pred_columns: list[str] | None = None
    pred_data: list[list] | None = None
    pred_truncated: bool = False
    # Retrieval and self-correction traces (Phase 4).
    tables: list[str] | None = None
    example_ids: list[str] | None = None
    attempts: int = 1
    attempt_log: list[dict] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


PRED_ROWS_STORED = 200


def to_jsonable(v: Any) -> Any:
    """JSON-safe value that compare.normalize_value() treats exactly like the original."""
    if v is None or isinstance(v, str | bool):
        return v
    if isinstance(v, int | float | Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds() / 86400
    return str(v)


def grade_answer(
    q: Question, gold: QueryResult, columns, rows, truncated: bool
) -> tuple[bool, str]:
    """Execution-accuracy verdict for an answer, shared by live scoring and re-scoring."""
    if truncated:
        return False, "result truncated"
    m = compare_results(gold.columns, gold.rows, columns, rows, order_matters=q.order_matters)
    return m.match, m.reason


def score_question(q: Question, res: AgentResult, gold: QueryResult | None) -> QuestionScore:
    """Compare one agent result to the gold answer (or the expected abstention)."""
    if q.expected == "answer":
        assert gold is not None, f"{q.id}: gold result missing"
        if res.outcome != "answer":
            correct, reason = False, f"no answer ({res.outcome})"
        else:
            r = res.result
            correct, reason = grade_answer(q, gold, r.columns, r.rows, r.truncated)
    else:
        # Either form of abstention counts; the exact form is recorded in `outcome`.
        correct = res.outcome in ("refuse", "clarify")
        reason = f"expected {q.expected}, got {res.outcome}"

    model = res.llm_calls[0].model if res.llm_calls else ""
    return QuestionScore(
        id=q.id,
        question=q.question,
        category=q.category,
        difficulty=q.difficulty,
        expected=q.expected,
        outcome=res.outcome,
        correct=correct,
        reason=reason,
        sql=res.sql,
        error=res.error,
        error_stage=res.error_stage,
        note=res.note,
        pred_rows=res.result.row_count if res.result else None,
        gold_rows=gold.row_count if gold else None,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        cost_usd=estimate_cost_usd(model, res.input_tokens, res.output_tokens),
        latency_ms=res.total_latency_ms,
        pred_columns=list(res.result.columns) if res.result else None,
        pred_data=[[to_jsonable(v) for v in row] for row in res.result.rows[:PRED_ROWS_STORED]]
        if res.result
        else None,
        pred_truncated=bool(res.result and res.result.truncated),
        tables=res.tables or None,
        example_ids=res.example_ids or None,
        attempts=max(1, len(res.attempts)),
        attempt_log=res.attempts or None,
    )


def _pct(num: int, den: int) -> float | None:
    return round(100 * num / den, 1) if den else None


def _percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (simple and explainable; fine for ~100 samples)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def aggregate(scores: list[QuestionScore]) -> dict:
    """Headline metrics plus breakdowns by difficulty and category."""
    answerable = [s for s in scores if s.expected == "answer"]
    abstain = [s for s in scores if s.expected != "answer"]
    with_sql = [s for s in answerable if s.sql]
    costs = [s.cost_usd for s in scores if s.cost_usd is not None]
    latencies = [s.latency_ms for s in scores]
    retried = [s for s in scores if s.attempts > 1]

    def breakdown(key: str, pool: list[QuestionScore]) -> dict:
        groups: dict[str, list[QuestionScore]] = defaultdict(list)
        for s in pool:
            groups[getattr(s, key)].append(s)
        return {
            k: {
                "n": len(v),
                "correct": sum(s.correct for s in v),
                "accuracy": _pct(sum(s.correct for s in v), len(v)),
            }
            for k, v in sorted(groups.items())
        }

    errors: dict[str, int] = defaultdict(int)
    for s in scores:
        if s.error_stage:
            errors[s.error_stage] += 1

    return {
        "n": len(scores),
        "n_answerable": len(answerable),
        "n_abstain_expected": len(abstain),
        # Headline: answerable questions whose result set matches gold.
        "execution_accuracy": _pct(sum(s.correct for s in answerable), len(answerable)),
        # Every question, counting correct abstentions as correct.
        "overall_accuracy": _pct(sum(s.correct for s in scores), len(scores)),
        # Answerable questions where the generated SQL ran without error.
        "valid_sql_rate": _pct(sum(s.outcome == "answer" for s in answerable), len(answerable)),
        "sql_produced_rate": _pct(len(with_sql), len(answerable)),
        "abstention_accuracy": _pct(sum(s.correct for s in abstain), len(abstain)),
        "false_abstention_rate": _pct(
            sum(s.outcome in ("refuse", "clarify") for s in answerable), len(answerable)
        ),
        "errors_by_stage": dict(errors),
        # Self-correction: questions that needed more than one attempt, and how many ended right.
        "n_retried": len(retried),
        "retry_success_rate": _pct(sum(s.correct for s in retried), len(retried)),
        "avg_attempts": round(sum(s.attempts for s in scores) / len(scores), 2) if scores else 0,
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "avg_input_tokens": round(sum(s.input_tokens for s in scores) / len(scores))
        if scores
        else 0,
        "avg_output_tokens": round(sum(s.output_tokens for s in scores) / len(scores))
        if scores
        else 0,
        "avg_cost_usd": round(sum(costs) / len(costs), 5) if costs else None,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        # Difficulty = execution accuracy on answerable questions (abstention has its own metric);
        # category covers every question, so `refusal` and `ambiguous` show up.
        "by_difficulty": breakdown("difficulty", answerable),
        "by_category": breakdown("category", scores),
    }
