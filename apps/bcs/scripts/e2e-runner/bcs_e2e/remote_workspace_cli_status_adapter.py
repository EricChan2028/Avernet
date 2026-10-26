"""Shared Workspace product-CLI adapter for status and termination leaves."""

from __future__ import annotations

from .case_adapters import AdapterCall
from .run_state import RunStateError
from .workspace_cli_profile import (
    validate_workspace_cli_status_profile,
)


_CASE_CLASSES = {
    "status.group.create": "setup",
    "status.group.read.active": "verify",
    "status.group.status": "write",
    "status.group.read.completed": "verify",
    "termination.group.create": "setup",
    "termination.group.read.active": "verify",
    "termination.group.terminate": "write",
    "termination.group.read.completed": "verify",
}


class RemoteWorkspaceCliStatusAdapter:
    """Drive eight exact Group status/termination leaves through product CLI."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_workspace_cli_status_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Workspace CLI status requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.completion_group_id: str | None = None
        self.termination_group_id: str | None = None

    def _call(self, operation: str, *, group_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("workspace product-CLI status result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("workspace product-CLI status evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = _CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def _require_completion_group(self) -> str:
        if self.completion_group_id is None:
            raise RunStateError("workspace CLI status requires its completion Group first")
        return self.completion_group_id

    def _require_termination_group(self) -> str:
        if self.termination_group_id is None:
            raise RunStateError("workspace CLI termination requires its termination Group first")
        return self.termination_group_id

    def create_retained_completion_group(self) -> AdapterCall:
        if self.completion_group_id is not None:
            raise RunStateError("workspace CLI completion Group creation is one-shot")
        result = self._call("status.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]):
            raise RunStateError("workspace CLI completion Group binding differs")
        self.completion_group_id = data.get("group_id")
        return result

    def read_completion_group_active(self) -> AdapterCall:
        return self._call("status.group.read.active", group_id=self._require_completion_group())

    def update_completion_group_status(self) -> AdapterCall:
        return self._call("status.group.status", group_id=self._require_completion_group())

    def read_completion_group_completed(self) -> AdapterCall:
        return self._call("status.group.read.completed", group_id=self._require_completion_group())

    def create_retained_termination_group(self) -> AdapterCall:
        if self.termination_group_id is not None:
            raise RunStateError("workspace CLI termination Group creation is one-shot")
        result = self._call("termination.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]):
            raise RunStateError("workspace CLI termination Group binding differs")
        self.termination_group_id = data.get("group_id")
        return result

    def read_termination_group_active(self) -> AdapterCall:
        return self._call("termination.group.read.active", group_id=self._require_termination_group())

    def terminate_termination_group(self) -> AdapterCall:
        return self._call("termination.group.terminate", group_id=self._require_termination_group())

    def read_termination_group_completed(self) -> AdapterCall:
        return self._call("termination.group.read.completed", group_id=self._require_termination_group())
