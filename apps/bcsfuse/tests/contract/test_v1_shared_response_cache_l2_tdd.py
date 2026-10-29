"""TDD contract for the cross-pod v1 L2 response cache."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.application.agent_search_cache import AgentSearchResultCache
from app.domain.models.bot_recommendation import (
    BotRecommendation,
    BotRecommendationRequest,
    BotRecommendationResponse,
)


class FakeSharedResponseCache:
    """Minimal process-independent cache used to model the L2 backend."""

    def __init__(self, *, fail_reads: bool = False, fail_writes: bool = False) -> None:
        self.values: dict[str, bytes] = {}
        self.read_keys: list[str] = []
        self.write_keys: list[tuple[str, bytes, int]] = []
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes

    def get(self, key: str, *, index_version: int) -> bytes | None:
        self.read_keys.append(key)
        if self.fail_reads:
            raise RuntimeError("L2 unavailable")
        return self.values.get(key)

    def store(
        self, key: str, payload: bytes, *, index_version: int, ttl_seconds: float
    ) -> bool:
        self.write_keys.append((key, payload, index_version))
        if self.fail_writes:
            raise RuntimeError("L2 write failed")
        self.values[key] = payload
        return True


class Downstream:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.calls = 0
        self.payload = payload

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        json.loads(b"".join(chunks))
        from starlette.responses import JSONResponse

        await JSONResponse(self.payload)(scope, receive, send)


def cache(version: int = 7) -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: version,
        max_entries=10,
        ttl_seconds=30.0,
    )


def search_request() -> Any:
    from app.bootstrap.oss_business_routes import SearchRequestOSS

    return SearchRequestOSS(query=" 支付 风险 ", top_k=3)


def search_response() -> dict[str, Any]:
    return {
        "query": "支付 风险",
        "top_k": 3,
        "mode": "auto",
        "results_count": 1,
        "results": [{"profile_key": "bot:reader:default", "score": 0.9}],
        "timing_ms": 1.2,
        "config_applied": {
            "reranker_model": None,
            "filters": {
                "availability": ["protected", "public"],
                "runtime_state": ["online"],
            },
        },
    }


def recommendation_request() -> BotRecommendationRequest:
    return BotRecommendationRequest(
        question=" 支付 风险 ",
        topK=3,
        enable_rerank=False,
    )


def recommendation_response(trace_id: str = "trace-pod-a") -> BotRecommendationResponse:
    return BotRecommendationResponse(
        trace_id=trace_id,
        type="recommend",
        driver_bot_id="bot:reader:default",
        recommendations=[
            BotRecommendation(
                profile_key="bot:reader:default",
                worker_id="bot:reader",
                score=0.9,
            )
        ],
        metadata={"cache_hit": False, "expand_factor": 3},
    )


def metric(name: str, operation: str, *, stage: str | None = None) -> float:
    from prometheus_client import REGISTRY

    sample = f"bcsfuse_search_shared_response_cache_{name}_total"
    labels = {"operation": operation}
    if stage is not None:
        labels["stage"] = stage
    value = REGISTRY.get_sample_value(sample, labels)
    return 0.0 if value is None else float(value)


async def post(app: Any, *, url: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(url, json=body)


def test_cross_pod_search_hit_is_answered_before_admission() -> None:
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )

    class PodASearchRoute:
        def __init__(self, result_cache: AgentSearchResultCache) -> None:
            self.calls = 0
            self.result_cache = result_cache

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                if not message.get("more_body", False):
                    break
            assert (
                store_v1_search_response(
                    result_cache=self.result_cache,
                    request=search_request(),
                    response=search_response(),
                    metadata={},
                )
                is True
            )
            await JSONResponse(search_response())(scope, receive, send)

    result_cache_pod_a = cache()
    shared_cache = FakeSharedResponseCache()
    downstream_pod_a = PodASearchRoute(result_cache_pod_a)
    pod_a = SearchResponseCacheMiddleware(
        app=downstream_pod_a,
        result_cache=result_cache_pod_a,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        await post(
            pod_a,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        await asyncio.sleep(0.05)
        fresh_pod_cache = cache()
        fresh_pod = SearchResponseCacheMiddleware(
            app=Downstream({"must_not_run": True}),
            result_cache=fresh_pod_cache,
            require_auth=lambda request: None,
            shared_response_cache=shared_cache,
        )
        result = await post(
            fresh_pod,
            url="/api/v1/search",
            body={"query": "支付 风险", "top_k": 3},
        )
        assert result.status_code == 200
        assert result.json()["results"][0]["profile_key"] == "bot:reader:default"
        assert fresh_pod_cache.stats["hits"] == 1

    asyncio.run(run())
    assert downstream_pod_a.calls == 1
    assert len(shared_cache.write_keys) == 1
    assert metric("hits", "search", stage="read") == 1


def test_poisoned_l2_search_payload_replays_downstream_and_counts_error() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        build_v1_search_response_cache_key,
    )

    result_cache = cache()
    shared_cache = FakeSharedResponseCache()
    key = build_v1_search_response_cache_key(
        result_cache=result_cache,
        request=search_request(),
        index_version=7,
    )
    shared_cache.values[key] = b"{invalid"
    downstream = Downstream({"downstream": True})
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        result = await post(
            middleware,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        assert result.status_code == 200
        assert result.json()["downstream"] is True

    before = metric("errors", "search", stage="decode")
    asyncio.run(run())
    assert downstream.calls == 1
    assert metric("errors", "search", stage="decode") - before == 1


def test_unavailable_l2_search_read_replays_downstream_and_counts_error() -> None:
    from app.adapters.http.search_response_cache import SearchResponseCacheMiddleware

    shared_cache = FakeSharedResponseCache(fail_reads=True)
    downstream = Downstream({"downstream": True})
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=cache(),
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        result = await post(
            middleware,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        assert result.status_code == 200
        assert result.json()["downstream"] is True

    before = metric("errors", "search", stage="read")
    asyncio.run(run())
    assert downstream.calls == 1
    assert metric("errors", "search", stage="read") - before == 1


def test_l2_search_payload_is_bound_to_index_version() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        build_v1_search_response_cache_key,
    )

    shared_cache = FakeSharedResponseCache()
    key_v7 = build_v1_search_response_cache_key(
        result_cache=cache(7),
        request=search_request(),
        index_version=7,
    )
    shared_cache.values[key_v7] = json.dumps(search_response()).encode("utf-8")
    downstream = Downstream({"downstream": True})
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=cache(8),
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        result = await post(
            middleware,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        assert result.status_code == 200
        assert result.json()["downstream"] is True

    before = metric("misses", "search", stage="read")
    asyncio.run(run())
    assert downstream.calls == 1
    assert metric("misses", "search", stage="read") - before == 1


def test_degraded_search_is_not_published_to_l2() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )

    shared_cache = FakeSharedResponseCache()
    result_cache = cache()
    downstream = Downstream(search_response())
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        assert (
            store_v1_search_response(
                result_cache=result_cache,
                request=search_request(),
                response=search_response(),
                metadata={"semantic_unavailable": True},
            )
            is False
        )
        result = await post(
            middleware,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        assert result.status_code == 200

    asyncio.run(run())
    assert shared_cache.write_keys == []


def test_cross_pod_recommendation_hit_rewrites_current_trace_id() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        build_v1_recommendation_response_cache_key,
    )

    shared_cache = FakeSharedResponseCache()
    payload = recommendation_response().model_dump_json().encode("utf-8")
    key = build_v1_recommendation_response_cache_key(
        result_cache=cache(),
        request=recommendation_request(),
        index_version=7,
    )
    shared_cache.values[key] = payload
    fresh_pod_cache = cache()
    middleware = RecommendationResponseCacheMiddleware(
        app=Downstream({"must_not_run": True}),
        result_cache=fresh_pod_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
        trace_id_provider=lambda: "trace-pod-b",
    )

    async def run() -> None:
        result = await post(
            middleware,
            url="/api/v1/recommend",
            body={"question": "支付 风险", "topK": 3, "enable_rerank": False},
        )
        assert result.status_code == 200
        assert result.json()["trace_id"] == "trace-pod-b"
        assert result.json()["recommendations"][0]["profile_key"] == "bot:reader:default"

    asyncio.run(run())
    assert fresh_pod_cache.stats["hits"] == 1
    assert metric("hits", "recommend", stage="read") == 1


def test_recommendation_group_request_bypasses_shared_l2() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )

    shared_cache = FakeSharedResponseCache()
    downstream = Downstream({"downstream": True})
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=cache(),
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        result = await post(
            middleware,
            url="/api/v1/recommend",
            body={
                "question": "支付 风险",
                "topK": 3,
                "group_id": "grp_test",
                "enable_rerank": False,
            },
        )
        assert result.status_code == 200
        assert result.json()["downstream"] is True

    asyncio.run(run())
    assert shared_cache.read_keys == []
    assert shared_cache.write_keys == []


def test_successful_recommendation_is_published_to_l2() -> None:
    from starlette.responses import JSONResponse

    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )

    class PodARecommendationRoute:
        def __init__(self, result_cache: AgentSearchResultCache) -> None:
            self.calls = 0
            self.result_cache = result_cache

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                if not message.get("more_body", False):
                    break
            response = recommendation_response("trace-first")
            assert (
                store_v1_recommendation_response(
                    result_cache=self.result_cache,
                    request=recommendation_request(),
                    response=response,
                )
                is True
            )
            payload = json.loads(response.model_dump_json())
            await JSONResponse(payload)(scope, receive, send)

    shared_cache = FakeSharedResponseCache()
    result_cache = cache()
    downstream = PodARecommendationRoute(result_cache)
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        result = await post(
            middleware,
            url="/api/v1/recommend",
            body={
                "question": " 支付 风险 ",
                "topK": 3,
                "enable_rerank": False,
            },
        )
        assert result.status_code == 200
        await asyncio.sleep(0.05)

    before = metric("stores", "recommend", stage="store")
    asyncio.run(run())
    assert downstream.calls == 1
    assert len(shared_cache.write_keys) == 1
    assert shared_cache.write_keys[0][2] == 7
    assert metric("stores", "recommend", stage="store") - before == 1


def test_composition_root_injects_shared_response_cache_into_both_routes() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
    )
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class Startup:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    class Store:
        index_owner = "pod-a"
        index_version = 7
        index_lag_seconds = 0
        index_stale = False
        read_only = False

        def check_ready(self) -> bool:
            return True

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", Startup())
    registry.register("vector_store", Store())
    shared = FakeSharedResponseCache()
    registry.register("shared_response_cache", shared)
    context = ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )
    app = create_bcsfuse_app(context)

    def middleware(cls: type[Any]) -> Any:
        return next(item for item in app.user_middleware if item.cls is cls)

    assert (
        middleware(SearchResponseCacheMiddleware).kwargs["shared_response_cache"]
        is shared
    )
    assert (
        middleware(RecommendationResponseCacheMiddleware).kwargs[
            "shared_response_cache"
        ]
        is shared
    )


def test_slow_l2_read_times_out_and_replays_downstream() -> None:
    import time

    from app.adapters.http.search_response_cache import SearchResponseCacheMiddleware

    class SlowSharedRead:
        def __init__(self) -> None:
            self.read_count = 0

        def get(self, key: str, *, index_version: int) -> bytes | None:
            self.read_count += 1
            time.sleep(0.5)
            return b'{"results":[]}'

        def store(self, *args: Any, **kwargs: Any) -> bool:
            return True

    shared = SlowSharedRead()
    downstream = Downstream({"downstream": True})
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=cache(),
        require_auth=lambda request: None,
        shared_response_cache=shared,
        shared_cache_timeout_seconds=0.01,
    )

    async def run() -> None:
        started_at = time.perf_counter()
        result = await post(
            middleware,
            url="/api/v1/search",
            body={"query": "支付 风险", "top_k": 3},
        )
        elapsed = time.perf_counter() - started_at
        assert result.status_code == 200
        assert result.json()["downstream"] is True
        assert elapsed < 0.2

    errors_before = metric("errors", "search", stage="read")
    asyncio.run(run())
    assert downstream.calls == 1
    assert metric("errors", "search", stage="read") - errors_before == 1


def test_repeated_l1_hits_do_not_republish_to_l2() -> None:
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )

    class RouteLikeDownstream:
        def __init__(self, result_cache: AgentSearchResultCache) -> None:
            self.calls = 0
            self.result_cache = result_cache

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                if not message.get("more_body", False):
                    break
            assert (
                store_v1_search_response(
                    result_cache=self.result_cache,
                    request=search_request(),
                    response=search_response(),
                    metadata={},
                )
                is True
            )
            await JSONResponse(search_response())(scope, receive, send)

    result_cache = cache()
    shared_cache = FakeSharedResponseCache()
    downstream = RouteLikeDownstream(result_cache)
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run() -> None:
        await post(
            middleware,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        await asyncio.sleep(0)
        await post(
            middleware,
            url="/api/v1/search",
            body={"query": "支付 风险", "top_k": 3},
        )
        await asyncio.sleep(0)
        await post(
            middleware,
            url="/api/v1/search",
            body={"query": "支付  风险", "top_k": 3},
        )
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert downstream.calls == 1
    assert len(shared_cache.write_keys) == 1


def test_l2_stage_metrics_distinguish_read_decode_and_store_errors() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        build_v1_search_response_cache_key,
        store_v1_search_response,
    )

    result_cache = cache()
    shared_cache = FakeSharedResponseCache(fail_writes=True)
    key = build_v1_search_response_cache_key(
        result_cache=result_cache,
        request=search_request(),
        index_version=7,
    )
    middleware = SearchResponseCacheMiddleware(
        app=Downstream({"downstream": True}),
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    read_before = metric("errors", "search", stage="read")
    decode_before = metric("errors", "search", stage="decode")
    store_before = metric("errors", "search", stage="store")
    stores_before = metric("stores", "search", stage="store")

    async def run() -> None:
        poison_cache = FakeSharedResponseCache()
        poison_cache.values[key] = b"{invalid"
        poison = SearchResponseCacheMiddleware(
            app=Downstream({"downstream": True}),
            result_cache=cache(),
            require_auth=lambda request: None,
            shared_response_cache=poison_cache,
        )
        await post(poison, url="/api/v1/search", body={"query": "支付 风险", "top_k": 3})

    asyncio.run(run())
    assert metric("errors", "search", stage="decode") - decode_before == 1

    class SearchStoringRoute:
        def __init__(self, result_cache: AgentSearchResultCache) -> None:
            self.calls = 0
            self.result_cache = result_cache

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                if not message.get("more_body", False):
                    break
            assert (
                store_v1_search_response(
                    result_cache=self.result_cache,
                    request=search_request(),
                    response=search_response(),
                    metadata={},
                )
                is True
            )
            from starlette.responses import JSONResponse

            await JSONResponse(search_response())(scope, receive, send)

    storing_middleware = SearchResponseCacheMiddleware(
        app=SearchStoringRoute(result_cache),
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared_cache,
    )

    async def run_write() -> None:
        await post(
            storing_middleware,
            url="/api/v1/search",
            body={"query": " 支付 风险 ", "top_k": 3},
        )
        await asyncio.sleep(0.05)

    asyncio.run(run_write())
    assert metric("errors", "search", stage="read") - read_before == 0
    assert metric("errors", "search", stage="store") - store_before == 1
    assert metric("stores", "search", stage="store") - stores_before == 0


def test_l2_write_store_false_is_an_error_not_a_success() -> None:
    from app.adapters.http.shared_response_cache_writer import (
        BoundedSharedResponseCacheWriter,
    )

    class FalseStore:
        values: dict[str, bytes] = {}

        def get(self, key: str, *, index_version: int) -> bytes | None:
            return None

        def store(
            self, key: str, payload: bytes, *, index_version: int, ttl_seconds: float
        ) -> bool:
            return False

    writer = BoundedSharedResponseCacheWriter(
        shared_response_cache=FalseStore(),
        operation="search",
        ttl_seconds=30.0,
        timeout_seconds=0.05,
        queue_size=8,
        worker_count=1,
    )

    errors_before = metric("errors", "search", stage="store")
    stores_before = metric("stores", "search", stage="store")

    async def run() -> None:
        writer.submit(key="key-false", payload=b"{}", index_version=7)
        await writer.wait_idle()

    asyncio.run(run())
    assert metric("errors", "search", stage="store") - errors_before == 1
    assert metric("stores", "search", stage="store") - stores_before == 0


def test_l2_bounded_writer_limits_pending_writes_and_reports_enqueue_errors() -> None:
    import threading

    from app.adapters.http.shared_response_cache_writer import (
        BoundedSharedResponseCacheWriter,
    )

    class BlockingStore:
        def __init__(self) -> None:
            self.write_count = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def get(self, key: str, *, index_version: int) -> bytes | None:
            return None

        def store(
            self, key: str, payload: bytes, *, index_version: int, ttl_seconds: float
        ) -> bool:
            self.write_count += 1
            self.started.set()
            self.release.wait(1)
            return True

    store = BlockingStore()
    writer = BoundedSharedResponseCacheWriter(
        shared_response_cache=store,
        operation="search",
        ttl_seconds=30.0,
        timeout_seconds=1.0,
        queue_size=1,
        worker_count=1,
    )

    enqueue_before = metric("errors", "search", stage="enqueue")

    async def run() -> None:
        assert writer.submit(key="running", payload=b"{}", index_version=7)
        assert await asyncio.to_thread(store.started.wait, 0.5)
        assert writer.submit(key="queued", payload=b"{}", index_version=7)
        assert writer.submit(key="dropped", payload=b"{}", index_version=7) is False
        store.release.set()
        await writer.wait_idle()

    asyncio.run(run())
    assert store.write_count == 2
    assert metric("errors", "search", stage="enqueue") - enqueue_before == 1
