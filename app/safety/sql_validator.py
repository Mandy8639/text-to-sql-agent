"""Validate generated SQL before it reaches the database (an allowlist, not a blocklist).

A query is allowed only if it is:
- exactly ONE statement,
- a read query: SELECT, or a set operation (UNION / INTERSECT / EXCEPT) of SELECTs; CTEs allowed,
- free of any write, DDL, permission, session, locking or utility node anywhere in the tree
  (this catches `WITH d AS (DELETE ... RETURNING *) SELECT ...` and `SELECT ... INTO`),
- reading only `olist` tables (qualified or not) or its own CTEs: no pg_catalog,
  information_schema, other schemas, or unknown names (pg_catalog is always on the search path,
  so an unqualified `pg_user` must be rejected by name),
- calling no dangerous functions (pg_*, lo_*, dblink*, config, and XML export of whole tables).

Allowed queries get a LIMIT on the outermost query (added, or lowered if above the cap).
The read-only database role remains the final backstop if this layer has a gap (D12).
"""

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.db.executor import QueryExecutionError

# sqlglot warns when it falls back to a generic Command (VACUUM, DO, SET ROLE); we reject
# those anyway, so the warning is noise.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECT = "postgres"
ALLOWED_SCHEMAS = {"", "olist"}

# Node types that must not appear anywhere in an allowed query. Looked up by name so a
# sqlglot version that lacks one of them doesn't break the import.
_DENIED_NODE_NAMES = [
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Grant", "Revoke", "Copy", "Command", "Into", "Lock", "Set",
    "Transaction", "Commit", "Rollback", "Use", "Analyze", "Pragma", "LoadData", "Cache",
    "Uncache", "Refresh", "Describe", "Kill",
]  # fmt: skip
DENIED_NODES = tuple(t for n in _DENIED_NODE_NAMES if (t := getattr(exp, n, None)))
_SET_OPERATION = getattr(exp, "SetOperation", None) or exp.Union

DENIED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink")
DENIED_FUNCTIONS = {
    "set_config", "current_setting",
    "query_to_xml", "query_to_xmlschema", "query_to_xml_and_xmlschema",
    "table_to_xml", "table_to_xmlschema", "table_to_xml_and_xmlschema",
    "cursor_to_xml", "cursor_to_xmlschema", "schema_to_xml", "database_to_xml",
    "inet_server_addr", "inet_server_port", "txid_current", "nextval", "setval",
}  # fmt: skip


class UnsafeSQLError(QueryExecutionError):
    """The query was rejected before reaching the database. `message` says why."""


@dataclass(frozen=True)
class ValidatedSQL:
    original: str
    sql: str  # what will be executed (LIMIT enforced)
    limit_added: bool


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def validate_sql(sql: str, allowed_tables: set[str], *, max_rows: int) -> ValidatedSQL:
    """Return the query to execute, or raise UnsafeSQLError explaining the rejection."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as exc:
        raise UnsafeSQLError(f"Could not parse the query: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError(f"Exactly one statement is allowed (found {len(statements)}).")
    root = statements[0]

    if not isinstance(root, exp.Select | _SET_OPERATION):
        raise UnsafeSQLError(f"Only SELECT queries are allowed (got {root.key.upper()}).")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        if isinstance(node, DENIED_NODES):
            raise UnsafeSQLError(f"{node.key.upper()} is not allowed in a read-only query.")
        if isinstance(node, exp.Table) and node.name:
            schema, name = node.db.lower(), node.name.lower()
            if node.catalog or schema not in ALLOWED_SCHEMAS:
                raise UnsafeSQLError(
                    f"Table {node.sql(dialect=DIALECT)} is outside the olist schema."
                )
            if name not in allowed_tables and not (schema == "" and name in cte_names):
                raise UnsafeSQLError(f"Unknown or disallowed table: {name}.")
        if isinstance(node, exp.Func):
            fn = _function_name(node)
            if fn.startswith(DENIED_FUNCTION_PREFIXES) or fn in DENIED_FUNCTIONS:
                raise UnsafeSQLError(f"Function {fn}() is not allowed.")

    limit = root.args.get("limit")
    current = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        current = int(limit.expression.name) if limit.expression.is_int else None
    limit_added = current is None or current > max_rows
    if limit_added:
        root = root.limit(max_rows, copy=True)
    return ValidatedSQL(original=sql, sql=root.sql(dialect=DIALECT), limit_added=limit_added)
