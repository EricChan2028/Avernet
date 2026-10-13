"""Keep publication background HTTP tasks isolated between tests."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from agentclaw.community.core.bot_public.services import bcsfuse_runtime_dispatch


@pytest.fixture(autouse=True)
def bcsfuse_executor(monkeypatch):
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(bcsfuse_runtime_dispatch, "_executor", executor)
        yield executor
