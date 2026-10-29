"""Registry of agent versions the eval harness can run. Add each new version here."""

from collections.abc import Callable
from typing import Protocol

from app.agent.baseline import BareSchemaBaselineAgent, BaselineAgent
from app.agent.types import AgentResult
from app.config import Settings
from app.db.connection import make_engine
from app.llm.factory import get_provider


class Agent(Protocol):
    version: str
    system_prompt: str

    def answer(self, question: str) -> AgentResult: ...


def _baseline(settings: Settings) -> Agent:
    return BaselineAgent.from_engine(
        get_provider(settings), make_engine(settings.agent_database_url)
    )


def _baseline_raw(settings: Settings) -> Agent:
    return BareSchemaBaselineAgent.from_engine(
        get_provider(settings), make_engine(settings.agent_database_url)
    )


AGENTS: dict[str, Callable[[Settings], Agent]] = {
    "baseline": _baseline,
    "baseline-raw": _baseline_raw,  # ablation: no schema documentation
}
