"""Maintain the final literal fallback while vector writes succeed."""

from __future__ import annotations

import logging
from typing import Any

from search_core.plugin_api.vector_point import VectorPoint as CoreVectorPoint
from search_core.providers.fallback import RegexFallbackIndex

from app.domain.services.vector_store_adapter import VectorStoreAdapter

logger = logging.getLogger(__name__)


def _profile_key(point: Any) -> str | None:
    payload = point.payload or {}
    key = payload.get("profile_key")
    if key:
        return str(key)
    worker_id = payload.get("worker_id") or payload.get("staff_id")
    profile_id = payload.get("profile_id")
    return f"{worker_id}:{profile_id}" if worker_id and profile_id else None


def _text_length(payload: dict) -> int:
    return len(str(
        payload.get("content")
        or payload.get("searchable_text")
        or payload.get("content_preview")
        or ""
    ))


class LiteralFallbackSnapshot:
    """In-process point view, deduplicated to the richest payload per profile."""

    def __init__(self) -> None:
        # Physical point IDs retain fragment identity so updates and deletes are exact.
        self._physical: dict[str, dict[str, Any]] = {}
        self._index = RegexFallbackIndex([])
        self.version = 0

    def clear(self) -> None:
        self._physical.clear()
        self._reindex()

    def upsert(self, points: list[Any]) -> None:
        changed = False
        for point in points:
            profile_key = _profile_key(point)
            if profile_key is None:
                continue
            payload = dict(point.payload or {})
            self._physical[str(point.id)] = {"profile_key": profile_key, "payload": payload}
            changed = True
        if changed:
            self._reindex()

    def upsert_raw(self, point_id: str, payload: dict) -> None:
        """Update legacy id/vector/metadata stores without copying vectors."""
        profile_key = payload.get("profile_key")
        if not profile_key:
            worker_id = payload.get("worker_id") or payload.get("staff_id")
            profile_id = payload.get("profile_id")
            profile_key = f"{worker_id}:{profile_id}" if worker_id and profile_id else None
        if not profile_key:
            return
        self._physical[str(point_id)] = {
            "profile_key": str(profile_key),
            "payload": dict(payload),
        }
        self._reindex()

    def remove_ids(self, point_ids: list[str]) -> None:
        removed = [str(point_id) for point_id in point_ids if str(point_id) in self._physical]
        for point_id in removed:
            self._physical.pop(point_id)
        if removed:
            self._reindex()

    def update_worker_payload(self, worker_id: str, payload_updates: dict) -> None:
        changed = False
        for entry in self._physical.values():
            if entry["payload"].get("worker_id") == worker_id:
                entry["payload"] = {**entry["payload"], **payload_updates}
                changed = True
        if changed:
            self._reindex()

    def remove_worker(self, worker_id: str) -> None:
        removed = [
            key for key, entry in self._physical.items()
            if entry["payload"].get("worker_id") == worker_id
        ]
        self.remove_ids(removed)

    def remove_profile(self, worker_id: str, profile_id: str) -> None:
        removed = [
            key for key, entry in self._physical.items()
            if entry["payload"].get("worker_id") == worker_id
            and entry["payload"].get("profile_id") == profile_id
        ]
        self.remove_ids(removed)

    def reload_from_store(self, store: VectorStoreAdapter) -> None:
        point_ids = store.get_vector_ids()
        physical: dict[str, dict[str, Any]] = {}
        for point_id in point_ids:
            point = store.get(point_id)
            profile_key = _profile_key(point) if point is not None else None
            if profile_key is None:
                continue
            physical[str(point_id)] = {
                "profile_key": profile_key,
                "payload": dict(point.payload or {}),
            }
        self._physical = physical
        self._reindex()

    def _reindex(self) -> None:
        selected = self._selected_profiles()
        self._index = RegexFallbackIndex([
            CoreVectorPoint(
                id=profile_key,
                vector=[],
                payload=dict(payload),
            )
            for profile_key, payload in selected.items()
        ])
        self.version += 1

    def _selected_profiles(self) -> dict[str, dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for entry in self._physical.values():
            profile_key = entry["profile_key"]
            payload = entry["payload"]
            previous = selected.get(profile_key)
            if previous is None or _text_length(payload) > _text_length(previous):
                selected[profile_key] = payload
        return selected

    @property
    def size(self) -> int:
        return len(self._physical)

    @property
    def profile_count(self) -> int:
        return len(self._selected_profiles())

    def keyword_search(self, query, limit, filters, allowed_ids):
        return self._index.keyword_search(query, limit, filters, allowed_ids)


class LiteralFallbackMaintainingVectorStore:
    """Proxy that maintains a local fallback only after durable writes succeed."""

    def __init__(self, backend: VectorStoreAdapter, fallback: LiteralFallbackSnapshot):
        self._backend = backend
        self.literal_fallback_index = fallback

    @property
    def backend(self) -> VectorStoreAdapter:
        return self._backend

    def index_dependency_readiness(self) -> dict[str, bool]:
        """Forward partial readiness while maintaining the local literal view."""
        result = dict(self._backend.index_dependency_readiness())
        # IndexState may acknowledge or rebuild a newly observed revision during
        # the same probe. Reload the process-local literal view after that
        # recovery without probing Qdrant twice.
        if result.pop("index_revision_recovered", False):
            self.literal_fallback_index.reload_from_store(self._backend)
        return result

    def check_ready(self) -> bool:
        # Some adapters use ``None`` for a successful probe; only an explicit
        # false value means unavailable.
        if self._backend.check_ready() is False:
            return False
        if getattr(self._backend, "index_revision_stale", False):
            if getattr(self._backend, "read_only", False):
                self.synchronize_read_view()
            else:
                self.rebuild_from_backend()
        return not bool(getattr(self._backend, "index_stale", False))

    def upsert(self, *args: Any, **kwargs: Any):
        result = self._backend.upsert(*args, **kwargs)
        supplied = args[0] if args else kwargs.get("points")
        if isinstance(supplied, str):
            metadata = args[2] if len(args) > 2 else kwargs.get("metadata", {})
            self.literal_fallback_index.upsert_raw(supplied, dict(metadata or {}))
        elif supplied:
            self.literal_fallback_index.upsert(supplied)
        return result

    def delete(self, ids):
        result = self._backend.delete(ids)
        normalized = [ids] if isinstance(ids, str) else list(ids)
        self.literal_fallback_index.remove_ids(normalized)
        return result

    def update_payload_by_worker(self, worker_id, payload_updates):
        result = self._backend.update_payload_by_worker(worker_id, payload_updates)
        self.literal_fallback_index.update_worker_payload(worker_id, payload_updates)
        return result

    def delete_by_worker(self, worker_id):
        result = self._backend.delete_by_worker(worker_id)
        self.literal_fallback_index.remove_worker(worker_id)
        return result

    def delete_by_profile(self, worker_id, profile_id):
        result = self._backend.delete_by_profile(worker_id, profile_id)
        self.literal_fallback_index.remove_profile(worker_id, profile_id)
        return result

    def clear(self):
        result = self._backend.clear()
        self.literal_fallback_index.clear()
        return result

    def synchronize_read_view(self, *args: Any, **kwargs: Any):
        """Refresh read-local projections without mutating another owner's index."""
        result = self._backend.synchronize_read_view(*args, **kwargs)
        if getattr(self._backend, "read_only", False):
            self.literal_fallback_index.reload_from_store(self._backend)
        return result

    def rebuild_from_backend(self, *args: Any, **kwargs: Any):
        if getattr(self._backend, "read_only", False):
            return self.synchronize_read_view(*args, **kwargs)
        result = self._backend.rebuild_from_backend(*args, **kwargs)
        self.literal_fallback_index.reload_from_store(self._backend)
        return result

    def sync_incremental(self, *args: Any, **kwargs: Any):
        if getattr(self._backend, "read_only", False):
            return self.synchronize_read_view(*args, **kwargs)
        result = self._backend.sync_incremental(*args, **kwargs)
        self.literal_fallback_index.reload_from_store(self._backend)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)


def wrap_vector_store_with_literal_fallback(
    store: VectorStoreAdapter,
) -> LiteralFallbackMaintainingVectorStore:
    """Wrap a composed vector store with an incrementally maintained snapshot."""
    fallback = LiteralFallbackSnapshot()
    try:
        fallback.reload_from_store(store)
    except Exception as error:
        logger.warning(
            "[LITERAL-FALLBACK] initial snapshot load failed; "
            "writes will maintain it: %s",
            error,
        )
    return LiteralFallbackMaintainingVectorStore(store, fallback)


__all__ = [
    "LiteralFallbackMaintainingVectorStore",
    "LiteralFallbackSnapshot",
    "wrap_vector_store_with_literal_fallback",
]
