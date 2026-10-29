"""Unit tests for pulling SQL out of model replies."""

import pytest

from app.agent.sql_extract import SQLExtractionError, extract_note, extract_sql


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("```sql\nSELECT 1;\n```", "SELECT 1"),
        ("```SQL\nSELECT 1\n```", "SELECT 1"),
        ("```postgresql\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("Here is the query:\n```sql\nSELECT 1\n```\nIt counts things.", "SELECT 1"),
        ("```sql\nSELECT 1\n```\nor\n```sql\nSELECT 2\n```", "SELECT 1"),  # first block wins
        ("SELECT count(*) FROM orders;", "SELECT count(*) FROM orders"),
        ("  with t as (select 1) select * from t", "with t as (select 1) select * from t"),
        ("```sql\nSELECT 1;;  \n```", "SELECT 1"),
    ],
)
def test_extracts_sql(reply, expected):
    assert extract_sql(reply) == expected


def test_keeps_multiline_formatting():
    reply = "```sql\nSELECT a,\n       b\nFROM t\nWHERE x = 1\n```"
    assert extract_sql(reply) == "SELECT a,\n       b\nFROM t\nWHERE x = 1"


def test_semicolon_inside_string_is_kept():
    assert extract_sql("```sql\nSELECT ';' AS s;\n```") == "SELECT ';' AS s"


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("```sql\nSELECT 1\n```\nAssumed delivered orders only.", "Assumed delivered orders only."),
        ("Query:\n```sql\nSELECT 1\n```", "Query:"),
        ("Before:\n\n```sql\nSELECT 1\n```\n\nAfter.", "Before:\n\nAfter."),
        ("```sql\nSELECT 1\n```", None),
        ("SELECT 1", None),
    ],
)
def test_extract_note(reply, expected):
    assert extract_note(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "I can't answer that from this data.",
        "The answer is to SELECT the right table.",  # SELECT not at the start
        "```sql\n\n```",
        "```sql\n;\n```",
    ],
)
def test_raises_when_no_sql(reply):
    with pytest.raises(SQLExtractionError):
        extract_sql(reply)
