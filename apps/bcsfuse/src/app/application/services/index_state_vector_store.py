"""Coordinate a durable index lease around any mutable vector store."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.models.index_state import IndexStateRecord
from app.domain.services.vector_index_state_store import VectorIndexStateStore

logger = logging.getLogger(__name__)


class IndexStateUnavailable(RuntimeError):
    """The shared owner and revision state could not be read or published."""


class IndexStateWriteForbidden(RuntimeError):
    """The active lease belongs to another application replica."""


class IndexStateMaintainingVectorStore:
    """Expose owner/readiness state and enforce the durable single-owner fence."""

    def __init__(
        self,
        backend: Any,
        *,
        store: VectorIndexStateStore,
        owner_id: str,
        collection_pair: str,
        stale_after_seconds: float,
        lease_seconds: float,
    ):
        if not owner_id:
            raise ValueError("owner_id is required")
        if not collection_pair:
            raise ValueError("collection_pair is required")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if lease_seconds <= stale_after_seconds:
            raise ValueError("lease_seconds must exceed stale_after_seconds")
        self.backend = backend
        self._store = store
        self._owner_id = owner_id
        self._collection_pair = collection_pair
        self._stale_after_seconds = stale_after_seconds
        self._lease_seconds = lease_seconds
        self._local_version = 0
        self.index_owner: str | None = None
        self.index_version = 0
        self.index_lag_seconds = 0.0
        self.index_stale = True
        self.read_only = True
        try:
            self._refresh_from_record(self._store.claim_owner(
                collection_pair, owner_id, lease_seconds=lease_seconds
            ))
        except Exception as error:
            logger.warning("[INDEX-STATE] initial ownership probe failed: %s", error)
            self._mark_unavailable()

    @property
    def collection_pair(self) -> str:
        return self._collection_pair

    @property
    def index_revision_stale(self) -> bool:
        return self._local_version < self.index_version

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds

    @property
    def lease_heartbeat_seconds(self) -> float:
        """Renew often enough to survive one missed background renewal."""
        return self._lease_seconds / 3.0

    def renew_owner_lease(self) -> bool:
        """Extend the owner lease without issuing a backend or index write."""
        try:
            self._refresh()
        except Exception as error:
            self._mark_unavailable()
            raise IndexStateUnavailable(
                f"index owner lease renewal failed: {type(error).__name__}"
            ) from error
        return not self.read_only

    def check_ready(self) -> bool:
        try:
            backend_ready = bool(self.backend.check_ready())
            self._refresh()
            return backend_ready
        except Exception as error:
            logger.warning("[INDEX-STATE] readiness probe failed: %s", error)
            self._mark_unavailable()
            return False

    def upsert(self, *args: Any, **kwargs: Any):
        with self._write():
            result = self.backend.upsert(*args, **kwargs)
        return result

    def delete(self, *args: Any, **kwargs: Any):
        with self._write():
            result = self.backend.delete(*args, **kwargs)
        return result

    def update_payload_by_worker(self, *args: Any, **kwargs: Any):
        with self._write():
            result = self.backend.update_payload_by_worker(*args, **kwargs)
        return result

    def delete_by_worker(self, *args: Any, **kwargs: Any):
        with self._write():
            result = self.backend.delete_by_worker(*args, **kwargs)
        return result

    def delete_by_profile(self, *args: Any, **kwargs: Any):
        with self._write():
            result = self.backend.delete_by_profile(*args, **kwargs)
        return result

    def clear(self, *args: Any, **kwargs: Any):
        with self._write():
            result = self.backend.clear(*args, **kwargs)
        return result

    def rebuild_from_backend(self, *args: Any, **kwargs: Any):
        self._refresh_read_only()
        if self.read_only:
            raise IndexStateWriteForbidden(
                f"collection pair {self._collection_pair!r} "
                f"is owned by {self.index_owner!r}"
            )
        result = self.backend.rebuild_from_backend(*args, **kwargs)
        self._mark_synchronized()
        return result

    def synchronize_read_view(self, *args: Any, **kwargs: Any):
        """Acknowledge the shared version without rebuilding a shared index."""
        self._refresh_read_only()
        if self.read_only:
            self._local_version = self.index_version
            self._apply_staleness()
            return {
                "read_only": True,
                "synchronized_version": self.index_version,
            }
        return self.rebuild_from_backend(*args, **kwargs)

    def sync_incremental(self, *args: Any, **kwargs: Any):
        self._refresh_read_only()
        if self.read_only:
            raise IndexStateWriteForbidden(
                f"collection pair {self._collection_pair!r} "
                f"is owned by {self.index_owner!r}"
            )
        result = self.backend.sync_incremental(*args, **kwargs)
        self._mark_synchronized()
        return result

    def close(self) -> None:
        backend_close = getattr(self.backend, "close", None)
        if backend_close is not None:
            backend_close()
        if self.index_owner == self._owner_id:
            try:
                self._store.release_owner(self._collection_pair, self._owner_id)
            except Exception as error:
                logger.warning("[INDEX-STATE] lease release failed: %s", error)

    def _write(self):
        return _OwneredWrite(self)

    def _refresh(self) -> IndexStateRecord:
        record = self._store.claim_owner(
            self._collection_pair,
            self._owner_id,
            lease_seconds=self._lease_seconds,
        )
        self._refresh_from_record(record)
        return record

    def _refresh_read_only(self) -> IndexStateRecord:
        record = self._store.read_owner(self._collection_pair)
        self._refresh_from_record(record)
        return record

    def _mark_synchronized(self) -> None:
        self._refresh_read_only()
        self._local_version = self.index_version
        self._apply_staleness()

    def _refresh_from_record(self, record: IndexStateRecord) -> None:
        self.index_owner = record.owner_id
        self.index_version = record.index_version
        self.index_lag_seconds = max(
            0.0, record.observed_at - record.index_updated_at
        )
        self.read_only = record.owner_id != self._owner_id
        self._apply_staleness()

    def _apply_staleness(self) -> None:
        lag_stale = self.index_lag_seconds > self._stale_after_seconds
        local_stale = self.read_only and self._local_version < self.index_version
        self.index_stale = lag_stale or local_stale or self.index_owner is None

    def _record_success(self, record: IndexStateRecord) -> None:
        self._local_version = record.index_version
        self._refresh_from_record(record)

    def _mark_unavailable(self) -> None:
        self.read_only = True
        self.index_stale = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)


class _OwneredWrite:
    def __init__(self, owner: IndexStateMaintainingVectorStore):
        self._owner = owner

    def __enter__(self):
        try:
            record = self._owner._refresh()
        except Exception as error:
            self._owner._mark_unavailable()
            raise IndexStateUnavailable(
                f"index owner state unavailable: {type(error).__name__}"
            ) from error
        if self._owner.read_only:
            raise IndexStateWriteForbidden(
                f"collection pair {self._owner._collection_pair!r} "
                f"is owned by {self._owner.index_owner!r}"
            )
        self._record = record
        # A lease owner that failed over and later resumed must resume from the
        # shared revision, not from its pre-failure in-process counter.
        self._owner._local_version = max(
            record.index_version, self._owner._local_version
        )

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            return False
        next_version = self._owner._local_version + 1
        try:
            published = self._owner._store.publish_index_version(
                self._owner._collection_pair,
                self._owner._owner_id,
                next_version,
            )
        except Exception as error:
            self._owner._mark_unavailable()
            raise IndexStateUnavailable(
                f"index revision publish failed: {type(error).__name__}"
            ) from error
        if published is None:
            self._owner._mark_unavailable()
            raise IndexStateWriteForbidden(
                "index lease changed before the update could be published"
            )
        self._owner._record_success(published)
        return False


def wrap_vector_store_with_index_state(
    vector_store: Any,
    *,
    store: VectorIndexStateStore,
    owner_id: str,
    collection_pair: str,
    stale_after_seconds: float,
    lease_seconds: float,
) -> IndexStateMaintainingVectorStore:
    """Return a composed store fenced by a shared durable lease."""
    return IndexStateMaintainingVectorStore(
        vector_store,
        store=store,
        owner_id=owner_id,
        collection_pair=collection_pair,
        stale_after_seconds=stale_after_seconds,
        lease_seconds=lease_seconds,
    )


__all__ = [
    "IndexStateMaintainingVectorStore",
    "IndexStateUnavailable",
    "IndexStateWriteForbidden",
    "wrap_vector_store_with_index_state",
]
