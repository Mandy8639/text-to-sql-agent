"""Structural checks on docs/schema.yaml (no database needed)."""

import pytest

from app.schema_doc import load_schema_doc

DOC = load_schema_doc()
TABLES = DOC["tables"]
COLUMN_PAIRS = [(t, c) for t, spec in TABLES.items() for c in spec["columns"]]


@pytest.mark.parametrize("table", TABLES)
def test_table_has_required_fields(table):
    spec = TABLES[table]
    for field in ["description", "grain", "primary_key", "row_count", "relationships", "columns"]:
        assert field in spec, f"{table} missing {field}"
    assert set(spec["primary_key"]) <= set(spec["columns"])


@pytest.mark.parametrize(("table", "column"), COLUMN_PAIRS)
def test_column_has_type_description_examples(table, column):
    spec = TABLES[table]["columns"][column]
    assert spec.get("type"), f"{table}.{column} has no type"
    assert spec.get("description"), f"{table}.{column} has no description"
    assert spec.get("examples"), f"{table}.{column} has no examples"


def test_relationships_point_at_documented_columns():
    for table, spec in TABLES.items():
        for rel in spec["relationships"]:
            assert rel["column"] in spec["columns"], f"{table}.{rel['column']}"
            ref_table, ref_column = rel["references"].split(".")
            assert ref_column in TABLES[ref_table]["columns"], rel["references"]


def test_business_notes_and_ambiguities_present():
    assert len(DOC["business_notes"]) >= 5
    assert all({"id", "question", "assumption"} <= set(a) for a in DOC["ambiguities"])
