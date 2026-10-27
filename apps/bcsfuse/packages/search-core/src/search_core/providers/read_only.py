"""A read-only proxy that prevents reader replicas from mutating an index."""

from __future__ import annotations

from search_core.plugin_api.vector_point import VectorPoint


class ReadOnlyRetrievalIndex:
    """Search through the backing index; reject every mutation before storage."""

    def __init__(self, backend: object):
        self._backend = backend

    def upsert(self, points: list[VectorPoint]) -> None:
        raise RuntimeError(
            "read-only retrieval index rejected upsert; route writes to the owner"
        )

    def delete(self, ids: list[str]) -> None:
        raise RuntimeError(
            "read-only retrieval index rejected delete; route writes to the owner"
        )

    def vector_search(self, vector, limit, filters, allowed_ids):
        return self._backend.vector_search(vector, limit, filters, allowed_ids)

    def keyword_search(self, query, limit, filters, allowed_ids):
        return self._backend.keyword_search(query, limit, filters, allowed_ids)
