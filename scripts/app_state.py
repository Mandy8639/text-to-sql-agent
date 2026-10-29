"""Create the writable `app` schema for the demo's spend counter: `python -m scripts.app_state`.

Least privilege, as for the other roles: `olist_app` (from APP_DATABASE_URL) can SELECT,
INSERT and UPDATE `app.spend_daily` and nothing else: no business data, no index, no DDL.
Idempotent; never drops existing spend rows.
"""

import logging

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import get_settings
from scripts.load_data import _psycopg_url

log = logging.getLogger("app_state")


def setup() -> None:
    settings = get_settings()
    if not settings.app_database_url:
        raise SystemExit("APP_DATABASE_URL is not set; nothing to create.")
    url = make_url(settings.app_database_url)
    role = sql.Identifier(url.username)
    with psycopg.connect(_psycopg_url(settings.database_url)) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS app")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS app.spend_daily (
                   day       DATE PRIMARY KEY,
                   usd       NUMERIC(12, 6) NOT NULL DEFAULT 0,
                   questions INTEGER NOT NULL DEFAULT 0)"""
        )
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (url.username,))
        verb = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(
            sql.SQL("{} ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.SQL(verb), role, sql.Literal(url.password)
            )
        )
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(url.database), role)
        )
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA app TO {}").format(role))
        cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON app.spend_daily TO {}").format(role))
    log.info("app.spend_daily ready; role %r can write only that table", url.username)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    setup()
