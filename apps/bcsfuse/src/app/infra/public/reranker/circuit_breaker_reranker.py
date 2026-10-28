"""Process-local circuit breaker around a reranker provider."""

from __future__ import annotations

import threading
import time
from functools import lru_cache
from collections.abc import Callable
from typing import Any

from app.infra.public.observability.search_dependency_metrics import (
    record_rerank_circuit_rejection,
    record_rerank_circuit_transition,
    set_rerank_circuit_open,
)


class RerankCircuitOpenError(RuntimeError):
    """The reranker circuit is open and this call was rejected early."""


class CircuitBreakerReranker:
    """Fail fast after repeated reranker failures; callers own fallback.

    The breaker is process-local and does not model service-wide health. A
    deterministic or no-op reranker may be used without this wrapper.
    """

    def __init__(
        self,
        backend: Any,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("reranker circuit failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError(
                "reranker circuit recovery_timeout_seconds must be positive"
            )
        self.backend = backend
        self.clock = clock
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.state = "closed"
        self._failed_attempts = 0
        self._state_changed_at = 0.0
        self._lock = threading.RLock()
        self.model = str(getattr(backend, "model", None) or "unknown")
        set_rerank_circuit_open(self.model, open_=False)

    def _transition(self, to_state: str, *, failed_attempts: int | None = None) -> None:
        from_state = self.state
        self.state = to_state
        self._state_changed_at = self.clock()
        if failed_attempts is not None:
            self._failed_attempts = failed_attempts
        record_rerank_circuit_transition(self.model, from_state, to_state)
        set_rerank_circuit_open(self.model, open_=to_state != "closed")

    def _reject(self) -> None:
        record_rerank_circuit_rejection(self.model)
        raise RerankCircuitOpenError(
            "reranker circuit is open; retry after the recovery timeout"
        )

    def _before_call(self) -> None:
        with self._lock:
            if self.state == "closed":
                return
            if self.state == "half_open":
                self._reject()
            if self.clock() - self._state_changed_at < self.recovery_timeout_seconds:
                self._reject()
            self._transition("half_open")

    def _after_failure(self) -> None:
        with self._lock:
            if self.state == "half_open":
                self._transition("open")
                return
            if self.state != "closed":
                return
            self._failed_attempts += 1
            if self._failed_attempts >= self.failure_threshold:
                self._transition("open")

    def _after_success(self) -> None:
        with self._lock:
            if self.state != "closed":
                self._transition("closed", failed_attempts=0)
            else:
                self._failed_attempts = 0

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5):
        if not candidates:
            return []
        self._before_call()
        try:
            result = self.backend.rerank(query, candidates, top_k)
        except Exception:
            self._after_failure()
            raise
        self._after_success()
        return result

    def __getattr__(self, item: str) -> Any:
        return getattr(self.backend, item)


__all__ = ["CircuitBreakerReranker", "RerankCircuitOpenError"]

@lru_cache(maxsize=32)
def get_circuit_breaker_reranker(model: str | None = None):
    """Return one process-local breaker per resolved reranker model.

    Fragment reranking may create a service for a request-specific model. These
    adapters share one breaker state per model inside this process, without
    claiming coordination between replicas.
    """
    from app.infra.config.search_provider_settings import reranker_circuit_options
    from app.infra.public.reranker.http_reranker import HttpReranker

    return CircuitBreakerReranker(
        HttpReranker(model=model),
        **reranker_circuit_options(),
    )
