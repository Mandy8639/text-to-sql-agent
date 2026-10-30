"""The UI's in-process backend applies the same rules as the API, and the page itself runs."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.config import Settings
from app.limits import Admission, clean_question
from app.service import QueryResponse
from app.spend import InMemorySpendStore, SpendGuard
from ui.backends import BackendMessage, LocalBackend

ROOT = Path(__file__).resolve().parents[2]


class FakeService:
    class agent:  # noqa: N801
        version = "v6-safe"

    def __init__(self):
        self.asked, self.feedbacks = [], []

    def ask(self, question):
        self.asked.append(question)
        return QueryResponse("req", "trace12345", question, "answer", cost_usd=0.001)

    def feedback(self, trace_id, positive):
        self.feedbacks.append((trace_id, positive))

    def health(self):
        return {"database": "ok", "retrieval_index": "ok"}


def backend(per_minute=100, cap=0.0):
    settings = Settings(_env_file=None, api_rate_limit_per_minute=per_minute)
    admission = Admission(settings, SpendGuard(InMemorySpendStore(), cap))
    return LocalBackend(FakeService(), admission, max_question_chars=500)


def test_local_backend_answers_and_normalises_the_question():
    b = backend()
    r = b.ask("  How many\norders?  ", "1.2.3.4")
    assert r["outcome"] == "answer" and b.service.asked == ["How many orders?"]


@pytest.mark.parametrize("bad", ["", "  a ", "x" * 501, "how many\x00orders"])
def test_local_backend_rejects_invalid_questions(bad):
    b = backend()
    with pytest.raises(BackendMessage, match="couldn't be accepted"):
        b.ask(bad, "1.2.3.4")
    assert b.service.asked == []


def test_local_backend_enforces_the_rate_limit_per_visitor():
    b = backend(per_minute=1)
    b.ask("How many orders?", "1.2.3.4")
    with pytest.raises(BackendMessage, match="Rate limit"):
        b.ask("How many orders?", "1.2.3.4")
    b.ask("How many orders?", "5.6.7.8")  # someone else is unaffected


def test_local_backend_enforces_the_daily_budget():
    b = backend(cap=0.0015)
    b.ask("How many orders?", "a")
    b.ask("How many orders?", "b")  # 0.002 spent, over the cap
    with pytest.raises(BackendMessage, match="daily budget"):
        b.ask("How many orders?", "c")
    assert len(b.service.asked) == 2


def test_local_backend_health_and_feedback():
    b = backend()
    assert b.health() == {
        "status": "ok",
        "agent": "v6-safe",
        "database": "ok",
        "retrieval_index": "ok",
    }
    b.feedback("trace12345", True)
    assert b.service.feedbacks == [("trace12345", True)]


def test_clean_question_collapses_whitespace():
    assert clean_question(" a  b\tc ", 10) == "a b c"


def test_page_runs_with_no_secrets_and_no_api(monkeypatch):
    """Runs the real script: catches the `import app` clash and a missing secrets file."""
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.delenv("UI_MODE", raising=False)
    at = AppTest.from_file(str(ROOT / "ui" / "streamlit_app.py"), default_timeout=30).run()
    assert not at.exception
    assert any("Agent offline" in e.value for e in at.sidebar.error)
    assert len(at.sidebar.button) == 7


def test_requirements_file_lists_what_the_inprocess_agent_needs():
    pins = (ROOT / "ui" / "requirements.txt").read_text()
    for package in (
        "streamlit==",
        "anthropic==",
        "fastembed==",
        "sqlglot==",
        "psycopg==",
        "langfuse==",
    ):
        assert package in pins
