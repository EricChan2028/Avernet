"""TDD contract for the unified v1 application result cache."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any, Callable

from app.application.agent_search_cache import AgentSearchResultCache
from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
)
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode


class RecordingCore:
    _vector_match_service = None
    _embedding_generator = None

    def __init__(self) -> None:
        self.embedding_calls: list[str] = []
        self.match_calls: list[dict[str, Any]] = []
        self.recommend_calls: list[dict[str, Any]] = []

    def embed(self, query: str) -> list[float]:
        self.embedding_calls.append(query)
        return [1.0]

    def match(self, **kwargs: Any) -> list[Any]:
        self.match_calls.append(kwargs)
        return [
            SimpleNamespace(
                profile_key="bot:reader:default",
                score=0.9,
                fragment_matches=[],
                metadata=SimpleNamespace(payload={"short_profile": "reader"}),
            )
        ]

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        self.recommend_calls.append(kwargs)
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={"candidate_source": "vector"},
        )


def search_command(query: str = "支付助手") -> AgentSearchCommand:
    return AgentSearchCommand(
        query=query,
        top_k=5,
        mode="auto",
        min_score=0.1,
        filters={"worker_id": "bot:reader"},
        reranker_model=None,
    )


def recommend_command(question: str = "支付助手") -> AgentRecommendationCommand:
    return AgentRecommendationCommand(
        question=question,
        top_k=5,
        enable_rerank=False,
        reranker_model=None,
        expand_factor=10,
        vector_min_score=0.01,
        rerank_min_score=0.6,
        min_score_explicit=True,
        filters={"worker_id": "bot:reader"},
    )


def service(
    core: RecordingCore,
    result_cache: AgentSearchResultCache,
) -> AgentSearchService:
    return AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        candidate_recommendation_service=core,
        result_cache=result_cache,
    )


def cache(
    index_version_provider: Callable[[], Any],
    *,
    max_entries: int = 10,
    ttl_seconds: float = 30.0,
) -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=index_version_provider,
        max_entries=max_entries,
        ttl_seconds=ttl_seconds,
        clock=lambda: 1.0,
    )


def test_search_result_hits_use_index_version_and_skip_core_twice() -> None:
    core = RecordingCore()
    result_cache = cache(lambda: 7)
    agent_service = service(core, result_cache)

    first = asyncio.run(agent_service.search(search_command()))
    second = asyncio.run(agent_service.search(search_command()))

    assert len(core.embedding_calls) == 1
    assert len(core.match_calls) == 1
    assert first.results[0].profile_key == "bot:reader:default"
    assert second.results[0].profile_key == "bot:reader:default"
    assert second.metadata["cache_hit"] is True
    assert result_cache.stats == {
        "hits": 1,
        "misses": 1,
        "entries": 1,
        "max_entries": 10,
        "ttl_seconds": 30.0,
    }


def test_search_cache_singleflight_executes_one_identical_request() -> None:
    class BlockingCore(RecordingCore):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def match(self, **kwargs: Any) -> list[Any]:
            self.match_calls.append(kwargs)
            self.entered.set()
            self.release.wait(timeout=1)
            return [SimpleNamespace(profile_key="bot:reader:default", score=1.0)]

    core = BlockingCore()
    result_cache = cache(lambda: 7)
    agent_service = service(core, result_cache)

    async def scenario() -> tuple[Any, Any]:
        first = asyncio.create_task(agent_service.search(search_command()))
        assert await asyncio.to_thread(core.entered.wait, 1)
        second = asyncio.create_task(agent_service.search(search_command()))
        await asyncio.to_thread(core.release.set)
        return await first, await second

    first, second = asyncio.run(scenario())

    assert len(core.embedding_calls) == 1
    assert len(core.match_calls) == 1
    assert first.results[0].profile_key == "bot:reader:default"
    assert second.results[0].profile_key == "bot:reader:default"


def test_index_version_change_invalidates_the_cached_search() -> None:
    class Versions:
        def __init__(self) -> None:
            self.values = [7, 7, 8, 8]

        def __call__(self) -> int:
            return self.values.pop(0)

    versions = Versions()
    core = RecordingCore()
    result_cache = cache(versions)
    agent_service = service(core, result_cache)

    asyncio.run(agent_service.search(search_command()))
    asyncio.run(agent_service.search(search_command()))
    asyncio.run(agent_service.search(search_command()))
    asyncio.run(agent_service.search(search_command()))

    assert len(core.embedding_calls) == 2
    assert len(core.match_calls) == 2


def test_unavailable_index_version_disables_cache_without_failing_request() -> None:
    class UnstableVersion:
        def __init__(self) -> None:
            self.failed_once = False

        def __call__(self) -> int:
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("state backend unavailable")
            return 7

    core = RecordingCore()
    result_cache = cache(UnstableVersion())
    agent_service = service(core, result_cache)

    command = search_command()
    first = asyncio.run(agent_service.search(command))
    second = asyncio.run(agent_service.search(command))
    third = asyncio.run(agent_service.search(command))

    assert len(core.match_calls) == 2
    assert first.metadata.get("cache_hit") is not True
    assert second.metadata.get("cache_hit") is not True
    assert third.metadata["cache_hit"] is True


def test_degraded_embedding_results_are_never_cached() -> None:
    class RecoveringCore(RecordingCore):
        def __init__(self) -> None:
            super().__init__()
            self.failed_once = False

        def embed(self, query: str) -> list[float]:
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("embedding outage")
            return [1.0]

    core = RecoveringCore()
    result_cache = cache(lambda: 7)
    agent_service = service(core, result_cache)

    degraded = asyncio.run(agent_service.search(search_command()))
    recovered = asyncio.run(agent_service.search(search_command()))

    assert degraded.metadata["semantic_unavailable"] is True
    assert recovered.metadata.get("semantic_unavailable") is None
    assert len(core.match_calls) == 2
    assert result_cache.stats["hits"] == 0


def test_recommendation_results_are_cached_and_singleflighted() -> None:
    core = RecordingCore()
    result_cache = cache(lambda: 7)
    agent_service = service(core, result_cache)
    command = recommend_command()

    first = asyncio.run(agent_service.recommend(command))
    second = asyncio.run(agent_service.recommend(command))

    assert len(core.recommend_calls) == 1
    assert first.response.question == "支付助手"
    assert second.metadata["cache_hit"] is True
    assert second.response.question == "支付助手"


def test_cache_returns_fresh_objects_so_routes_cannot_mutate_a_stored_result() -> None:
    core = RecordingCore()
    result_cache = cache(lambda: 7)
    agent_service = service(core, result_cache)
    command = recommend_command()

    first = asyncio.run(agent_service.recommend(command))
    second = asyncio.run(agent_service.recommend(command))
    first.response.metadata["candidate_source"] = "mutated-by-route"
    third = asyncio.run(agent_service.recommend(command))

    assert third.response.metadata["candidate_source"] == "vector"


def test_result_cache_rejects_invalid_capacity_and_ttl() -> None:
    import pytest

    with pytest.raises(ValueError, match="positive"):
        AgentSearchResultCache(
            index_version_provider=lambda: 7,
            max_entries=0,
            ttl_seconds=30.0,
        )
    with pytest.raises(ValueError, match="non-negative"):
        AgentSearchResultCache(
            index_version_provider=lambda: 7,
            max_entries=10,
            ttl_seconds=-1.0,
        )


def test_composed_apps_share_one_v1_result_cache_with_routes(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class Config:
        def __init__(self) -> None:
            self.values = {
                "search.result_cache.max_entries": 17,
                "search.result_cache.ttl_seconds": 25.0,
            }

        def get_int(self, key: str, default: int) -> int:
            return int(self.values.get(key, default))

        def get_float(self, key: str, default: float) -> float:
            return float(self.values.get(key, default))

    class Startup:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    class Auth:
        def validate_request(self, request: Any) -> bool:
            return True

    class VectorStore:
        connection = SimpleNamespace(mode="server")
        collection_name = "worker_profiles"
        index_owner = "pod-a"
        index_version = 11
        index_lag_seconds = 0
        index_stale = False
        read_only = False

        def check_ready(self) -> bool:
            return True

        @property
        def path(self) -> str:
            return "/tmp/agent-search-cache-test"

    class RouteAgentSearchService:
        caches = []

        def __init__(self, **kwargs: Any) -> None:
            RouteAgentSearchService.caches.append(kwargs.get("result_cache"))

        async def search(self, command: AgentSearchCommand) -> Any:
            return SimpleNamespace(
                query=command.query,
                normalized_query=command.query,
                top_k=command.top_k,
                mode=command.mode,
                results=(),
                metadata={},
                config_applied={},
                elapsed_ms=1,
            )

        async def recommend(self, command: AgentRecommendationCommand) -> Any:
            return SimpleNamespace(
                question=command.question,
                response=CandidateRecommendationResponse(
                    question=command.question,
                    mode=RetrievalMode.EXPERT_DIAGNOSIS,
                ),
                config_applied={},
                elapsed_ms=1,
            )

    monkeypatch.setattr(
        "app.bootstrap.oss_business_routes.AgentSearchService",
        RouteAgentSearchService,
    )
    monkeypatch.setattr(
        "app.adapters.http.recommend_routes.AgentSearchService",
        RouteAgentSearchService,
    )
    monkeypatch.setattr(
        "app.adapters.http.recommend_routes.get_candidate_recommendation_service",
        lambda: RecordingCore(),
    )
    monkeypatch.setattr(
        "app.adapters.http.recommend_routes._rewrite_with_group_context",
        lambda **kwargs: kwargs["question"],
    )

    registry = ProviderRegistry()
    registry.register("config", Config())
    registry.register("secret_provider", object())
    registry.register("startup_provider", Startup())
    registry.register("auth", Auth())
    registry.register("vector_store", VectorStore())
    app = create_bcsfuse_app(
        ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry)
    )

    with TestClient(app) as client:
        search = client.post("/api/v1/search", json={"query": "支付", "top_k": 1})
        recommend = client.post(
            "/api/v1/recommend",
            json={"question": "支付", "topK": 1, "enable_rerank": False},
        )

    assert search.status_code == 200, search.text
    assert recommend.status_code == 200, recommend.text
    shared_cache = app.state.agent_search_result_cache
    assert shared_cache is not None
    assert shared_cache.stats["max_entries"] == 17
    assert shared_cache.stats["ttl_seconds"] == 25.0
    assert RouteAgentSearchService.caches == [shared_cache, shared_cache]
