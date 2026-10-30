"""What every entry point enforces before spending money: input rules, rate limits, the budget.

Shared by the FastAPI app and the UI's in-process mode, so a demo without a separate API
(Streamlit Community Cloud) gets exactly the same protection.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from app.config import Settings
from app.db.connection import make_engine
from app.spend import InMemorySpendStore, PostgresSpendStore, SpendGuard


class RateLimited(RuntimeError):
    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def clean_question(question: str, max_chars: int) -> str:
    """Normalise a question, or raise ValueError saying why it can't be accepted."""
    cleaned = " ".join(question.split())  # collapse whitespace and newlines
    if len(cleaned) < 3:
        raise ValueError("question is too short")
    if len(cleaned) > max_chars:
        raise ValueError(f"question is longer than {max_chars} characters")
    if any(ord(c) < 32 for c in cleaned):
        raise ValueError("question contains control characters")
    return cleaned


class RateLimiter:
    """Sliding-window limit per key, in memory (one process)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """None if allowed, else seconds until the next request is allowed."""
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return round(60 - (now - hits[0]), 1)
            hits.append(now)
            return None


def make_spend_guard(settings: Settings) -> SpendGuard:
    store = (
        PostgresSpendStore(make_engine(settings.app_database_url))
        if settings.app_database_url
        else InMemorySpendStore()
    )
    return SpendGuard(store, settings.daily_budget_usd)


class Admission:
    """Per-visitor limit, global limit, and daily budget, checked in that order."""

    def __init__(self, settings: Settings, guard: SpendGuard | None = None) -> None:
        self.visitor = RateLimiter(settings.api_rate_limit_per_minute)
        self.everyone = RateLimiter(settings.api_global_rate_limit_per_minute)
        self.guard = guard or make_spend_guard(settings)

    def check_rate(self, visitor: str) -> None:
        for limiter, key, scope in ((self.visitor, visitor, "per visitor"),
                                    (self.everyone, "*", "in total")):  # fmt: skip
            retry_after = limiter.check(key)
            if retry_after is not None:
                raise RateLimited(
                    f"Rate limit: {limiter.per_minute} questions per minute {scope}.", retry_after
                )

    def check_budget(self) -> None:
        self.guard.check()

    def record(self, usd: float) -> None:
        self.guard.record(usd)
