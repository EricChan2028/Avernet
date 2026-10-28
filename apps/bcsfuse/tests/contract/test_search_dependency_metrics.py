"""Protocols expose search dependencies so latency and failures are visible."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from app.domain.models.vector_search_hit import VectorSearchHit
from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from search_core.config.qdrant import LOCAL_QDRANT


class EmptyPersistence:
    def load_all(self):
        return []


def sample(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else float(value)


def metric_count(name: str, labels: dict[str, str]) -> float:
    return sample(f"{name}_count", labels) if name.endswith("_seconds") else sample(name, labels)


def store(tmp_path):
    return QdrantMySQLVectorStore(
        collection_name="metrics_test",
        qdrant_path=str(tmp_path / "qdrant"),
        dimension=4,
        persistence_backend=EmptyPersistence(),
        connection=LOCAL_QDRANT,
    )


def test_dense_and_keyword_queries_record_qdrant_duration_and_lock_wait(tmp_path, monkeypatch):
    vector_store = store(tmp_path)
    hit = VectorSearchHit(id="worker:default", score=0.8)
    monkeypatch.setattr(vector_store._qdrant, "search", lambda *args, **kwargs: [hit])
    monkeypatch.setattr(vector_store._keywords, "search", lambda *args, **kwargs: [hit])

    vector_store.search([0.1, 0.2, 0.3, 0.4], top_k=1, filters={})
    vector_store.text_search("python", top_k=1, filters={})

    for operation in ("dense", "keyword"):
        assert metric_count(
            "bcsfuse_qdrant_search_duration_seconds",
            {"operation": operation, "mode": "local", "outcome": "success"},
        ) >= 1
        assert metric_count(
            "bcsfuse_qdrant_lock_wait_seconds",
            {"operation": operation, "mode": "local"},
        ) >= 1


def test_qdrant_failures_record_errors_and_do_not_become_empty_results(tmp_path, monkeypatch):
    vector_store = store(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(vector_store._qdrant, "search", fail)
    monkeypatch.setattr(vector_store._keywords, "search", fail)

    with pytest.raises(RuntimeError):
        vector_store.search([0.1, 0.2, 0.3, 0.4], top_k=1)
    with pytest.raises(RuntimeError):
        vector_store.text_search("python", top_k=1)

    for operation in ("dense", "keyword"):
        assert sample(
            "bcsfuse_qdrant_errors_total", {"operation": operation, "mode": "local"}
        ) >= 1
        assert sample(
            "bcsfuse_qdrant_lock_wait_seconds_count",
            {"operation": operation, "mode": "local"},
        ) >= 1
        assert sample(
            "bcsfuse_qdrant_search_duration_seconds_count",
            {"operation": operation, "mode": "local", "outcome": "error"},
        ) >= 1


def test_http_reranker_success_and_failure_are_measured(monkeypatch):
    from app.infra.public.reranker.http_reranker import HttpReranker

    monkeypatch.setenv("RERANKER_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("RERANKER_API_KEY", "enabled-metrics-test")
    reranker = HttpReranker(model="bge-reranker-v2-m3")
    monkeypatch.setattr(reranker, "_call_api", lambda *args, **kwargs: [0.8])

    candidates = [{"id": "worker", "text": "python"}]
    assert reranker.rerank("python", candidates, top_k=1)[0].score == 0.8

    assert sample("bcsfuse_rerank_calls_total", {"outcome": "success"}) >= 1
    assert sample(
        "bcsfuse_rerank_duration_seconds_count", {"outcome": "success"}
    ) >= 1

    def fail(*args, **kwargs):
        raise RuntimeError("rerank unavailable")

    monkeypatch.setattr(reranker, "_call_api", fail)
    with pytest.raises(RuntimeError):
        reranker.rerank("python", candidates, top_k=1)

    assert sample("bcsfuse_rerank_calls_total", {"outcome": "error"}) >= 1
    assert sample(
        "bcsfuse_rerank_duration_seconds_count", {"outcome": "error"}
    ) >= 1


def test_real_embedding_success_and_failure_are_measured(monkeypatch):
    from app.infra.embedding.config.embedding_settings import EmbeddingSettings
    from app.infra.embedding.providers.real_provider import (
        EmbeddingAPIError,
        RealEmbeddingProvider,
    )

    provider = RealEmbeddingProvider(
        settings=EmbeddingSettings(
            base_url="http://127.0.0.1:1/v1",
            auth_token="test-only",
            model="embedding-metrics-test",
            dimension=64,
        ),
        max_retries=0,
    )
    monkeypatch.setattr(
        provider, "_call_embedding_api", lambda texts: [[0.25] * 64 for _ in texts]
    )

    result = provider.embed_batch(["metrics"])
    assert len(result[0]) == 64
    assert sample(
        "bcsfuse_embedding_calls_total",
        {"model": "embedding-metrics-test", "outcome": "success"},
    ) >= 1
    assert sample(
        "bcsfuse_embedding_duration_seconds_count",
        {"model": "embedding-metrics-test", "outcome": "success"},
    ) >= 1

    def fail(*args, **kwargs):
        raise EmbeddingAPIError("embedding unavailable")

    monkeypatch.setattr(provider, "_call_embedding_api", fail)
    with pytest.raises(EmbeddingAPIError):
        provider.embed_batch(["metrics"])

    assert sample(
        "bcsfuse_embedding_calls_total",
        {"model": "embedding-metrics-test", "outcome": "error"},
    ) >= 1
    assert sample(
        "bcsfuse_embedding_duration_seconds_count",
        {"model": "embedding-metrics-test", "outcome": "error"},
    ) >= 1
    assert sample(
        "bcsfuse_embedding_errors_total", {"model": "embedding-metrics-test"}
    ) >= 1
