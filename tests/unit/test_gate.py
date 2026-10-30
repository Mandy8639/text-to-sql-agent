"""The regression gate's rules: nothing here calls the LLM."""

import json

import pytest

from evals.dataset import load_split
from evals.gate import (
    BASELINE as BASELINE_FILE,
)
from evals.gate import (
    CRITICAL,
    MAX_RECHECKS,
    GateResult,
    decide,
    render,
    to_recheck,
)

BASELINE = {
    "meta": {"commit": "abc1234", "model": "claude-haiku-5-5"},
    "correct": {"agg-02": True, "agg-05": True, "ref-01": True, "ts-06": False},
    "unstable": ["win-10"],
    "execution_accuracy": 97.0,
    "abstention_accuracy": 100.0,
    "avg_cost_usd": 0.001,
    "latency_ms_p95": 4000.0,
}
ALL_PASS = {"agg-02": True, "agg-05": True, "ref-01": True, "ts-06": False}


def _decide(current=None, rechecked=None, cost=0.001, p95=4000.0):
    return decide(BASELINE, current or ALL_PASS, rechecked or {}, avg_cost_usd=cost, p95_ms=p95)


def test_no_change_passes():
    result = _decide()
    assert result.status == "pass" and result.exit_code == 0 and not result.broken


def test_failure_that_passes_on_recheck_is_a_flake():
    result = _decide({**ALL_PASS, "ref-01": False}, {"ref-01": True})
    assert result.status == "pass" and result.flaky == ["ref-01"]


def test_critical_question_failing_twice_blocks():
    result = _decide({**ALL_PASS, "ref-01": False}, {"ref-01": False})
    assert result.status == "regression" and result.exit_code == 1
    assert "ref-01" in result.reasons[0]


def test_one_ordinary_break_is_tolerated_two_are_not():
    one = _decide({**ALL_PASS, "agg-02": False}, {"agg-02": False})
    assert one.status == "pass" and one.broken == ["agg-02"]
    two = {**ALL_PASS, "agg-02": False, "agg-05": False}
    assert _decide(two, {"agg-02": False, "agg-05": False}).status == "regression"


def test_question_not_rechecked_counts_as_broken():
    # More failures than MAX_RECHECKS skip the recheck, and must not read as "passed".
    assert _decide({**ALL_PASS, "agg-02": False}).broken == ["agg-02"]


def test_unstable_question_cannot_break():
    assert _decide({**ALL_PASS, "win-10": False}).status == "pass"


def test_new_question_cannot_break():
    assert _decide({**ALL_PASS, "new-01": False}).status == "pass"


def test_fixed_question_is_reported():
    assert _decide({**ALL_PASS, "ts-06": True}).fixed == ["ts-06"]


@pytest.mark.parametrize(
    ("cost", "p95", "expected"),
    [
        (0.00125, 4000.0, "pass"),  # exactly at the limit
        (0.0013, 4000.0, "regression"),
        (0.001, 6000.0, "pass"),
        (0.001, 6100.0, "regression"),
    ],
)
def test_cost_and_latency_limits(cost, p95, expected):
    assert _decide(cost=cost, p95=p95).status == expected


def test_recheck_only_stable_failures():
    current = {**ALL_PASS, "agg-02": False, "new-01": False}
    assert to_recheck(BASELINE, current) == ["agg-02"]


def test_too_many_failures_skip_the_recheck():
    ids = {f"q{i}": False for i in range(MAX_RECHECKS + 1)}
    assert to_recheck({"correct": dict.fromkeys(ids, True)}, ids) == []


def test_inconclusive_has_its_own_exit_code():
    assert GateResult(status="inconclusive").exit_code == 2


def test_report_names_what_broke():
    result = _decide({**ALL_PASS, "ref-01": False}, {"ref-01": False})
    current = {
        "meta": {"model": "claude-haiku-5-5"},
        "metrics": {
            "execution_accuracy": 97.0,
            "abstention_accuracy": 75.0,
            "avg_cost_usd": 0.001,
            "latency_ms_p95": 4000.0,
        },
    }
    report = render(result, BASELINE, current)
    assert "Eval gate failed" in report and "Broke: ref-01" in report


def test_critical_questions_are_dev_questions():
    # The gate must never read test or hold-out questions (see evals/gate.py).
    assert {q.id for q in load_split("dev")} >= CRITICAL


def test_recorded_baseline_covers_exactly_the_dev_split():
    recorded = json.loads(BASELINE_FILE.read_text())
    assert set(recorded["correct"]) | set(recorded["unstable"]) == {q.id for q in load_split("dev")}
    assert {i for i, ok in recorded["correct"].items() if ok} >= CRITICAL
