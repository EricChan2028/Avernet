"""Worker writes finish before vector work, and responses precede background work."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from src.bootstrap import oss_worker_lifecycle_routes as lifecycle
from src.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore,
)
from src.infra.adapters.in_memory_worker_runtime_state_store import (
    InMemoryWorkerRuntimeStateStore,
)
from src.interfaces.api import worker_profile_parity_routes as profile_routes
from src.interfaces.api.worker_profile_routes import analysis, common, sync


async def _invoke(app, method, path, payload=None, timeline=None):
    events = []
    body = json.dumps(payload).encode() if payload is not None else b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 12345), "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            events.append(("status", message["status"]))
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            events.append(("response", json.loads(message["body"])))
            if timeline is not None:
                timeline.append("response")

    await app(scope, receive, send)
    return events


def test_online_returns_before_vector_sync(monkeypatch):
    events = []

    class RuntimeService:
        def set_online(self, worker_id, *, updated_by, defer_vector_sync):
            assert defer_vector_sync is True
            events.append("state_written")
            return worker_id

        def sync_online_vectors_if_current(self, worker_id):
            events.append("vector_synced")

    monkeypatch.setattr(lifecycle, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(lifecycle, "_get_runtime_service", lambda request: RuntimeService())
    monkeypatch.setattr(lifecycle, "_runtime_response", lambda worker: {"worker_id": worker})
    app = FastAPI()
    app.include_router(lifecycle.router)

    async def invoke():
        body_events = await _invoke(app, "PUT", "/v1/workers/bot:owner/online", timeline=events)
        return body_events

    body_events = asyncio.run(invoke())
    assert body_events[0] == ("status", 200)
    assert body_events[1] == ("response", {"worker_id": "bot:owner"})
    assert events == ["state_written", "response", "vector_synced"]


def test_sync_returns_before_index_and_analysis(monkeypatch):
    events = []
    workers = InMemoryWorkerRegistryStore()
    runtime = InMemoryWorkerRuntimeStateStore()
    profiles = MagicMock()
    app = FastAPI()
    app.state.context = SimpleNamespace(registry={
        "worker_registry_store": workers,
        "worker_runtime_state_store": runtime,
        "worker_profile_content_store": profiles,
    })
    app.include_router(profile_routes.compat_router, prefix="/v1")
    monkeypatch.setattr(common, "_require_auth", lambda request: None)
    monkeypatch.setattr("src.application.utils.drm_config_helper.is_capability_verify_enabled", lambda: False)

    def background_vectors(worker_id, worker_store, runtime_store, build_index):
        assert worker_store.get_by_id(worker_id) is not None
        assert profiles.upsert_profile.called
        events.append("vectors")

    async def background_analysis(**kwargs):
        events.append("analysis")

    monkeypatch.setattr(sync, "_sync_vectors_after_response", background_vectors)
    monkeypatch.setattr(analysis, "_analyze_and_persist_async", AsyncMock(side_effect=background_analysis))

    body_events = asyncio.run(_invoke(app, "POST", "/v1/workers/bot:owner/sync", {
        "name": "Bot", "sync_llm": True, "profile": {"profile_id": "default"},
    }, timeline=events))

    assert body_events[0] == ("status", 200)
    assert body_events[1][0] == "response"
    assert body_events[1][1]["success"] is True
    assert events == ["response", "vectors", "analysis"]
