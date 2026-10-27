import asyncio

# TDD cases for the minimal HA refactor: degrade with feedback, do not turn failures into empty 200 responses.
from search_core.api.models import RetrievalRequest
from search_core.core.retrieval import HybridRetrievalService
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
