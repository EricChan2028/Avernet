"""Cross-environment adapter for the source-bound collaboration-team Story."""

from __future__ import annotations

from .case_adapters import AdapterCall
from .collaboration_team_runner import CollaborationTeamRunner
from .run_state import RunStateError


class RemoteCollaborationTeamAdapter:
    """Drive the eight collaboration-team CLI obligations through one Runner."""

    def __init__(self, runner: CollaborationTeamRunner):
        if not isinstance(runner, CollaborationTeamRunner):
            raise RunStateError("collaboration-team requires its product-CLI runner")
        self.runner = runner
        self.driver_id = runner.driver_id
        self.engineering_id = runner.engineering_id
        self.customer_service_id = runner.customer_service_id
        self.qa_id = runner.qa_id
        self.request_id = ""
        self.completed_group_id = ""
        self.terminated_group_id = ""

    def _call(self, operation: str, **kwargs) -> AdapterCall:
        result = self.runner.execute(operation, **kwargs)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("collaboration-team runner result is invalid")
        evidence = dict(result["evidence"])
        evidence["case_operation"] = operation
        return AdapterCall(data=result["data"], evidence=evidence)

    def _formal(self, group_id: str) -> AdapterCall:
        result = self.runner.read_formal_group_page(group_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("collaboration-team formal read result is invalid")
        evidence = dict(result["evidence"])
        evidence["case_operation"] = "collaboration.formal_group_list"
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

    def protect_customer_service_visibility(self) -> AdapterCall:
        protected = self._call("visibility.protected")
        data = protected.data
        if (not isinstance(data, dict)
                or data.get("success") is not True
                or data.get("data") != {
                    "bot_uuid": self.customer_service_id,
                    "visibility": "protected"}):
            raise RunStateError("protected visibility acknowledgement is not exact")
        return self._merge([protected],
                           customer_service_id=self.customer_service_id,
                           protected=True, restored_public=False)

    def request_reject_and_restore_customer_service_friendship(self) -> AdapterCall:
        request = self._call("friend.request")
        row = request.data.get("data") if isinstance(request.data, dict) else None
        if (not isinstance(row, dict)
                or request.data.get("success") is not True
                or row.get("id") in (None, "")
                or row.get("from_bot") != self.engineering_id
                or row.get("to_bot") != self.customer_service_id
                or row.get("status") != "pending"):
            raise RunStateError("friendship request response is not exact")
        self.request_id = row["id"]
        selected = self._call("friend.requests.select")
        matches = [item for item in selected.data if isinstance(item, dict)
                   and item.get("id") == self.request_id
                   and item.get("from_bot") == self.engineering_id
                   and item.get("to_bot") == self.customer_service_id
                   and item.get("status") == "pending"]
        if not isinstance(selected.data, list) or len(matches) != 1:
            raise RunStateError("rejected request cannot be uniquely identified")
        rejected = self._call("friend.reject", request_id=self.request_id)
        if (not isinstance(rejected.data, dict)
                or rejected.data.get("success") is not True):
            raise RunStateError("friendship rejection was not acknowledged")
        final = self._call("friend.list")
        rows = final.data if isinstance(final.data, list) else None
        if rows is None or any(isinstance(row, dict)
                               and row.get("bot_uuid") == self.customer_service_id
                               for row in rows):
            raise RunStateError("rejected agent remains in the CLI friend list")
        restored = self._call("visibility.public")
        data = restored.data
        if (not isinstance(data, dict)
                or data.get("success") is not True
                or data.get("data") != {
                    "bot_uuid": self.customer_service_id,
                    "visibility": "public"}):
            raise RunStateError("public visibility restore is not exact")
        return self._merge([request, selected, rejected, final, restored],
                           request_id=self.request_id,
                           requester=self.engineering_id,
                           target=self.customer_service_id,
                           request_status="pending",
                           rejection_acknowledged=True,
                           requester_final_list_contains_target=False,
                           rejected_request_identifiable=True,
                           customer_service_id=self.customer_service_id,
                           restored_public=True)

    def create_and_read_release_group(self) -> AdapterCall:
        created = self._call("group.create", topic="CLI release coordination")
        if not isinstance(created.data, dict) or not created.data.get("group_id"):
            raise RunStateError("collaboration Group create result is invalid")
        self.completed_group_id = created.data["group_id"]
        read = self._call("group.read.active", group_id=self.completed_group_id)
        if (not isinstance(read.data, dict)
                or read.data.get("id") != self.completed_group_id
                or read.data.get("driver_bot") != self.driver_id):
            raise RunStateError("collaboration Group readback is not exact")
        return self._merge([created, read],
                           group_id=self.completed_group_id,
                           driver=self.driver_id,
                           participants=[self.driver_id, self.engineering_id])

    @staticmethod
    def _participant_ids(data: object) -> list[str]:
        if not isinstance(data, dict) or not isinstance(data.get("participants"), list):
            return []
        return [row.get("bot_uuid") for row in data["participants"]
                if isinstance(row, dict)]

    def add_and_read_release_group_member(self) -> AdapterCall:
        added = self._call("group.add-member", group_id=self.completed_group_id)
        if (not isinstance(added.data, dict)
                or added.data != {"group": self.completed_group_id, "bot": self.qa_id}):
            raise RunStateError("Group member addition result is not exact")
        read = self._call("group.read.after-member", group_id=self.completed_group_id)
        participants = self._participant_ids(read.data)
        expected = {self.driver_id, self.engineering_id, self.qa_id}
        if (set(participants) != expected or len(participants) != len(expected)
                or read.data.get("id") != self.completed_group_id):
            raise RunStateError("expanded Group readback is not exact")
        return self._merge([added, read],
                           group_id=self.completed_group_id,
                           added_member=self.qa_id,
                           participant_ids=participants)

    def compare_group_list_with_formal_api(self) -> AdapterCall:
        listing = self._call("group.list")
        items = listing.data.get("items") if isinstance(listing.data, dict) else None
        cli_group_ids = [row.get("id", row.get("group_id")) for row in items
                         if isinstance(row, dict)] if isinstance(items, list) else []
        if (not isinstance(listing.data, dict) or not isinstance(items, list)
                or listing.data.get("returned") != len(items)
                or self.completed_group_id not in cli_group_ids):
            raise RunStateError("CLI Group listing is invalid")
        formal = self._formal(self.completed_group_id)
        if (not isinstance(formal.data, dict)
                or formal.data.get("item_count") != len(items)
                or formal.data.get("requested_group_id") != self.completed_group_id
                or formal.data.get("requested_group_present") is not True):
            raise RunStateError("formal bot-group count differs from the CLI listing")
        return self._merge([listing, formal],
                           group_id=self.completed_group_id,
                           cli_returned=len(items),
                           api_item_count=formal.data["item_count"],
                           cli_contains_group=True,
                           api_contains_group=True)

    def fuse_release_group(self) -> AdapterCall:
        fused = self._call("group.fuse", group_id=self.completed_group_id)
        if (not isinstance(fused.data, dict)
                or "recommendation" not in fused.data):
            raise RunStateError("Group fusion did not return a recommendation payload")
        return self._merge([fused],
                           group_id=self.completed_group_id,
                           requested_participant_bot_ids=[self.driver_id, self.engineering_id, self.qa_id],
                           recommendation_present=True)

    def complete_release_group(self) -> AdapterCall:
        completed = self._call("group.status", group_id=self.completed_group_id)
        if (not isinstance(completed.data, dict)
                or completed.data.get("updated") is not True
                or completed.data.get("group_id") != self.completed_group_id
                or completed.data.get("status") != "completed"
                or completed.data.get("changed_by") != self.driver_id):
            raise RunStateError("Group completion acknowledgement is not exact")
        read = self._call("group.read.completed", group_id=self.completed_group_id)
        if (not isinstance(read.data, dict)
                or read.data.get("id") != self.completed_group_id
                or read.data.get("status") != "completed"):
            raise RunStateError("completed Group readback is not exact")
        return self._merge([completed, read],
                           group_id=self.completed_group_id,
                           completed=True)

    def terminate_release_group(self) -> AdapterCall:
        created = self._call("termination.group.create", topic="CLI termination drill")
        if not isinstance(created.data, dict) or not created.data.get("group_id"):
            raise RunStateError("termination drill Group create result is invalid")
        self.terminated_group_id = created.data["group_id"]
        terminated = self._call("group.terminate", group_id=self.terminated_group_id)
        if (not isinstance(terminated.data, dict)
                or terminated.data.get("terminated") is not True
                or terminated.data.get("group_id") != self.terminated_group_id
                or terminated.data.get("status") != "completed"
                or terminated.data.get("terminated_by") != self.driver_id):
            raise RunStateError("Group termination acknowledgement is not exact")
        read = self._call("termination.group.read.completed",
                           group_id=self.terminated_group_id)
        if (not isinstance(read.data, dict)
                or read.data.get("id") != self.terminated_group_id
                or read.data.get("status") != "completed"):
            raise RunStateError("terminated Group readback is not exact")
        return self._merge([created, terminated, read],
                           group_id=self.terminated_group_id,
                           terminated=True,
                           completed=True)
