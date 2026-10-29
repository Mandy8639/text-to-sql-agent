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
