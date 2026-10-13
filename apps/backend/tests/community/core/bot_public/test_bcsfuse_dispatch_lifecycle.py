"""Coalesce per-Worker state, bound admission, and cancel queued calls on shutdown."""

import asyncio
from contextvars import ContextVar
from threading import Barrier, Event

from agentclaw.community.core.bot_public.services import bcsfuse_runtime_dispatch
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

from tests.community.core.bot_public.test_bot_public_service import (
    _wait_for_bcsfuse_sync,
)


def test_same_worker_coalesces_pending_states_with_latest_context(bcsfuse_dispatcher):
    entered = Event()
    release = Event()
    marker = ContextVar("notification_marker", default="missing")
    observed = []

    def sync(bot_id, worker_id, public):
        observed.append((public, marker.get()))
        if len(observed) == 1:
            entered.set()
            assert release.wait(3)

    try:
        marker.set("first")
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "bot", "bot:owner", "1")
        assert entered.wait(2)
        marker.set("superseded")
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "bot", "bot:owner", "0")
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "bot", "bot:owner", "1")
        marker.set("latest")
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "bot", "bot:owner", "0")
        assert observed == [("1", "first")]
    finally:
        release.set()
        _wait_for_bcsfuse_sync()
    assert observed == [("1", "first"), ("0", "latest")]


def test_shutdown_cancels_pending_calls_and_rejects_new_work(bcsfuse_dispatcher, caplog):
    entered = Barrier(5)
    release = Event()
    finished = Event()
    queued_called = Event()

    def sync(bot_id, worker_id, public):
        if bot_id.startswith("active"):
            entered.wait(timeout=3)
            try:
                assert release.wait(3)
            finally:
                finished.set()
        else:
            queued_called.set()

    try:
        for i in range(4):
            bcsfuse_dispatcher.dispatch_runtime_sync(sync, f"active{i}", f"active{i}:owner", "1")
        entered.wait(timeout=3)
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "queued", "queued:owner", "0")
        asyncio.run(bcsfuse_dispatcher.shutdown())
        assert not finished.is_set(), "shutdown waited for blocked HTTP calls"
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "late", "late:owner", "1")
        assert "reason=shutdown" in caplog.text
    finally:
        release.set()
        bcsfuse_dispatcher._executor.shutdown(wait=True, cancel_futures=True)
    assert not queued_called.is_set()


def test_worker_capacity_keeps_latest_state_for_existing_workers(
    bcsfuse_dispatcher, monkeypatch, caplog,
):
    monkeypatch.setattr(bcsfuse_runtime_dispatch, "_MAX_TRACKED_WORKERS", 1)
    entered = Event()
    release = Event()
    observed = []

    def sync(bot_id, worker_id, public):
        observed.append((worker_id, public))
        if public == "1":
            entered.set()
            assert release.wait(3)

    try:
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "bot", "bot:owner", "1")
        assert entered.wait(2)
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "new", "new:owner", "0")
        bcsfuse_dispatcher.dispatch_runtime_sync(sync, "bot", "bot:owner", "0")
        assert "reason=capacity" in caplog.text
    finally:
        release.set()
        _wait_for_bcsfuse_sync()
    assert observed == [("bot:owner", "1"), ("bot:owner", "0")]


def test_same_downstream_worker_serializes_across_tenants_with_original_context(bcsfuse_dispatcher):
    entered = Event()
    offline_finished = Event()
    release = Event()
    observed = []

    def sync(bot_id, worker_id, public):
        if public == "1":
            entered.set()
            assert release.wait(3)
        observed.append((public, get_current_avernet_tenant()))
        if public == "0":
            offline_finished.set()

    try:
        with avernet_tenant_scope("tenant-a"):
            bcsfuse_dispatcher.dispatch_runtime_sync(sync, "slow", "same-worker", "1")
        assert entered.wait(2)
        with avernet_tenant_scope("tenant-b"):
            bcsfuse_dispatcher.dispatch_runtime_sync(sync, "other", "same-worker", "0")
        assert not offline_finished.wait(0.2), "same downstream Worker was updated concurrently"
    finally:
        release.set()
        _wait_for_bcsfuse_sync()
    assert observed == [("1", "tenant-a"), ("0", "tenant-b")]
