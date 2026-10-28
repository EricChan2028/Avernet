"""Observed public reranker wrapper for the OSS composition."""

from __future__ import annotations

from time import perf_counter

from app.infra.config.search_provider_settings import reranker_options
from app.infra.public.observability.search_dependency_metrics import (
    record_rerank_call,
)
from search_core.providers.reranker import HttpReranker as SharedHttpReranker


class HttpReranker(SharedHttpReranker):
    """HTTP reranker adapter, now exposing dependency metrics."""

    def __init__(self, model=None):
        super().__init__(**reranker_options(model))

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5):
        started_at = perf_counter()
        try:
            result = super().rerank(query, candidates, top_k)
        except Exception:
            record_rerank_call("error", perf_counter() - started_at)
            raise
        record_rerank_call("success", perf_counter() - started_at)
        return result


def get_reranker(model: str | None = None):
    """Explicit request model wins; omitted model uses the TC process default."""
    from app.infra.public.reranker.circuit_breaker_reranker import (
        get_circuit_breaker_reranker,
    )

    return get_circuit_breaker_reranker(model)
