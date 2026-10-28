"""Thread-safe in-process cache for query embeddings."""

from __future__ import annotations

import hashlib
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Callable

from app.infra.public.embedding.real_embedding_provider import (
    RealEmbeddingProvider,
)


_EMBEDDING_CACHE_METRICS: dict[str, object] | None = None


def _get_embedding_cache_metrics() -> dict[str, object]:
    global _EMBEDDING_CACHE_METRICS
    if _EMBEDDING_CACHE_METRICS is not None:
        return _EMBEDDING_CACHE_METRICS

    from prometheus_client import Counter, Gauge

    _EMBEDDING_CACHE_METRICS = {
        "hits": Counter(
            "bcsfuse_embedding_cache_hits_total",
            "Embedding cache hits",
            ["model"],
        ),
        "misses": Counter(
            "bcsfuse_embedding_cache_misses_total",
            "Embedding cache misses requiring a backend call",
            ["model"],
        ),
        "errors": Counter(
            "bcsfuse_embedding_cache_errors_total",
            "Failed embedding cache backend calls",
            ["model"],
        ),
        "evictions": Counter(
            "bcsfuse_embedding_cache_evictions_total",
            "Entries evicted by embedding cache capacity",
            ["model"],
        ),
        "entries": Gauge(
            "bcsfuse_embedding_cache_entries",
            "Embedding cache entries currently held",
            ["model"],
        ),
    }
    return _EMBEDDING_CACHE_METRICS


def normalize_embedding_query(text: str) -> str:
    """Normalize only Unicode- and whitespace-equivalent queries.

    Case folding and alias rewriting are intentionally omitted so a cache key
    never collapses inputs that a model could treat differently.
    """
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


class CachedEmbeddingProvider(RealEmbeddingProvider):
    """Cache an exact model, dimension, and normalized query embedding.

    Calls are single-flight by cache key. The backend always receives the
    original text; normalization is used only to derive the cache key. Backend
    failures are not cached.
    """

    def __init__(
        self,
        backend: RealEmbeddingProvider,
        *,
        max_entries: int = 10000,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("embedding cache max_entries must be positive")
        if ttl_seconds < 0:
            raise ValueError("embedding cache ttl_seconds cannot be negative")

        metrics = _get_embedding_cache_metrics()
        self._embedding_metrics = metrics
        self.backend = backend
        self.cache_max_entries = max_entries
        self.cache_ttl_seconds = ttl_seconds
        self._model_label = str(getattr(backend._settings, "model", "unknown"))
        self._clock = clock
        self._cache_lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._inflight: dict[str, threading.Event] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_errors = 0
        self._cache_evictions = 0

        super().__init__(settings=backend._settings)

    def _key(self, text: str) -> str:
        normalized = normalize_embedding_query(text)
        settings = self.backend._settings
        material = (
            f"{self._model_label}\0{int(settings.dimension)}\0{normalized}"
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _record_hit(self) -> None:
        self._cache_hits += 1
        self._embedding_metrics["hits"].labels(self._model_label).inc()

    def _record_miss(self) -> None:
        self._cache_misses += 1
        self._embedding_metrics["misses"].labels(self._model_label).inc()

    def _record_error(self) -> None:
        self._cache_errors += 1
        self._embedding_metrics["errors"].labels(self._model_label).inc()

    def _record_eviction(self) -> None:
        self._cache_evictions += 1
        self._embedding_metrics["evictions"].labels(self._model_label).inc()

    def _sync_entry_gauge(self) -> None:
        self._embedding_metrics["entries"].labels(self._model_label).set(
            len(self._cache)
        )

    @property
    def stats(self) -> dict[str, float | int]:
        total = self._cache_hits + self._cache_misses
        ratio = float(self._cache_hits / total) if total else 0.0
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_errors": self._cache_errors,
            "cache_evictions": self._cache_evictions,
            "cache_entries": len(self._cache),
            "cache_hit_ratio": ratio,
        }

    def embed(self, text: str) -> list[float]:
        key = self._key(text)
        owner: threading.Event | None = None
        while owner is None:
            with self._cache_lock:
                now = self._clock()
                if self.cache_ttl_seconds > 0:
                    entry = self._cache.get(key)
                    if entry is not None:
                        expires_at, vector = entry
                        if now < expires_at:
                            self._cache.move_to_end(key)
                            self._record_hit()
                            return list(vector)
                        self._cache.pop(key, None)
                        self._sync_entry_gauge()

                event = self._inflight.get(key)
                if event is None:
                    owner = threading.Event()
                    self._inflight[key] = owner
                    self._record_miss()

            if event is not None:
                event.wait()
                # The owner may have failed. Loop without immediately recording
                # a new miss so a valid entry can still be consumed as a hit.

        try:
            vector = self.backend.embed(text)
        except BaseException:
            with self._cache_lock:
                self._inflight.pop(key, None)
                self._record_error()
                self._sync_entry_gauge()
            owner.set()
            raise

        with self._cache_lock:
            if self.cache_ttl_seconds > 0:
                self._cache[key] = (self._clock() + self.cache_ttl_seconds, list(vector))
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_max_entries:
                    self._cache.popitem(last=False)
                    self._record_eviction()
                self._sync_entry_gauge()
            self._inflight.pop(key, None)
        owner.set()
        return list(vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Index bulk writes intentionally bypass the query cache.
        return self.backend.embed_batch(texts)

    def close(self) -> None:
        try:
            self.backend.close()
        finally:
            super().close()
