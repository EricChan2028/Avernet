"""Unified application service for v1 search and recommendation.

The HTTP routes supply protocol-specific requests and projections.  This
service owns the common application policy: query normalization, default
scopes, bounded execution, fast search defaults, and stable dependency-error
semantics.  Search and recommendation remain two projections of the shared
retrieval application path rather than two unrelated route implementations.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.application.agent_search_cache import AgentSearchResultCache
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode


def default_search_filters(
    filters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge request filters with the v1 search and recommendation defaults."""

    merged: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        # Copy mutable filter arrays without corrupting valid V1 scalar
        # aliases such as worker_id; ``list("bot:owner")`` would become a
        # character array and no longer match a payload value.
        merged[key] = (
            list(value) if isinstance(value, (list, tuple, set)) else value
        )
    merged.setdefault("availability", ["protected", "public"])
    merged.setdefault("runtime_state", ["online"])
    return merged


@dataclass(frozen=True, slots=True)
class AgentSearchCommand:
    """Transport-independent v1 search command."""

    query: str
    top_k: int
    mode: str = "auto"
    min_score: float = 0.0
    filters: Mapping[str, Any] | None = None
    reranker_model: str | None = None


MAX_RECOMMEND_EXPAND_FACTOR = 3


@dataclass(frozen=True, slots=True)
class AgentRecommendationCommand:
    """Transport-independent v1 recommendation command."""

    question: str
    top_k: int
    enable_rerank: bool
    reranker_model: str | None
    expand_factor: int
    vector_min_score: float
    rerank_min_score: float | None
    min_score_explicit: bool
    filters: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentSearchOutcome:
    """The raw retrieval result plus common execution diagnostics."""

    query: str
    normalized_query: str
    top_k: int
    mode: str
    results: tuple[Any, ...]
    metadata: dict[str, Any]
    config_applied: dict[str, Any]
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class AgentRecommendationOutcome:
    """Candidate response before the HTTP recommendation projection."""

    question: str
    response: CandidateRecommendationResponse
    config_applied: dict[str, Any]
    elapsed_ms: float
    diagnostics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


Offload = Callable[[Callable[[], Any]], Awaitable[Any]]


async def _default_offload(operation: Callable[[], Any]) -> Any:
    """Run synchronous search work away from the event loop by default."""

    return await asyncio.to_thread(operation)


def normalize_query(query: str) -> str:
    """Return a stable cache/retrieval representation without raw whitespace."""

    if not isinstance(query, str):
        raise ValueError("query must be a string")
    return " ".join(unicodedata.normalize("NFKC", query).split())


