"""The validator must accept every gold query and must not change any result.

The validator re-renders SQL through sqlglot (to add the LIMIT), so this also checks that the
re-rendering never changes what a query means: executing the validated SQL must return exactly
what the original gold SQL returns, for all dev, test and hold-out questions.
"""

import pytest

from app.db.executor import execute_readonly
from app.safety.guard import SafeExecutor
from app.schema_doc import load_schema_doc
from evals.dataset import load_split

pytestmark = pytest.mark.integration

GOLD = [q for q in load_split("all") + load_split("holdout") if q.gold_sql]


@pytest.fixture(scope="module")
def safe(agent_engine):
    return SafeExecutor.from_doc(agent_engine, load_schema_doc(), max_rows=5000)


@pytest.mark.parametrize("q", GOLD, ids=[q.id for q in GOLD])
def test_gold_query_passes_and_returns_identical_rows(agent_engine, safe, q):
    raw = execute_readonly(agent_engine, q.gold_sql, max_rows=5000)
    checked = safe(q.gold_sql)
    assert checked.columns == raw.columns
    if "ORDER BY" in q.gold_sql.upper():
        assert checked.rows == raw.rows
    else:
        assert sorted(map(repr, checked.rows)) == sorted(map(repr, raw.rows))
