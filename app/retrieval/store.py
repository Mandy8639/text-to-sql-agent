"""Similarity search over the pgvector index built by scripts/build_index.py."""

from dataclasses import dataclass

from pgvector.sqlalchemy import Vector  # noqa: F401  (registers the type with SQLAlchemy)
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.exc import ProgrammingError

from app.retrieval.embedder import Embedder


class RetrievalIndexError(RuntimeError):
    """The index is missing or was built with a different embedding model."""


@dataclass(frozen=True)
class ScoredTable:
    table: str
    content: str  # rendered table documentation, ready for the prompt
    score: float  # cosine similarity, higher = closer


@dataclass(frozen=True)
class Example:
    question_id: str
    question: str
    sql: str
    score: float


class VectorStore:
    """Read-only access to rag.* through the `olist_rag` role."""

    def __init__(self, engine: Engine, embedder: Embedder) -> None:
        self.engine = engine
        self.embedder = embedder
        self._check_model()

    def _check_model(self) -> None:
        try:
            with self.engine.connect() as conn:
                built_with = conn.execute(
                    text("SELECT value FROM rag.index_meta WHERE key = 'embedding_model'")
                ).scalar_one_or_none()
        except ProgrammingError as exc:
            raise RetrievalIndexError("Retrieval index missing: run `make build-index`.") from exc
        if built_with != self.embedder.model_name:
            raise RetrievalIndexError(
                f"Index built with {built_with!r} but the embedder is "
                f"{self.embedder.model_name!r}: rebuild with `make build-index`."
            )

    def embed(self, question: str) -> list[float]:
        return self.embedder.embed_queries([question])[0]

    def search_tables(self, query_vec: list[float], k: int) -> list[ScoredTable]:
        stmt = text(
            "SELECT table_name, content, 1 - (embedding <=> :q) AS score "
            "FROM rag.schema_chunks ORDER BY embedding <=> :q LIMIT :k"
        ).bindparams(bindparam("q", type_=Vector()))
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, {"q": query_vec, "k": k}).all()
        return [ScoredTable(r.table_name, r.content, float(r.score)) for r in rows]

    def all_tables(self) -> dict[str, str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT table_name, content FROM rag.schema_chunks")).all()
        return {r.table_name: r.content for r in rows}

    def search_examples(
        self, query_vec: list[float], k: int, *, exclude_question: str | None = None
    ) -> list[Example]:
        """Nearest dev examples. `exclude_question` drops an exact match, so evaluating on the
        dev split never shows a question its own answer."""
        stmt = text(
            "SELECT question_id, question, sql, 1 - (embedding <=> :q) AS score "
            "FROM rag.examples "
            "WHERE lower(question) <> lower(coalesce(:exclude, '')) "
            "ORDER BY embedding <=> :q LIMIT :k"
        ).bindparams(bindparam("q", type_=Vector()))
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, {"q": query_vec, "k": k, "exclude": exclude_question}).all()
        return [Example(r.question_id, r.question, r.sql, float(r.score)) for r in rows]
