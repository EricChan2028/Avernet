"""Serializable metadata for one vector collection-pair lease."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexStateRecord:
    """Durable owner and revision metadata observed from a shared store."""

    collection_pair: str
    owner_id: str
    owner_expires_at: float
    index_version: int
    index_updated_at: float
    observed_at: float

    def __post_init__(self) -> None:
        if not self.collection_pair or not self.owner_id:
            raise ValueError("collection_pair and owner_id are required")
        if self.index_version < 0:
            raise ValueError("index_version must not be negative")
        if self.owner_expires_at <= 0:
            raise ValueError("owner_expires_at must be positive")
        if self.index_updated_at <= 0:
            raise ValueError("index_updated_at must be positive")
        if self.observed_at < self.index_updated_at:
            raise ValueError("observed_at must not precede index_updated_at")


__all__ = ["IndexStateRecord"]
