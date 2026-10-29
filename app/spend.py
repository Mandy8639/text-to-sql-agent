"""Daily spend cap for the public demo.

Before each question the API checks today's spend (UTC); after it, the question's LLM cost
is added. Once the cap is reached, questions get a polite 503 until midnight UTC.

The counter lives in Postgres (`app.spend_daily`), written by the `olist_app` role, which can
touch only that table, so a container restart can't reset the budget. Without
APP_DATABASE_URL (local development) an in-memory counter is used instead.
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine, text


class BudgetExceeded(RuntimeError):
    pass


class SpendStore(Protocol):
    def spent_today(self) -> float: ...

    def add(self, usd: float) -> None: ...


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


class InMemorySpendStore:
    def __init__(self) -> None:
        self._by_day: dict[str, float] = {}
        self._lock = threading.Lock()

    def spent_today(self) -> float:
        return self._by_day.get(_today(), 0.0)

    def add(self, usd: float) -> None:
        with self._lock:
            day = _today()
            self._by_day[day] = self._by_day.get(day, 0.0) + usd


class PostgresSpendStore:
    """app.spend_daily(day, usd, questions); upsert per question (atomic in Postgres)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def spent_today(self) -> float:
        with self.engine.connect() as conn:
            usd = conn.execute(
                text("SELECT usd FROM app.spend_daily WHERE day = CAST(:d AS date)"),
                {"d": _today()},
            ).scalar_one_or_none()
        return float(usd or 0)

    def add(self, usd: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app.spend_daily (day, usd, questions) "
                    "VALUES (CAST(:d AS date), :usd, 1) "
                    "ON CONFLICT (day) DO UPDATE SET usd = app.spend_daily.usd + EXCLUDED.usd, "
                    "questions = app.spend_daily.questions + 1"
                ),
                {"d": _today(), "usd": Decimal(str(round(usd, 6)))},
            )


class SpendGuard:
    def __init__(self, store: SpendStore, daily_cap_usd: float) -> None:
        self.store = store
        self.daily_cap_usd = daily_cap_usd

    def check(self) -> None:
        if self.daily_cap_usd > 0 and self.store.spent_today() >= self.daily_cap_usd:
            raise BudgetExceeded(
                "The demo's daily budget has been used up. Please try again tomorrow (UTC)."
            )

    def record(self, usd: float) -> None:
        if usd > 0:
            self.store.add(usd)
