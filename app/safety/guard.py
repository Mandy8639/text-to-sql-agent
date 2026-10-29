"""Safe execution: validate, then run read-only with a row cap and statement timeout.

Layers, outermost first (defense in depth):
1. injection screen on the question (injection.py), before any LLM call
2. SQL validator (sql_validator.py): allowlist of statement types, tables and functions
3. executor: READ ONLY transaction, statement timeout, capped fetch (app/db/executor.py)
4. database role: SELECT on olist only (scripts/readonly_role.py)
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine

from app.db.executor import QueryResult, execute_readonly
from app.safety.sql_validator import ValidatedSQL, validate_sql


@dataclass
class SafeExecutor:
    engine: Engine
    allowed_tables: set[str]
    max_rows: int = 1000
    timeout_ms: int = 15_000

    @classmethod
    def from_doc(cls, engine: Engine, doc: dict[str, Any], **kwargs) -> "SafeExecutor":
        return cls(engine, {t.lower() for t in doc["tables"]}, **kwargs)

    def check(self, sql: str) -> ValidatedSQL:
        """Validate only (raises UnsafeSQLError). LIMIT max_rows + 1 keeps `truncated` exact."""
        return validate_sql(sql, self.allowed_tables, max_rows=self.max_rows + 1)

    def run(self, checked: ValidatedSQL) -> QueryResult:
        return execute_readonly(
            self.engine, checked.sql, max_rows=self.max_rows, timeout_ms=self.timeout_ms
        )

    def __call__(self, sql: str) -> QueryResult:
        return self.run(self.check(sql))
