"""Shared fixtures for tests that need the loaded Postgres database."""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import get_settings
from app.db.connection import make_engine


def _connect_or_skip(url: str, what: str) -> Engine:
    engine = make_engine(url, connect_timeout_s=3)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        reason = str(exc.orig).splitlines()[0]
        pytest.skip(f"{what} not reachable (run `make db-up` / `make load-data`): {reason}")
    return engine


def _require_loaded(engine: Engine) -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM olist.orders LIMIT 1"))
    except ProgrammingError:
        pytest.skip("olist schema not loaded (run `make load-data`)")


@pytest.fixture(scope="session")
def admin_engine():
    engine = _connect_or_skip(get_settings().database_url, "Postgres (admin)")
    _require_loaded(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def agent_engine(admin_engine):
    engine = _connect_or_skip(get_settings().agent_database_url, "Postgres (read-only agent)")
    yield engine
    engine.dispose()
