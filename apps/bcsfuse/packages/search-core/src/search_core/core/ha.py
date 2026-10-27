"""Transport-agnostic ownership and health primitives for multi-replica indexes."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol


class IndexOwnershipConflict(RuntimeError):
    """A collection pair already has another active owner."""


@dataclass(frozen=True)
class IndexOwnerLease:
    collection_pair: str
    owner_id: str


class IndexOwnershipStore(Protocol):
    """A process-independent backing store must implement these operations."""

    def owner_of(self, collection_pair: str) -> str | None: ...

    def set_owner(self, collection_pair: str, owner_id: str) -> None: ...

    def clear_owner(self, collection_pair: str, owner_id: str) -> None: ...


class InMemoryOwnershipStore:
    """Single-process store used by tests and deterministic local compositions."""

    def __init__(self):
        self._owners: dict[str, str] = {}
        self._lock = RLock()

    def owner_of(self, collection_pair: str) -> str | None:
        with self._lock:
            return self._owners.get(collection_pair)

    def set_owner(self, collection_pair: str, owner_id: str) -> None:
        with self._lock:
            self._owners[collection_pair] = owner_id

    def clear_owner(self, collection_pair: str, owner_id: str) -> None:
        with self._lock:
            if self._owners.get(collection_pair) == owner_id:
                del self._owners[collection_pair]


class OwnershipCoordinator:
    """Enforce one writer per collection pair through the injected store."""

    def __init__(self, *, store: IndexOwnershipStore):
        self._store = store

    def acquire(self, *, collection_pair: str, owner_id: str) -> IndexOwnerLease:
        if not collection_pair or not owner_id:
            raise ValueError("collection_pair and owner_id are required")
        current = self._store.owner_of(collection_pair)
        if current is not None and current != owner_id:
            raise IndexOwnershipConflict(
                f"collection pair {collection_pair!r} is owned by {current!r}"
            )
        self._store.set_owner(collection_pair, owner_id)
        return IndexOwnerLease(collection_pair, owner_id)

    def release(self, lease: IndexOwnerLease) -> None:
        if not isinstance(lease, IndexOwnerLease):
            raise TypeError("lease must be an IndexOwnerLease")
        self._store.clear_owner(lease.collection_pair, lease.owner_id)


@dataclass(frozen=True)
class IndexHealth:
    """Serializable revision metadata shared by index writer and readers."""

    collection_name: str
    owner_id: str
    index_version: int
    updated_at: float
    stale_after_seconds: float
    read_only: bool

    def __post_init__(self):
        if not self.collection_name or not self.owner_id:
            raise ValueError("collection and owner identity are required")
        if self.index_version < 0:
            raise ValueError("index_version must not be negative")
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")

    @classmethod
    def from_mapping(cls, mapping: dict[str, object]) -> "IndexHealth":
        required = (
            "collection_name",
            "owner_id",
            "index_version",
            "updated_at",
            "stale_after_seconds",
            "read_only",
        )
        missing = [key for key in required if key not in mapping]
        if missing:
            raise ValueError(f"index health metadata missing: {', '.join(missing)}")
        return cls(
            collection_name=str(mapping["collection_name"]),
            owner_id=str(mapping["owner_id"]),
            index_version=int(mapping["index_version"]),
            updated_at=float(mapping["updated_at"]),
            stale_after_seconds=float(mapping["stale_after_seconds"]),
            read_only=bool(mapping["read_only"]),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "collection_name": self.collection_name,
            "owner_id": self.owner_id,
            "index_version": self.index_version,
            "updated_at": self.updated_at,
            "stale_after_seconds": self.stale_after_seconds,
            "read_only": self.read_only,
        }

    def lag_seconds(self, *, now: float) -> float:
        return max(0.0, now - self.updated_at)

    def is_stale(self, *, now: float) -> bool:
        return self.lag_seconds(now=now) > self.stale_after_seconds
