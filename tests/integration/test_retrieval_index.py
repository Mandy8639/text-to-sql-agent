"""The pgvector index and the read-only retrieval role, against the real database."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.config import get_settings
from app.db.connection import make_engine
from app.retrieval.embedder import get_embedder
from app.retrieval.store import VectorStore
from evals.dataset import load_split

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def rag_engine():
    engine = make_engine(get_settings().rag_database_url, connect_timeout_s=3)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM rag.index_meta LIMIT 1"))
    except (OperationalError, ProgrammingError):
        pytest.skip("retrieval index not built (run `make build-index`)")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def store(rag_engine):
    return VectorStore(rag_engine, get_embedder(get_settings()))


def test_table_search_ranks_relevant_tables(store):
    tables = [t.table for t in store.search_tables(store.embed("average review score"), 3)]
    assert "order_reviews" in tables


def test_index_holds_only_dev_examples(rag_engine):
    with rag_engine.connect() as conn:
        ids = {r[0] for r in conn.execute(text("SELECT question_id FROM rag.examples"))}
    assert ids
    assert not ids & {q.id for q in load_split("test")}


def test_exact_question_is_excluded_from_examples(store):
    dev_q = next(q for q in load_split("dev") if q.gold_sql)
    found = store.search_examples(store.embed(dev_q.question), 3, exclude_question=dev_q.question)
    assert dev_q.id not in [e.question_id for e in found]


def test_rag_role_cannot_read_business_data_or_write(rag_engine):
    with rag_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT count(*) FROM olist.orders"))
    with rag_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("DELETE FROM rag.examples"))


def test_agent_role_cannot_read_the_index(agent_engine, rag_engine):
    with agent_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT count(*) FROM rag.examples"))


# --- demo spend counter (Phase 7) -------------------------------------------------------------


@pytest.fixture(scope="module")
def app_engine():
    url = get_settings().app_database_url
    if not url:
        pytest.skip("APP_DATABASE_URL not set (run `make setup-app-db`)")
    engine = make_engine(url, connect_timeout_s=3)
    yield engine
    engine.dispose()


def test_spend_store_adds_atomically(app_engine):
    from app.spend import PostgresSpendStore

    store = PostgresSpendStore(app_engine)
    before = store.spent_today()
    store.add(0.000123)
    store.add(0.000077)
    assert store.spent_today() == pytest.approx(before + 0.0002, abs=1e-6)


def test_app_role_can_write_only_the_counter(app_engine):
    with app_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT count(*) FROM olist.orders"))
    with app_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("DROP TABLE app.spend_daily"))
    with app_engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT count(*) FROM rag.examples"))
