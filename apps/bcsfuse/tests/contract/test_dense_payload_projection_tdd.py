from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.application.services.worker_vector_match_types import (
    FragmentProfileCandidate,
)
from app.domain.models.metadata_record import MetadataRecord
from app.domain.models.profile_fragment import FragmentMatch
from app.domain.models.vector_search_hit import VectorSearchHit
from search_core.providers.qdrant import QdrantLocalVectorStore


class RecordingMetadataStore:
    def get(self, profile_key: str) -> MetadataRecord | None:
        return None

    def filter(self, filters: dict[str, Any] | None):
        if not filters:
            return []
        return [
            MetadataRecord(
                profile_key="writer:default",
                staff_id="writer",
                profile_id="default",
                profile_type="default",
                source_root="test",
            )
        ]


class RecordingVectorStore:
    def __init__(self, hit: VectorSearchHit) -> None:
        self.hit = hit
        self.search_calls: list[dict[str, Any]] = []
        self.size_calls = 0

    def size(self) -> int:
        self.size_calls += 1
        return 1

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
        *,
        payload_fields: list[str] | None = None,
    ) -> list[VectorSearchHit]:
        self.search_calls.append(
            {
                "vector": vector,
                "top_k": top_k,
                "filters": filters,
                "payload_fields": payload_fields,
            }
        )
        return [self.hit]

    def text_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorSearchHit]:
        return []


@dataclass
class RecordingProfileContentStore:
    calls: list[tuple[str, str]]

    def get(self, worker_id: str, profile_id: str):
        self.calls.append((worker_id, profile_id))
        return {"contents": {"profile": "full profile content"}, "display_name": "Writer"}


def _hit() -> VectorSearchHit:
    return VectorSearchHit(
        id="writer:default:profile:0",
        score=0.9,
        payload={
            "profile_key": "writer:default",
            "worker_id": "writer",
            "profile_id": "default",
            "fragment_type": "profile",
            "active_skills": ["writing"],
            "short_profile": "Writer",
            "display_name": "Writer",
            "content_preview": "short preview",
            "content": "full payload content",
        },
    )


def _service(store: RecordingVectorStore):
    return WorkerVectorMatchService(
        vector_store=store,
        metadata_store=RecordingMetadataStore(),
        fragment_config=FragmentRetrievalConfig(
            enable_fragment_embedding=True,
            reranker_model=None,
            expand_factor=1,
            aggregation_strategy="weighted_sum",
        ),
    )


def test_fragment_dense_search_requests_a_bounded_payload_projection() -> None:
    store = RecordingVectorStore(_hit())
    service = _service(store)

    results = service.match(
        query_embedding=[0.1, 0.2],
        top_k=1,
        query="writer",
        mode="fragment",
        filters={"runtime_state": ["online"], "availability": ["public"]},
        vector_min_score=0.01,
        runtime_config={"reranker_model": None, "expand_factor": 1},
    )

    assert len(results) == 1
    fields = store.search_calls[0]["payload_fields"]
    assert fields is not None
    assert set(fields) == {
        "profile_key",
        "worker_id",
        "profile_id",
        "profile_type",
        "fragment_type",
        "domains",
        "active_skills",
        "short_profile",
        "display_name",
        "content_preview",
        "_external_id",
    }
    assert "content" not in fields
    assert results[0].metadata.short_profile == "Writer"
    assert results[0].fragment_matches[0].content_preview == "short preview"


def test_rerank_profiles_reload_full_content_even_when_preview_is_available() -> None:
    store = RecordingVectorStore(_hit())
    service = _service(store)
    content_store = RecordingProfileContentStore([])
    service.set_profile_content_store(content_store)
    candidate = FragmentProfileCandidate(
        profile_key="writer:default",
        aggregated_score=0.9,
        fragments=[
            FragmentMatch(
                fragment_type="profile",
                fragment_id="writer:default:profile:0",
                score=0.9,
                weighted_score=0.9,
                content_preview="short preview",
                content="",
            )
        ],
        metadata={
            "worker_id": "writer",
            "profile_id": "default",
            "short_profile": "Writer",
        },
    )

    service._load_rerank_profiles([candidate])

    assert content_store.calls == [("writer", "default")]
    assert candidate.metadata["profile"] == "full profile content"
    assert candidate.metadata["display_name"] == "Writer"


def test_hot_search_paths_do_not_probe_remote_size() -> None:
    store = RecordingVectorStore(_hit())
    service = _service(store)

    service.match(
        query_embedding=[0.1, 0.2],
        top_k=1,
        query="writer",
        mode="fragment",
        filters={"runtime_state": ["online"], "availability": ["public"]},
        vector_min_score=0.01,
        runtime_config={"reranker_model": None, "expand_factor": 1},
    )
    service.match(
        query_embedding=[0.1, 0.2],
        top_k=1,
        query="writer",
        mode="legacy",
        filters={"runtime_state": ["online"], "availability": ["public"]},
        vector_min_score=0.01,
    )

    assert store.search_calls
    assert store.size_calls == 0


def test_qdrant_search_payload_fields_are_sent_as_with_payload_projection() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def query_points(self, **kwargs: Any):
            self.calls.append(kwargs)
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="14d7cba3-e036-4b1d-8de4-03e18fddc3de",
                        score=0.8,
                        payload={"profile_key": "writer:default"},
                    )
                ]
            )

    store = QdrantLocalVectorStore(collection_name="test", path="test", dimension=2)
    store._client = FakeClient()
    store._collection_initialized = True

    result = store.search(
        [0.1, 0.2],
        top_k=1,
        payload_fields=["profile_key", "content_preview"],
    )

    assert result[0].payload == {"profile_key": "writer:default"}
    assert store._client.calls[0]["with_payload"] == [
        "profile_key",
        "content_preview",
    ]


def test_fragment_search_budget_keeps_a_32_fragment_stable_prefix() -> None:
    store = RecordingVectorStore(_hit())
    service = _service(store)

    assert (
        service._calculate_search_k(
            top_k=3,
            has_filters=True,
            has_excluded=False,
            expand_factor=3,
        )
        == 32
    )
