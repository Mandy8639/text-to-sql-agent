"""Offline schema-retrieval recall, no LLM calls: `python -m evals.retrieval_recall --split dev`.

For each answerable question, the tables its gold SQL uses are the tables retrieval must
find. Recall@k = share of questions where every needed table is in the retrieved set (top-k
by similarity, plus join-path tables). Tune k on dev; report test only once k is chosen.
"""

import argparse
import re

from app.config import get_settings
from app.db.connection import make_engine
from app.retrieval.embedder import get_embedder
from app.retrieval.schema_graph import connect
from app.retrieval.store import VectorStore
from app.schema_doc import load_schema_doc
from evals.dataset import load_split


def tables_in_sql(sql: str, tables: list[str]) -> set[str]:
    return {t for t in tables if re.search(rf"\b{t}\b", sql)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--ks", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    parser.add_argument("--show-misses", type=int, default=0, help="print misses at this k")
    args = parser.parse_args()

    settings = get_settings()
    doc = load_schema_doc()
    names = list(doc["tables"])
    store = VectorStore(make_engine(settings.rag_database_url), get_embedder(settings))
    questions = [q for q in load_split(args.split) if q.gold_sql]
    ranked = {q.id: [t.table for t in store.search_tables(store.embed(q.question), len(names))]
              for q in questions}  # fmt: skip

    print(f"{args.split}: {len(questions)} answerable questions")
    for k in args.ks:
        hits, sizes = 0, []
        for q in questions:
            got = set(connect(doc, ranked[q.id][:k]))
            need = tables_in_sql(q.gold_sql, names)
            sizes.append(len(got))
            if need <= got:
                hits += 1
            elif k == args.show_misses:
                print(f"  miss {q.id}: need {sorted(need)} got {sorted(got)}")
        recall, avg = 100 * hits / len(questions), sum(sizes) / len(sizes)
        print(f"  k={k}: recall {recall:5.1f}%  avg tables {avg:.1f}")


if __name__ == "__main__":
    main()
