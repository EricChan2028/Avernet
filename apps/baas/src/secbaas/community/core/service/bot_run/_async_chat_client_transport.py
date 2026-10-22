"""Transport seam for :class:`AsyncChatClient`.

The orchestrator only talks to a transport object.  ``ws`` and ``a2a``
implementations therefore stay in their own modules and cannot reach into
each other's internals.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol


class AsyncChatTransportOwner(Protocol):
    """Minimal state surface required by a transport implementation."""

    uri: str
    client_id: str
    client_version: str
    headers: dict[str, str]

    @property
    def _client(self) -> Any | None:  # noqa: SLF001
        raise NotImplementedError

    def _make_ws_client(self) -> Any:  # noqa: SLF001
        raise NotImplementedError

    def _a2a_base_url_from_ws(self, ws_uri: str) -> str | None:  # noqa: SLF001
        raise NotImplementedError

    def _event_bridge_snapshot(self) -> dict[str, Any]:  # noqa: SLF001
        raise NotImplementedError

    @property
    def _sessions(self) -> dict[str, Any]:  # noqa: SLF001
        raise NotImplementedError

    @property
    def _ignore_case(self) -> bool:  # noqa: SLF001
        raise NotImplementedError

    @staticmethod
    def _handle_terminal_error(  # noqa: SLF001
        state: Any,
        session_key: str,
        error_msg: str,
        source: str,
        error_code: str = "",
    ) -> None:
        raise NotImplementedError


class AsyncChatTransportBase:
    """Base contract shared by the concrete WS and A2A transports."""

    def __init__(self, owner: AsyncChatTransportOwner) -> None:
        self.owner = owner

    @property
    def transport_name(self) -> str:
        """Actual transport selected by the last successful ``connect``."""
        raise NotImplementedError

    async def create(self) -> tuple[Any, dict[str, Any]]:
        """Create and connect a transport-specific client."""
        raise NotImplementedError

    async def send_target(
        self,
        *,
        session_key: str,
        attachments: list[Any] | None,
        request_kind: Literal["send", "inject"] = "send",
    ) -> Any:
        """Return the client responsible for a chat/inject request."""
        raise NotImplementedError

    async def abort_target(self, *, session_key: str) -> Any:
        """Return the client responsible for ``chat.abort``."""
        raise NotImplementedError

    async def interaction_target(
        self, *, session_key: str
    ) -> tuple[Any, dict[str, Any]]:
        """Return the resolve-interaction client and extra call kwargs."""
        raise NotImplementedError

    def bind_fallback_client(self, client: Any, event_handlers: dict[str, Any]) -> None:
        """Bind an implementation-owned secondary client, if applicable."""
        return None

    async def close(self) -> None:
        """Close implementation-owned secondary clients and state."""
        return None

    def has_active_sessions(self) -> bool:
        """Whether implementation-specific clients keep this entry active."""
        return False

    def tracks_session(self, session_key: str) -> bool:
        """Whether an implementation-specific client tracks ``session_key``."""
        return False

    def release_terminal_ownership(
        self, session_key: str, chat_state: Any, payload: dict[str, Any]
    ) -> None:
        """Release implementation-specific ownership at a terminal frame."""
        return None

    def release_failed_request(self, session_key: str) -> None:
        """Release ownership when the engine never starts the run."""
        return None
