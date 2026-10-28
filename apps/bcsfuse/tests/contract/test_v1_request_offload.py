from __future__ import annotations

import threading
import time
from time import monotonic

from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode


class AcceptingAuthProvider:
    def validate_request(self, request):
        return True


class StartupProvider:
    async def initialize(self):
        return None

    async def shutdown(self):
        return None


class ReadyVectorStore:
    connection = type("Connection", (), {"mode": "server"})()
    collection_name = "worker_profiles"
    index_owner = "pod-a"
    index_version = 1
    index_lag_seconds = 0
    index_stale = False
    read_only = False

    def check_ready(self):
        return True

    @property
    def path(self):
        return "/tmp/bcsfuse-offload-test"


def _build_app(*, with_auth: bool = False):
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", ReadyVectorStore())
    if with_auth:
        registry.register("auth", AcceptingAuthProvider())
    return create_bcsfuse_app(ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry))


def test_v1_search_core_call_does_not_block_the_event_loop(monkeypatch):
    entered = threading.Event()

    class EmbeddingGenerator:
        def embed(self, query: str) -> list[float]:
            entered.set()
            time.sleep(0.5)
            return [1.0]

    class VectorMatchService:
        def match(self, **kwargs) -> list[object]:
            return []

    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_embedding_generator",
        lambda: EmbeddingGenerator(),
    )
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_vector_match_service",
        lambda: VectorMatchService(),
    )

    app = _build_app(with_auth=True)
    with TestClient(app) as client:
        def run_search():
            response = client.post("/api/v1/search", json={"query": "支付", "top_k": 1})
            assert response.status_code == 200
            assert response.json()["results_count"] == 0

        search_thread = threading.Thread(target=run_search)
        search_thread.start()
        try:
            assert entered.wait(timeout=1), "search did not reach its embedding call"
            started = monotonic()
            ready = client.get("/ready")
            elapsed = monotonic() - started
            assert ready.status_code == 200
            assert elapsed < 0.40, f"blocking V1 search blocked /ready for {elapsed:.3f}s"
        finally:
            search_thread.join(timeout=2)
        assert not search_thread.is_alive()


def test_v1_recommend_core_call_does_not_block_the_event_loop(monkeypatch):
    entered = threading.Event()

    class RecommendationService:
        _vector_match_service = None
        _embedding_generator = None

        def recommend(self, **kwargs) -> CandidateRecommendationResponse:
            entered.set()
            time.sleep(0.5)
            return CandidateRecommendationResponse(
                question="支付",
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                metadata={"candidate_source": "fake"},
            )

    monkeypatch.setattr(
        "app.adapters.http.recommend_routes.get_candidate_recommendation_service",
        lambda: RecommendationService(),
    )

    app = _build_app(with_auth=True)
    with TestClient(app) as client:
        def run_recommend():
            response = client.post(
                "/api/v1/recommend",
                json={"question": "支付", "topK": 1, "enable_rerank": False},
            )
            assert response.status_code == 200
            assert response.json()["recommendations"] == []

        recommend_thread = threading.Thread(target=run_recommend)
        recommend_thread.start()
        try:
            assert entered.wait(timeout=1), "recommend did not reach its core service"
            started = monotonic()
            ready = client.get("/ready")
            elapsed = monotonic() - started
            assert ready.status_code == 200
            assert elapsed < 0.40, f"blocking V1 recommend blocked /ready for {elapsed:.3f}s"
        finally:
            recommend_thread.join(timeout=2)
        assert not recommend_thread.is_alive()
