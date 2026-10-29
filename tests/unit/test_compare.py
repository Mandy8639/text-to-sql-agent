"""Unit tests for result-set matching (execution accuracy)."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from evals.compare import compare_results, normalize_value


def match(gold_cols, gold_rows, pred_cols, pred_rows, order_matters=False):
    return compare_results(gold_cols, gold_rows, pred_cols, pred_rows, order_matters=order_matters)


# --- should match ----------------------------------------------------------------------------


def test_identical():
    assert match(["n"], [(5,)], ["n"], [(5,)]).match


def test_column_names_ignored():
    assert match(["total_orders"], [(99441,)], ["count"], [(99441,)]).match


def test_row_order_ignored_by_default():
    gold = [("SP", 10), ("RJ", 5)]
    assert match(["s", "n"], gold, ["s", "n"], list(reversed(gold))).match


def test_column_order_ignored():
    assert match(["s", "n"], [("SP", 10), ("RJ", 5)], ["n", "s"], [(10, "SP"), (5, "RJ")]).match


def test_extra_predicted_columns_allowed():
    gold = [("bed_bath_table",), ("health_beauty",)]
    pred = [("health_beauty", 9634), ("bed_bath_table", 11097)]
    assert match(["category"], gold, ["category", "units"], pred).match


def test_numeric_tolerance_and_types():
    assert match(["v"], [(Decimal("4.09"),)], ["v"], [(4.0868,)]).match
    assert match(["v"], [(Decimal("16008872.12"),)], ["v"], [(16008872.1234,)]).match
    assert match(["n"], [(625,)], ["n"], [(Decimal("625"),)]).match


def test_strings_case_and_whitespace():
    assert match(["d"], [("Monday",)], ["d"], [("monday   ",)]).match


def test_unknown_equals_null():
    assert match(["c"], [("unknown",)], ["c"], [(None,)]).match


@pytest.mark.parametrize(
    "pred",
    [
        date(2017, 11, 1),
        datetime(2017, 11, 1),
        "2017-11",
        "2017-11-01",
        "2017-11-01 00:00:00",
    ],
)
def test_month_representations(pred):
    assert match(["month"], [(date(2017, 11, 1),)], ["m"], [(pred,)]).match


def test_year_number_matches_year_date():
    gold = [(2017, 45101), (2018, 54011)]
    pred = [(datetime(2017, 1, 1), 45101), (datetime(2018, 1, 1), 54011)]
    assert match(["year", "orders"], gold, ["y", "n"], pred).match


def test_interval_compared_as_days():
    assert match(["days"], [(Decimal("12.56"),)], ["d"], [(timedelta(days=12, hours=13.4),)]).match


def test_order_matters_when_flagged():
    gold = [("a", 1), ("b", 2)]
    assert match(["c", "r"], gold, ["c", "r"], gold, order_matters=True).match


def test_both_empty():
    assert match(["x"], [], ["y"], []).match


# --- should not match ------------------------------------------------------------------------


def test_wrong_value():
    res = match(["n"], [(625,)], ["n"], [(626,)])
    assert not res.match
    assert "no predicted column" in res.reason


def test_wrong_row_count():
    res = match(["s"], [("SP",), ("RJ",)], ["s"], [("SP",)])
    assert not res.match
    assert "row count" in res.reason


def test_missing_column():
    assert not match(["s", "n"], [("SP", 10)], ["s"], [("SP",)]).match


def test_order_violation_when_flagged():
    gold = [("a", 1), ("b", 2)]
    pred = [("b", 2), ("a", 1)]
    assert not match(["c", "r"], gold, ["c", "r"], pred, order_matters=True).match


def test_rows_must_line_up_not_just_columns():
    # Each column has the same values, but paired differently: SP-5, RJ-10 is a wrong answer.
    gold = [("SP", 10), ("RJ", 5)]
    pred = [("SP", 5), ("RJ", 10)]
    res = match(["s", "n"], gold, ["s", "n"], pred)
    assert not res.match
    assert "rows do not line up" in res.reason


def test_percentage_vs_fraction_is_different():
    assert not match(["pct"], [(Decimal("97.02"),)], ["p"], [(0.9702,)]).match


def test_relative_tolerance_is_tight_for_big_totals():
    # Off by about 270k (canceled orders included): must fail.
    assert not match(["r"], [(Decimal("15738280.00"),)], ["r"], [(16008872.12,)]).match


def test_different_months_do_not_match():
    assert not match(["m"], [(date(2017, 11, 1),)], ["m"], [("2017-12",)]).match


def test_normalize_value_basics():
    assert normalize_value(None) is None
    assert normalize_value(Decimal("1.50")) == 1.5
    assert normalize_value("  SP ") == "sp"
    assert normalize_value(datetime(2018, 1, 1, 10, 30)) == "2018-01-01 10:30:00"
