"""Plugin contract for durable multi-replica vector index state."""

from __future__ import annotations

from typing import Protocol

from app.domain.models.index_state import IndexStateRecord


class VectorIndexStateStore(Protocol):
    """Share one collection-pair owner and revision across application processes."""

    def claim_owner(
        self, collection_pair: str, owner_id: str, *, lease_seconds: float
    ) -> IndexStateRecord: ...

    def read_owner(self, collection_pair: str) -> IndexStateRecord: ...

    def publish_index_version(
        self, collection_pair: str, owner_id: str, index_version: int
    ) -> IndexStateRecord | None: ...

    def release_owner(self, collection_pair: str, owner_id: str) -> None: ...


__all__ = ["VectorIndexStateStore"]
