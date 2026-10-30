"""Startup and final fallback contracts when Qdrant is unavailable."""

from __future__ import annotations

from typing import Any

import pytest
from search_core.plugin_api.vector_point import VectorPoint as CoreVectorPoint

from app.application.services.literal_fallback_maintenance import (
    LiteralFallbackSnapshot,
)
from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)
from app.providers.public.vector_index_startup_provider import (
    VectorIndexStartupProvider,
)


class StartupSpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def initialize(self) -> None:
        self.events.append("delegate.initialize")

    async def shutdown(self) -> None:
        self.events.append("delegate.shutdown")


class RebuildFailsVectorStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def rebuild_from_backend(self) -> None:
        self.events.append("vector.rebuild")
        raise RuntimeError("qdrant unavailable")

    def close(self) -> None:
        self.events.append("vector.close")


class DurableFallbackStore:
    """Expose durable metadata while the Qdrant-dependent id scan is down."""

    def __init__(self) -> None:
        self.loaded = False

    def load_fallback_points(self) -> list[CoreVectorPoint]:
        self.loaded = True
        return [
            CoreVectorPoint(
                id="worker:default:full",
                vector=[0.1],
                payload={
                    "profile_key": "worker:default",
                    "worker_id": "worker",
                    "profile_id": "default",
                    "content": "python search assistant",
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]

    def get_vector_ids(self) -> list[str]:
        raise RuntimeError("qdrant unavailable")


class DurableFallbackVectorStore:
    def search(self, *args: Any, **kwargs: Any):
        raise RuntimeError("dense unavailable")

    def text_search(self, *args: Any, **kwargs: Any):
        raise RuntimeError("keyword unavailable")

    def size(self) -> int:
        return 1.0


@pytest.mark.asyncio
async def test_startup_rebuild_failure_keeps_delegate_alive() -> None:
    events: list[str] = []
    provider = VectorIndexStartupProvider(
        StartupSpy(events),
        RebuildFailsVectorStore(events),
    )

    await provider.initialize()

    assert events == ["delegate.initialize", "vector.rebuild"]

    await provider.shutdown()

    assert events[-2:] == ["vector.close", "delegate.shutdown"]


def test_literal_snapshot_loads_from_durable_fallback_points() -> None:
    store = DurableFallbackStore()
    snapshot = LiteralFallbackSnapshot()

    snapshot.reload_from_store(store)

    assert store.loaded
    assert snapshot.size == 1
    assert snapshot.profile_count == 1
    assert snapshot.keyword_search(
        "python search",
        3,
        {"runtime_state": ["online"], "availability": ["public"]},
        None,
    )


def test_all_retrieval_failure_returns_deterministic_final_default(tmp_path) -> None:
    snapshot = LiteralFallbackSnapshot()
    snapshot.upsert(
        [
            CoreVectorPoint(
                id="worker:default:full",
                vector=[],
                payload={
                    "profile_key": "worker:default",
                    "worker_id": "worker",
                    "profile_id": "default",
                    "content": "python search assistant",
                    "runtime_state": "online",
                    "availability": "public",
                    "short_profile": "Search assistant",
                },
            )
        ]
    )
    service = WorkerVectorMatchService(
        vector_store=DurableFallbackVectorStore(),
        metadata_store=FileMetadataStoreAdapter(
            storage_dir=str(tmp_path / "metadata")
        ),
        fallback_index=snapshot,
        fragment_config=FragmentRetrievalConfig(
            enable_fragment_embedding=True,
            reranker_model=None,
        ),
    )
    runtime_config: dict[str, Any] = {}

    results = service.match(
        query_embedding=[0.1],
        query="completely unrelated unique query",
        mode="fragment",
        top_k=1,
        vector_min_score=0.01,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert results[0].reasons == ["literal_fallback_default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["literal_fallback_default_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is True


class BoundedDurablePersistence:
    def __init__(self, points: list[CoreVectorPoint], batch_size: int) -> None:
        self.points = points
        self.batch_size = batch_size
        self.call_count = 0

    def iter_batches(self, batch_size: int):
        self.call_count += 1
        for index in range(0, len(self.points), batch_size):
            yield self.points[index : index + batch_size]


def test_durable_fallback_loader_is_qdrant_independent() -> None:
    from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
        QdrantMySQLVectorStore,
    )

    point = CoreVectorPoint(
        id="worker:default:full",
        vector=[0.1],
        payload={"profile_key": "worker:default", "content": "fallback"},
    )
    store = QdrantMySQLVectorStore.__new__(QdrantMySQLVectorStore)
    store._persistence = BoundedDurablePersistence([point], 1)

    loaded = store.load_fallback_points(batch_size=1)

    assert [point.id for point in loaded] == [point.id]
    assert store._persistence.call_count == 1
