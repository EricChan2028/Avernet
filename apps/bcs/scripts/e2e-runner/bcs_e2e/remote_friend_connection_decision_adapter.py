"""Dual-environment adapter for two exact friend-connection decisions.

The cross-environment contract uses the public-v1 friend-connection route with the
Human Cookie that owns both dedicated Bots. It performs no fallback, no retry, no
reverse identity, and leaves both the rejected request and accepted friendship in place.
"""

from __future__ import annotations

from .case_adapters import AdapterCall
from .profile import ConfigError
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

_BINDINGS = (
    "friend_connection_decision.reject.create",
    "friend_connection_decision.accept.create",
    "friend_connection_decision.member_requests.read",
    "friend_connection_decision.reject",
    "friend_connection_decision.driver_requests.read",
    "friend_connection_decision.accept",
    "friend_connection_decision.driver_connections.read",
)


class RemoteFriendConnectionDecisionAdapter:
    """Perform only the reviewed reject-then-accept owned-Bot sequence."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("friend-connection decisions require an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"):
            raise RunStateError("friend-connection decisions require a public-v1 Singlebox or gateway profile")
        if profile.get("relationships", {}).get("friendship") != "bounded-owned-bot-decisions":
            raise RunStateError("friend-connection decisions require explicit bounded effect admission")
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
        self._rejected = False

    @staticmethod
    def _actor(actor_id: str) -> dict:
        return {"type": "bot", "id": actor_id}

    def _create(self, from_actor_id: str, to_actor_id: str) -> tuple[list[str], dict]:
        data, record = self.writer.create_friend_connection_request(
            from_actor_id, to_actor_id
        )
        if (data.get("status") != "pending"
                or data.get("auto_accepted") is not False
                or data.get("edge_ids") != []
                or not isinstance(data.get("request_ids"), list)
                or not 1 <= len(data["request_ids"]) <= 2
                or any(not isinstance(value, str) or not value
                       for value in data["request_ids"])
                or len(set(data["request_ids"])) != len(data["request_ids"])):
            raise RunStateError("friend-connection create did not return an exact finite pending request")
        return data["request_ids"], record

    def _select_request(self, *, operation: str, receiver: str,
                        sender: str, candidates: list[str]) -> tuple[str, dict]:
        observation = self.client.read(operation)
        page, record = observation.data, observation.evidence
        items = page.get("items") if isinstance(page, dict) else None
        if (not isinstance(items, list) or len(items) > 20
                or page.get("page") != 1 or page.get("page_size") != 20
                or type(page.get("total")) is not int):
            raise RunStateError("friend-connection request page is outside the bounded contract")
        matches = [
            item for item in items
            if isinstance(item, dict)
            and item.get("request_id") in candidates
            and item.get("from_actor") == self._actor(sender)
            and item.get("to_actor") == self._actor(receiver)
            and item.get("status") == "pending"
        ]
        if len(matches) != 1 or matches[0].get("request_id") not in candidates:
            raise RunStateError("the exact newly created pending request was not uniquely found")
        return matches[0]["request_id"], record

    def _connections(self, *, peer: str) -> tuple[bool, dict]:
        observation = self.client.read("friend.decision.driver_connections.read")
        page, record = observation.data, observation.evidence
        items = page.get("items") if isinstance(page, dict) else None
        if (not isinstance(items, list) or len(items) > 20
                or page.get("page") != 1 or page.get("page_size") != 20
                or type(page.get("total")) is not int):
            raise RunStateError("friend-connection page is outside the bounded contract")
        matches = [item for item in items
                   if isinstance(item, dict)
                   and item.get("actor") == self._actor(peer)]
        return len(matches) == 1, record

    def _decide(self, *, decision: str, sender: str, receiver: str,
                read_operation: str) -> AdapterCall:
        candidates, create_record = self._create(sender, receiver)
        request_id, read_record = self._select_request(
            operation=read_operation, receiver=receiver, sender=sender,
            candidates=candidates,
        )
        decision_data, decision_record = self.writer.decision_friend_connection_request(
            decision=decision, request_id=request_id,
            from_actor_id=sender, to_actor_id=receiver,
        )
        if (decision_data.get("request_id") != request_id
                or decision_data.get("from_actor") != self._actor(sender)
                or decision_data.get("to_actor") != self._actor(receiver)
                or decision_data.get("status") != (
                    "approved" if decision == "accept" else "rejected")
                or decision_data.get("decided_by") != self._actor(receiver)):
            raise RunStateError("friend-connection decision did not acknowledge the exact request")
        visible, connection_record = self._connections(peer=self.member_actor_id)
        expected_visible = decision == "accept"
        if visible is not expected_visible:
            raise RunStateError("friend-connection list readback differs from the decision")
        data = {
            "request_id": request_id,
            "status": decision_data["status"],
            "friendship_visible": visible,
            "from_actor_id": sender,
            "to_actor_id": receiver,
        }
        evidence = {
            "transport": "public-v1-http",
            "operation": f"friend.decision.{decision}",
            "fixture_effect": "BOUNDED_OWNED_BOTS_RETAINED_DECISIONS",
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "projection": data,
            "requests": [create_record, read_record, decision_record,
                         connection_record],
        }
        secrets = {**self.client.headers, **self.writer.headers}
        if any(secret and secret in repr(evidence) for secret in secrets.values()):
            raise RunStateError("friend-connection evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def reject_friend_connection_request(self) -> AdapterCall:
        if self._rejected:
            raise RunStateError("friend-connection rejection is a one-shot operation")
        result = self._decide(
            decision="reject", sender=self.driver_actor_id,
            receiver=self.member_actor_id,
            read_operation="friend.decision.member_requests.read",
        )
        self._rejected = True
        return result

    def accept_friend_connection_request(self) -> AdapterCall:
        if not self._rejected:
            raise RunStateError("friend-connection acceptance must follow rejection")
        return self._decide(
            decision="accept", sender=self.member_actor_id,
            receiver=self.driver_actor_id,
            read_operation="friend.decision.driver_requests.read",
        )
