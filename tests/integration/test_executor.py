"""Executor behaviour against the real database, as the read-only agent role."""

import pytest

from app.db.executor import QueryExecutionError, execute_readonly

pytestmark = pytest.mark.integration


def test_returns_columns_and_rows(agent_engine):
    res = execute_readonly(
        agent_engine, "SELECT order_status, count(*) AS n FROM orders GROUP BY 1 ORDER BY n DESC"
    )
    assert res.columns == ["order_status", "n"]
    assert res.rows[0] == ("delivered", 96478)
    assert not res.truncated
    assert res.elapsed_ms > 0


def test_truncates_at_max_rows(agent_engine):
    res = execute_readonly(agent_engine, "SELECT seller_id FROM sellers", max_rows=10)
    assert res.row_count == 10
    assert res.truncated


def test_exactly_max_rows_is_not_truncated(agent_engine):
    res = execute_readonly(agent_engine, "SELECT * FROM generate_series(1, 5)", max_rows=5)
    assert res.row_count == 5
    assert not res.truncated


@pytest.mark.parametrize(
    ("sql", "sqlstate", "fragment"),
    [
        ("SELEC 1", "42601", "syntax error"),
        ("SELECT nope FROM orders", "42703", 'column "nope" does not exist'),
        ("SELECT * FROM no_such_table", "42P01", 'relation "no_such_table" does not exist'),
        ("SELECT 1/0", "22012", "division by zero"),
        ("DELETE FROM orders", "25006", "read-only transaction"),
    ],
)
def test_database_errors_are_wrapped(agent_engine, sql, sqlstate, fragment):
    with pytest.raises(QueryExecutionError) as info:
        execute_readonly(agent_engine, sql)
    assert info.value.sqlstate == sqlstate
    assert fragment in info.value.message
    assert "\n" not in info.value.message  # one clean line, no driver internals


def test_timeout(agent_engine):
    with pytest.raises(QueryExecutionError) as info:
        execute_readonly(agent_engine, "SELECT pg_sleep(2)", timeout_ms=200)
    assert info.value.timed_out


def test_read_only_even_if_session_was_tampered(agent_engine):
    """A pooled connection left in READ WRITE mode must still run the next query read-only."""
    with agent_engine.connect() as conn:
        conn.exec_driver_sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        conn.commit()
    with pytest.raises(QueryExecutionError) as info:
        execute_readonly(agent_engine, "CREATE TEMP TABLE t (x int)")
    # Rejected by the per-transaction READ ONLY, before privileges are even checked.
    assert info.value.sqlstate == "25006"
    agent_engine.dispose()  # don't leave the tampered connection in the pool
