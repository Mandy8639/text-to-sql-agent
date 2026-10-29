"""Pick a chart for a result set, by rules (no LLM): deterministic, free, explainable.

time column + numeric column(s), >= 2 rows    -> line   (x = time, y = up to 3 measures)
one text column + numeric column, 2-30 rows   -> bar    (x = category, y = first measure)
single value, or anything else                -> none   (a metric or a table reads better)
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

MAX_BAR_ROWS = 30
_PERIOD_STR = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T].*)?$")
_TIME_NAMES = re.compile(r"(^|_)(month|year|date|day|week|quarter|period|time|ts)(_|$)", re.I)

ChartType = Literal["line", "bar", "none"]


@dataclass(frozen=True)
class ChartSpec:
    type: ChartType
    x: str | None = None
    y: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float | Decimal) and not isinstance(v, bool)


def _column_kind(name: str, values: list[Any]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "empty"
    if all(isinstance(v, date | datetime) for v in vals):
        return "time"
    if all(isinstance(v, str) and _PERIOD_STR.match(v) for v in vals):
        return "time"
    if all(_is_number(v) for v in vals):
        # A small-integer column named like a period (month = 1..12, year = 2017) is time.
        if _TIME_NAMES.search(name) and all(float(v).is_integer() for v in vals):
            return "time"
        return "number"
    return "text"


def choose_chart(columns: list[str], rows: list[tuple]) -> ChartSpec:
    if len(rows) < 2:
        return ChartSpec("none", reason="single row: shown as a value, not a chart")
    kinds = {c: _column_kind(c, [r[i] for r in rows]) for i, c in enumerate(columns)}
    times = [c for c, k in kinds.items() if k == "time"]
    numbers = [c for c, k in kinds.items() if k == "number"]
    texts = [c for c, k in kinds.items() if k == "text"]

    if times and numbers:
        return ChartSpec("line", x=times[0], y=numbers[:3], reason="time column with measures")
    if len(texts) == 1 and numbers and len(rows) <= MAX_BAR_ROWS:
        return ChartSpec("bar", x=texts[0], y=numbers[:1], reason="one category with a measure")
    if len(texts) == 1 and numbers:
        return ChartSpec(
            "none", reason=f"more than {MAX_BAR_ROWS} categories: a table reads better"
        )
    return ChartSpec("none", reason="no clear category/time + measure shape")
