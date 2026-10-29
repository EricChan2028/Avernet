"""Keyword retrieval must not pay an exact-ID scroll for semantic prose."""

from unittest.mock import Mock

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.keyword import QdrantKeywordIndex


def _index() -> QdrantKeywordIndex:
    index = QdrantKeywordIndex(exact_match_field="_keyword_worker_id")
    index.upsert(
        [
            VectorPoint(
                id="worker-only-id:default:full",
                vector=[],
                payload={
                    "worker_id": "worker-only-id",
                    "_keyword_worker_id": "worker-only-id",
                    "name": "支付查询助手",
                    "content": "支付 query helper",
                },
            )
        ]
    )
    return index


def test_exact_worker_id_uses_one_scroll() -> None:
    index = _index()
    original_scroll = index._client.scroll
    calls: list[object] = []

    def scrolling(*args, **kwargs):
        calls.append((args, kwargs))
        return original_scroll(*args, **kwargs)

    index._client.scroll = scrolling
    try:
        results = index.search("worker-only-id", 2, {})
        assert [hit.id for hit in results] == ["worker-only-id:default:full"]
        assert len(calls) == 1
    finally:
        index.close()


def test_natural_language_query_skips_exact_worker_id_scroll() -> None:
    index = _index()
    natural_scroll = Mock(side_effect=AssertionError("prose must not scroll"))
    index._client.scroll = natural_scroll
    try:
        results = index.search("支付 query helper", 2, {})
        assert [hit.id for hit in results] == ["worker-only-id:default:full"]
        natural_scroll.assert_not_called()
    finally:
        index.close()
