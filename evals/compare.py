"""Decide whether a predicted result set matches the gold result set (execution accuracy).

Tolerances, chosen so that correct answers written differently still match while wrong answers
don't:
- Column names and order are ignored. Each gold column must match a distinct predicted column
  by its values; extra predicted columns are allowed (e.g. the agent also shows a count).
- Row order is ignored unless the question says order matters.
- Numbers match within 0.01 absolute or 1e-6 relative (rounding, int vs. decimal).
- Strings are compared trimmed and case-insensitively; "unknown" equals NULL (schema.yaml tells
  the agent to label missing categories "unknown").
- Time periods: dates, timestamps at midnight, 'YYYY-MM' strings and year numbers are compared
  at the coarser granularity of the two columns, so 2017-11-01, '2017-11' and
  2017-11-01 00:00:00 are the same month, and 2017 equals 2017-01-01 for a yearly column.
- Intervals (e.g. avg of two timestamps' difference) are converted to decimal days.
"""

import itertools
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

_YEAR = re.compile(r"^\d{4}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ t]00:00:00)?$")
_MAX_ASSIGNMENTS = 5000  # cap on column-mapping combinations tried


@dataclass(frozen=True)
class MatchResult:
    match: bool
    reason: str


def normalize_value(v: Any) -> Any:
    """Map a DB value to float | str | None, so different representations compare equal."""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, int | float | Decimal):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, timedelta):
        return v.total_seconds() / 86400
    if isinstance(v, datetime):
        if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0):
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip().casefold()
    if s == "unknown":
        return None
    if m := _DAY.match(s):
        return m.group(1)
    return s


# --- time granularity ------------------------------------------------------------------------

_LEVELS = {"year": 0, "month": 1, "day": 2}


def _granularity(col: Sequence[Any]) -> str | None:
    """'year' / 'month' / 'day' if every non-null value looks like that period, else None."""
    vals = [v for v in col if v is not None]
    if not vals:
        return None
    if all(isinstance(v, float) and v.is_integer() and 1900 <= v <= 2100 for v in vals):
        return "year"
    if all(isinstance(v, str) for v in vals):
        for level, pattern in (("year", _YEAR), ("month", _MONTH), ("day", _DAY)):
            if all(pattern.match(v) for v in vals):
                return level
    return None


def _coarsen(col: Sequence[Any], level: str) -> list[Any]:
    out = []
    for v in col:
        if v is None:
            out.append(None)
        elif isinstance(v, float):  # a year number
            out.append(str(int(v)))
        else:
            out.append(v[: {"year": 4, "month": 7, "day": 10}[level]])
    return out


def _aligned(gold: list[Any], pred: list[Any]) -> tuple[list[Any], list[Any]]:
    """If both columns are time periods, express them at the coarser shared granularity."""
    g_level, p_level = _granularity(gold), _granularity(pred)
    if g_level and p_level and g_level != p_level:
        level = min(g_level, p_level, key=_LEVELS.__getitem__)
        return _coarsen(gold, level), _coarsen(pred, level)
    return gold, pred


# --- value comparison ------------------------------------------------------------------------


def values_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(0.01 + 1e-9, 1e-6 * max(abs(a), abs(b)))
    return a == b


def _sort_key(v: Any) -> tuple:
    if v is None:
        return (0, 0)
    if isinstance(v, float):
        return (1, round(v, 2))
    return (2, v)


def _sequences_equal(a: Sequence, b: Sequence, ordered: bool) -> bool:
    """Element-wise comparison; rows/values are sorted first unless order matters."""
    if len(a) != len(b):
        return False
    if not ordered:
        a, b = sorted(a, key=_row_key), sorted(b, key=_row_key)
    return all(_row_equal(x, y) for x, y in zip(a, b, strict=True))


def _row_key(x: Any) -> tuple:
    return tuple(_sort_key(v) for v in x) if isinstance(x, tuple) else _sort_key(x)


def _row_equal(x: Any, y: Any) -> bool:
    if isinstance(x, tuple):
        return all(values_equal(a, b) for a, b in zip(x, y, strict=True))
    return values_equal(x, y)


# --- result-set comparison -------------------------------------------------------------------


def compare_results(
    gold_columns: Sequence[str],
    gold_rows: Sequence[Sequence[Any]],
    pred_columns: Sequence[str],
    pred_rows: Sequence[Sequence[Any]],
    *,
    order_matters: bool = False,
) -> MatchResult:
    """Return whether the predicted result answers the question the same way as gold."""
    if len(gold_rows) != len(pred_rows):
        return MatchResult(False, f"row count {len(pred_rows)} != gold {len(gold_rows)}")
    if not gold_rows:
        return MatchResult(True, "both empty")

    gold_cols = [[normalize_value(r[i]) for r in gold_rows] for i in range(len(gold_columns))]
    pred_cols = [[normalize_value(r[j]) for r in pred_rows] for j in range(len(pred_columns))]
    if len(pred_cols) < len(gold_cols):
        return MatchResult(False, f"{len(pred_cols)} columns < gold {len(gold_cols)}")

    # Candidate predicted columns for each gold column, judged column-by-column first.
    candidates: list[list[int]] = []
    for i, g in enumerate(gold_cols):
        matches = [
            j for j, p in enumerate(pred_cols) if _sequences_equal(*_aligned(g, p), order_matters)
        ]
        if not matches:
            return MatchResult(
                False, f"no predicted column matches gold column {gold_columns[i]!r}"
            )
        candidates.append(matches)

    # Then check whole rows for each injective mapping of gold -> predicted columns.
    for n, mapping in enumerate(itertools.product(*candidates)):
        if n >= _MAX_ASSIGNMENTS:
            break
        if len(set(mapping)) < len(mapping):
            continue
        pairs = [_aligned(gold_cols[i], pred_cols[j]) for i, j in enumerate(mapping)]
        gold_tuples = list(zip(*(g for g, _ in pairs), strict=True))
        pred_tuples = list(zip(*(p for _, p in pairs), strict=True))
        if _sequences_equal(gold_tuples, pred_tuples, order_matters):
            return MatchResult(True, "match")
    return MatchResult(False, "columns match individually but rows do not line up")
