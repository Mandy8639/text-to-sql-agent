"""Print a Markdown data profile of the loaded `olist` schema: `python -m scripts.profile_data`.

Row counts, timestamp ranges, and null rates for every column that has nulls. The output is
pasted into the README's "Data profile" section.
"""

from sqlalchemy import Engine, text

from app.config import get_settings
from app.db.connection import make_engine


def _columns(engine: Engine) -> list[tuple[str, str, str]]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'olist' ORDER BY table_name, ordinal_position"
                )
            )
        ]


def profile(engine: Engine) -> str:
    """Return the profile as a Markdown string."""
    columns = _columns(engine)
    tables = sorted({t for t, _, _ in columns})
    lines: list[str] = []

    with engine.connect() as conn:
        counts = {
            t: conn.execute(text(f"SELECT count(*) FROM olist.{t}")).scalar_one() for t in tables
        }

        lines += ["**Row counts**", "", "| Table | Rows |", "|---|---:|"]
        lines += [f"| `{t}` | {counts[t]:,} |" for t in tables]

        lines += ["", "**Timestamp ranges**", "", "| Column | Min | Max |", "|---|---|---|"]
        for t, c, dtype in columns:
            if dtype.startswith("timestamp"):
                lo, hi = conn.execute(
                    text(f'SELECT min("{c}")::date, max("{c}")::date FROM olist.{t}')
                ).one()
                lines.append(f"| `{t}.{c}` | {lo} | {hi} |")

        lines += ["", "**Null rates** (columns with any nulls)", "", "| Column | Nulls | % |"]
        lines.append("|---|---:|---:|")
        for t, c, _ in columns:
            nulls = conn.execute(
                text(f'SELECT count(*) FROM olist.{t} WHERE "{c}" IS NULL')
            ).scalar_one()
            if nulls:
                lines.append(f"| `{t}.{c}` | {nulls:,} | {100 * nulls / counts[t]:.1f}% |")

    return "\n".join(lines)


def main() -> None:
    print(profile(make_engine(get_settings().database_url)))


if __name__ == "__main__":
    main()
