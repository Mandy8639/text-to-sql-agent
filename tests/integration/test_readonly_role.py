"""Prove the agent's database role can read but never write.

Every statement below must be rejected by Postgres itself (no application-level validation is
involved), and the data must be unchanged afterwards.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

# SQLSTATE codes: 25006 = read_only_sql_transaction, 42501 = insufficient_privilege
REJECTED = {"25006", "42501"}

WRITE_STATEMENTS = {
    "insert": "INSERT INTO olist.sellers VALUES ('x', '00000', 'nowhere', 'XX')",
    "update": "UPDATE olist.orders SET order_status = 'canceled'",
    "delete": "DELETE FROM olist.order_reviews",
    "truncate": "TRUNCATE olist.order_items",
    "drop_table": "DROP TABLE olist.customers",
    "alter_table": "ALTER TABLE olist.products ADD COLUMN hacked int",
    "create_table_olist": "CREATE TABLE olist.evil (id int)",
    "create_table_public": "CREATE TABLE public.evil (id int)",
    "create_temp_table": "CREATE TEMP TABLE evil (id int)",
    "drop_schema": "DROP SCHEMA olist CASCADE",
    "create_role": "CREATE ROLE evil",
    "grant": "GRANT INSERT ON olist.orders TO olist_agent",
}


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def test_can_select(agent_engine):
    with agent_engine.connect() as conn:
        # search_path is set to olist, so unqualified names work.
        n = conn.execute(text("SELECT count(*) FROM orders")).scalar_one()
    assert n > 0


@pytest.mark.parametrize("name", WRITE_STATEMENTS)
def test_write_is_rejected(agent_engine, name):
    with agent_engine.connect() as conn, pytest.raises(DBAPIError) as info:
        conn.execute(text(WRITE_STATEMENTS[name]))
    assert _sqlstate(info.value) in REJECTED, info.value


@pytest.mark.parametrize("name", ["insert", "update", "delete", "drop_table"])
def test_write_rejected_even_after_disabling_read_only(agent_engine, name):
    """Turning off read-only mode is allowed for a session, but privileges still block writes."""
    with agent_engine.connect() as conn:
        try:
            conn.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE"))
            conn.commit()
            with pytest.raises(DBAPIError) as info:
                conn.execute(text(WRITE_STATEMENTS[name]))
        finally:
            # Session settings survive in the pool; discard this tampered connection.
            conn.invalidate()
    assert _sqlstate(info.value) == "42501", info.value


def test_role_attributes(agent_engine):
    with agent_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        read_only = conn.execute(text("SHOW default_transaction_read_only")).scalar_one()
        timeout = conn.execute(text("SHOW statement_timeout")).scalar_one()
    assert not any(row)
    assert read_only == "on"
    assert timeout == "30s"


def test_data_unchanged_after_attacks(admin_engine, agent_engine):
    with admin_engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM olist.order_reviews")).scalar_one()
    for name in ["delete", "truncate"]:
        with agent_engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(WRITE_STATEMENTS[name]))
    with admin_engine.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM olist.order_reviews")).scalar_one()
    assert before == after
