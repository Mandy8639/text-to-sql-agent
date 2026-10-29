"""Unit tests for CLI table formatting."""

from decimal import Decimal

from app.cli import format_table
from app.db.executor import QueryResult


def _result(rows, truncated=False):
    return QueryResult(columns=["state", "revenue"], rows=rows, truncated=truncated, elapsed_ms=1)


def test_aligns_columns_and_formats_values():
    out = format_table(_result([("SP", Decimal("1234.50")), ("RJ", None)]), max_rows=10)
    lines = out.splitlines()
    assert lines[0] == "state | revenue "
    assert lines[2] == "SP    | 1,234.50"
    assert lines[3] == "RJ    | NULL    "
    assert lines[-1] == "(2 of 2 rows shown)"


def test_reports_hidden_and_truncated_rows():
    rows = [("SP", Decimal(1))] * 5
    assert format_table(_result(rows), max_rows=2).splitlines()[-1] == "(2 of 5 rows shown)"
    out = format_table(_result(rows, truncated=True), max_rows=2)
    assert out.splitlines()[-1] == "(2 of 5+ rows shown)"
