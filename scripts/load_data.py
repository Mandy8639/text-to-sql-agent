"""Load the Olist CSVs into Postgres: `python -m scripts.load_data` (or `make load-data`).

Steps, all in ONE transaction so a failure leaves the previous data intact:
1. COPY raw CSVs into an all-TEXT `staging` schema
2. Build the typed `olist` schema (scripts/sql/olist_schema.sql)
3. Create/refresh the agent's read-only role and grants
4. Log row counts (raw vs. loaded), drop `staging`, ANALYZE

Re-running is safe: it rebuilds everything from the CSVs.
"""

import argparse
import logging
import time
from pathlib import Path

import psycopg

from app.config import get_settings
from scripts.readonly_role import ensure_readonly_role
from scripts.staging import load_staging

log = logging.getLogger("load_data")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "scripts" / "sql" / "olist_schema.sql"
OLIST_TABLES = [
    "category_translation",
    "geolocation",
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
]


def _psycopg_url(sqlalchemy_url: str) -> str:
    """psycopg wants `postgresql://`, SQLAlchemy uses `postgresql+psycopg://`."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _row_counts(cur: psycopg.Cursor) -> dict[str, int]:
    counts = {}
    for table in OLIST_TABLES:
        cur.execute(f"SELECT count(*) FROM olist.{table}")  # names are our own constants
        counts[table] = cur.fetchone()[0]
    return counts


def load(raw_dir: Path) -> dict[str, int]:
    """Run the full load. Returns final row counts per `olist` table."""
    settings = get_settings()
    start = time.perf_counter()

    with psycopg.connect(_psycopg_url(settings.database_url)) as conn:  # commits on success
        staged = load_staging(conn, raw_dir)
        with conn.cursor() as cur:
            log.info("building olist schema")
            cur.execute(SCHEMA_SQL.read_text())
            ensure_readonly_role(conn, settings.agent_database_url)
            loaded = _row_counts(cur)
            cur.execute("DROP SCHEMA staging CASCADE")
            cur.execute("ANALYZE")

    log.info("%-22s %10s %10s", "table", "raw rows", "loaded")
    for table, n in loaded.items():
        log.info("%-22s %10s %10d", table, staged.get(table, "-"), n)
    log.info("done in %.1fs", time.perf_counter() - start)
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load(args.raw_dir)


if __name__ == "__main__":
    main()
