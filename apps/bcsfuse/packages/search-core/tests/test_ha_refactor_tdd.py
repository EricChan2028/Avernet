import asyncio
import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# TDD cases for the minimal HA refactor: fail visibly, never turn failures into
# empty 200 responses, enforce request boundaries, and keep only one index owner.
from search_core.api.models import RetrievalRequest, VerifiedIdentity
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.vector_point import VectorPoint
from search_core.plugin_api.vector_search_hit import VectorSearchHit


class Embedding:
    def embed(self, text):
        return [1.0, 0.0]


class FailingEmbedding:
    def embed(self, text):
        raise RuntimeError("semantic service unavailable")


class KeywordOnlyIndex:
    def keyword_search(self, query, limit, filters, allowed_ids):
        return [
            VectorSearchHit(
                id="keyword-agent",
                score=0.4,
                payload={"name": "Keyword Agent", "content": "keyword match"},
            )
        ]

    def vector_search(self, vector, limit, filters, allowed_ids):
        return []


class HybridIndex:
    def keyword_search(self, query, limit, filters, allowed_ids):
        return [
            VectorSearchHit(
                id="keyword-agent",
                score=0.4,
                payload={"name": "Keyword Agent", "content": "keyword match"},
            )
        ]

    def vector_search(self, vector, limit, filters, allowed_ids):
        return [
            VectorSearchHit(
                id="semantic-agent",
                score=0.8,
                payload={"name": "Semantic Agent", "content": "semantic match"},
            )
        ]


class FailingReranker:
    def rerank(self, query, candidates, top_k):
        raise RuntimeError("reranker service unavailable")


def test_semantic_failure_degrades_to_keyword_only_result():
    service = HybridRetrievalService(
        index=KeywordOnlyIndex(), embedding=FailingEmbedding()
    )
    request = RetrievalRequest("agent", 1, {}, None)
    result = asyncio.run(service.retrieve(request))

    assert [item.item_id for item in result.items] == ["keyword-agent"]
    assert result.status == "degraded"
    assert result.reasons == ("semantic_unavailable",)


def test_reranker_failure_preserves_previous_ranking_and_reports_degradation():
    service = HybridRetrievalService(
        index=HybridIndex(), embedding=Embedding(), reranker=FailingReranker()
    )
    request = RetrievalRequest("agent", 2, {}, None)
    result = asyncio.run(service.retrieve(request))

    assert len(result.items) == 2
    assert result.status == "degraded"
    assert result.reasons == ("reranker_unavailable",)
    assert {item.item_id for item in result.items} == {
        "keyword-agent",
        "semantic-agent",
    }


def test_collection_pair_has_one_owner_and_release_enables_failsafe_takeover():
    from search_core.core.ha import (
        IndexOwnershipConflict,
        InMemoryOwnershipStore,
        OwnershipCoordinator,
    )

    coordinator = OwnershipCoordinator(store=InMemoryOwnershipStore())
    lease = coordinator.acquire(collection_pair="agent_search", owner_id="pod-a")

    with pytest.raises(IndexOwnershipConflict):
        coordinator.acquire(collection_pair="agent_search", owner_id="pod-b")

    coordinator.release(lease)
    takeover = coordinator.acquire(collection_pair="agent_search", owner_id="pod-b")
    assert takeover.owner_id == "pod-b"


def test_index_health_metadata_can_be_shared_and_evaluated_by_any_replica():
    from search_core.core.ha import IndexHealth

    health = IndexHealth(
        collection_name="agent_search",
        owner_id="pod-a",
        index_version=7,
        updated_at=100.0,
        stale_after_seconds=30,
        read_only=True,
    )
    replica_health = IndexHealth.from_mapping(health.as_mapping())

    assert replica_health == health
    assert health.lag_seconds(now=99.0) == 0
    assert health.lag_seconds(now=130.0) == 30
    assert health.is_stale(now=130.0) is False
    assert health.is_stale(now=130.001) is True
    assert replica_health.is_stale(now=130.001) is True


