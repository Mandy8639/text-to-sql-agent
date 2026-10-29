"""API tests with a fake service (no database, no LLM)."""

import pytest
from fastapi.testclient import TestClient

from api.main import RateLimiter, create_app
from app.config import Settings
from app.service import QueryResponse


class FakeAgent:
    version = "v6-safe"


class FakeService:
    def __init__(self, error: Exception | None = None):
        self.agent = FakeAgent()
        self.error = error
        self.questions: list[str] = []
        self.feedback_calls: list[tuple] = []

    def ask(self, question: str) -> QueryResponse:
        if self.error:
            raise self.error
        self.questions.append(question)
        return QueryResponse(
            request_id="req123", trace_id="trace12345", question=question, outcome="answer",
            explanation="There are 99,441 orders.", explanation_grounded=True,
            sql="SELECT count(*) AS orders FROM orders", columns=["orders"], rows=[[99441]],
            row_count=1, cost_usd=0.001, latency_ms=12.0,
        )  # fmt: skip

    def feedback(self, trace_id, positive, comment=None):
        self.feedback_calls.append((trace_id, positive, comment))


def client(service=None, per_minute=100, health=None):
    service = service or FakeService()
    settings = Settings(_env_file=None, api_rate_limit_per_minute=per_minute)
    app = create_app(
        lambda: service, settings, health_check=health or (lambda s: {"database": "ok"})
    )
    return TestClient(app), service


def test_query_returns_the_full_response():
    with client()[0] as c:
        r = c.post("/query", json={"question": "  How many\norders are there?  "})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "answer" and body["rows"] == [[99441]]
    assert body["trace_id"] == "trace12345" and body["chart"]["type"] == "none"
    assert body["question"] == "How many orders are there?"  # whitespace normalised


@pytest.mark.parametrize(
    "payload",
    [{}, {"question": ""}, {"question": "  a "}, {"question": "x" * 501}, {"question": 42},
     {"question": "how many\x00orders"}],
)  # fmt: skip
def test_invalid_questions_are_rejected(payload):
    with client()[0] as c:
        assert c.post("/query", json=payload).status_code == 422


def test_rate_limit_returns_429_with_retry_after():
    with client(per_minute=2)[0] as c:
        codes = [
            c.post("/query", json={"question": "How many orders?"}).status_code for _ in range(3)
        ]
        last = c.post("/query", json={"question": "How many orders?"})
    assert codes == [200, 200, 429]
    assert last.status_code == 429 and int(last.headers["Retry-After"]) > 0


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])
    assert limiter.check("ip") is None and limiter.check("ip") is None
    assert limiter.check("ip") is not None
    assert limiter.check("other-ip") is None  # per client
    now[0] = 61.0
    assert limiter.check("ip") is None


def test_unexpected_errors_do_not_leak_internals():
    svc = FakeService(error=RuntimeError("password=hunter2 at db.internal:5432"))
    test_client = TestClient(create_app(lambda: svc, Settings(_env_file=None),
                                        health_check=lambda s: {}), raise_server_exceptions=False)  # fmt: skip
    with test_client as c:
        r = c.post("/query", json={"question": "How many orders?"})
    assert r.status_code == 500
    assert set(r.json()) == {"error", "error_id"} and "hunter2" not in r.text


def test_feedback_is_forwarded():
    c, svc = client()
    with c:
        r = c.post(
            "/feedback", json={"trace_id": "trace12345", "positive": True, "comment": "great"}
        )
        bad = c.post("/feedback", json={"trace_id": "x'; DROP--", "positive": True})
    assert r.status_code == 204 and svc.feedback_calls == [("trace12345", True, "great")]
    assert bad.status_code == 422


def test_health_reports_degraded_with_503():
    with client(health=lambda s: {"database": "ok", "retrieval_index": "error: X"})[0] as c:
        r = c.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    with client()[0] as c:
        assert c.get("/health").json() == {"status": "ok", "agent": "v6-safe", "database": "ok"}


def test_schema_lists_documented_tables():
    with client()[0] as c:
        body = c.get("/schema").json()
    assert (
        "orders" in body["tables"]
        and "order_purchase_timestamp" in body["tables"]["orders"]["columns"]
    )
