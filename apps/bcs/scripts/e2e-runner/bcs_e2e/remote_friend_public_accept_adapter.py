"""Dual-environment adapter for public auto-acceptance and its friend list."""

from __future__ import annotations

from .case_adapters import AdapterCall
from .profile import ConfigError
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

_BINDINGS = (
    "friend_connection_public_accept.create",
    "friend_connection_public_accept.driver_connections.read",
)


class RemoteFriendPublicAcceptAdapter:
    """Create one exact public friendship and independently read the friend list."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("public friendship auto-acceptance requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"):
            raise RunStateError("public auto-acceptance requires a public-v1 Singlebox or gateway profile")
        if profile.get("relationships", {}).get("friendship") != "bounded-owned-bot-public-acceptance":
            raise RunStateError("public auto-acceptance requires explicit bounded effect admission")
        try:
            for binding_name in _BINDINGS:
                require_edge_case_binding(profile, binding_name)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.profile = profile
        self.driver_actor_id = profile["actors"]["driver"]["bot_id"]
        self.member_actor_id = profile["actors"]["member"]["bot_id"]
        self.client = ReadClient(profile)
        self.writer = WriteClient(profile)
        self._executed = False

    @staticmethod
    def _actor(actor_id: str) -> dict:
        return {"type": "bot", "id": actor_id}

    def _friend_count(self, *, operation: str) -> tuple[int, dict]:
        observation = self.client.read(operation)
        page, record = observation.data, observation.evidence
        items = page.get("items") if isinstance(page, dict) else None
        if (not isinstance(items, list) or len(items) > 20
                or page.get("page") != 1 or page.get("page_size") != 20
                or type(page.get("total")) is not int):
            raise RunStateError("friend-connection page is outside the bounded contract")
        matches = [item for item in items
                   if isinstance(item, dict)
                   and item.get("actor") == self._actor(self.member_actor_id)]
        if len(matches) > 1:
            raise RunStateError("the exact member appears more than once in the driver friend list")
        return len(matches), record

    def auto_accept_public_friend_connection(self) -> AdapterCall:
        if self._executed:
            raise RunStateError("public friendship auto-acceptance is a one-shot operation")
        count_before, before_record = self._friend_count(
            operation="friend.public.driver_connections.read")
        if count_before:
            raise RunStateError("the exact owned Bot pair is already friends; no idempotent pass")
        create, create_record = self.writer.create_public_friend_connection_request(
            self.driver_actor_id, self.member_actor_id
        )
        request_ids = create.get("request_ids")
        edge_ids = create.get("edge_ids")
        if (create.get("status") != "approved"
                or create.get("auto_accepted") is not True
                or not isinstance(request_ids, list) or len(request_ids) != 2
                or any(not isinstance(value, str) or not value for value in request_ids)
                or len(set(request_ids)) != 2
                or not isinstance(edge_ids, list) or len(edge_ids) != 2
                or any(type(value) is not int or value < 1 for value in edge_ids)
                or len(set(edge_ids)) != 2):
            raise RunStateError("public friendship create was not the expected exact Bot-Bot auto-acceptance")
        count_after, after_record = self._friend_count(
            operation="friend.public.driver_connections.read")
        if count_after != 1:
            raise RunStateError("public auto-acceptance was not visible in the exact driver friend list")
        data = {
            "status": create["status"],
            "auto_accepted": True,
            "friendship_visible_before": False,
            "friendship_visible_after": True,
            "request_count": len(request_ids),
            "edge_count": len(edge_ids),
            "from_actor_id": self.driver_actor_id,
            "to_actor_id": self.member_actor_id,
        }
        evidence = {
            "transport": "public-v1-http",
            "operation": "friend.public.create",
            "fixture_effect": "BOUNDED_OWNED_BOTS_RETAINED_PUBLIC_ACCEPTANCE",
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "projection": data,
            "requests": [before_record, create_record, after_record],
        }
        secrets = {**self.client.headers, **self.writer.headers}
        if any(secret and secret in repr(evidence) for secret in secrets.values()):
            raise RunStateError("public auto-acceptance evidence must not expose a credential")
        self._executed = True
        return AdapterCall(data=data, evidence=evidence)

    def read_established_friend_list(self) -> AdapterCall:
        if not self._executed:
            raise RunStateError("public friend-list read must follow established auto-acceptance")
        friend_count, record = self._friend_count(
            operation="friend.public.driver_connections.read")
        if friend_count != 1:
            raise RunStateError("the established public friend list is outside the bounded contract")
        data = {
            "driver_actor_id": self.driver_actor_id,
            "friend_actor_id": self.member_actor_id,
            "list_contains_friend": True,
            "friend_count": friend_count,
        }
        evidence = {
            "transport": "public-v1-http",
            "operation": "friend.public.driver_connections.read",
            "fixture_effect": "BOUNDED_OWNED_BOTS_RETAINED_PUBLIC_ACCEPTANCE",
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "projection": data,
            "requests": [record],
        }
        secrets = {**self.client.headers, **self.writer.headers}
        if any(secret and secret in repr(evidence) for secret in secrets.values()):
            raise RunStateError("public friend-list evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)
