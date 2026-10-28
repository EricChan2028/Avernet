"""Durable ownership and staleness behavior around the composed vector store."""

import pytest
from fastapi.testclient import TestClient

from app.application.services.index_state_vector_store import (
    IndexStateMaintainingVectorStore,
    IndexStateUnavailable,
    IndexStateWriteForbidden,
    wrap_vector_store_with_index_state,
)
from app.infra.public.stores.sqlite_index_state_store import SQLiteIndexStateStore


class RecordingVectorStore:
    def __init__(self, *, ready=True, fail=False):
        self.ready = ready
        self.fail = fail
        self.points = []
        self.deleted = []
        self.rebuilds = 0
        self.syncs = 0
        self.closed = False

    def _maybe_fail(self):
        if self.fail:
            raise RuntimeError("index operation failed")

    def upsert(self, points, **kwargs):
        self._maybe_fail()
        self.points.extend(points)
        return None

    def delete(self, ids, **kwargs):
        self._maybe_fail()
        self.deleted.extend(ids)
        return len(ids)

    def update_payload_by_worker(self, worker_id, updates):
        self._maybe_fail()
        return 1

    def delete_by_worker(self, worker_id):
        self._maybe_fail()
        return 1

    def delete_by_profile(self, worker_id, profile_id):
        self._maybe_fail()
        return 1

    def clear(self):
        self._maybe_fail()
        self.points.clear()

    def rebuild_from_backend(self):
        self._maybe_fail()
        self.rebuilds += 1
        return {"points": len(self.points)}

    def sync_incremental(self):
        self._maybe_fail()
        self.syncs += 1
        return {"upserted": 0, "deleted": 0}

    def check_ready(self):
        return self.ready

    def close(self):
        self.closed = True


def wrapped(tmp_path, owner_id, backend=None, **kwargs):
    store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    return wrap_vector_store_with_index_state(
        backend or RecordingVectorStore(),
        store=store,
        owner_id=owner_id,
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=60,
        lease_seconds=120,
        **kwargs,
    )


def test_first_replica_acquires_ownership_and_publishes_versions(tmp_path):
    owner = wrapped(tmp_path, "pod-a")

    assert owner.index_owner == "pod-a"
    assert owner.read_only is False
    owner.upsert(["point"])
    assert owner.index_version == 1

    owner.close()
    assert owner.backend.closed is True


def test_second_replica_is_read_only_and_cannot_reach_storage(tmp_path):
    first = wrapped(tmp_path, "pod-a")
    second = wrapped(tmp_path, "pod-b")

    assert first.index_owner == "pod-a"
    assert second.index_owner == "pod-a"
    assert second.read_only is True

    with pytest.raises(IndexStateWriteForbidden):
        second.upsert(["point"])

    assert second.backend.points == []
    first.close()
    second.close()


def test_release_enables_second_replica_takeover_after_owner_leases(tmp_path):
    first = wrapped(tmp_path, "pod-a")
    first.close()
    second = wrapped(tmp_path, "pod-b")

    assert second.index_owner == "pod-b"
    assert second.read_only is False
    second.close()


def test_owner_version_change_marks_reader_stale_until_rebuild(tmp_path):
    first = wrapped(tmp_path, "pod-a")
    second = wrapped(tmp_path, "pod-b")
    first.upsert(["point"])
    second.check_ready()

    assert second.index_version == 1
    assert second.index_lag_seconds >= 0
    assert second.index_stale is True

    assert second.synchronize_read_view() == {
        "read_only": True,
        "synchronized_version": 1,
    }
    assert second.backend.rebuilds == 0
    assert second.index_stale is False

    first.close()
    second.close()


def test_backend_failure_does_not_publish_a_new_index_version(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    owner.backend.fail = True

    with pytest.raises(RuntimeError, match="index operation failed"):
        owner.upsert(["point"])
    owner.backend.fail = False
    owner.check_ready()
    assert owner.index_version == 0
    owner.close()


def test_state_store_failure_block_writes_and_marks_index_stale(tmp_path):
    backend = RecordingVectorStore()
    state_store = SQLiteIndexStateStore(str(tmp_path / "state.sqlite"))
    state_store.claim_owner = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("state unavailable"))
    owner = IndexStateMaintainingVectorStore(
        backend,
        store=state_store,
        owner_id="pod-a",
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=60,
        lease_seconds=120,
    )

    with pytest.raises(IndexStateUnavailable):
        owner.upsert(["point"])

    assert backend.points == []
    assert owner.read_only is True
    assert owner.index_stale is True


