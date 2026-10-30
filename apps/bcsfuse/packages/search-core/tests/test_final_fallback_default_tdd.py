"""The final fallback remains deterministic when no literal token overlaps."""

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.fallback import RegexFallbackIndex


def _point(name: str) -> VectorPoint:
    return VectorPoint(
        id=f"worker:{name}",
        vector=[],
        payload={
            "profile_key": f"worker:{name}",
            "worker_id": "worker",
            "profile_id": name,
            "content": f"{name} assistant profile",
            "runtime_state": "online",
            "availability": "public",
        },
    )


def test_default_search_returns_filtered_profiles_without_word_overlap() -> None:
    index = RegexFallbackIndex([_point("alpha"), _point("beta")])

    hits = index.default_search(
        limit=1,
        filters={"runtime_state": ["online"], "availability": ["public"]},
    )

    assert len(hits) == 1
    assert hits[0].id == "worker:alpha"
    assert hits[0].score == 0.01
    assert hits[0].payload["profile_key"] == "worker:alpha"


def test_default_search_respects_empty_allowed_ids() -> None:
    index = RegexFallbackIndex([_point("alpha")])

    assert index.default_search(limit=1, filters={}, allowed_ids=frozenset()) == []
