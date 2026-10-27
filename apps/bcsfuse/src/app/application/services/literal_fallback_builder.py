"""Build an in-process literal snapshot for final degradation."""

from __future__ import annotations

import logging
from typing import Any

from search_core.plugin_api.vector_point import VectorPoint as CoreVectorPoint
from search_core.providers.fallback import RegexFallbackIndex

from app.domain.services.vector_store_adapter import VectorStoreAdapter

logger = logging.getLogger(__name__)


def _profile_key(point: Any) -> str | None:
    payload = point.payload or {}
    explicit = payload.get("profile_key")
    if explicit:
        return str(explicit)
    worker_id = payload.get("worker_id") or payload.get("staff_id")
    profile_id = payload.get("profile_id")
    if worker_id and profile_id:
        return f"{worker_id}:{profile_id}"
    return None


def _text_length(point: Any) -> int:
    payload = point.payload or {}
    return len(str(
        payload.get("content")
        or payload.get("searchable_text")
        or payload.get("content_preview")
        or ""
    ))


def build_literal_fallback_index(store: VectorStoreAdapter) -> RegexFallbackIndex | None:
    """Build one literal point per profile, or return no fallback on snapshot failure.

    A read failure must not produce a silently incomplete snapshot. Returning
    ``None`` excludes the final fallback instead of fabricating stale coverage.
    """
    try:
        point_ids = store.get_vector_ids()
    except Exception as error:
        logger.warning("[LITERAL-FALLBACK] snapshot id read failed: %s", error)
        return None

    selected: dict[str, Any] = {}
    try:
        for point_id in point_ids:
            point = store.get(point_id)
            if point is None:
                continue
            profile_key = _profile_key(point)
            if profile_key is None:
                continue
            previous = selected.get(profile_key)
            if previous is None or _text_length(point) > _text_length(previous):
                selected[profile_key] = point
    except Exception as error:
        logger.warning("[LITERAL-FALLBACK] snapshot point read failed: %s", error)
        return None

    points = [
        CoreVectorPoint(
            id=profile_key,
            vector=list(point.vector),
            payload=dict(point.payload or {}),
        )
        for profile_key, point in selected.items()
    ]
    if not points:
        logger.warning("[LITERAL-FALLBACK] no profile points available for snapshot")
        return None

    logger.info("[LITERAL-FALLBACK] built snapshot with %d profiles", len(points))
    return RegexFallbackIndex(points)


__all__ = ["build_literal_fallback_index"]
