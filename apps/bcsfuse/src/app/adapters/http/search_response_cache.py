"""Pre-admission response caching for the v1 search route."""

from __future__ import annotations

import asyncio
import json
import os
from time import perf_counter
from typing import Any, Callable

from fastapi import HTTPException, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.application.agent_search_cache import AgentSearchResultCache
from app.application.agent_search_service import default_search_filters
from app.bootstrap.oss_business_routes import SearchRequestOSS
from app.bootstrap.search_capacity import MAX_SEARCH_BODY_BYTES, _get_search_metrics
from app.infra.config.feature_flags import FeatureFlags
from app.infra.public.observability.shared_response_cache_metrics import (
    record_shared_response_cache_error,
    record_shared_response_cache_hit,
    record_shared_response_cache_miss,
    record_shared_response_cache_store,
)

RequireAuth = Callable[[Request], None]
V1_SEARCH_RESPONSE_CACHE_OPERATION = "v1_search_response"
DEFAULT_L2_TTL_SECONDS = 300.0
DEFAULT_L2_TIMEOUT_SECONDS = 0.05


def build_v1_search_response_cache_key(
    *,
    result_cache: AgentSearchResultCache,
    request: SearchRequestOSS,
    index_version: int,
) -> str:
    """Build the final route-projection cache key from request semantics."""

    filters = default_search_filters(request.filters)
    return result_cache.key(
        V1_SEARCH_RESPONSE_CACHE_OPERATION,
        index_version=index_version,
        material={
            "normalized_query": result_cache.normalize_query(request.query),
            "top_k": request.top_k,
            "mode": request.mode,
            "min_score": request.min_score,
            "filters": filters,
            "reranker_model": os.getenv("RERANKER_MODEL"),
            "profile_embedding_enabled": (
                FeatureFlags.is_profile_embedding_index_enabled()
            ),
        },
    )


def store_v1_search_response(
    *,
    result_cache: AgentSearchResultCache | None,
    request: SearchRequestOSS,
    response: dict[str, Any],
    metadata: dict[str, Any],
) -> bool:
    """Store a non-degenerate final search HTTP projection."""

    if result_cache is None:
        return False
    if not _cacheable_response(metadata):
        return False

    index_version = result_cache.current_index_version()
    if index_version is None:
        return False
    key = build_v1_search_response_cache_key(
        result_cache=result_cache,
        request=request,
        index_version=index_version,
    )
    result_cache.store(
        key,
        json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ),
    )
    return True


def _cacheable_response(metadata: dict[str, Any]) -> bool:
    if metadata.get("semantic_unavailable") is True:
        return False
    if metadata.get("keyword_unavailable") is True:
        return False
    if metadata.get("literal_fallback_used") is True:
        return False
    if metadata.get("rerank_degraded") is True:
        return False
    return metadata.get("score_source") != "regex_fallback"


