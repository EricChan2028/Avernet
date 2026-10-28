"""Runtime maintenance contract for the final literal fallback snapshot."""

from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.domain.models.vector_point import VectorPoint as DomainVectorPoint
from app.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)


class OutageBackend:
    def __init__(self, fail=False):
        self.fail = fail
        self.points = {}

    def upsert(self, points):
        if self.fail:
            raise RuntimeError("vector write unavailable")
        for point in points:
            self.points[point.id] = point

    def delete(self, ids):
        removed = 0
        for point_id in ids:
            if self.points.pop(point_id, None) is not None:
                removed += 1
        return removed

    def delete_by_worker(self, worker_id):
        removed = [
            point_id for point_id, point in self.points.items()
            if point.payload.get("worker_id") == worker_id
        ]
        return self.delete(removed)

    def delete_by_profile(self, worker_id, profile_id):
        removed = [
            point_id for point_id, point in self.points.items()
            if point.payload.get("worker_id") == worker_id
            and point.payload.get("profile_id") == profile_id
        ]
        return self.delete(removed)

    def clear(self):
        self.points.clear()

    def update_payload_by_worker(self, worker_id, payload_updates):
        count = 0
        for point in self.points.values():
            if point.payload.get("worker_id") == worker_id:
                point.payload.update(payload_updates)
                count += 1
        return count

    def size(self):
        return len(self.points)

    def search(self, vector, top_k, filters=None):
        raise RuntimeError("dense unavailable")

    def text_search(self, query, top_k, filters=None):
        raise RuntimeError("keyword unavailable")

    def get_vector_ids(self):
        return list(self.points)

    def get(self, point_id):
        return self.points.get(point_id)

    def rebuild_from_backend(self):
        pass


def point(profile_key="worker:default", content="星河 数据分析助手", **payload):
    worker_id, profile_id = profile_key.rsplit(":", 1)
    return DomainVectorPoint(
        id=f"{profile_key}:full",
        vector=[0.1],
        payload={
            "id": f"{profile_key}:full",
            "profile_key": profile_key,
            "worker_id": worker_id,
            "profile_id": profile_id,
            "content": content,
            "runtime_state": "online",
            "availability": "public",
            **payload,
        },
    )


def hoisted_store():
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    return wrap_vector_store_with_literal_fallback(OutageBackend())


def service_for(store, tmp_path):
    return WorkerVectorMatchService(
        vector_store=store,
        metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
        fallback_index=store.literal_fallback_index,
        fragment_config=FragmentRetrievalConfig(enable_fragment_embedding=True),
    )


def test_successful_upsert_updates_final_fallback_snapshot(tmp_path):
    store = hoisted_store()
    service = service_for(store, tmp_path)

    store.upsert([point()])

    results = service.match(
        query_embedding=[0.1],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config={},
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert results[0].score > 0.0


def test_delete_removes_profile_from_final_fallback_snapshot(tmp_path):
    store = hoisted_store()
    service = service_for(store, tmp_path)
    store.upsert([point()])

    store.delete([f"worker:default:full"])

    results = service.match(
        query_embedding=[0.1],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config={},
    )

    assert results == []


def test_payload_state_updates_are_visible_in_final_fallback(tmp_path):
    store = hoisted_store()
    service = service_for(store, tmp_path)
    store.upsert([point()])

    assert store.update_payload_by_worker("worker", {"runtime_state": "offline"}) == 1

    results = service.match(
        query_embedding=[0.1],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config={},
    )

    assert results == []


def test_failed_backend_write_does_not_mutate_final_fallback(tmp_path):
    backend = OutageBackend(fail=True)
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    store = wrap_vector_store_with_literal_fallback(backend)
    service = service_for(store, tmp_path)

    try:
        store.upsert([point()])
    except RuntimeError:
        pass
    else:
        raise AssertionError("backend failure must propagate")

    assert service.match(
        query_embedding=[0.1],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config={},
    ) == []


def test_startup_rebuild_reloads_final_fallback_snapshot(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = OutageBackend()
    backend.upsert([point()])
    store = wrap_vector_store_with_literal_fallback(backend)
    store.literal_fallback_index.clear()
    service = service_for(store, tmp_path)

    store.rebuild_from_backend()

    assert [result.profile_key for result in service.match(
        query_embedding=[0.1],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config={},
    )] == ["worker:default"]


def test_legacy_in_memory_upsert_signature_maintains_snapshot():
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )
    from app.infra.public.vectorstores.in_memory_vector_store import InMemoryVectorStore

    backend = InMemoryVectorStore(dimension=1)
    store = wrap_vector_store_with_literal_fallback(backend)

    store.upsert(
        "worker:default:full",
        [1.0],
        {
            "profile_key": "worker:default",
            "worker_id": "worker",
            "profile_id": "default",
            "content": "星河 数据分析助手",
            "runtime_state": "online",
            "availability": "public",
        },
    )

    hits = store.literal_fallback_index.keyword_search(
        "星河 数据分析", 1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]


def test_batch_upsert_is_deduplicated_and_versioned():
    store = hoisted_store()
    version_before = store.literal_fallback_index.version

    store.upsert([
        point("first:default", "星河 数据分析助手"),
        point("first:default", "星河 数据分析 second richer content", profile_id="z"),
        point("second:default", "数据分析"),
    ])

    assert store.literal_fallback_index.profile_count == 2
    assert store.literal_fallback_index.version == version_before + 1
    assert len(store.literal_fallback_index.keyword_search(
        "星河 数据分析 second richer content", 2,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )) == 2


def test_builder_reuses_maintained_snapshot_from_composition():
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )

    store = hoisted_store()
    store.upsert([point()])

    assert build_literal_fallback_index(store) is store.literal_fallback_index


def test_worker_deletion_updates_final_fallback_snapshot(tmp_path):
    store = hoisted_store()
    service = service_for(store, tmp_path)
    store.upsert([
        point("worker:default"),
        point("other:default"),
    ])

    assert store.delete_by_worker("worker") == 1

    assert [
        result.profile_key
        for result in service.match(
            query_embedding=[0.1],
            query="星河 数据分析",
            mode="fragment",
            top_k=3,
            runtime_config={},
        )
    ] == ["other:default"]


def test_profile_deletion_updates_final_fallback_snapshot(tmp_path):
    store = hoisted_store()
    service = service_for(store, tmp_path)
    store.upsert([
        point("worker:default"),
        point("other:default"),
    ])

    assert store.delete_by_profile("other", "default") == 1

    assert [
        result.profile_key
        for result in service.match(
            query_embedding=[0.1],
            query="星河 数据分析",
            mode="fragment",
            top_k=3,
            runtime_config={},
        )
    ] == ["worker:default"]


def test_clear_updates_final_fallback_snapshot(tmp_path):
    store = hoisted_store()
    service = service_for(store, tmp_path)
    store.upsert([point()])

    store.clear()

    assert service.match(
        query_embedding=[0.1],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config={},
    ) == []
    assert store.literal_fallback_index.size == 0
