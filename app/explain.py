"""Plain-English explanation of a result, grounded only in the returned rows.

The result rows are untrusted (review comments are customer-written), so they are passed
inside a fenced <data> block that the prompt says to treat as data. After generation, a simple
grounding check lists every number in the explanation that does not appear in the data (after
rounding / separators / percent signs), so an invented figure is flagged rather than silently
shown.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.agent.prompts import load_prompt
from app.llm.base import LLMError, LLMProvider, LLMResponse

MAX_ROWS_IN_PROMPT = 40
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


@dataclass
class Explanation:
    text: str | None
    grounded: bool | None = None  # None when no LLM explanation was produced
    ungrounded_numbers: list[str] = field(default_factory=list)
    response: LLMResponse | None = None
    error: str | None = None


def json_safe(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, timedelta):
        return round(v.total_seconds() / 86400, 4)
    return v


def _data_numbers(rows: list[tuple], extra_text: str) -> set[float]:
    found: set[float] = set()
    for row in rows:
        for v in row:
            if isinstance(v, int | float | Decimal) and not isinstance(v, bool):
                found.add(float(v))
            elif v is not None:
                found.update(_parse(m) for m in _NUMBER.findall(str(v)))
    found.update(_parse(m) for m in _NUMBER.findall(extra_text))
    return found


def _parse(token: str) -> float:
    return float(token.replace(",", ""))


def _is_grounded(x: float, data: set[float]) -> bool:
    """x matches a data value exactly, after rounding to 0-2 decimals, or as a ratio x100.

    Shorthand like "1.1 million" is not matched, so it gets flagged: a false alarm is
    preferable to a missed invented number."""
    for d in data:
        for candidate in (d, d * 100):
            if abs(candidate - x) < 1e-9 or any(round(candidate, n) == x for n in (0, 1, 2)):
                return True
    return False


def check_grounding(text: str, rows: list[tuple], context: str) -> list[str]:
    """Numbers in `text` that appear nowhere in the rows or the question/note context."""
    data = _data_numbers(rows, context)
    ungrounded = []
    for token in _NUMBER.findall(text):
        value = _parse(token)
        # Numbers from the question or note (e.g. "top 5", "2017") count as grounded too.
        if not _is_grounded(value, data):
            ungrounded.append(token)
    return ungrounded


def explain(
    llm: LLMProvider,
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple],
    *,
    truncated: bool,
    note: str | None,
) -> Explanation:
    shown = rows[:MAX_ROWS_IN_PROMPT]
    data = {
        "columns": columns,
        "rows": [[json_safe(v) for v in r] for r in shown],
        "rows_shown": len(shown),
        "rows_total": len(rows),
        "truncated": truncated or len(rows) > len(shown),
    }
    user = (
        f"Question: {question}\n\nAnalyst's note: {note or 'none'}\n\n"
        f"SQL that produced the result:\n{sql}\n\n<data>\n{json.dumps(data, default=str)}\n</data>"
    )
    try:
        resp = llm.complete(load_prompt("explain", 1).template, user, max_tokens=2000)
    except LLMError as exc:
        return Explanation(None, error=str(exc))
    text = resp.text.strip()
    ungrounded = check_grounding(text, shown, f"{question} {note or ''}")
    return Explanation(text, grounded=not ungrounded, ungrounded_numbers=ungrounded, response=resp)
