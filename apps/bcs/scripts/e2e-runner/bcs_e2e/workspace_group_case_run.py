"""Command entry for the reviewed dual Workspace Group HTTP cases."""

from __future__ import annotations

from pathlib import Path
import json

from .inventory import executor_digest
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_profile import (
    validate_workspace_group_governance_profile,
    validate_workspace_group_lifecycle_profile,
)
from .workspace_group_governance_contract import (
    CASE_ID as GOVERNANCE_CASE_ID,
    RemoteWorkspaceGroupGovernanceAdapter,
    execute_workspace_group_governance_case,
)
from .workspace_group_lifecycle_contract import (
    CASE_ID as LIFECYCLE_CASE_ID,
    RemoteWorkspaceGroupLifecycleAdapter,
    execute_workspace_group_lifecycle_case,
)

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-workspace-group-case-run-v1"
CASE_KEYS = ("lifecycle", "governance")
_CASES = {
    "lifecycle": {
        "case_id": LIFECYCLE_CASE_ID,
        "validator": validate_workspace_group_lifecycle_profile,
        "adapter": RemoteWorkspaceGroupLifecycleAdapter,
        "execute": execute_workspace_group_lifecycle_case,
        "fixture_effect": "RETAINED_BOUNDED_OWNED_BOTS_GROUP_LIFECYCLE",
        "credential_kind": None,
    },
    "governance": {
        "case_id": GOVERNANCE_CASE_ID,
        "validator": validate_workspace_group_governance_profile,
        "adapter": RemoteWorkspaceGroupGovernanceAdapter,
        "execute": execute_workspace_group_governance_case,
        "fixture_effect": "RETAINED_BOUNDED_OWNED_BOTS_GROUP_GOVERNANCE",
        "credential_kind": None,
    },
}


def load_workspace_group_case_profile(path: Path, case_key: str) -> dict:
    validator = _CASES[case_key]["validator"]
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"workspace {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ConfigError):
        raise ConfigError(f"workspace {case_key} profile is unreadable JSON") from None
    return validator(raw)


def dual_workspace_group_case_run(
    *,
    profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("workspace_case must be lifecycle or governance")
    run_id = identifier(run_id, f"workspace {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(f"workspace {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_workspace_group_case_profile(profile_path, case_key)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError(f"workspace {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"workspace {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    profile_file = state_dir / f"workspace-{case_key}-profile.json"
    binding_file = state_dir / f"workspace-{case_key}-binding.json"
    report_file = state_dir / f"workspace-{case_key}-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": _CASES[case_key]["case_id"],
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = _CASES[case_key]["adapter"](profile)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f"workspace {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "state_dir": str(state_dir),
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "case": execution.snapshot(),
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "resource_disposition": _CASES[case_key]["fixture_effect"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": (
            "owned_local_bot_token" if profile["environment"]["tier"] == "local" else "human_cookie"
        ),
    }
    write(report_file, report)
    return report
