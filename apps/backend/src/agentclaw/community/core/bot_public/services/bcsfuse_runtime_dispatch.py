"""Run best-effort BCSFuse notifications outside publication requests."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context

from agentclaw.community.log import get_logger

logger = get_logger()

# A single worker preserves submission order, including online then offline.
# HTTP keeps its existing timeout; publication requests never wait on the queue.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bcsfuse-runtime")


def dispatch_runtime_sync(
    sync: Callable[[str, str, str], None], bot_id: str, worker_id: str, public: str,
) -> None:
    """Carry request context and log dispatch failures without changing publication."""
    runtime_state = "online" if public == "1" else "offline"

    def log_failure(future: Future) -> None:
        error = future.exception()
        if error is not None:
            logger.warning(
                "[_sync_bcsfuse_runtime_state] Background BCSFuse sync failed: "
                "bot_id=%s worker_id=%s runtime_state=%s error=%s",
                bot_id, worker_id, runtime_state, error,
            )

    try:
        future = _executor.submit(copy_context().run, sync, bot_id, worker_id, public)
        future.add_done_callback(log_failure)
        logger.info(
            "[_sync_bcsfuse_runtime_state] Scheduled background BCSFuse sync: "
            "bot_id=%s worker_id=%s runtime_state=%s",
            bot_id, worker_id, runtime_state,
        )
    except RuntimeError as error:
        logger.warning(
            "[_sync_bcsfuse_runtime_state] Background BCSFuse dispatch failed: "
            "bot_id=%s worker_id=%s runtime_state=%s error=%s",
            bot_id, worker_id, runtime_state, error,
        )
