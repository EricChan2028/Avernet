"""TC environment configuration around the shared model client."""

from app.infra.config.search_provider_settings import reranker_options
from search_core.providers.reranker import HttpReranker as SharedHttpReranker
from search_core.providers.reranker import requests


class HttpReranker(SharedHttpReranker):
    def __init__(self, model=None):
        super().__init__(**reranker_options(model))


def get_reranker(model: str | None = None):
    """Return the process-local circuit-breaker reranker.

    Explicit request model wins; omitted model uses the TC process default.
    This keeps runtime model overrides on the same fail-fast path as the
    composition root instead of silently bypassing the breaker.
    """
    from app.infra.public.reranker.circuit_breaker_reranker import (
        get_circuit_breaker_reranker as get_breaker,
    )

    return get_breaker(model)


def get_circuit_breaker_reranker(model: str | None = None):
    """Compatibility alias for explicit breaker construction."""
    return get_reranker(model)
