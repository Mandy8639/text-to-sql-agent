"""Smoke test: Postgres is reachable and pgvector is installed.

Skips (with a reason) if the database is not running, so `make test` stays usable without
Docker. Run `make db-up` first to exercise it.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db.connection import make_engine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    eng = make_engine(get_settings().database_url, connect_timeout_s=3)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        reason = str(exc.orig).splitlines()[0]
        pytest.skip(f"Postgres not reachable (run `make db-up`): {reason}")
    yield eng
    eng.dispose()


def test_postgres_version_is_16(engine):
    with engine.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
    assert version.startswith("16")


def test_pgvector_extension_installed(engine):
    with engine.connect() as conn:
        installed = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
    assert installed is not None, "pgvector extension missing; check docker/postgres/init"
