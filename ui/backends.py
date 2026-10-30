"""Where the UI gets its answers. Two interchangeable backends:

- ApiBackend (default): HTTP calls to the FastAPI service; the UI holds no secrets.
- LocalBackend (UI_MODE=inprocess): runs QueryService inside the Streamlit process, for hosts
  with a single app and no separate API (Streamlit Community Cloud). It applies the same
  validation, rate limits and spend cap as the API (app/limits.py).

`app.*` is imported lazily: the UI Docker image only has the API backend's dependencies.
"""

from dataclasses import asdict
from typing import Any, Protocol


class BackendMessage(Exception):
    """Something to show the user instead of a result. `level` is 'warning' or 'error'."""

    def __init__(self, message: str, level: str = "warning") -> None:
        super().__init__(message)
        self.message = message
        self.level = level


class Backend(Protocol):
    def ask(self, question: str, visitor: str) -> dict[str, Any]: ...

    def feedback(self, trace_id: str, positive: bool) -> None: ...

    def health(self) -> dict[str, Any] | None: ...


class ApiBackend:
    def __init__(self, api_url: str) -> None:
        self.api_url = api_url

    def ask(self, question: str, visitor: str) -> dict[str, Any]:
        import httpx

        try:
            r = httpx.post(
                f"{self.api_url}/query",
                json={"question": question},
                headers={"X-Client-IP": visitor},
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise BackendMessage(
                f"Can't reach the API at {self.api_url}. Start it with `make run-api`.", "error"
            ) from exc
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 503):  # rate limit / daily demo budget
            raise BackendMessage(r.json().get("detail", "Too many questions; wait a minute."))
        if r.status_code == 422:
            raise BackendMessage(
                "That question couldn't be accepted (too short, too long, or invalid)."
            )
        raise BackendMessage(
            f"Something went wrong (error id {r.json().get('error_id', '?')}).", "error"
        )

    def feedback(self, trace_id: str, positive: bool) -> None:
        import httpx

        try:
            r = httpx.post(
                f"{self.api_url}/feedback",
                json={"trace_id": trace_id, "positive": positive},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise BackendMessage("Couldn't send the feedback.", "error") from exc
        if r.status_code != 204:
            raise BackendMessage("Couldn't send the feedback.", "error")

    def health(self) -> dict[str, Any] | None:
        import httpx

        try:
            return httpx.get(f"{self.api_url}/health", timeout=10).json()
        except (httpx.HTTPError, ValueError):
            return None


class LocalBackend:
    def __init__(self, service: Any, admission: Any, max_question_chars: int) -> None:
        self.service = service
        self.admission = admission
        self.max_question_chars = max_question_chars

    @classmethod
    def from_settings(cls, settings: Any) -> "LocalBackend":
        from app.limits import Admission
        from app.observability.logs import configure_logging
        from app.service import QueryService

        configure_logging()
        return cls(
            QueryService.from_settings(settings),
            Admission(settings),
            settings.api_max_question_chars,
        )

    def ask(self, question: str, visitor: str) -> dict[str, Any]:
        from app.limits import RateLimited, clean_question
        from app.spend import BudgetExceeded

        try:
            cleaned = clean_question(question, self.max_question_chars)
        except ValueError as exc:
            raise BackendMessage(f"That question couldn't be accepted: {exc}.") from exc
        try:
            self.admission.check_rate(visitor)
            self.admission.check_budget()
        except (RateLimited, BudgetExceeded) as exc:
            raise BackendMessage(str(exc)) from exc
        response = self.service.ask(cleaned)
        self.admission.record(response.cost_usd)
        return asdict(response)

    def feedback(self, trace_id: str, positive: bool) -> None:
        self.service.feedback(trace_id, positive)

    def health(self) -> dict[str, Any] | None:
        checks = self.service.health()
        ok = all(v == "ok" for v in checks.values())
        return {"status": "ok" if ok else "degraded", "agent": self.service.agent.version, **checks}
