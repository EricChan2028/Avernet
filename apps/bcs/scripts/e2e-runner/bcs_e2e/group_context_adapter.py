"""Cross-environment adapter for the source-bound Group Context CLI Story."""
from __future__ import annotations

from .case_adapters import AdapterCall
from .collaboration_team_runner import CollaborationTeamRunner
from .run_state import RunStateError


class RemoteGroupContextAdapter:
    """Drive the nine Group Context obligations through one product CLI Runner."""

    def __init__(self, runner: CollaborationTeamRunner):
        if not isinstance(runner, CollaborationTeamRunner):
            raise RunStateError("Group Context requires its product-CLI runner")
        self.runner = runner
        self.driver_id = runner.driver_id

    def _call(self, operation: str, **kwargs) -> AdapterCall:
        result = self.runner.execute(operation, **kwargs)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("Group Context runner result is invalid")
        evidence = dict(result["evidence"])
        evidence["case_operation"] = operation
        return AdapterCall(data=result["data"], evidence=evidence)

    @staticmethod
    def _merge(records: list[AdapterCall], **data) -> AdapterCall:
        return AdapterCall(
            data=data,
            evidence={
                "transport": records[0].evidence["transport"],
                "requests": [dict(call.evidence) for call in records],
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False,
            },
        )

    def create_workspace(self) -> AdapterCall:
        created = self._call("group_context.workspace",
                             topic="CLI group-context workspace")
        if not isinstance(created.data, dict) or not created.data.get("group_id"):
            raise RunStateError("Group Context workspace create result is invalid")
        if created.data.get("driver_bot") != self.driver_id:
            raise RunStateError("Group Context workspace driver differs")
        return self._merge([created],
                           group_id=created.data["group_id"],
                           session_id=created.data["session_id"])

    def add_group_context_entry(self) -> AdapterCall:
        added = self._call("group_context.add.group")
        if not isinstance(added.data, dict) or added.data.get("status") != "ok":
            raise RunStateError("Group Context group add acknowledgement is not exact")
        return self._merge([added], scope_level="group",
                           context_name=self.runner._group_context["ctx_name"],
                           content=self.runner._group_context["group_content"])

    def add_session_context_entry(self) -> AdapterCall:
        added = self._call("group_context.add.session")
        if not isinstance(added.data, dict) or added.data.get("status") != "ok":
            raise RunStateError("Group Context session add acknowledgement is not exact")
        return self._merge([added], scope_level="session",
                           context_name=self.runner._group_context["ctx_name"],
                           content=self.runner._group_context["session_content"])

    def add_round_context_entry(self) -> AdapterCall:
        added = self._call("group_context.add.round")
        if not isinstance(added.data, dict) or added.data.get("status") != "ok":
            raise RunStateError("Group Context round add acknowledgement is not exact")
        return self._merge([added], scope_level="round",
                           context_name=self.runner._group_context["ctx_name"],
                           content=self.runner._group_context["round_content"])

    def _get_context_items(self, call: AdapterCall) -> list[dict]:
        if not isinstance(call.data, dict) or not isinstance(call.data.get("items"), list):
            raise RunStateError("Group Context get response is invalid")
        return [item for item in call.data["items"] if isinstance(item, dict)]

    def get_initial_context(self) -> AdapterCall:
        call = self._call("group_context.get.initial")
        rows = self._get_context_items(call)
        name = self.runner._group_context["ctx_name"]
        content = self.runner._group_context["group_content"]
        if not any(item.get("name") == name and item.get("content") == content for item in rows):
            raise RunStateError("Group Context initial get is not exact")
        return self._merge([call], context_name=name, content=content)

    def update_group_context_entry(self) -> AdapterCall:
        updated = self._call("group_context.update.group")
        if not isinstance(updated.data, dict) or updated.data.get("status") != "ok":
            raise RunStateError("Group Context update acknowledgement is not exact")
        return self._merge([updated],
                           context_name=self.runner._group_context["ctx_name"],
                           content=self.runner._group_context["group_updated"])

    def list_context_entries(self) -> AdapterCall:
        listing = self._call("group_context.list.entries")
        if not isinstance(listing.data, dict) or not isinstance(listing.data.get("contexts"), list):
            raise RunStateError("Group Context list response is invalid")
        name = self.runner._group_context["ctx_name"]
        rows = [row for row in listing.data["contexts"]
                if isinstance(row, dict) and row.get("name") == name]
        if not rows or not any(row.get("permission") == "WRO" for row in rows):
            raise RunStateError("Group Context list is not exact")
        return self._merge([listing], context_name=name,
                           visible_permission="WRO",
                           returned_contexts=len(listing.data["contexts"]))

    def get_updated_context(self) -> AdapterCall:
        call = self._call("group_context.get.updated")
        rows = self._get_context_items(call)
        name = self.runner._group_context["ctx_name"]
        updated = self.runner._group_context["group_updated"]
        if not any(item.get("name") == name and item.get("content") == updated for item in rows):
            raise RunStateError("Group Context updated get is not exact")
        return self._merge([call], context_name=name, content=updated)

    def cleanup_group_context(self) -> AdapterCall:
        result = self.runner.delete_group(self.runner._group_context["group_id"])
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("Group Context cleanup result is invalid")
        evidence = dict(result["evidence"])
        evidence["case_operation"] = "group_context.cleanup"
        call = AdapterCall(data=result["data"], evidence=evidence)
        if not isinstance(call.data, dict) or call.data.get("deleted") is not True:
            raise RunStateError("Group Context cleanup acknowledgement is not exact")
        return call
