"""Independent LocalMock inventory reconciliation against a Run's journal.

Both files must be private/trusted. This detects ledger drift, not adversaries
who can rewrite the ledger and the independent mock snapshot together.
"""
from __future__ import annotations

from .mock_state import DELETION_NAMES, RESOURCE_NAMES, MockStateError


_KIND_NAMES = {"group": "groups", "session": "sessions", "file": "files"}


def verify_snapshot_scope(snapshot: dict, run_id: str, records: list[dict]) -> dict:
    if snapshot["run_id"] != run_id:
        raise MockStateError("cleanup inventory belongs to another Run")
    by_handle = {row["handle"]: row for row in records}
    declared = {name: set() for name in RESOURCE_NAMES}
    for row in records:
        name = _KIND_NAMES.get(row["kind"])
        resource_id = row["resource_id"]
        if (name is None or row["origin"] != "created" or row["state"] not in {"live", "deleted"}
                or resource_id not in snapshot[name]):
            raise MockStateError("cleanup resource has no independent created-resource proof")
        proof = snapshot["provenance"][name][resource_id]
        if (proof["origin"] != "created" or proof["case_id"] != row["case_id"]
                or proof["attempt"] != row["attempt"]):
            raise MockStateError("cleanup creation provenance disagrees with the ledger")
        expected_parent = None
        if name != "groups":
            parent = by_handle.get(row["parent"])
            if parent is None or parent["kind"] != ("group" if name == "sessions" else "session"):
                raise MockStateError("cleanup ledger parent scope is invalid")
            expected_parent = parent["resource_id"]
        elif row["parent"] is not None:
            raise MockStateError("cleanup group cannot have a parent")
        if proof["parent_id"] != expected_parent:
            raise MockStateError("cleanup resource belongs to a different parent")
        if row["state"] == "deleted" and resource_id not in snapshot[DELETION_NAMES[name]]:
            raise MockStateError("ledger deletion has no independent deletion evidence")
        declared[name].add(resource_id)
    for name in RESOURCE_NAMES:
        if declared[name] != set(snapshot[name]):
            raise MockStateError("unaccounted resources remain in the Run's local mock inventory")
    for session_id, value in snapshot["sessions"].items():
        if session_id not in snapshot["deleted_sessions"] and value["group_id"] in snapshot["deleted"]:
            raise MockStateError("a deleted mock group still has a live session")
    for file_id, value in snapshot["files"].items():
        if file_id not in snapshot["deleted_files"] and value["session_id"] in snapshot["deleted_sessions"]:
            raise MockStateError("a deleted mock session still has a live file")
    if snapshot["connected_sessions"]:
        raise MockStateError("local mock subscriptions must be closed before cleanup")
    for file_id in snapshot["shared_files"].values():
        if snapshot["files"][file_id]["status"] != "ready":
            raise MockStateError("local mock share state is inconsistent")
    return {"status": "VERIFIED", "source": "local-mock-snapshot",
            "resource_count": len(records), "generation": snapshot["generation"]}
