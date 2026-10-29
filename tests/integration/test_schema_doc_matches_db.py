"""docs/schema.yaml must describe exactly the tables and columns that exist in the database."""

import pytest
from sqlalchemy import text

from app.schema_doc import load_schema_doc

pytestmark = pytest.mark.integration

# YAML type (as written in the DDL) -> information_schema.columns.data_type
TYPE_MAP = {
    "TEXT": "text",
    "CHAR": "character",
    "TIMESTAMP": "timestamp without time zone",
    "NUMERIC": "numeric",
    "INTEGER": "integer",
    "SMALLINT": "smallint",
    "DOUBLE PRECISION": "double precision",
}


@pytest.fixture(scope="module")
def db_columns(admin_engine):
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema = 'olist'"
            )
        )
        return {(r.table_name, r.column_name): (r.data_type, r.is_nullable == "YES") for r in rows}


def _doc_columns():
    doc = load_schema_doc()
    return {
        (t, c): spec for t, table in doc["tables"].items() for c, spec in table["columns"].items()
    }


def test_same_columns(db_columns):
    doc_cols = set(_doc_columns())
    db_cols = set(db_columns)
    assert db_cols - doc_cols == set(), "columns in DB but undocumented"
    assert doc_cols - db_cols == set(), "documented columns missing from DB"


def test_types_and_nullability_match(db_columns):
    for key, spec in _doc_columns().items():
        base_type = spec["type"].split("(")[0]
        db_type, db_nullable = db_columns[key]
        assert TYPE_MAP[base_type] == db_type, f"{key}: doc {spec['type']}, db {db_type}"
        # Columns marked nullable in the doc must be nullable in the DB. (Some DB-nullable
        # columns, like product_category_name_english, are also marked; FK columns aren't.)
        if spec.get("nullable"):
            assert db_nullable, f"{key} documented nullable but NOT NULL in DB"


def test_row_counts_match(admin_engine):
    with admin_engine.connect() as conn:
        for table, spec in load_schema_doc()["tables"].items():
            n = conn.execute(text(f"SELECT count(*) FROM olist.{table}")).scalar_one()
            assert n == spec["row_count"], table
