"""Command entry for the dual Workspace status and termination case."""

from __future__ import annotations

from pathlib import Path
import json

from .inventory import executor_digest
from .remote_workspace_cli_status_adapter import RemoteWorkspaceCliStatusAdapter
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_commands import WorkspaceCliRunnerError
from .workspace_cli_profile import validate_workspace_cli_status_profile
from .workspace_cli_runner import WorkspaceCliRunner
from .workspace_cli_status_contract import CASE_ID, WORKSPACE_PARENT, execute_workspace_cli_status_case

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-workspace-cli-status-run-v1"
BINDING_FILE = "workspace-cli-status-binding.json"
PROFILE_FILE = "workspace-cli-status-profile.json"
REPORT_FILE = "workspace-cli-status-run.json"


def load_workspace_cli_status_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("workspace CLI status profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ConfigError):
        raise ConfigError("workspace CLI status profile is unreadable JSON") from None
    return validate_workspace_cli_status_profile(raw)


def dual_workspace_cli_status_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    run_id = identifier(run_id, "Workspace CLI status run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError("workspace CLI status requires explicit write permission and exact Run confirmation")
    profile = load_workspace_cli_status_profile(profile_path)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError("workspace CLI status requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("workspace CLI status requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    write(state_dir / PROFILE_FILE, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": WORKSPACE_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(state_dir / BINDING_FILE, binding)
    try:
        runner = WorkspaceCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, OSError) as error:
        raise ConfigError(f"workspace CLI status runner is unavailable: {error}") from None
    adapter = RemoteWorkspaceCliStatusAdapter(profile, runner)
    execution = execute_workspace_cli_status_case(adapter)
    snapshot = execution.snapshot()
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
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
        "case": snapshot,
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "resource_disposition": "RETAINED_DYNAMIC_GROUPS_AND_INITIAL_SESSIONS",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": (
            "owned_local_bot_token" if profile["environment"]["tier"] == "local"
            else "human_cookie_with_owned_driver_bot_token"
            if profile["auth"]["kind"] == "human-cookie-with-owned-driver-bot-token"
            else "owned_bot_token" if profile["auth"]["kind"] == "owned-bot-tokens"
            else "human_cookie"
        ),
    }
    write(state_dir / REPORT_FILE, report)
    return report
