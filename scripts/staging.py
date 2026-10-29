"""Copy the raw Olist CSVs into an all-TEXT `staging` schema.

Loading everything as TEXT first means a malformed value can never abort the COPY; type
conversion and cleaning happen afterwards in SQL, where every rule is visible and testable.
"""

import csv
import logging
from pathlib import Path

import psycopg
from psycopg import sql

log = logging.getLogger(__name__)

# CSV file -> staging table name
CSV_TABLES: dict[str, str] = {
    "olist_customers_dataset.csv": "customers",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_orders_dataset.csv": "orders",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "product_category_name_translation.csv": "category_translation",
}


def _read_header(path: Path) -> list[str]:
    # utf-8-sig strips the byte-order mark some files start with.
    with path.open(encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def load_staging(conn: psycopg.Connection, raw_dir: Path) -> dict[str, int]:
    """Create `staging` and COPY every CSV into it. Returns row counts per table."""
    missing = [name for name in CSV_TABLES if not (raw_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing CSVs in {raw_dir}: {', '.join(missing)}")

    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS staging CASCADE")
        cur.execute("CREATE SCHEMA staging")
        for filename, table in CSV_TABLES.items():
            path = raw_dir / filename
            columns = _read_header(path)
            ident = sql.Identifier("staging", table)
            cur.execute(
                sql.SQL("CREATE TABLE {} ({})").format(
                    ident,
                    sql.SQL(", ").join(
                        sql.SQL("{} TEXT").format(sql.Identifier(c)) for c in columns
                    ),
                )
            )
            copy_stmt = sql.SQL("COPY {} FROM STDIN WITH (FORMAT csv, HEADER true)").format(ident)
            with path.open(encoding="utf-8-sig", newline="") as f, cur.copy(copy_stmt) as copy:
                while chunk := f.read(1 << 20):
                    copy.write(chunk)
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
            counts[table] = cur.fetchone()[0]
            log.info("staged %-22s %9d rows", table, counts[table])
    return counts