def test_other_direct_write_methods_use_the_same_ownership_boundary(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    reader = wrapped(tmp_path, "pod-b")

    methods = [
        ("upsert", ("point",)),
        ("delete", ("point",)),
        ("update_payload_by_worker", ("worker", {"runtime_state": "offline"})),
        ("delete_by_worker", ("worker",)),
        ("delete_by_profile", ("worker", "default")),
        ("clear", ()),
    ]
    for name, args in methods:
        with pytest.raises(IndexStateWriteForbidden):
            getattr(reader, name)(*args)

    assert reader.backend.points == []
    assert reader.backend.deleted == []
    owner.close()
    reader.close()


def test_composed_readiness_and_lifecycle_view_owner_and_version(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.provider_registry import ProviderRegistry

    class StartupProvider:
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    owner = wrapped(tmp_path, "pod-a")
    composed = wrap_vector_store_with_literal_fallback(owner)
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", composed)
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["index_owner"] == "pod-a"
        assert body["index_version"] == 0
        assert body["index_stale"] is False
        assert body["read_only"] is False


class SharedPoint:
    def __init__(self, point_id: str, payload: dict):
        self.id = point_id
        self.payload = payload


class SharedViewVectorStore(RecordingVectorStore):
    def __init__(self):
        super().__init__()
        self.view_loads = 0

    def get_vector_ids(self):
        self.view_loads += 1
        return [point.id for point in self.points]

    def get(self, point_id):
        for point in self.points:
            if point.id == point_id:
                return point
        return None


def _make_point(profile_key="worker:default"):
    return SharedPoint(
        f"{profile_key}:full",
        {
            "profile_key": profile_key,
            "worker_id": profile_key.rsplit(":", 1)[0],
            "profile_id": profile_key.rsplit(":", 1)[1],
            "content": "支付 账单 助手",
            "runtime_state": "online",
            "availability": "public",
        },
    )


def test_read_only_rebuild_is_rejected_before_backend_rebuild(tmp_path):
    backend = SharedViewVectorStore()
    owner = wrapped(tmp_path, "pod-a", backend=backend)
    reader = wrapped(tmp_path, "pod-b", backend=backend)

    with pytest.raises(IndexStateWriteForbidden, match="owned by 'pod-a'"):
        reader.rebuild_from_backend()

    assert backend.rebuilds == 0
    assert reader.read_only is True
    owner.close()
    reader.close()


def test_read_only_incremental_sync_is_rejected_before_backend_sync(tmp_path):
    backend = SharedViewVectorStore()
    owner = wrapped(tmp_path, "pod-a", backend=backend)
    reader = wrapped(tmp_path, "pod-b", backend=backend)

    with pytest.raises(IndexStateWriteForbidden, match="owned by 'pod-a'"):
        reader.sync_incremental()

    assert backend.syncs == 0
    owner.close()
    reader.close()


def test_composed_reader_rebuild_synchronizes_view_without_mutating_qdrant(
    tmp_path,
):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-a", backend=backend)
    )
    reader_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-b", backend=backend)
    )

    owner_composed.upsert([_make_point()])
    assert reader_composed.check_ready() is True
    assert reader_composed.index_stale is False
    assert reader_composed.index_revision_stale is False
    rebuilds_before = backend.rebuilds

    result = reader_composed.rebuild_from_backend()

    assert reader_composed.index_stale is False
    assert reader_composed.read_only is True
    assert backend.rebuilds == rebuilds_before
    assert result == {
        "read_only": True,
        "synchronized_version": 1,
    }
    hits = reader_composed.literal_fallback_index.keyword_search(
        "支付 账单",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]

    owner_composed.close()
    reader_composed.close()
    assert backend.closed is True


def test_composed_owner_rebuild_still_executes_backend_rebuild(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-a", backend=backend)
    )

    owner_composed.rebuild_from_backend()

    assert backend.rebuilds == 1
    assert owner_composed.read_only is False
    owner_composed.close()

def test_composed_reader_readiness_recovers_revision_without_manual_restart(
    tmp_path,
):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-a", backend=backend)
    )
    reader_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-b", backend=backend)
    )

    owner_composed.upsert([_make_point()])
    view_loads_start = backend.view_loads
    rebuilds_start = backend.rebuilds

    assert reader_composed.check_ready() is True

    assert reader_composed.index_stale is False
    assert reader_composed.index_revision_stale is False
    assert backend.rebuilds == rebuilds_start
    assert backend.view_loads == view_loads_start + 1

    assert reader_composed.check_ready() is True
    assert backend.view_loads == view_loads_start + 1

    hits = reader_composed.literal_fallback_index.keyword_search(
        "支付 账单",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]

    owner_composed.close()
    reader_composed.close()


def test_real_ready_endpoint_recovers_stale_reader_without_index_mutation(
    tmp_path,
):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.provider_registry import ProviderRegistry

    class StartupProvider:
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-owner", backend=backend)
    )
    reader_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-reader", backend=backend)
    )
    owner_composed.upsert([_make_point()])

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", reader_composed)
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    with TestClient(app) as client:
        response = client.get("/ready")
        body = response.json()

    assert response.status_code == 200
    assert body["ready"] is True
    assert body["read_only"] is True
    assert body["index_stale"] is False
    assert body["index_owner"] == "pod-owner"
    assert backend.rebuilds == 0
    hits = reader_composed.literal_fallback_index.keyword_search(
        "支付 账单",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]

    owner_composed.close()
    reader_composed.close()
