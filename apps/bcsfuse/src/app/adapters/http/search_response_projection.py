"""V1 search response projection kept independent of HTTP route policy."""

from __future__ import annotations

from typing import Any

from app.application.agent_search_service import AgentSearchOutcome
from app.bootstrap.oss_business_routes import SearchRequestOSS


def build_v1_search_response(
    request: SearchRequestOSS,
    outcome: AgentSearchOutcome,
    *,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Project one application search outcome into the stable v1 schema."""

    return {
        "query": request.query,
        "top_k": request.top_k,
        "mode": request.mode,
        "results_count": len(outcome.results),
        "results": [_project_result(result) for result in outcome.results],
        "timing_ms": round(elapsed_ms, 2),
        "config_applied": outcome.config_applied,
    }


def _project_result(result: Any) -> dict[str, Any]:
    parts = result.profile_key.split(":")
    metadata = getattr(result, "metadata", None)
    short_profile = ""
    if metadata is not None:
        short_profile = getattr(metadata, "short_profile", "")
        if not short_profile:
            short_profile = getattr(metadata, "payload", {}).get(
                "short_profile", ""
            )
    return {
        "profile_key": result.profile_key,
        "worker_id": (
            ":".join(parts[:-1]) if len(parts) > 1 else result.profile_key
        ),
        "profile_id": parts[-1] if len(parts) > 1 else "default",
        "score": result.score,
        "aggregated_score": getattr(result, "aggregated_score", None),
        "fragment_matches": [
            _project_fragment(fragment)
            for fragment in (getattr(result, "fragment_matches", None) or [])[:5]
        ],
        "short_profile": short_profile,
        "source": "vector",
    }


def _project_fragment(fragment: Any) -> dict[str, Any]:
    content = (
        getattr(fragment, "content", "")
        or getattr(fragment, "content_preview", "")
    )
    return {
        "fragment_type": getattr(fragment, "fragment_type", "unknown"),
        "score": getattr(fragment, "score", 0.0),
        "weighted_score": getattr(fragment, "weighted_score", 0.0),
        "content_preview": getattr(fragment, "content_preview", "")[:100],
        "content": content,
    }


__all__ = ["build_v1_search_response"]
