"""Process-local circuit breaker around a real embedding provider."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from app.infra.public.observability.search_dependency_metrics import (
    record_embedding_circuit_rejection,
    record_embedding_circuit_transition,
    set_embedding_circuit_open,
)


class EmbeddingCircuitOpenError(RuntimeError):
    """The embedding circuit is open and this call was rejected early."""


class CircuitBreakerEmbeddingProvider:
    """Fail fast after repeated embedding failures while callers own fallback.

    The breaker is process-local by design. It limits wasted capacity while an
    embedding service is unavailable, but it does not claim distributed health
    or coordinate retries across replicas.
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
            raise ValueError("embedding circuit failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError(
                "embedding circuit recovery_timeout_seconds must be positive"
            )

        self.backend = backend
        self.clock = clock
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.state = "closed"
        self._failed_attempts = 0
        self._state_changed_at = 0.0
        self._lock = threading.RLock()
        settings = getattr(backend, "_settings", None)
        self.model = str(
            getattr(backend, "model", None)
            or getattr(settings, "model", None)
            or "unknown"
        )
        set_embedding_circuit_open(self.model, open_=False)

    @property
    def dimension(self) -> int:
        return int(self.backend.dimension)

    def _transition(
        self, to_state: str, *, failed_attempts: int | None = None
    ) -> None:
        from_state = self.state
        self.state = to_state
        self._state_changed_at = self.clock()
        if failed_attempts is not None:
            self._failed_attempts = failed_attempts
        record_embedding_circuit_transition(self.model, from_state, to_state)
        set_embedding_circuit_open(self.model, open_=to_state != "closed")

    def _reject(self) -> None:
        record_embedding_circuit_rejection(self.model)
        raise EmbeddingCircuitOpenError(
            "embedding circuit is open; retry after the recovery timeout"
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
                # Another admitted call already opened the circuit.
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

    def _execute(self, operation: Callable[[], Any]) -> Any:
        try:
            result = operation()
        except Exception:
            self._after_failure()
            raise
        self._after_success()
        return result

    def embed(self, text: str) -> list[float]:
        self._before_call()
        return self._execute(lambda: self.backend.embed(text))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return self.backend.embed_batch(texts)
        self._before_call()
        return self._execute(lambda: self.backend.embed_batch(texts))

    def close(self) -> None:
        self.backend.close()

    def __getattr__(self, item: str) -> Any:
        return getattr(self.backend, item)


__all__ = [
    "CircuitBreakerEmbeddingProvider",
    "EmbeddingCircuitOpenError",
]
