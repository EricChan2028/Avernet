"""Bounded worker capacity dedicated to search and recommendation delivery."""

from __future__ import annotations

import functools
from typing import Any

from anyio import to_thread

_search_thread_limiter: to_thread.CapacityLimiter = to_thread.CapacityLimiter(64)


def configure_search_thread_capacity(max_workers: int) -> None:
    """Align the dedicated search worker pool with process admission."""
    global _search_thread_limiter
    if max_workers <= 0:
        raise ValueError("search thread capacity must be positive")
    _search_thread_limiter = to_thread.CapacityLimiter(max_workers)


def get_search_thread_capacity() -> int:
    """Return the configured dedicated search worker capacity."""
    return int(_search_thread_limiter.total_tokens)


def run_search_in_threadpool(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run admitted search work on its own bounded pool.

    This coroutine deliberately returns an awaitable to match
    Starlette's run_in_threadpool. It keeps admission, rejected with 429 by
    SearchCapacityMiddleware, aligned with the available search workers.
    """
    call = functools.partial(func, *args, **kwargs)
    return to_thread.run_sync(call, limiter=_search_thread_limiter)
