"""Command entry for the remaining reviewed dual Workspace product-CLI cases."""

from __future__ import annotations

from pathlib import Path
import json

from .inventory import executor_digest
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_commands import WorkspaceCliRunnerError
from .workspace_cli_create_get_contract import (
    CASE_ID as CREATE_GET_CASE_ID,
    FIXTURE_EFFECT as CREATE_GET_FIXTURE_EFFECT,
    RemoteWorkspaceCliCreateGetAdapter,
    execute_workspace_cli_create_get_case,
)
from .workspace_cli_detail_contract import (
    CASE_ID as DETAIL_CASE_ID,
    FIXTURE_EFFECT as DETAIL_FIXTURE_EFFECT,
    RemoteWorkspaceCliDetailAdapter,
    execute_workspace_cli_detail_case,
)
from .workspace_cli_fuse_contract import (
    CASE_ID as FUSE_CASE_ID,
    FIXTURE_EFFECT as FUSE_FIXTURE_EFFECT,
    RemoteWorkspaceCliFuseAdapter,
    execute_workspace_cli_fuse_case,
)
from .workspace_cli_list_contract import (
    CASE_ID as LIST_CASE_ID,
    FIXTURE_EFFECT as LIST_FIXTURE_EFFECT,
    RemoteWorkspaceCliListAdapter,
    execute_workspace_cli_list_case,
)
from .workspace_cli_member_contract import (
    CASE_ID as MEMBER_CASE_ID,
    FIXTURE_EFFECT as MEMBER_FIXTURE_EFFECT,
    RemoteWorkspaceCliMemberAdapter,
    execute_workspace_cli_member_case,
)
from .workspace_cli_profile import (
    validate_workspace_cli_detail_profile,
    validate_workspace_cli_fuse_profile,
    validate_workspace_cli_list_profile,
    validate_workspace_cli_member_profile,
    validate_workspace_cli_profile,
)
from .workspace_cli_runner import WorkspaceCliRunner

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-workspace-cli-case-run-v1"
WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_KEYS = ("create-get", "detail", "list", "fuse", "member")

_CASES = {
    "create-get": {
        "case_id": CREATE_GET_CASE_ID,
        "validator": validate_workspace_cli_profile,
        "adapter": RemoteWorkspaceCliCreateGetAdapter,
        "execute": execute_workspace_cli_create_get_case,
        "fixture_effect": CREATE_GET_FIXTURE_EFFECT,
    },
    "detail": {
        "case_id": DETAIL_CASE_ID,
        "validator": validate_workspace_cli_detail_profile,
        "adapter": RemoteWorkspaceCliDetailAdapter,
        "execute": execute_workspace_cli_detail_case,
        "fixture_effect": DETAIL_FIXTURE_EFFECT,
    },
    "list": {
        "case_id": LIST_CASE_ID,
        "validator": validate_workspace_cli_list_profile,
        "adapter": RemoteWorkspaceCliListAdapter,
        "execute": execute_workspace_cli_list_case,
        "fixture_effect": LIST_FIXTURE_EFFECT,
    },
    "fuse": {
        "case_id": FUSE_CASE_ID,
        "validator": validate_workspace_cli_fuse_profile,
        "adapter": RemoteWorkspaceCliFuseAdapter,
        "execute": execute_workspace_cli_fuse_case,
        "fixture_effect": FUSE_FIXTURE_EFFECT,
    },
    "member": {
        "case_id": MEMBER_CASE_ID,
        "validator": validate_workspace_cli_member_profile,
        "adapter": RemoteWorkspaceCliMemberAdapter,
        "execute": execute_workspace_cli_member_case,
        "fixture_effect": MEMBER_FIXTURE_EFFECT,
    },
}


def load_workspace_cli_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("workspace_case must be create-get, detail, list, fuse or member")
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"workspace {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError(f"workspace {case_key} profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def dual_workspace_cli_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("workspace_case must be create-get, detail, list, fuse or member")
    run_id = identifier(run_id, f"workspace {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(f"workspace {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_workspace_cli_case_profile(profile_path, case_key)
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
        "parent_id": WORKSPACE_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        runner = WorkspaceCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
        adapter = _CASES[case_key]["adapter"](profile, runner)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, OSError) as error:
        raise ConfigError(f"workspace {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": WORKSPACE_PARENT,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
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
