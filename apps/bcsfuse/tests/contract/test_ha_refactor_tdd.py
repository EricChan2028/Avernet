import re

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from search_core.adapters.http import create_search_router
from search_core.api.models import VerifiedIdentity
from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.embedding.providers.real_provider import RealEmbeddingProvider


@pytest.fixture(autouse=True)
def disable_background_index(monkeypatch):
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()
    yield
    FeatureFlags.reset()


class StartupProvider:
    async def initialize(self):
        return None

    async def shutdown(self):
        return None


class AcceptingAuthProvider:
    def validate_request(self, request):
        return True


class FakeVectorStore:
    def __init__(
        self,
        *,
        owner_id,
        index_version,
        index_lag_seconds,
        index_stale=False,
        is_ready=True,
        read_only=False,
    ):
        self.connection = type("Connection", (), {"mode": "server"})()
        self.collection_name = "worker_profiles"
        self.index_owner = owner_id
        self.index_version = index_version
        self.index_lag_seconds = index_lag_seconds
        self.index_stale = index_stale
        self.check_ready = lambda: is_ready
        self.read_only = read_only

    @property
    def path(self):
        return "/tmp/ha/%s" % self.index_owner


def build_runtime_context(vector_store):
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", vector_store)
    return ApplicationContext(
        mode="runtime", startup_profile="opensource", registry=registry
    )


async def trusted_identity(request: Request):
    return VerifiedIdentity("authenticated-caller", None)


def test_ready_identifier_is_visible_with_index_owner_and_lag():
    store = FakeVectorStore(owner_id="pod-a", index_version=7, index_lag_seconds=0)
    app = create_bcsfuse_app(build_runtime_context(store))

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["index_owner"] == "pod-a"
    assert body["index_version"] == 7
    assert body["index_lag_seconds"] == 0
    assert body["index_stale"] is False


def test_ready_distinguishes_primary_writer_from_stale_read_only_replica():
    stale_store = FakeVectorStore(
        owner_id="pod-b",
        index_version=7,
        index_lag_seconds=61,
        index_stale=True,
        is_ready=False,
        read_only=True,
    )
    app = create_bcsfuse_app(build_runtime_context(stale_store))

    with TestClient(app) as client:
        response = client.get("/ready")

    body = response.json()
    assert response.status_code == 503
    assert body["index_stale"] is True
    assert body["index_lag_seconds"] == 61
    assert body["read_only"] is True
    assert body["index_owner"] == "pod-b"


def test_unauthenticated_recommend_is_rejected_before_service_execution():
    # No ``auth`` provider is registered; a read-only store is intentional here.
    store = FakeVectorStore(owner_id="pod-a", index_version=7, index_lag_seconds=0)
    app = create_bcsfuse_app(build_runtime_context(store))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend", json={"question": "支付助手", "topK": 1}
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_read_only_replica_rejects_worker_writes_with_a_stable_contract():
    store = FakeVectorStore(
        owner_id="pod-b",
        index_version=7,
        index_lag_seconds=0,
        read_only=True,
    )
    context = build_runtime_context(store)
    context.registry.register("auth", AcceptingAuthProvider())
    app = create_bcsfuse_app(context)

    with TestClient(app) as client:
        response = client.post(
            "/v1/workers",
            json={
                "id": "bot:reader-write",
                "name": "Reader Write",
                "type": "bot",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INDEX_READ_ONLY"


def test_metrics_expose_index_owner_and_staleness_for_each_replica():
    store = FakeVectorStore(
        owner_id="pod-a",
        index_version=7,
        index_lag_seconds=3,
    )
    app = create_bcsfuse_app(build_runtime_context(store))

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    text = response.text
    for expected in [
        r'bcsfuse_index_ready\{[^}]*collection="worker_profiles"[^}]*owner="pod-a"[^}]*\} 1',
        r'bcsfuse_index_stale\{[^}]*collection="worker_profiles"[^}]*owner="pod-a"[^}]*\} 0',
        r'bcsfuse_index_lag_seconds\{[^}]*collection="worker_profiles"[^}]*owner="pod-a"[^}]*\} 3(?:\.0)?',
    ]:
        assert re.search(expected, text), expected


def test_oversized_filters_are_rejected_before_search_execution():
    class Application:
        async def execute(self, command, identity):
            raise AssertionError("oversized filter payload reached execute()")

    router = create_search_router(Application(), trusted_identity)
    app = FastAPI()
    app.include_router(router)
    oversized = {f"tag{i}": ["x"] for i in range(17)}

    with TestClient(app) as client:
        response = client.post(
            "/api/v2/search",
            json={"query": "助手", "limit": 5, "filters": oversized},
        )

    assert response.status_code in {413, 422}
    assert response.json()["detail"] is not None


def test_embedding_provider_has_no_default_inter_request_sleep(monkeypatch):
    provider = RealEmbeddingProvider(
        settings=EmbeddingSettings(
            base_url="http://127.0.0.1:1/v1",
            auth_token="test-only",
            model="controlled-embedding",
            dimension=64,
        ),
        max_retries=0,
    )

    def reject_sleep(_):
        raise AssertionError("provider attempted an inter-request sleep")

    monkeypatch.setattr(
        "app.infra.embedding.providers.real_provider.time.sleep", reject_sleep
    )

    def respond(request):
        return httpx.Response(
            200, json={"data": [{"embedding": [0.25] * 64}]}
        )

    provider._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert provider.embed("first") == [0.25] * 64
        assert provider.embed("second") == [0.25] * 64
    finally:
        provider.close()
