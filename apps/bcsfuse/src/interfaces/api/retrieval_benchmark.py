"""Opt-in, authenticated storage benchmark; never mounted by the public app.

Hosts explicitly supply the already-loaded store, real embedding provider and
access token. Only preparation calls the model. Search never writes the store.
"""

import math
import os
import secrets
import socket
import threading
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from starlette.concurrency import run_in_threadpool

from src.domain.services.embedding_provider import EmbeddingProvider
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.interfaces.api.retrieval_benchmark_info import runtime_info

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
FilterValue = str | int | bool | list[str] | list[int]


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[Query] = Field(min_length=1, max_length=64)
    with_vectors: bool = True


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_set_id: str = Field(min_length=1, max_length=128)
    sample_index: int = Field(ge=0, le=63)
    mode: Literal["vector", "keyword", "both"] = "both"
    top_k: int = Field(default=10, ge=1, le=1000)
    filters: dict[str, FilterValue] = Field(default_factory=dict, max_length=16)

    @field_validator("filters")
    @classmethod
    def bounded_filters(cls, filters):
        for key, value in filters.items():
            values = value if isinstance(value, list) else [value]
            if len(key) > 128 or len(values) > 128 or any(len(str(v)) > 512 for v in values):
                raise ValueError("filter is too large")
        return filters


@dataclass(frozen=True)
class SampleSet:
    id: str
    queries: list[str]
    vectors: list[list[float]]


def create_retrieval_benchmark_router(
    *, store: VectorStoreAdapter, embedding: EmbeddingProvider,
    model_name: str, dimension: int, token: str, max_inflight: int = 32,
) -> APIRouter:
    """Mount only from an explicitly enabled, non-production composition root."""
    if not token.strip() or not model_name.strip() or dimension <= 0 or not 1 <= max_inflight <= 128:
        raise ValueError("benchmark needs token, model name, dimension and 1..128 inflight requests")
    instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
    samples = SampleSet("", [], [])
    preparation_lock = threading.Lock()
    inflight = anyio.CapacityLimiter(max_inflight)

    async def authenticate(authorization: str = Header(default="")) -> None:
        if not secrets.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "benchmark authorization required")

    router = APIRouter(prefix="/internal/bench", tags=["Retrieval benchmark"],
                       dependencies=[Depends(authenticate)])

    def prepare_sync(request: PrepareRequest) -> SampleSet:
        vectors = []
        if request.with_vectors:
            for query in request.queries:
                vector = embedding.embed(query)
                if (len(vector) != dimension or not all(math.isfinite(v) for v in vector)
                        or not any(v != 0 for v in vector)):
                    raise ValueError("invalid embedding output")
                vectors.append(list(vector))
        return SampleSet(uuid4().hex, list(request.queries), vectors)

    @router.post("/samples")
    async def prepare(request: PrepareRequest):
        nonlocal samples
        if not preparation_lock.acquire(blocking=False):
            raise HTTPException(409, "sample preparation already running")
        try:
            candidate = await run_in_threadpool(prepare_sync, request)
            samples = candidate  # Publish the entire set only after all embeddings succeed.
        except Exception as error:
            raise HTTPException(503, "sample preparation failed; previous set retained") from error
        finally:
            preparation_lock.release()
        return {"instance_id": instance_id, "sample_set_id": candidate.id,
                "sample_count": len(candidate.queries), "dimension": dimension,
                "sample_source": "configured_embedding" if candidate.vectors else "text_only",
                "model": model_name if candidate.vectors else None}

    @router.get("/info")
    async def info():
        try:
            count = await run_in_threadpool(store.size)
        except Exception as error:
            raise HTTPException(503, "local index unavailable") from error
        current = samples
        return {**runtime_info(), "instance_id": instance_id,
                "store_type": type(store).__name__, "vector_count": count,
                "dimension": dimension, "embedding_provider": type(embedding).__name__,
                "model": model_name, "sample_set_id": current.id,
                "sample_count": len(current.queries), "max_inflight": max_inflight,
                "sample_source": ("configured_embedding" if current.vectors else "text_only"),
                "result_cache": False, "storage_only": True}

    def execute(request: SearchRequest, current: SampleSet, dispatched_at: float):
        entered_at = perf_counter()
        vector_hits, keyword_hits = [], []
        vector_ms = keyword_ms = 0.0
        filters = request.filters or None
        if request.mode in ("vector", "both"):
            started = perf_counter()
            vector_hits = store.search(current.vectors[request.sample_index], request.top_k, filters)
            vector_ms = (perf_counter() - started) * 1000
        if request.mode in ("keyword", "both"):
            started = perf_counter()
            keyword_hits = store.text_search(current.queries[request.sample_index], request.top_k, filters)
            keyword_ms = (perf_counter() - started) * 1000
        store_ms = (perf_counter() - entered_at) * 1000

        def project(hits):
            return [{"id": hit.id, "score": hit.score} for hit in hits]

        return {"instance_id": instance_id, "sample_set_id": current.id,
                "sample_index": request.sample_index, "mode": request.mode,
                "sample_source": "configured_embedding" if current.vectors else "text_only",
                "vector_hits": project(vector_hits), "keyword_hits": project(keyword_hits),
                "timing_ms": {"dispatch_wait": (entered_at - dispatched_at) * 1000,
                              "store": store_ms, "vector": vector_ms, "keyword": keyword_ms}}

    @router.post("/search")
    async def search(request: SearchRequest):
        current = samples
        if current.id != request.sample_set_id:
            raise HTTPException(409, "sample set missing or replaced; pin requests to one process")
        if request.sample_index >= len(current.queries):
            raise HTTPException(404, "sample index not found")
        if request.mode != "keyword" and not current.vectors:
            raise HTTPException(409, "prepare samples with real embeddings first")
        try:
            inflight.acquire_nowait()
        except anyio.WouldBlock as error:
            raise HTTPException(429, "benchmark inflight limit reached") from error
        try:
            return await run_in_threadpool(partial(execute, request, current, perf_counter()))
        except Exception as error:
            raise HTTPException(503, "storage benchmark failed") from error
        finally:
            inflight.release()

    return router
