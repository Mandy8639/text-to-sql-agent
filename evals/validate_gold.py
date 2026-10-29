"""Run every gold query and show what it returns: `python -m evals.validate_gold`.

Gold SQL runs with the same read-only role the agent uses, so a gold query that only works
with admin rights is caught here. Flags queries that fail, return no rows, or return only NULLs.
"""

import argparse
import sys

from app.config import get_settings
from app.db.connection import make_engine
from app.db.executor import QueryExecutionError, execute_readonly
from evals.dataset import load_split


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", default="all", choices=["all", "dev", "test", "holdout"])
    parser.add_argument("--show", type=int, default=3, help="rows to print per query")
    args = parser.parse_args()

    engine = make_engine(get_settings().agent_database_url)
    failures = 0
    for q in load_split(args.split):
        if not q.gold_sql:
            continue
        try:
            res = execute_readonly(engine, q.gold_sql, max_rows=5000)
        except QueryExecutionError as exc:
            failures += 1
            print(f"FAIL  {q.id}: {exc.message}")
            continue
        flags = []
        if res.row_count == 0:
            flags.append("EMPTY")
        elif all(v is None for row in res.rows for v in row):
            flags.append("ALL-NULL")
        if res.truncated:
            flags.append("TRUNCATED")
        if q.verify:
            flags.append("verify")
        failures += bool({"EMPTY", "ALL-NULL"} & set(flags))
        preview = "; ".join(str(r) for r in res.rows[: args.show])
        print(f"ok    {q.id:8} {res.row_count:>4} rows {res.columns} {' '.join(flags)}")
        print(f"        {preview}")
    print(f"\n{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
