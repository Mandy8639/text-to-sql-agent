"""Build the retrieval index in Postgres (pgvector): `python -m scripts.build_index`.

Creates schema `rag` with
- rag.schema_chunks: one row per table (its rendered documentation + embedding)
- rag.examples:      dev-split question/SQL pairs for few-shot retrieval (never test)
- rag.index_meta:    which embedding model built the index, so a mismatch fails loudly
and a read-only `olist_rag` role (from RAG_DATABASE_URL) that can read only this schema.
Re-running rebuilds everything in one transaction.
"""

import logging
import re
import time

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from sqlalchemy.engine import make_url

from app.agent.schema_render import render_table
from app.config import get_settings
from app.retrieval.embedder import get_embedder
from app.schema_doc import load_schema_doc
from evals.dataset import assert_no_leakage, few_shot_pool
from scripts.load_data import _psycopg_url

log = logging.getLogger("build_index")


def table_passage(name: str, spec: dict, doc: dict) -> str:
    """Text embedded for a table: its description, notes, column descriptions, and every
    business rule that mentions it. The rules carry vocabulary such as "revenue", which lives
    in business_notes rather than in the table's own description."""
    cols = "; ".join(f"{c}: {s['description']}" for c, s in spec["columns"].items())
    rules = " ".join(
        n["note"].strip() for n in doc["business_notes"] if re.search(rf"\b{name}\b", n["note"])
    )
    return (
        f"Table {name}. {spec['description'].strip()} Grain: {spec['grain']}. "
        f"{(spec.get('notes') or '').strip()} Columns: {cols} Rules: {rules}"
    )


def _ensure_rag_role(cur: psycopg.Cursor, rag_url: str) -> str:
    url = make_url(rag_url)
    role = sql.Identifier(url.username)
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (url.username,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL("{} ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
            sql.SQL(verb), role, sql.Literal(url.password)
        )
    )
    cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(url.database), role)
    )
    # The vector type and operators live in `public`, which PUBLIC can no longer use (D12).
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA rag TO {}").format(role))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA rag TO {}").format(role))
    return url.username


def build() -> dict[str, int]:
    settings = get_settings()
    doc = load_schema_doc()
    embedder = get_embedder(settings)
    start = time.perf_counter()

    tables = list(doc["tables"].items())
    table_vecs = embedder.embed_passages([table_passage(n, s, doc) for n, s in tables])

    examples = few_shot_pool()  # dev only, by construction
    assert_no_leakage(
        texts=[q.question for q in examples] + [q.gold_sql for q in examples],
        example_ids=[q.id for q in examples],
    )
    example_vecs = embedder.embed_passages([q.question for q in examples])

    with psycopg.connect(_psycopg_url(settings.database_url)) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS rag CASCADE")
            cur.execute("CREATE SCHEMA rag")
            dim = embedder.dim
            cur.execute(
                f"""CREATE TABLE rag.schema_chunks (
                        table_name TEXT PRIMARY KEY,
                        content    TEXT NOT NULL,
                        embedding  vector({dim}) NOT NULL)"""
            )
            cur.execute(
                f"""CREATE TABLE rag.examples (
                        question_id TEXT PRIMARY KEY,
                        question    TEXT NOT NULL,
                        sql         TEXT NOT NULL,
                        embedding   vector({dim}) NOT NULL)"""
            )
            cur.execute("CREATE TABLE rag.index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            # Brute-force cosine search: with ~40 rows an ANN index (HNSW) would only add noise.
            cur.executemany(
                "INSERT INTO rag.schema_chunks VALUES (%s, %s, %s)",
                [(n, render_table(n, s), v) for (n, s), v in zip(tables, table_vecs, strict=True)],
            )
            cur.executemany(
                "INSERT INTO rag.examples VALUES (%s, %s, %s, %s)",
                [
                    (q.id, q.question, q.gold_sql, v)
                    for q, v in zip(examples, example_vecs, strict=True)
                ],
            )
            cur.executemany(
                "INSERT INTO rag.index_meta VALUES (%s, %s)",
                [
                    ("embedding_model", embedder.model_name),
                    ("schema_doc_version", str(doc["version"])),
                    ("split", "dev"),
                ],
            )
            role = _ensure_rag_role(cur, settings.rag_database_url)

    log.info(
        "indexed %d tables and %d dev examples with %s in %.1fs (read role %r)",
        len(tables), len(examples), embedder.model_name, time.perf_counter() - start, role,
    )  # fmt: skip
    return {"tables": len(tables), "examples": len(examples)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    build()
