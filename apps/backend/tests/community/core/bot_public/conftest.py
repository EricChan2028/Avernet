"""Keep application-owned BCSFuse background tasks isolated between tests."""

import asyncio

import pytest
from agentclaw.community.core.bot_public.services.bcsfuse_runtime_dispatch import (
    BcsFuseRuntimeDispatcher,
)


@pytest.fixture(autouse=True)
def bcsfuse_dispatcher(monkeypatch):
    from tests.community.core.bot_public import test_bot_public_service

    dispatcher = BcsFuseRuntimeDispatcher()
    monkeypatch.setattr(test_bot_public_service, "_TEST_DISPATCHER", dispatcher)
    yield dispatcher
    asyncio.run(dispatcher.shutdown())
    dispatcher._executor.shutdown(wait=True, cancel_futures=True)
