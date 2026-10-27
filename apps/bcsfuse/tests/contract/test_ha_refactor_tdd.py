import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from search_core.adapters.http import create_search_router
from search_core.api.models import VerifiedIdentity
from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry


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
    store = FakeVectorStore(
        owner_id="pod-a", index_version=7, index_lag_seconds=0
    )
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


def test_oversized_filters_are_rejected_before_search_execution():
    class Application:
        async def execute(self, command, identity):
            raise AssertionError("oversized filter payload reached execute()")

    router = create_search_router(Application(), trusted_identity)
    app = FastAPI()
    app.include_router(router)
    oversized = {f"tag{i}": ["x"] for i in range(100001)}

    with TestClient(app) as client:
        response = client.post(
            "/api/v2/search",
            json={"query": "助手", "limit": 5, "filters": oversized},
        )

    assert response.status_code in {413, 422}
    assert response.json()["detail"] is not None
