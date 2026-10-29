"""Registry of agent versions the eval harness can run. Add each new version here."""

from collections.abc import Callable
from typing import Protocol

from app.agent.baseline import BareSchemaBaselineAgent, BaselineAgent
from app.agent.pipeline import VERSIONS, PipelineAgent
from app.agent.types import AgentResult
from app.config import Settings
from app.db.connection import make_engine
from app.llm.factory import get_provider
from app.observability.tracing import get_tracer
from app.retrieval.embedder import get_embedder
from app.retrieval.store import VectorStore


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


def _pipeline(name: str) -> Callable[[Settings], Agent]:
    def build(settings: Settings) -> Agent:
        config = VERSIONS[name]
        needs_store = config.schema_mode == "retrieved" or config.few_shot_k > 0
        store = (
            VectorStore(make_engine(settings.rag_database_url), get_embedder(settings))
            if needs_store
            else None
        )
        return PipelineAgent.from_engine(
            config,
            get_provider(settings),
            make_engine(settings.agent_database_url),
            store,
            tracer=get_tracer(settings),
        )

    return build


AGENTS: dict[str, Callable[[Settings], Agent]] = {
    "baseline": _baseline,
    "baseline-raw": _baseline_raw,  # ablation: no schema documentation
    **{name: _pipeline(name) for name in VERSIONS},  # v2..v6, raw-fewshot
}