class SearchResponseCacheMiddleware:
    """Answer warmed v1 search projections before capacity admission."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        result_cache: AgentSearchResultCache | None,
        require_auth: RequireAuth,
        metrics: dict[str, Any] | None = None,
        shared_response_cache: Any | None = None,
        shared_cache_ttl_seconds: float = DEFAULT_L2_TTL_SECONDS,
        shared_cache_timeout_seconds: float = DEFAULT_L2_TIMEOUT_SECONDS,
    ) -> None:
        self.app = app
        self._result_cache = result_cache
        self._require_auth = require_auth
        self._metrics = metrics or _get_search_metrics()
        self._shared_response_cache = shared_response_cache
        self._shared_cache_ttl_seconds = shared_cache_ttl_seconds
        self._shared_cache_timeout_seconds = shared_cache_timeout_seconds
        self._inflight: dict[str, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/search"
        ):
            await self.app(scope, receive, send)
            return

        (
            hit_response,
            replay_receive,
            outcome,
            started_at,
            missed_request,
        ) = await self._hit_response(scope, receive)

        if hit_response is not None:
            self._record_preflight_request(outcome, started_at)
            await hit_response(scope, replay_receive, send)
            return

        coalescing_key = (
            None
            if missed_request is None or self._result_cache is None
            else self._coalescing_key(missed_request)
        )
        if coalescing_key is None:
            await self.app(scope, replay_receive, send)
            return

        lock = self._inflight.setdefault(coalescing_key, asyncio.Lock())
        try:
            async with lock:
                recovered_response = (
                    None
                    if missed_request is None
                    else self._cached_response(missed_request)
                )
                if recovered_response is not None:
                    self._record_preflight_request(200, started_at)
                    await recovered_response(scope, replay_receive, send)
                    return
                recording_send = self._recording_send(
                    request=missed_request,
                    key=coalescing_key,
                    send=send,
                )
                await self.app(scope, replay_receive, recording_send)
        finally:
            if not lock.locked() and self._inflight.get(coalescing_key) is lock:
                self._inflight.pop(coalescing_key, None)

    async def _hit_response(
        self, scope: Scope, receive: Receive
    ) -> tuple[
        Response | None, Receive, int | None, float, SearchRequestOSS | None
    ]:
        started_at = perf_counter()
        if self._result_cache is None:
            return None, receive, None, started_at, None

        declared_size = _declared_content_length(scope)
        if declared_size is not None and declared_size > MAX_SEARCH_BODY_BYTES:
            return None, receive, None, started_at, None

        replay_receive, messages = await self._bounded_receive(receive)
        if replay_receive is None:
            return None, receive, None, started_at, None

        try:
            parsed = json.loads(_join_messages(messages))
        except (TypeError, ValueError):
            return None, replay_receive, None, started_at, None
        if not isinstance(parsed, dict):
            return None, replay_receive, None, started_at, None

        try:
            request = SearchRequestOSS.model_validate(parsed)
        except ValueError:
            return None, replay_receive, None, started_at, None

        try:
            self._require_auth(Request(scope, replay_receive))
        except HTTPException as error:
            return (
                Response(
                    status_code=error.status_code,
                    media_type="application/json",
                    content=json.dumps({"detail": error.detail}).encode("utf-8"),
                ),
                replay_receive,
                error.status_code,
                started_at,
                None,
            )

        index_version = self._result_cache.current_index_version()
        if index_version is None:
            return None, replay_receive, None, started_at, request
        key = build_v1_search_response_cache_key(
            result_cache=self._result_cache,
            request=request,
            index_version=index_version,
        )
        cached_payload = self._result_cache.get(key)
        if isinstance(cached_payload, bytes):
            return (
                Response(cached_payload, media_type="application/json"),
                replay_receive,
                200,
                started_at,
                None,
            )
        shared_payload = await self._shared_payload(key, index_version)
        if shared_payload is None:
            return None, replay_receive, None, started_at, request
        # Promote a shared hit into L1 so subsequent requests remain process-local.
        self._result_cache.store(key, shared_payload)
        promoted_payload = self._result_cache.get(key)
        if not isinstance(promoted_payload, bytes):
            return None, replay_receive, None, started_at, request
        return (
            Response(promoted_payload, media_type="application/json"),
            replay_receive,
            200,
            started_at,
            None,
        )

    async def _shared_payload(self, key: str, index_version: int) -> bytes | None:
        if self._shared_response_cache is None:
            return None
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self._shared_response_cache.get,
                    key,
                    index_version=index_version,
                ),
                timeout=self._shared_cache_timeout_seconds,
            )
        except Exception:
            record_shared_response_cache_error("search")
            return None
        if not isinstance(payload, bytes):
            record_shared_response_cache_miss("search")
            return None
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            record_shared_response_cache_error("search")
            return None
        if not isinstance(parsed, dict):
            record_shared_response_cache_error("search")
            return None
        record_shared_response_cache_hit("search")
        return payload

    def _recording_send(
        self, *, request: SearchRequestOSS, key: str, send: Send
    ) -> Send:
        status_code: int | None = None
        body_chunks: list[bytes] = []

        async def recording(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 0))
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and status_code == 200
            ):
                self._schedule_l2_publish(
                    request=request,
                    key=key,
                    body=b"".join(body_chunks),
                )

        return recording

    def _schedule_l2_publish(
        self, *, request: SearchRequestOSS, key: str, body: bytes
    ) -> None:
        if self._shared_response_cache is None or body == b"":
            return
        task = asyncio.create_task(
            self._publish_l2_response(request=request, key=key, body=body)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _publish_l2_response(
        self, *, request: SearchRequestOSS, key: str, body: bytes
    ) -> None:
        assert self._shared_response_cache is not None
        index_version = self._result_cache.current_index_version()
        if index_version is None:
            return
        # L1 contains only projections the route explicitly stored as
        # non-degraded. Never infer cacheability from HTTP status alone.
        payload = self._result_cache.get_without_metrics(key)
        if not isinstance(payload, bytes):
            return
        try:
            stored = await asyncio.wait_for(
                asyncio.to_thread(
                    self._shared_response_cache.store,
                    key,
                    payload,
                    index_version=index_version,
                    ttl_seconds=self._shared_cache_ttl_seconds,
                ),
                timeout=self._shared_cache_timeout_seconds,
            )
        except Exception:
            record_shared_response_cache_error("search")
            return
        if stored:
            record_shared_response_cache_store("search")

    def _coalescing_key(self, request: SearchRequestOSS) -> str | None:
        assert self._result_cache is not None
        index_version = self._result_cache.current_index_version()
        if index_version is None:
            return None
        return build_v1_search_response_cache_key(
            result_cache=self._result_cache,
            request=request,
            index_version=index_version,
        )

    def _cached_response(self, request: SearchRequestOSS) -> Response | None:
        assert self._result_cache is not None
        index_version = self._result_cache.current_index_version()
        if index_version is None:
            return None
        cached_payload = self._result_cache.get(
            build_v1_search_response_cache_key(
                result_cache=self._result_cache,
                request=request,
                index_version=index_version,
            )
        )
        if not isinstance(cached_payload, bytes):
            return None
        return Response(cached_payload, media_type="application/json")

    def _record_preflight_request(self, outcome: int, started_at: float) -> None:
        labels = {"operation": "search", "api_version": "v1"}
        inflight = self._metrics["inflight"].labels(**labels)
        inflight.inc()
        try:
            self._metrics["requests"].labels(
                **labels, outcome=str(outcome)
            ).inc()
            self._metrics["duration"].labels(
                **labels, outcome=str(outcome)
            ).observe(perf_counter() - started_at)
        finally:
            inflight.dec()

    async def _bounded_receive(
        self, receive: Receive
    ) -> tuple[Receive | None, list[Message]]:
        messages: list[Message] = []
        total_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None, messages
            if message["type"] != "http.request":
                continue
            messages.append(message)
            total_bytes += len(message.get("body", b""))
            if total_bytes > MAX_SEARCH_BODY_BYTES:
                if messages and messages[-1].get("more_body", False):
                    messages.append(
                        {"type": "http.request", "body": b"", "more_body": False}
                    )
                return _replayed_receive(messages), messages
            if not message.get("more_body", False):
                return _replayed_receive(messages), messages


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if bytes(name).lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _replayed_receive(messages: list[Message]) -> Receive:
    iterator = iter(messages)
    terminal_sent = False

    async def receive() -> Message:
        nonlocal terminal_sent
        try:
            return next(iterator)
        except StopIteration:
            if not terminal_sent:
                terminal_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

    return receive


def _join_messages(messages: list[Message]) -> bytes:
    return b"".join(message.get("body", b"") for message in messages)


__all__ = [
    "SearchResponseCacheMiddleware",
    "build_v1_search_response_cache_key",
    "store_v1_search_response",
]
