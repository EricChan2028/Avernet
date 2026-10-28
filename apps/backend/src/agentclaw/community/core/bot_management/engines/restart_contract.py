"""Core-internal collaborators supplied to engine-owned restart execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService


@dataclass(frozen=True)
class RestartServices:
    repository: Any
    task_queue: Any
    get_bot: Callable[[str, str], dict]
    template_service: Any
    # Existing BotService instance, reused by the engine-owned restart executor.
    lifecycle: BotService


class RestartPreconditionError(RuntimeError):
    """An engine vetoed restart before mutation; message is safe for users.

    Callers must preserve the old target and expose their existing failure
    state. This is terminal, unlike an ambiguous provider submission failure.
    """
