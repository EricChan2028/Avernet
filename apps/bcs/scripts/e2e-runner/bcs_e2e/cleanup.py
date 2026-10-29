"""Exact-resource cleanup executor for a prepared E2E Run ledger."""
from __future__ import annotations

from .execution_safety import inspect_execution
from .ledger import Ledger


class CleanupError(RuntimeError):
    pass


_KIND_ORDER = {"file": 0, "session": 1, "group": 2}


def _resource_id(resources: dict[str, dict], handle: str) -> str:
    resource = resources.get(handle)
    if not resource or not isinstance(resource.get("resource_id"), str):
        raise CleanupError("cleanup parent resource ID is missing")
    return resource["resource_id"]


def _converge(delete, verify, *, require_deleted: bool = False) -> None:
    """Delete one object; never turn denial or unknown outcome into success.

    Adapters may normalize documented already-absent responses, but an arbitrary
    exception is not proof of absence, even if a subsequent GET returns 404.
    """
    try:
        result = delete()
        payload = result[0] if isinstance(result, tuple) else result
        if require_deleted and (not isinstance(payload, dict) or payload.get("deleted") is not True):
            raise CleanupError("file cleanup delete response was not confirmed")
        if isinstance(payload, dict) and payload.get("deleted") is False:
            raise CleanupError("cleanup delete response was not successful")
    except Exception as error:  # adapters redact transport errors at their boundary
        raise CleanupError(f"cleanup delete failed: {type(error).__name__}") from None
    try:
        verify()
    except Exception as error:
        raise CleanupError(f"cleanup verification failed: {type(error).__name__}") from None


def verify_scope(ledger: Ledger, adapter) -> None:
    verify = getattr(adapter, "verify_cleanup_scope", None)
    if not callable(verify):
        raise CleanupError("adapter has no independent cleanup scope verifier")
    result = verify(ledger.run_id, ledger.resource_snapshot())
    if not isinstance(result, dict) or result.get("status") != "VERIFIED":
        raise CleanupError("independent cleanup scope verification did not pass")




def execute_cleanup(ledger: Ledger, adapter, *, dry_run: bool = True) -> dict:
    plan = ledger.cleanup_plan()
    execution = inspect_execution(ledger)
    termination_blockers = execution["issues"]
    if dry_run:
        return {
            "schema_version": 1,
            "status": "DRY_RUN" if plan["status"] == "PLANNED" and not termination_blockers else "BLOCKED",
            "dry_run": True,
            "run_id": plan["run_id"],
            "resources": plan["resources"],
            "unresolved_handles": plan["unresolved_handles"],
            "retained_handles": plan["retained_handles"],
            "termination_blockers": termination_blockers,
            "execution_safety": execution,
        }
    if plan["status"] != "PLANNED" or plan["retained_handles"] or termination_blockers:
        return {
            "schema_version": 1,
            "status": "BLOCKED",
            "dry_run": False,
            "run_id": plan["run_id"],
            "resources": [],
            "unresolved_handles": plan["unresolved_handles"],
            "retained_handles": plan["retained_handles"],
            "termination_blockers": termination_blockers,
            "execution_safety": execution,
        }

    try:
        verify_scope(ledger, adapter)
    except Exception as error:
        return {"schema_version": 1, "status": "BLOCKED", "dry_run": False,
                "run_id": plan["run_id"], "resources": [], "phase": "SCOPE_VERIFICATION",
                "error": type(error).__name__, "remaining": plan["resources"],
                "retained_handles": plan["retained_handles"], "unresolved_handles": plan["unresolved_handles"]}

    resources = {item["handle"]: item for item in plan["resources"]}
    outcomes: list[dict] = []
    for resource in sorted(plan["resources"], key=lambda item: _KIND_ORDER[item["kind"]]):
        handle = resource["handle"]
        kind = resource["kind"]
        resource_id = resource["resource_id"]
        try:
            if kind == "group":
                _converge(
                    lambda: adapter.group_delete(resource_id),
                    lambda: adapter.group_verify_deleted(resource_id),
                )
                ledger.mark_deleted(handle)
            elif kind == "session":
                group_id = _resource_id(resources, resource["parent"])
                _converge(
                    lambda: adapter.session_delete(resource_id),
                    lambda: adapter.session_verify_deleted(resource_id),
                )
                ledger.mark_deleted(handle)
                outcomes.append({"kind": kind, "resource_id": resource_id, "parent_id": group_id})
                continue
            elif kind == "file":
                session_id = _resource_id(resources, resource["parent"])
                _converge(
                    lambda: adapter.file_delete(session_id, resource_id),
                    lambda: adapter.file_verify_deleted(session_id, resource_id),
                    require_deleted=True,
                )
                ledger.mark_deleted(handle)
                outcomes.append({"kind": kind, "resource_id": resource_id, "parent_id": session_id})
                continue
            else:
                raise CleanupError("unsupported cleanup resource kind")
            outcomes.append({"kind": kind, "resource_id": resource_id})
        except Exception as error:
            # Preserve the first failure and stop before deleting any parent.
            return {
                "schema_version": 1,
                "status": "BLOCKED",
                "dry_run": False,
                "run_id": plan["run_id"],
                "resources": outcomes,
                "failed": {"kind": kind, "resource_id": resource_id,
                           "error": type(error).__name__},
                "remaining": ledger.cleanup_plan()["resources"],
            }
    final_plan = ledger.cleanup_plan()
    try:
        verify_scope(ledger, adapter)
        if inspect_execution(ledger, required=execution["status"] != "NOT_TRACKED")["status"] == "BLOCKED":
            raise CleanupError("execution evidence changed during cleanup")
        if final_plan["resources"] or final_plan["unresolved_handles"] or final_plan["retained_handles"]:
            raise CleanupError("cleanup still has outstanding or retained resources")
    except Exception as error:
        return {"schema_version": 1, "status": "BLOCKED", "dry_run": False,
                "run_id": plan["run_id"], "resources": outcomes, "phase": "FINAL_VERIFICATION",
                "error": type(error).__name__, "remaining": final_plan["resources"],
                "retained_handles": final_plan["retained_handles"], "unresolved_handles": final_plan["unresolved_handles"]}
    return {
        "schema_version": 1, "status": "PASS", "dry_run": False,
        "run_id": plan["run_id"], "resources": outcomes, "remaining": [],
        "retained_handles": [], "unresolved_handles": [],
    }
