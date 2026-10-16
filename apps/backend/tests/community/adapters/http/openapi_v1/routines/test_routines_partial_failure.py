"""Routine listing must distinguish resolver failure from a real empty list."""

from types import SimpleNamespace

import pytest

from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.adapters.http.openapi_v1.routines.router import (
    list_routines,
)
from agentclaw.community.core.cron.errors import CronRelayError


@pytest.mark.asyncio
async def test_list_routines_rejects_partial_runtime_failure():
    """A draft runtime that cannot be read is a 502-shaped cron relay error.

    ``CronRelayServiceProtocol.list_all_crons`` still returns a successful
    aggregate when target resolution fails, placing failures in
    ``failed_targets`` and possibly empty rows in ``data``. The per-bot
    routine route must fail rather than let Bot Workshop render that as
    ``暂无定时任务``.
    """

    class PartialFailureService:
        async def list_all_crons(self, *args, **kwargs):
            return {
                "success": True,
                "data": [],
                "failed_targets": [
                    {"bot_id": "bot-x", "reason": "resolver_failed"},
                ],
            }

    request = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(CronRelayError, match="routine runtime list failed"):
        await list_routines(
            page=PageParams(page=1, page_size=20),
            user_id="u1",
            owner_id="u1",
            bot_id="bot-x",
            status=None,
            factory=PartialFailureService(),
            request=request,
        )
