"""Publication returns while BCSFuse HTTP is pending; identity and results stay stable."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
from unittest.mock import MagicMock

import pytest

from tests.community.core.bot_public.test_bot_public_service import (
    _make_bot,
    _make_service,
)

MODULE = "agentclaw.community.core.bot_public.services.bot_public_service"


@pytest.mark.parametrize("scope", [None, "user"])
@pytest.mark.parametrize("public, endpoint", [("1", "online"), ("0", "offline")])
def test_approval_callback_returns_before_bcsfuse_http_finishes(monkeypatch, scope, public, endpoint):
    entered = Event()
    release = Event()
    finished = Event()
    request_marker = ContextVar("publication_test_marker", default="missing")
    requests = []
    observed_markers = []
    config = MagicMock(base_url="http://bcsfuse.test", worker_id_with_owner=True)
    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = _make_bot(ext={
        "public_approval": {
            "puid": "p1", "status": "PROCESSING", "public": public,
            "permission_owner": "caller", "friend_approval": "0",
        },
    })
    bcn = MagicMock()
    bcn.get_attributes.return_value = {"friend_ext": {
        "public_user_approval": {
            "visibility": "public" if public == "1" else "private",
            "status": "PROCESSING",
        },
    }}
    bcn.patch_attributes.return_value = {}
    service = _make_service(bot_repository=repository, bcn_service=bcn, bcsfuse_config=config)
    monkeypatch.setattr(service, "_sync_access_mode_and_relations_or_raise", lambda *a: None)

    def blocking_http(request, *, timeout):
        requests.append(request)
        observed_markers.append(request_marker.get())
        entered.set()
        try:
            assert release.wait(5), "test did not release BCSFuse request"
            response = MagicMock()
            response.__enter__.return_value.status = 200
            return response
        finally:
            finished.set()

    monkeypatch.setattr(f"{MODULE}.urlopen", blocking_http)

    def callback():
        request_marker.set("approval-context")
        return service.handle_public_approval_callback(
            "bot1" if scope is None else "bot1:owner1", "owner1", "p1", "AGREE",
            public_scope=scope,
        )

    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(callback)
        try:
            assert entered.wait(3), "callback did not invoke BCSFuse"
            result = future.result(timeout=0.5)
            assert result["success"] is True
            assert not finished.is_set(), "callback waited for BCSFuse"
            assert requests[0].full_url == f"http://bcsfuse.test/v1/workers/bot1:owner1/{endpoint}"
            assert requests[0].get_method() == "PUT"
            assert observed_markers == ["approval-context"]
            if scope is None:
                assert repository.update_by_owner.call_args.args[2]["public"] == public
            else:
                assert bcn.patch_attributes.call_args.kwargs["body"]["user_visibility"] == (
                    "public" if public == "1" else "private"
                )
        finally:
            release.set()
            assert finished.wait(3)


def test_background_sync_preserves_online_then_offline_order(bcsfuse_executor):
    from agentclaw.community.core.bot_public.services.bcsfuse_runtime_dispatch import (
        dispatch_runtime_sync,
    )

    entered = Event()
    release = Event()
    states = []

    def sync(bot_id, worker_id, public):
        if public == "1":
            entered.set()
            assert release.wait(3)
        states.append(public)

    try:
        dispatch_runtime_sync(sync, "bot1", "bot1:owner1", "1")
        assert entered.wait(3)
        dispatch_runtime_sync(sync, "bot1", "bot1:owner1", "0")
        assert states == []
    finally:
        release.set()
    bcsfuse_executor.submit(lambda: None).result(timeout=3)
    assert states == ["1", "0"]


def test_background_failure_is_logged_without_stopping_later_syncs(bcsfuse_executor, caplog):
    from agentclaw.community.core.bot_public.services.bcsfuse_runtime_dispatch import (
        dispatch_runtime_sync,
    )

    completed = []

    def sync(bot_id, worker_id, public):
        if public == "1":
            raise RuntimeError("notification failed")
        completed.append(worker_id)

    dispatch_runtime_sync(sync, "bot1", "bot1:owner1", "1")
    dispatch_runtime_sync(sync, "bot1", "bot1:owner1", "0")
    bcsfuse_executor.submit(lambda: None).result(timeout=3)
    assert "notification failed" in caplog.text
    assert "worker_id=bot1:owner1" in caplog.text
    assert completed == ["bot1:owner1"]
