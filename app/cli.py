"""Ask the agent a question from the terminal.

python -m app.cli "Which 5 states have the most customers?"
"""

import argparse
import sys
from decimal import Decimal

from app.agent.baseline import BaselineAgent
from app.agent.types import AgentResult
from app.config import get_settings
from app.db.connection import make_engine
from app.db.executor import QueryResult
from app.llm.base import LLMError
from app.llm.factory import get_provider
from app.llm.pricing import estimate_cost_usd


def _fmt(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Decimal):
        return f"{value:,}"
    return str(value)


def format_table(result: QueryResult, max_rows: int) -> str:
    """Render rows as a plain-text table (no extra dependencies)."""
    rows = [[_fmt(v) for v in row] for row in result.rows[:max_rows]]
    widths = [len(c) for c in result.columns]
    for row in rows:
        widths = [max(w, len(v)) for w, v in zip(widths, row, strict=True)]

    def line(cells: list[str]) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True))

    out = [line(result.columns), "-+-".join("-" * w for w in widths), *map(line, rows)]
    shown = min(result.row_count, max_rows)
    total = f"{result.row_count}+" if result.truncated else str(result.row_count)
    out.append(f"({shown} of {total} rows shown)")
    return "\n".join(out)


def format_stats(res: AgentResult) -> str:
    model = res.llm_calls[0].model if res.llm_calls else "?"
    cost = estimate_cost_usd(model, res.input_tokens, res.output_tokens)
    cost_s = f"${cost:.4f}" if cost is not None else "n/a"
    return (
        f"[{res.agent_version} | {model} | {res.input_tokens:,} in / {res.output_tokens:,} out "
        f"tokens | {cost_s} | {res.total_latency_ms / 1000:.1f}s]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask the text-to-SQL agent a question.")
    parser.add_argument("question", help="Question in plain English (quote it).")
    parser.add_argument("--rows", type=int, default=20, help="Max rows to print (default 20).")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        llm = get_provider(settings)
    except LLMError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    # The agent only ever gets the read-only connection.
    agent = BaselineAgent.from_engine(llm, make_engine(settings.agent_database_url))

    res = agent.answer(args.question)

    if res.sql:
        print("SQL:\n" + res.sql + "\n")
    if res.ok:
        print(format_table(res.result, args.rows))
        if res.note:
            print(f"\nNote: {res.note}")
    else:
        print(f"Error ({res.error_stage}): {res.error}", file=sys.stderr)
        if res.error_stage == "extract" and res.raw_reply:
            print(f"\nModel reply:\n{res.raw_reply}", file=sys.stderr)
    print("\n" + format_stats(res))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
