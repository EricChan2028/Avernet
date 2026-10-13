"""Bound background BCSFuse calls while preserving each Worker's state ordering."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import Context, copy_context
from dataclasses import dataclass
from threading import Condition

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = get_logger()
_MAX_WORKERS = 4
_MAX_TRACKED_WORKERS = 1024


@dataclass(frozen=True)
class _SyncJob:
    context: Context
    sync: Callable[[str, str, str], None]
    bot_id: str
    worker_id: str
    public: str


class BcsFuseRuntimeDispatcher(LifecycleBase):
    """Application-owned pool: one in-flight call and one latest pending state per Worker."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=_MAX_WORKERS, thread_name_prefix="bcsfuse-runtime",
        )
        self._condition = Condition()
        self._running: set[tuple[str, str]] = set()
        self._pending: dict[tuple[str, str], _SyncJob] = {}
        self._closed = False

    def dispatch_runtime_sync(
        self, sync: Callable[[str, str, str], None], bot_id: str, worker_id: str, public: str,
    ) -> None:
        key = (get_current_avernet_tenant(), worker_id)
        job = _SyncJob(copy_context(), sync, bot_id, worker_id, public)
        with self._condition:
            if self._closed or (
                key not in self._running and len(self._running) >= _MAX_TRACKED_WORKERS
            ):
                logger.warning(
                    "[_sync_bcsfuse_runtime_state] Background dispatch rejected: "
                    "bot_id=%s worker_id=%s public=%s reason=%s",
                    bot_id, worker_id, public, "shutdown" if self._closed else "capacity",
                )
                return
            coalesced = key in self._pending
            self._pending[key] = job
            if key not in self._running:
                self._running.add(key)
                self._submit_locked(key)
        logger.info(
            "[_sync_bcsfuse_runtime_state] Scheduled background BCSFuse sync: "
            "bot_id=%s worker_id=%s runtime_state=%s coalesced=%s",
            bot_id, worker_id, "online" if public == "1" else "offline", coalesced,
        )

    def _submit_locked(self, key: tuple[str, str]) -> None:
        def clear_cancelled(future: Future) -> None:
            if future.cancelled():
                with self._condition:
                    self._running.discard(key)
                    self._pending.pop(key, None)
                    self._condition.notify_all()

        try:
            future = self._executor.submit(self._run_worker, key)
            future.add_done_callback(clear_cancelled)
        except RuntimeError:
            self._pending.pop(key, None)
            self._running.discard(key)
            self._condition.notify_all()
            logger.exception(
                "[_sync_bcsfuse_runtime_state] Background dispatch failed: worker_id=%s", key[1],
            )

    def _run_worker(self, key: tuple[str, str]) -> None:
        with self._condition:
            job = self._pending.pop(key, None)
            if self._closed or job is None:
                self._running.discard(key)
                self._condition.notify_all()
                return
        try:
            job.context.run(job.sync, job.bot_id, job.worker_id, job.public)
        except Exception:
            logger.exception(
                "[_sync_bcsfuse_runtime_state] Background BCSFuse sync failed: "
                "bot_id=%s worker_id=%s runtime_state=%s",
                job.bot_id, job.worker_id, "online" if job.public == "1" else "offline",
            )
        finally:
            with self._condition:
                if not self._closed and key in self._pending:
                    # Resubmit at the queue tail so a busy Worker does not monopolize a thread.
                    self._submit_locked(key)
                else:
                    self._running.discard(key)
                    self._condition.notify_all()

    async def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            discarded = len(self._pending)
            self._pending.clear()
        # Cancel queued work; only up to four in-flight HTTP calls retain their existing timeout.
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info(
            "[_sync_bcsfuse_runtime_state] Dispatcher stopped: discarded_workers=%s", discarded,
        )
