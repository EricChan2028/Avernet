"""Independent lexical/semantic recall, rank fusion and optional model reranking."""

from __future__ import annotations

import asyncio
import math

from search_core.api.errors import SearchUnavailable
from search_core.api.models import RetrievalRequest, SearchItem, SearchResult
from search_core.core.ranking import reciprocal_rank_fusion
from search_core.plugin_api.embedding import EmbeddingProvider
from search_core.plugin_api.reranker import Reranker
from search_core.plugin_api.retrieval import RetrievalIndex


class HybridRetrievalService:
    """Run both recall branches independently; preserve a visible degradation path.

    Synchronous providers run off the event loop. A single branch failure is
    reported as a degraded result when the other branch succeeds. If both fail,
    a configured in-process literal fallback may be used; no fallback is a
    declared retrieval failure and must not become an empty success.
    """

    def __init__(
        self,
        *,
        index: RetrievalIndex,
        embedding: EmbeddingProvider,
        reranker: Reranker | None = None,
        candidate_limit: int = 30,
        fallback_index: RetrievalIndex | None = None,
    ):
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        self._index = index
        self._embedding = embedding
        self._reranker = reranker
        self._candidate_limit = candidate_limit
        self._fallback_index = fallback_index

    async def retrieve(self, request: RetrievalRequest) -> SearchResult:
        if not request.query.strip() or not 0 < request.limit <= self._candidate_limit:
            raise ValueError("query and limit within candidate_limit are required")
        if request.allowed_ids == frozenset() or any(
            not values for values in request.filters.values()
        ):
            return SearchResult()

        def semantic():
            vector = self._embedding.embed(request.query)
            return self._index.vector_search(
                vector,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            )

        recall_results = await asyncio.gather(
            asyncio.to_thread(semantic),
            asyncio.to_thread(
                self._index.keyword_search,
                request.query,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            ),
            return_exceptions=True,
        )
        semantic_failure = _exception_from(recall_results[0])
        keyword_failure = _exception_from(recall_results[1])

        if semantic_failure is not None and keyword_failure is not None:
            return await self._regex_fallback(
                request, semantic_failure, keyword_failure
            )

        reasons = []
        if semantic_failure is not None:
            reasons.append("semantic_unavailable")
            vector_hits = []
        else:
            vector_hits = recall_results[0]
        if keyword_failure is not None:
            reasons.append("keyword_unavailable")
            keyword_hits = []
        else:
            keyword_hits = recall_results[1]

        hits = {hit.id: hit for hit in [*keyword_hits, *vector_hits]}
        _validate_authorization(hits, request.allowed_ids)
        ranking = reciprocal_rank_fusion(
            [[hit.id for hit in vector_hits], [hit.id for hit in keyword_hits]]
        )
        candidates = ranking[: self._candidate_limit]
        scores = [(item.item_id, item.score) for item in candidates]
        score_source = "rrf"
        if self._reranker is not None and candidates:
            try:
                reranked = await asyncio.to_thread(
                    self._reranker.rerank,
                    request.query,
                    [
                        {
                            "id": item.item_id,
                            "text": str(
                                hits[item.item_id].payload.get("content") or ""
                            ),
                        }
                        for item in candidates
                    ],
                    request.limit,
                )
                expected = {item.item_id for item in candidates}
                ids = [item.candidate_id for item in reranked]
                if len(ids) != len(set(ids)) or not set(ids) <= expected:
                    raise ValueError("invalid reranker candidate identities")
                if any(not math.isfinite(item.score) for item in reranked):
                    raise ValueError("non-finite reranker score")
                scores = [(item.candidate_id, item.score) for item in reranked]
                score_source = "reranker"
            except ValueError as exc:
                # Malformed or unauthorized model output never becomes a fallback.
                raise SearchUnavailable("invalid reranker response") from exc
            except Exception:
                reasons.append("reranker_unavailable")

        return SearchResult(
            items=_items(hits, scores[: request.limit], score_source),
            status="degraded" if reasons else "ok",
            reasons=tuple(dict.fromkeys(reasons)),
        )

    async def _regex_fallback(
        self,
        request: RetrievalRequest,
        semantic_failure: Exception,
        keyword_failure: Exception,
    ) -> SearchResult:
        if self._fallback_index is None:
            raise SearchUnavailable("retrieval dependency failed") from keyword_failure
        try:
            fallback_hits = await asyncio.to_thread(
                self._fallback_index.keyword_search,
                request.query,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            )
        except Exception as exc:
            raise SearchUnavailable("regex fallback failed") from exc
        hits = {hit.id: hit for hit in fallback_hits}
        _validate_authorization(hits, request.allowed_ids)
        ranking = reciprocal_rank_fusion([[hit.id for hit in fallback_hits]])
        scores = [(item.item_id, item.score) for item in ranking]
        return SearchResult(
            items=_items(hits, scores[: request.limit], "regex_fallback"),
            status="degraded",
            reasons=(
                "semantic_unavailable",
                "keyword_unavailable",
                "regex_fallback",
            ),
        )


def _exception_from(result: object) -> Exception | None:
    return result if isinstance(result, Exception) else None


def _validate_authorization(hits, allowed_ids) -> None:
    if allowed_ids is not None and not hits.keys() <= allowed_ids:
        raise SearchUnavailable("index returned an unauthorized candidate")


def _items(hits, scores, score_source):
    return tuple(
        SearchItem(
            item_id=item_id,
            name=str(hits[item_id].payload.get("name") or ""),
            summary=str(hits[item_id].payload.get("content") or ""),
            score=score,
            score_source=score_source,
        )
        for item_id, score in scores
    )
