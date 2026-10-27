"""Final literal fallback contract for TC worker vector matching."""

from unittest.mock import Mock

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.fallback import RegexFallbackIndex

from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)


def fallback_point(profile_key="worker:default", content="星河 数据分析助手"):
    return VectorPoint(
        id=profile_key,
        vector=[0.0],
        payload={
            "id": profile_key,
            "profile_key": profile_key,
            "worker_id": "worker",
            "profile_id": "default",
            "runtime_state": "online",
            "availability": "public",
            "content": content,
        },
    )


def service_for(vector_store, fallback_index, tmp_path):
    return WorkerVectorMatchService(
        vector_store=vector_store,
        metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
        fallback_index=fallback_index,
        fragment_config=FragmentRetrievalConfig(enable_fragment_embedding=True),
    )


def test_literal_fallback_returns_stable_result_when_dense_and_keyword_are_down(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert results[0].score > 0.0
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is True


def test_keyword_availability_prevents_literal_fallback(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.return_value = []
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
    )

    assert results == []
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "vector_weighted"
    assert diagnostics["literal_fallback_used"] is False


def test_empty_vector_index_is_not_an_outage_and_has_no_literal_fallback(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 0
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
    )

    assert results == []
    assert runtime_config["_retrieval"]["literal_fallback_used"] is False


def test_literal_fallback_still_respects_exclusions(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store,
        RegexFallbackIndex(
            [
                fallback_point("worker:default", "星河 数据分析助手"),
                fallback_point("second:default", "星河 数据分析助手"),
            ]
        ),
        tmp_path,
    )

    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        excluded_profile_keys=["worker:default"],
    )

    assert [result.profile_key for result in results] == ["second:default"]


def test_literal_fallback_builder_creates_deduplicated_profile_snapshot():
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )
    from app.domain.models.vector_point import VectorPoint as DomainVectorPoint

    class SnapshotStore:
        def __init__(self, points):
            self._points = {point.id: point for point in points}

        def get_vector_ids(self):
            return list(self._points)

        def get(self, point_id):
            return self._points.get(point_id)

    store = SnapshotStore([
        DomainVectorPoint(
            id="worker:default:full",
            vector=[1.0],
            payload={
                "profile_key": "worker:default",
                "content": "first short",
                "runtime_state": "online",
                "availability": "public",
            },
        ),
        DomainVectorPoint(
            id="worker:default:skills:1",
            vector=[0.5],
            payload={
                "profile_key": "worker:default",
                "content": "星河 数据分析 second richer content",
                "runtime_state": "online",
                "availability": "public",
            },
        ),
        DomainVectorPoint(
            id="offline:default:full",
            vector=[0.5],
            payload={
                "profile_key": "offline:default",
                "content": "星河 数据分析",
                "runtime_state": "offline",
                "availability": "public",
            },
        ),
    ])

    fallback = build_literal_fallback_index(store)

    assert fallback is not None
    hits = fallback.keyword_search(
        "星河 数据分析", 10,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]
    assert "richer" in hits[0].payload["content"]


def test_literal_fallback_builder_returns_none_on_snapshot_read_failure():
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )

    class FailingStore:
        def get_vector_ids(self):
            raise RuntimeError("durable snapshot unavailable")

        def get(self, point_id):
            raise AssertionError("must not read individual points after listing fails")

    assert build_literal_fallback_index(FailingStore()) is None


def test_composition_root_injects_literal_fallback_snapshot(monkeypatch, tmp_path):
    from app.adapters.http.dependencies import fusion_dependencies
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class SnapshotStore:
        def get_vector_ids(self):
            return ["worker:default"]

        def get(self, point_id):
            return DomainPoint(
                id=point_id,
                vector=[1.0],
                payload={
                    "profile_key": "worker:default",
                    "content": "星河 数据分析",
                    "runtime_state": "online",
                    "availability": "public",
                },
            )

        def size(self):
            return 1

    from app.domain.models.vector_point import VectorPoint as DomainPoint
    context = ApplicationContext(
        mode="test",
        startup_profile="acceptance",
        registry=ProviderRegistry(),
    )
    context.registry.register("vector_store", SnapshotStore())
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "qdrant"))
    monkeypatch.delenv("ENABLE_REGISTRY_AWARE_FILTERING", raising=False)

    try:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(context)
        service = fusion_dependencies._get_vector_match_service()
        assert service is not None
        expected = build_literal_fallback_index(context.registry.get("vector_store"))
        assert service._fallback_index is not None
        assert service._fallback_index.keyword_search(
            "星河 数据分析", 1,
            {"runtime_state": ("online",), "availability": ("public",)},
            None,
        ) == expected.keyword_search(
            "星河 数据分析", 1,
            {"runtime_state": ("online",), "availability": ("public",)},
            None,
        )
    finally:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(None)