def test_reader_replica_rejects_index_writes_before_reaching_storage():
    from search_core.providers.read_only import ReadOnlyRetrievalIndex

    class RecordingIndex:
        def __init__(self):
            self.calls = []

        def upsert(self, points):
            self.calls.append("upsert")

        def delete(self, ids):
            self.calls.append("delete")

    backend = RecordingIndex()
    readonly = ReadOnlyRetrievalIndex(backend)

    with pytest.raises(RuntimeError, match="read-only"):
        readonly.upsert([VectorPoint(id="x", vector=[1.0])])
    with pytest.raises(RuntimeError, match="read-only"):
        readonly.delete(["x"])

    assert backend.calls == []


def test_regex_static_fallback_honors_filters_authority_and_literal_terms():
    from search_core.providers.fallback import RegexFallbackIndex

    fallback = RegexFallbackIndex(
        [
            VectorPoint(
                id="payment-agent",
                vector=[],
                payload={
                    "name": "Payment Agent",
                    "content": "handle payment invoices",
                    "availability": "public",
                },
            )
        ]
    )

    hits = fallback.keyword_search(
        "please handle PAYMENT invoice",
        2,
        {"availability": ("public",)},
        frozenset({"payment-agent"}),
    )
    assert [hit.id for hit in hits] == ["payment-agent"]
    assert fallback.keyword_search(
        "please handle PAYMENT invoice", 2, {"availability": ("private",)}, None
    ) == []
    assert fallback.keyword_search(
        "please handle PAYMENT invoice", 2, {}, frozenset()
    ) == []
    assert fallback.keyword_search(".*", 2, {}, None) == []


def test_keyword_and_semantic_outage_degrades_to_regex_static_fallback():
    from search_core.providers.fallback import RegexFallbackIndex

    class FailingIndex:
        def keyword_search(self, *args):
            raise RuntimeError("keyword index unavailable")

        def vector_search(self, *args):
            raise RuntimeError("dense index unavailable")

    fallback = RegexFallbackIndex(
        [
            VectorPoint(
                id="payment-agent",
                vector=[],
                payload={
                    "name": "Payment Agent",
                    "content": "handle payment invoices",
                    "availability": "public",
                },
            )
        ]
    )
    service = HybridRetrievalService(
        index=FailingIndex(),
        embedding=FailingEmbedding(),
        fallback_index=fallback,
    )

    result = asyncio.run(
        service.retrieve(RetrievalRequest("please handle payment invoice", 1, {}, None))
    )

    assert [item.item_id for item in result.items] == ["payment-agent"]
    assert result.status == "degraded"
    assert set(result.reasons) == {
        "semantic_unavailable",
        "keyword_unavailable",
        "regex_fallback",
    }


async def authenticated(request: Request):
    return VerifiedIdentity("authenticated-caller", None)


@pytest.mark.parametrize(
    "filters",
    [
        {f"tag{i}": ["value"] for i in range(17)},
        {"domains": [f"value{i}" for i in range(33)]},
    ],
)
def test_filter_cardinality_is_rejected_before_application_execution(filters):
    class Application:
        async def execute(self, command, identity):
            raise AssertionError("oversized filter reached execute()")

    app = FastAPI()
    from search_core.adapters.http import create_search_router

    app.include_router(create_search_router(Application(), authenticated))
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/search", json={"query": "x", "filters": filters}
        )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] is not None


def test_oversized_request_body_is_rejected_before_json_parsing():
    class Application:
        async def execute(self, command, identity):
            raise AssertionError("oversized request body reached execute()")

    app = FastAPI()
    from search_core.adapters.http import create_search_router

    app.include_router(create_search_router(Application(), authenticated))
    payload = json.dumps(
        {"query": "x", "extensions": {"padding": "x" * 65_536}}
    ).encode()
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/search",
            content=payload,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["detail"] is not None
