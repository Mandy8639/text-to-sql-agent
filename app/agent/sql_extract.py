"""Pull the SQL query out of a model's free-text reply."""

import re

# ```sql ... ``` (language tag optional, any case). Non-greedy, so the first block wins.
_FENCED = re.compile(r"```[ \t]*(?:sql|postgresql|postgres)?[ \t]*\n(.*?)```", re.I | re.S)
# A reply that is just a query, with no code fence.
_BARE = re.compile(r"^\s*(SELECT|WITH)\b", re.I)


class SQLExtractionError(ValueError):
    """The reply contained no recognisable SQL."""


def extract_sql(reply: str) -> str:
    """Return the SQL from `reply`, without surrounding whitespace or trailing semicolons.

    Prefers the first fenced code block; falls back to a reply that starts with SELECT/WITH.
    Does not validate the SQL. That is the safety layer's job (Phase 5).
    """
    match = _FENCED.search(reply)
    if match:
        sql = match.group(1)
    elif _BARE.match(reply):
        sql = reply
    else:
        raise SQLExtractionError("No SQL code block found in the model reply.")

    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SQLExtractionError("The SQL code block in the model reply is empty.")
    return sql


def extract_note(reply: str) -> str | None:
    """Return the model's prose outside the first SQL code block (e.g. a stated assumption)."""
    note = _FENCED.sub("", reply, count=1)
    note = re.sub(r"\n{3,}", "\n\n", note).strip()  # close the gap the code block left
    if not note or _BARE.match(reply):
        return None
    return note


# A deliberate non-answer: "REFUSE: reason" or "CLARIFY: question" at the start of the reply
# (tolerating markdown emphasis or backticks the model may add around the keyword).
_ABSTAIN = re.compile(r"^[\s*`_]*(REFUSE|CLARIFY)[*`_]*\s*:\s*(.+)", re.I | re.S)


def extract_abstention(reply: str) -> tuple[str, str] | None:
    """Return ("refuse" | "clarify", text) if the reply abstains, else None."""
    m = _ABSTAIN.match(reply)
    if not m:
        return None
    return m.group(1).lower(), m.group(2).strip().strip("`*").strip()
