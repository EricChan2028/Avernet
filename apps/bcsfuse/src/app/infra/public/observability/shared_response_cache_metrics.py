"""Prometheus metrics for the cross-pod L1/L2 response cache."""

from __future__ import annotations

_SHARED_CACHE_METRICS: dict[str, object] | None = None


def _metrics() -> dict[str, object]:
    global _SHARED_CACHE_METRICS
    if _SHARED_CACHE_METRICS is not None:
        return _SHARED_CACHE_METRICS

    from prometheus_client import Counter

    labels = ["operation", "stage"]
    _SHARED_CACHE_METRICS = {
        "hits": Counter(
            "bcsfuse_search_shared_response_cache_hits_total",
            "Successful cross-pod L2 response cache hits",
            labels,
        ),
        "misses": Counter(
            "bcsfuse_search_shared_response_cache_misses_total",
            "Cross-pod L2 response cache misses requiring downstream execution",
            labels,
        ),
        "stores": Counter(
            "bcsfuse_search_shared_response_cache_stores_total",
            "Successful cross-pod L2 response cache stores",
            labels,
        ),
        "errors": Counter(
            "bcsfuse_search_shared_response_cache_errors_total",
            "Cross-pod L2 response cache errors by processing stage",
            labels,
        ),
    }
    return _SHARED_CACHE_METRICS


def record_shared_response_cache_hit(operation: str, *, stage: str) -> None:
    _metrics()["hits"].labels(operation=operation, stage=stage).inc()


def record_shared_response_cache_miss(operation: str, *, stage: str) -> None:
    _metrics()["misses"].labels(operation=operation, stage=stage).inc()


def record_shared_response_cache_store(operation: str, *, stage: str) -> None:
    _metrics()["stores"].labels(operation=operation, stage=stage).inc()


def record_shared_response_cache_error(operation: str, *, stage: str) -> None:
    _metrics()["errors"].labels(operation=operation, stage=stage).inc()
