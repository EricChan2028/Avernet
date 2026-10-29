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

from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode


@dataclass(frozen=True, slots=True)
class AgentSearchCommand:
    """Transport-independent v1 search command."""

    query: str
    top_k: int
    mode: str = "auto"
    min_score: float = 0.0
    filters: Mapping[str, Any] | None = None
    reranker_model: str | None = None


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
        offload: Offload = _default_offload,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._embedding_generator = embedding_generator
        self._vector_match_service = vector_match_service
        self._candidate_recommendation_service = candidate_recommendation_service
        self._offload = offload
        self._clock = clock

    @staticmethod
    def _validated_query(value: str, *, field_name: str) -> str:
        normalized = normalize_query(value)
        if not normalized:
            raise ValueError(f"{field_name} must not be blank")
        return normalized

    def _default_filters(
        self, filters: Mapping[str, Any] | None
    ) -> dict[str, Any]:
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
        runtime_config = {
            "expand_factor": command.expand_factor,
            "reranker_model": None
            if not command.enable_rerank
            else command.reranker_model,
        }
        started = self._clock()

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
        )


__all__ = [
    "AgentRecommendationCommand",
    "AgentRecommendationOutcome",
    "AgentSearchCommand",
    "AgentSearchOutcome",
    "AgentSearchService",
    "normalize_query",
]
