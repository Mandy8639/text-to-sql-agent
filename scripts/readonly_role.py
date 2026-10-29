"""Create (or update) the read-only Postgres role the agent connects with.

Defense in depth: even if a generated query slips past SQL validation (Phase 5), this role
cannot change anything. It is
- granted SELECT on the `olist` schema only (no INSERT/UPDATE/DELETE/DDL privileges),
- stripped of database-level CREATE and TEMPORARY rights,
- forced into read-only transactions with a statement timeout,
- not a superuser and unable to create roles or databases.
"""

import logging

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

DATA_SCHEMA = "olist"
STATEMENT_TIMEOUT = "30s"


def ensure_readonly_role(conn: psycopg.Connection, agent_database_url: str) -> str:
    """Create/refresh the agent role described by `agent_database_url`. Returns the role name."""
    url = make_url(agent_database_url)
    role, password, database = url.username, url.password, url.database
    if not (role and password and database):
        raise ValueError("AGENT_DATABASE_URL must include a user, password, and database name.")

    role_id = sql.Identifier(role)
    db_id = sql.Identifier(database)
    schema_id = sql.Identifier(DATA_SCHEMA)

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(
            sql.SQL(
                "{} ROLE {} LOGIN PASSWORD {} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.SQL(verb), role_id, sql.Literal(password))
        )

        # Session defaults applied every time the role connects.
        for setting, value in [
            ("default_transaction_read_only", "on"),
            ("statement_timeout", STATEMENT_TIMEOUT),
            ("search_path", DATA_SCHEMA),
        ]:
            cur.execute(
                sql.SQL("ALTER ROLE {} SET {} = {}").format(
                    role_id, sql.Identifier(setting), sql.Literal(value)
                )
            )

        # By default every role (PUBLIC) may create temp tables and use the public schema.
        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db_id))
        cur.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db_id, role_id))

        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema_id, role_id))
        cur.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema_id, role_id)
        )

    log.info("read-only role %r ready (SELECT on %s only)", role, DATA_SCHEMA)
    return role