class AgentSearchService:
    """Single application entrypoint used by search and recommendation routes."""

    def __init__(
        self,
        *,
        embedding_generator: Any | None = None,
        vector_match_service: Any | None = None,
        candidate_recommendation_service: Any | None = None,
        result_cache: AgentSearchResultCache | None = None,
        offload: Offload = _default_offload,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._embedding_generator = embedding_generator
        self._vector_match_service = vector_match_service
        self._candidate_recommendation_service = candidate_recommendation_service
        self._result_cache = result_cache
        self._offload = offload
        self._clock = clock

    async def _cached_or_execute(
        self,
        operation: str,
        *,
        normalized_query: str,
        material: dict[str, Any],
        metadata: dict[str, Any],
        execute: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Return a result through the bounded, index-aware process cache."""

        if self._result_cache is None:
            return await execute()

        index_version = self._result_cache.current_index_version()
        if index_version is None:
            return await execute()

        key = self._result_cache.key(
            operation,
            index_version=index_version,
            material={
                "normalized_query": self._result_cache.normalize_query(
                    normalized_query
                ),
                **material,
            },
        )
        hit = self._result_cache.get(key)
        if hit is not None:
            metadata["cache_hit"] = True
            if getattr(hit, "metadata", None) is not None:
                hit.metadata["cache_hit"] = True
            return hit

        self._result_cache.record_miss()
        async with self._result_cache.singleflight(key):
            hit = self._result_cache.get_without_metrics(key)
            if hit is not None:
                metadata["cache_hit"] = True
                if getattr(hit, "metadata", None) is not None:
                    hit.metadata["cache_hit"] = True
                return hit

            outcome = await execute()
            outcome_metadata = (
                outcome.response.metadata
                if isinstance(outcome, AgentRecommendationOutcome)
                else outcome.metadata
            )
            if not self._has_degradation_markers(outcome_metadata):
                self._result_cache.store(key, outcome)
            outcome.metadata["cache_hit"] = False
            return outcome

    @staticmethod
    def _has_degradation_markers(metadata: dict[str, Any]) -> bool:
        if metadata.get("keyword_unavailable") is True:
            return True
        if metadata.get("semantic_unavailable") is True:
            return True
        if metadata.get("literal_fallback_used") is True:
            return True
        if metadata.get("score_source") == "regex_fallback":
            return True
        return metadata.get("rerank_degraded") is True

    @staticmethod
    def _validated_query(value: str, *, field_name: str) -> str:
        normalized = normalize_query(value)
        if not normalized:
            raise ValueError(f"{field_name} must not be blank")
        return normalized

    def _default_filters(
        self, filters: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        return default_search_filters(filters)

    async def _run(self, operation: Callable[[], Any]) -> Any:
        return await self._offload(operation)

    async def search(self, command: AgentSearchCommand) -> AgentSearchOutcome:
        """Execute the shared search retrieval chain and return raw matches."""

        if command.top_k <= 0:
            raise ValueError("top_k must be positive")
        normalized_query = self._validated_query(command.query, field_name="query")
        filters = self._default_filters(command.filters)
        runtime_config = {"reranker_model": command.reranker_model}
        metadata: dict[str, Any] = {}
        started = self._clock()

        async def execute_search() -> AgentSearchOutcome:
            try:
                query_embedding = await self._run(
                    lambda: self._embedding_generator.embed(normalized_query)
                )
            except Exception:
                # The bounded keyword/literal chain in the match service remains
                # usable when the semantic model is unavailable.
                metadata["semantic_unavailable"] = True
                query_embedding = []

            try:
                results = await self._run(
                    lambda: self._vector_match_service.match(
                        query_embedding=query_embedding,
                        top_k=command.top_k,
                        query=normalized_query,
                        mode=command.mode,
                        min_score=command.min_score,
                        filters=filters,
                        runtime_config=runtime_config,
                    )
                )
            except Exception as error:
                raise RuntimeError("search retrieval unavailable") from error

            config_applied = dict(runtime_config)
            config_applied["filters"] = filters
            # The delegate records retrieval degradation in runtime_config
            # because it is transport-independent. Publish it here as outcome
            # metadata so projection and cache policy can reject degraded data.
            metadata.update(config_applied.get("_retrieval", {}))

            return AgentSearchOutcome(
                query=command.query,
                normalized_query=normalized_query,
                top_k=command.top_k,
                mode=command.mode,
                results=tuple(results or []),
                metadata=metadata,
                config_applied=config_applied,
                elapsed_ms=(self._clock() - started) * 1000,
            )

        return await self._cached_or_execute(
            "search",
            normalized_query=normalized_query,
            material={
                "top_k": command.top_k,
                "mode": command.mode,
                "min_score": command.min_score,
                "filters": filters,
                "reranker_model": command.reranker_model,
            },
            metadata=metadata,
            execute=execute_search,
        )

    async def recommend(
        self, command: AgentRecommendationCommand
    ) -> AgentRecommendationOutcome:
        """Run recommendation through the same normalized application boundary."""

        if command.top_k <= 0:
            raise ValueError("topK must be positive")
        if command.expand_factor <= 0:
            raise ValueError("expand_factor must be positive")
        if self._candidate_recommendation_service is None:
            raise ValueError("candidate_recommendation_service is required")
        normalized_question = self._validated_query(
            command.question, field_name="question"
        )
        filters = self._default_filters(command.filters)
        effective_expand_factor = min(command.expand_factor, MAX_RECOMMEND_EXPAND_FACTOR)
        runtime_config = {
            "expand_factor": effective_expand_factor,
            "reranker_model": None
            if not command.enable_rerank
            else command.reranker_model,
        }
        started = self._clock()
        metadata: dict[str, Any] = {}

        async def execute_recommend() -> AgentRecommendationOutcome:
            try:
                response = await self._run(
                    lambda: self._candidate_recommendation_service.recommend(
                        question=normalized_question,
                        mode=RetrievalMode.EXPERT_DIAGNOSIS,
                        participants=None,
                        max_candidates=command.top_k,
                        runtime_config=runtime_config,
                        filters=filters,
                        vector_min_score=command.vector_min_score,
                        rerank_min_score=command.rerank_min_score,
                    )
                )
            except Exception as error:
                raise RuntimeError("recommendation service unavailable") from error

            return AgentRecommendationOutcome(
                question=command.question,
                response=response,
                config_applied=dict(runtime_config),
                elapsed_ms=(self._clock() - started) * 1000,
                diagnostics={
                    "min_score_explicit": command.min_score_explicit,
                    "vector_min_score": command.vector_min_score,
                    "rerank_min_score": command.rerank_min_score,
                },
                metadata=metadata,
            )

        return await self._cached_or_execute(
            "recommend",
            normalized_query=normalized_question,
            material={
                "top_k": command.top_k,
                "enable_rerank": command.enable_rerank,
                "reranker_model": command.reranker_model,
                "expand_factor": effective_expand_factor,
                "vector_min_score": command.vector_min_score,
                "rerank_min_score": command.rerank_min_score,
                "filters": filters,
            },
            metadata=metadata,
            execute=execute_recommend,
        )


__all__ = [
    "AgentRecommendationCommand",
    "AgentRecommendationOutcome",
    "AgentSearchCommand",
    "AgentSearchOutcome",
    "AgentSearchService",
    "MAX_RECOMMEND_EXPAND_FACTOR",
    "normalize_query",
]
