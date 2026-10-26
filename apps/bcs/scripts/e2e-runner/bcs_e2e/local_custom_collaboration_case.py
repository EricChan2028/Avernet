"""Owned-local custom-collaboration parent case.

This joins the three already-migrated custom-collaboration slices into one
exact parent lifecycle, in the reviewed source order: validate, one-shot
trial, persistent Group create, then exact Group cleanup. It does not grant
remote or release credit.
"""
from __future__ import annotations

from pathlib import Path
import time

from .local_cli import (
    LocalCliError,
    OwnedBcsProcess,
    _request_trial_json,
    create_custom_collaboration_group,
    custom_collaboration_group_delete_path,
    run_custom_collaboration_trial,
    validate_collaboration_yaml,
)
from .local_operator_cli_contract import (
    CUSTOM_COLLABORATION_PARENT,
    CUSTOM_COLLABORATION_REQUIREMENTS,
    custom_collaboration_source_binding,
)
from .profile import identifier
from .run_state import write

CASE_ID = "LOCAL-CUSTOM-COLLABORATION-LIFECYCLE-01"
SUITE = "owned-local-custom-collaboration-lifecycle-v1"
REQUIREMENTS = CUSTOM_COLLABORATION_REQUIREMENTS


def custom_collaboration_case_plan() -> dict:
    """Bind the complete reviewed parent to one explicit local case."""
    binding = custom_collaboration_source_binding()
    if (binding["parent"] != CUSTOM_COLLABORATION_PARENT
            or tuple(row["id"] for row in binding["requirements"]) != REQUIREMENTS
            or len(binding["requirements"]) != 3):
        raise LocalCliError("custom collaboration case source binding changed")
    return {
        "case_id": CASE_ID,
        "legacy_id": CUSTOM_COLLABORATION_PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "parent_requirement_count": 3,
    }


def custom_collaboration_case_adaptability() -> dict:
    plan = custom_collaboration_case_plan()
    return {
        "case_id": plan["case_id"],
        "legacy_id": plan["legacy_id"],
        "requirement_ids": plan["requirement_ids"],
        "selected_obligations": [
            "cli-collaboration-validate",
            "cli-collaboration-trial",
            "cli-collaboration-create-persistent-group",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_PARENT_EXECUTOR",
        "cli_surface": "product-bcs-cli-collaboration",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Owned-local implementation and mock-verified CLI evidence exist; "
            "preprod effect admission and execution remain separate gates."
        ),
    }


def _cleanup_created_group(runtime: OwnedBcsProcess, *, group_id: str, driver_id: str,
                           driver_token: str) -> None:
    """Delete only the exact persistent custom-collaboration Group created by this case."""
    response = _request_trial_json(
        runtime, "DELETE", custom_collaboration_group_delete_path(group_id, driver_id),
        None, driver_token, 200,
    )
    if response.get("deleted") is not True or response.get("id") != group_id:
        raise LocalCliError("custom collaboration persistent Group cleanup differs")


def run_custom_collaboration_case(runtime: OwnedBcsProcess, *, state_dir: Path,
                                  binary: Path, binary_sha256: str,
                                  yaml_path: Path, driver_id: str, reviewer_id: str,
                                  human_id: str, driver_token: str,
                                  timeout_seconds: int = 30) -> dict:
    """Run all three reviewed custom-collaboration obligations as one local case."""
    if not state_dir.is_absolute() or state_dir.is_symlink() or not state_dir.is_dir():
        raise LocalCliError("custom collaboration case requires a fresh absolute state directory")
    identifier(CASE_ID, "CLI case ID")
    identifier(driver_id, "custom collaboration driver Bot ID")
    identifier(reviewer_id, "custom collaboration reviewer Bot ID")
    identifier(human_id, "custom collaboration Human participant ID")
    identifier(driver_token, "custom collaboration driver Bot credential")
    if driver_id == reviewer_id:
        raise LocalCliError("custom collaboration requires two distinct Bots")
    plan = custom_collaboration_case_plan()
    started = time.monotonic()

    validate = validate_collaboration_yaml(
        runtime, state_dir=state_dir, case_id=CASE_ID,
        binary=binary, binary_sha256=binary_sha256, yaml_path=yaml_path,
        timeout_seconds=timeout_seconds,
    )
    trial = run_custom_collaboration_trial(
        runtime, state_dir=state_dir, case_id=CASE_ID,
        binary=binary, binary_sha256=binary_sha256, yaml_path=yaml_path,
        driver_id=driver_id, reviewer_id=reviewer_id, human_id=human_id,
        driver_token=driver_token, timeout_seconds=timeout_seconds,
    )
    created = create_custom_collaboration_group(
        runtime, state_dir=state_dir, case_id=CASE_ID,
        binary=binary, binary_sha256=binary_sha256, yaml_path=yaml_path,
        driver_id=driver_id, reviewer_id=reviewer_id, driver_token=driver_token,
        timeout_seconds=timeout_seconds,
    )
    group_id = created.get("group_id")
    if not isinstance(group_id, str) or not group_id:
        raise LocalCliError("custom collaboration persistent Group ID is missing")
    _cleanup_created_group(runtime, group_id=group_id,
                           driver_id=driver_id, driver_token=driver_token)

    result = {
        "schema_version": 1,
        "status": "LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS",
        "case_id": CASE_ID,
        "legacy_id": CUSTOM_COLLABORATION_PARENT,
        "assertions": [{"id": name, "status": "PASS"} for name in REQUIREMENTS],
        "selected_requirement_ids": list(REQUIREMENTS),
        "parent_requirement_count": 3,
        "full_parent_coverage": "LOCAL_PARENT_REQUIREMENTS_ONLY",
        "operations": {
            "validate": validate,
            "trial": trial,
            "create-persistent-group": {**created, "group_cleanup": "DELETED"},
        },
        "persistent_group_cleanup": "DELETED",
        "binary_sha256": binary_sha256,
        "yaml_path": str(yaml_path),
        "driver_bot": driver_id,
        "human_participant": human_id,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "legacy_counts": plan,
    }
    write(state_dir / "local-custom-collaboration.json", result)
    return result
