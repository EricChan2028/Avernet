"""Offline review gate for the 6.6 local entry switch.

This module never starts a process, writes a ledger, reads a network endpoint,
or changes Rust unit-test semantics.  It reviews the frozen migration plan and
reports whether the local shell default stays on the owned-local core.  The
runtime switch itself is intentionally represented by the shell default and this
reviewed decision.
"""
from __future__ import annotations

from typing import Any

from .inventory import reviewed_release_inventory
from .migration_inventory import migration_plan

_CONTRACT = "bcs-local-entry-switch-review-v1"
_EXPECTED_PARENT_COUNT = 29


def _global_blocker(kind: str, value: Any) -> str:
    """Use stable exact strings so consumers can match one blocker, not prose."""
    if kind in {"parents.missing", "parents.unexpected", "executor.case.duplicate"}:
        suffix = ",".join(value)
    elif kind in {"partial.executors.present"}:
        suffix = ",".join(value)
    else:
        suffix = str(value)
    return f"{kind}:{suffix}"


def _execution_binding(row: dict[str, Any]) -> dict[str, str] | None:
    command = row.get("execution_command")
    if isinstance(command, str) and command.strip():
        return {"kind": "RUNNER_COMMAND", "value": command}

    contract = row.get("programmatic_execution_contract")
    if isinstance(contract, str) and contract.strip():
        return {
            "kind": "PROGRAMMATIC_EXECUTION_CONTRACT",
            "value": contract,
        }
    return None


def _executor_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project one executor without copying optional unbounded source data."""
    blockers: list[str] = []

    case_id = row.get("case_id") if isinstance(row.get("case_id"), str) else ""
    label = case_id or "<missing-case-id>"

    reporting = row.get("canonical_reporting")
    if not isinstance(reporting, str) or "INTEGRATED_LOCAL" not in reporting:
        blockers.append(
            f"canonical.reporting:{label}:{reporting!r} must contain INTEGRATED_LOCAL"
        )

    remote_validation = row.get("remote_validation")
    if remote_validation != "NOT_RUN":
        blockers.append(f"remote.validation:{label}:{remote_validation!r} expected NOT_RUN")

    release_credit = row.get("grants_release_credit")
    if release_credit is not False:
        blockers.append(f"release.credit:{label}:{release_credit!r} expected False")

    binding = _execution_binding(row)
    if binding is None:
        blockers.append(
            f"execution.binding:{label}:"
            "missing execution_command or programmatic_execution_contract"
        )

    for field in ("legacy_id", "case_id", "suite"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            blockers.append(f"executor.field:{label}:{field} must be a non-empty string")

    status = "READY" if not blockers else "BLOCKED"
    return {
        "parent": row.get("legacy_id"),
        "case_id": row.get("case_id"),
        "suite": row.get("suite"),
        "canonical_reporting": row.get("canonical_reporting"),
        "remote_validation": row.get("remote_validation"),
        "grants_release_credit": row.get("grants_release_credit"),
        "execution_binding": binding,
        "status": status,
        "blockers": blockers,
    }


def review_local_entry_switch() -> dict[str, Any]:
    """Review the migration floor backing the selected local shell default."""
    plan = migration_plan()
    blockers: list[str] = []

    rows = plan.get("local_registered_executors")
    partials = plan.get("local_partial_executors")

    if not isinstance(rows, list):
        blockers.append(_global_blocker("executor.inventory", "local_registered_executors must be a list"))
        rows = []
    else:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                blockers.append(_global_blocker("executor.row", index))
        rows = [row for row in rows if isinstance(row, dict)]

        case_ids = [
            row.get("case_id") for row in rows
            if isinstance(row.get("case_id"), str) and row.get("case_id")
        ]
        duplicates = sorted({
            case_id for case_id in case_ids if case_ids.count(case_id) > 1
        })
        if duplicates:
            blockers.append(_global_blocker("executor.case.duplicate", duplicates))

    if not isinstance(partials, list):
        blockers.append(_global_blocker("partial.executors", "local_partial_executors must be a list"))
        partials = []
    partial_case_ids = [
        row.get("case_id") for row in partials if isinstance(row, dict)
        and isinstance(row.get("case_id"), str) and row.get("case_id")
    ]
    if partials:
        blockers.append(_global_blocker("partial.executors.present", partial_case_ids))

    expected_inventory = reviewed_release_inventory()
    expected_parents = {
        row.get("id") for row in expected_inventory
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    actual_parents = {
        row.get("legacy_id") for row in rows
        if isinstance(row, dict) and isinstance(row.get("legacy_id"), str)
    }

    if len(rows) != _EXPECTED_PARENT_COUNT:
        blockers.append(_global_blocker("executor.count", f"{len(rows)} expected 29"))

    if missing := sorted(expected_parents - actual_parents):
        blockers.append(_global_blocker("parents.missing", missing))
    if unexpected := sorted(actual_parents - expected_parents):
        blockers.append(_global_blocker("parents.unexpected", unexpected))

    executor_rows = [_executor_row(row) for row in rows]
    blockers.extend(
        blocker
        for projected in executor_rows
        for blocker in projected["blockers"]
    )

    status = "READY" if not blockers and all(
        projected["status"] == "READY" for projected in executor_rows
    ) else "BLOCKED"
    return {
        "schema_version": 1,
        "contract": _CONTRACT,
        "status": status,
        "ready_to_execute": status == "READY",
        "scope": "OFFLINE_REVIEW_ONLY",
        "entry_switch": ("PERFORMED_LOCAL_SHELL_DEFAULT" if status == "READY" else "NOT_PERFORMED"),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "expected_executor_count": _EXPECTED_PARENT_COUNT,
        "executor_count": len(rows),
        "partial_executor_count": len(partials),
        "executors": executor_rows,
        "blockers": blockers,
        "legacy_entry": "LEGACY_SINGLEBOX_E2E_ENTRY_EXPLICIT_ROLLBACK_ONLY",
        "new_core": "OWNED_LOCAL_BCS_E2E_RUNNER_CORE",
        "rollback": {
            "policy": "KEEP_OLD_LOCAL_ENTRY_AND_PREPROD_LEDGER_AVAILABLE",
            "local_action": "EXPLICITLY_SELECT_LEGACY_LOCAL_ORCHESTRATION",
            "preprod_action": "STOP_NEW_DISPATCH_WITHOUT_ERASING_EVIDENCE",
            "in_flight_resources": "RUN_ORIGINAL_CLEANUP_AND_RETENTION_POLICY",
            "db_migration": "NONE",
        },
        "ledger_recovery": {
            "automatic_replay": "NOT_ALLOWED",
            "ledger_retention": "KEEP_RUN_LEDGERS_AND_CLEANUP_VERSIONS",
            "failed_pool_disposition": "RETAIN_FOR_DIAGNOSIS_OR_EXPLICIT_OWNER_REVIEW",
            "authorization": "EXPLICIT_RECOVERY_REVIEW_REQUIRED",
        },
        "rust_unit_test_semantics": "UNCHANGED",
    }
