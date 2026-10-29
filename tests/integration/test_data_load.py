"""Check the loaded `olist` schema against known properties of the source data.

Primary/foreign keys and CHECK constraints are enforced by Postgres at load time; these tests
cover what constraints cannot: expected row counts, cleaning rules, and totals preserved.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

EXPECTED_ROWS = {
    "orders": 99_441,
    "customers": 99_441,
    "order_items": 112_650,
    "order_payments": 103_886,
    "order_reviews": 99_224,  # 104,719 physical lines: comments contain newlines
    "products": 32_951,
    "sellers": 3_095,
    "geolocation": 19_015,  # collapsed from 1,000,163 raw rows to one per zip prefix
    "category_translation": 73,  # 71 from the file + 2 we added
}


def _scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


@pytest.mark.parametrize("table", EXPECTED_ROWS)
def test_row_counts(admin_engine, table):
    assert _scalar(admin_engine, f"SELECT count(*) FROM olist.{table}") == EXPECTED_ROWS[table]


def test_every_category_is_translated(admin_engine):
    untranslated = _scalar(
        admin_engine,
        "SELECT count(*) FROM olist.products "
        "WHERE product_category_name IS NOT NULL AND product_category_name_english IS NULL",
    )
    assert untranslated == 0


def test_null_categories_preserved(admin_engine):
    assert (
        _scalar(
            admin_engine, "SELECT count(*) FROM olist.products WHERE product_category_name IS NULL"
        )
        == 610
    )


def test_money_totals_preserved(admin_engine):
    # Totals computed from the raw CSVs; casting must not lose cents.
    assert _scalar(admin_engine, "SELECT sum(price) FROM olist.order_items") == Decimal(
        "13591643.70"
    )
    assert _scalar(admin_engine, "SELECT sum(payment_value) FROM olist.order_payments") == Decimal(
        "16008872.12"
    )


def test_purchase_date_range(admin_engine):
    with admin_engine.connect() as conn:
        lo, hi = conn.execute(
            text(
                "SELECT min(order_purchase_timestamp)::text, max(order_purchase_timestamp)::text "
                "FROM olist.orders"
            )
        ).one()
    assert lo == "2016-09-04 21:15:19"
    assert hi == "2018-10-17 17:30:18"


def test_geolocation_cleaned(admin_engine):
    with admin_engine.connect() as conn:
        accented = conn.execute(
            text("SELECT count(*) FROM olist.geolocation WHERE geolocation_city ~ '[áàâãéêíóôõúç]'")
        ).scalar_one()
        outside = conn.execute(
            text(
                "SELECT count(*) FROM olist.geolocation "
                "WHERE geolocation_lat NOT BETWEEN -34 AND 5.5 "
                "OR geolocation_lng NOT BETWEEN -74 AND -34"
            )
        ).scalar_one()
    assert accented == 0
    assert outside == 0


def test_zip_prefixes_keep_leading_zeros(admin_engine):
    assert (
        _scalar(
            admin_engine,
            "SELECT count(*) FROM olist.customers WHERE customer_zip_code_prefix LIKE '0%'",
        )
        > 0
    )


def test_staging_schema_dropped(admin_engine):
    assert _scalar(admin_engine, "SELECT count(*) FROM pg_namespace WHERE nspname = 'staging'") == 0
