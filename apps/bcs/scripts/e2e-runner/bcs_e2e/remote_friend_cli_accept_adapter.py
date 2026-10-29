"""Cross-environment product-CLI adapter for FRIEND.cli-accept."""

from __future__ import annotations

from .case_adapters import AdapterCall
from .friend_cli_profile import validate_friend_cli_profile
from .run_state import RunStateError


_CASE_CLASSES = {
    "friend.requests.before": "precheck",
    "friend.request": "request",
    "friend.requests.select": "request",
    "friend.accept": "decision",
    "friend.list": "verify",
}


class RemoteFriendCliAcceptAdapter:
    """Drive the exact four business CLI leaves through distinct role contexts."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_friend_cli_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("FRIEND.cli-accept requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.request_id: str | None = None

    def _call(self, operation: str, *, request_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, request_id=request_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("friendship product-CLI result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("friendship product-CLI evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = _CASE_CLASSES[operation]
        evidence["credentials_sent"] = True
        return AdapterCall(data=result["data"], evidence=evidence)

    def read_initial_member_requests(self) -> AdapterCall:
        return self._call("friend.requests.before")

    def request_driver_friendship(self) -> AdapterCall:
        if self.request_id is not None:
            raise RunStateError("friendship request is a one-shot operation")
        result = self._call("friend.request")
        data = result.data
        row = data.get("data") if isinstance(data, dict) else None
        if not isinstance(row, dict):
            raise RunStateError("friendship request returned no exact request data")
        self.request_id = row.get("id")
        return result

    def select_exact_member_request(self) -> AdapterCall:
        if self.request_id is None:
            raise RunStateError("request selection must follow friendship request")
        return self._call("friend.requests.select")

    def accept_member_friend_request(self) -> AdapterCall:
        if self.request_id is None:
            raise RunStateError("request acceptance must follow request selection")
        result = self._call("friend.accept", request_id=self.request_id)
        return result

    def read_final_driver_friend_list(self) -> AdapterCall:
        if self.request_id is None:
            raise RunStateError("final friend list must follow acceptance")
        return self._call("friend.list")
