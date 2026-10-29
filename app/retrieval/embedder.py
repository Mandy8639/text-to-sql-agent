"""Text embeddings behind a small interface, so the model or vendor is swappable.

Queries and passages are embedded differently: retrieval models like bge are trained with
a query instruction prefix, which fastembed's `query_embed` adds.
"""

from functools import lru_cache
from typing import Protocol

from app.config import Settings


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Local ONNX embeddings via fastembed. No API key; the model downloads once."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # imported lazily: loading the model takes ~1 s

        self.model_name = model_name
        self._model = TextEmbedding(model_name, cache_dir=cache_dir)
        self.dim = len(self.embed_queries(["probe"])[0])

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.query_embed(texts)]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.passage_embed(texts)]


@lru_cache
def _fastembed(model_name: str, cache_dir: str) -> FastEmbedder:
    return FastEmbedder(model_name, cache_dir)


def get_embedder(settings: Settings) -> Embedder:
    """The configured embedder (cached per process: the model is loaded once)."""
    if settings.embedding_provider == "fastembed":
        return _fastembed(settings.embedding_model, settings.embedding_cache_dir)
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")
