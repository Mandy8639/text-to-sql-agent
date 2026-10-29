"""Run a generated query on the read-only connection and return its rows."""

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

# Postgres SQLSTATE for "canceling statement due to statement timeout".
_QUERY_CANCELED = "57014"


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool  # True if the query returned more than `max_rows` rows
    elapsed_ms: float

    @property
    def row_count(self) -> int:
        return len(self.rows)


class QueryExecutionError(RuntimeError):
    """The database rejected or failed the query. `message` is safe to show the user or LLM."""

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.sqlstate = sqlstate

    @property
    def timed_out(self) -> bool:
        return self.sqlstate == _QUERY_CANCELED


def execute_readonly(
    engine: Engine, sql: str, *, max_rows: int = 1000, timeout_ms: int = 15_000
) -> QueryResult:
    """Execute `sql` in a READ ONLY transaction with a statement timeout; always roll back.

    The transaction-level settings are applied per call rather than trusting the session,
    because pooled connections can carry session changes from earlier use (see D12). At most
    `max_rows` rows are fetched; `truncated` reports whether more existed.
    """
    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            result = conn.execute(text(sql))
            if not result.returns_rows:
                raise QueryExecutionError("Statement returned no rows (not a SELECT query).")
            columns = list(result.keys())
            rows = [tuple(r) for r in result.fetchmany(max_rows + 1)]
            conn.rollback()
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        # First line of the Postgres message, e.g. 'column "x" does not exist'. No driver
        # internals or connection details.
        detail = str(exc.orig).strip().splitlines()[0] if exc.orig else type(exc).__name__
        raise QueryExecutionError(detail, sqlstate) from exc

    return QueryResult(
        columns=columns,
        rows=rows[:max_rows],
        truncated=len(rows) > max_rows,
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )
