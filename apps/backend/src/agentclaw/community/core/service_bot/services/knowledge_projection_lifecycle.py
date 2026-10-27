"""Bounded due-only reconciliation of optional knowledge effects."""

from __future__ import annotations

import asyncio

from injector import inject

from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()


class KnowledgeProjectionLifecycle(LifecycleBase):
    @inject
    def __init__(
        self, repo: BotPublishRepositoryProtocol, policy: KnowledgePublicationPolicy
    ):
        self._repo = repo
        self._policy = policy
        self._task = None
        self._stop = None

    async def startup(self):
        if self._policy.enabled and self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    async def shutdown(self):
        if self._task is not None:
            self._stop.set()
            # Drain the bounded current DB batch before infrastructure teardown.
            # Cancelling to_thread would only cancel the await, not its thread.
            await self._task
            self._task = None
            self._stop = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._repo.reconcile_knowledge_projection)
            except Exception:
                logger.exception("Knowledge projection discovery failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
            except TimeoutError:
                pass
