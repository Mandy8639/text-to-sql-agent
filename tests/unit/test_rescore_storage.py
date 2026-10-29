"""Stored result rows must re-score exactly like the live rows, and summary rebuild works."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from evals.compare import compare_results, normalize_value
from evals.metrics import QuestionScore, aggregate, to_jsonable
from evals.summary import rebuild_summary

VALUES = [
    None,
    "SP",
    "unknown",
    True,
    42,
    Decimal("16008872.12"),
    4.0868,
    date(2017, 11, 1),
    datetime(2017, 11, 1),
    datetime(2018, 1, 1, 10, 30, 5),
    timedelta(days=12, hours=13),
]


def test_jsonable_values_normalize_like_originals():
    for v in VALUES:
        stored = json.loads(json.dumps(to_jsonable(v)))
        assert normalize_value(stored) == normalize_value(v), v


def test_stored_rows_give_same_verdict():
    gold_cols, gold_rows = ["month", "revenue"], [(date(2018, 1, 1), Decimal("1102639.41"))]
    live = [(datetime(2018, 1, 1), Decimal("1102639.41"))]
    stored = [tuple(json.loads(json.dumps([to_jsonable(v) for v in r]))) for r in live]
    assert compare_results(gold_cols, gold_rows, ["m", "r"], live).match
    assert compare_results(gold_cols, gold_rows, ["m", "r"], stored).match


def _score(qid, correct, difficulty="easy"):
    return QuestionScore(
        id=qid, question="?", category="aggregation", difficulty=difficulty, expected="answer",
        outcome="answer", correct=correct, reason="", sql="SELECT 1", error=None,
        error_stage=None, note=None, pred_rows=1, gold_rows=1, input_tokens=10,
        output_tokens=5, cost_usd=0.01, latency_ms=1000.0,
    )  # fmt: skip


def test_rebuild_summary_skips_partial_runs(tmp_path):
    def write(name, partial, scores):
        meta = {"run_at": f"2026-10-29T1{name}:00:00Z", "agent_version": f"v-{name}",
                "model": "m", "split": "test", "commit": "abc", "partial": partial}  # fmt: skip
        run = {
            "meta": meta,
            "metrics": aggregate(scores),
            "questions": [s.to_dict() for s in scores],
        }
        (tmp_path / f"{name}.json").write_text(json.dumps(run))

    write("1", False, [_score("a", True), _score("b", False)])
    write("2", True, [_score("a", True)])
    text = rebuild_summary(tmp_path, tmp_path / "summary.md")
    assert "| v-1 |" in text and "**50.0**" in text
    assert "v-2" not in text


def test_rebuild_summary_skips_invalid_and_superseded_runs(tmp_path):
    def write(name, scores, **meta_extra):
        meta = {"run_at": "2026-10-29T10:00:00Z", "agent_version": f"v-{name}", "model": "m",
                "split": "test", "commit": "abc", **meta_extra}  # fmt: skip
        run = {"meta": meta, "metrics": {}, "questions": [s.to_dict() for s in scores]}
        (tmp_path / f"{name}.json").write_text(json.dumps(run))

    write("outage", [_score("a", False)], invalid=True)
    write("orig", [_score("a", False)])
    write("fixed", [_score("a", True)], rescored_from="orig.json")
    text = rebuild_summary(tmp_path, tmp_path / "summary.md")
    assert "v-outage" not in text and "v-orig" not in text
    assert "v-fixed" in text and "rescored" in text
