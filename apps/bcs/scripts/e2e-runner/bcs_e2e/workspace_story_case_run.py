"""Command entry for the remaining dual Workspace HTTP stories.

The incident core, Workspace round trip, and proposal confirmation already
have reviewed Case contracts and adapters.  This entry only binds a strict
Profile to a fresh Run, executes one selected Case, and writes the same
three-file local receipt contract used by the other command entries.
"""

from __future__ import annotations

from pathlib import Path
import json

from .inventory import executor_digest
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .workspace_cli_profile import (
    validate_workspace_incident_core_profile,
    validate_workspace_proposal_profile,
    validate_workspace_roundtrip_profile,
)
from .workspace_incident_core_contract import (
    CASE_ID as INCIDENT_CASE_ID,
    FIXTURE_EFFECT as INCIDENT_EFFECT,
    WORKSPACE_PARENT,
    RemoteWorkspaceIncidentCoreAdapter,
    execute_workspace_incident_core_case,
)
from .workspace_proposal_contract import (
    CASE_ID as PROPOSAL_CASE_ID,
    FIXTURE_EFFECT as PROPOSAL_EFFECT,
    RemoteWorkspaceProposalAdapter,
    execute_workspace_proposal_case,
)
from .workspace_roundtrip_contract import (
    CASE_ID as ROUNDTRIP_CASE_ID,
    FIXTURE_EFFECT as ROUNDTRIP_EFFECT,
    RemoteWorkspaceRoundtripAdapter,
    execute_workspace_roundtrip_case,
)

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-workspace-story-case-run-v1"
CASE_KEYS = ("incident-core", "roundtrip", "proposal")
_CASES = {
    "incident-core": {
        "case_id": INCIDENT_CASE_ID,
        "validator": validate_workspace_incident_core_profile,
        "adapter": RemoteWorkspaceIncidentCoreAdapter,
        "execute": execute_workspace_incident_core_case,
        "fixture_effect": INCIDENT_EFFECT,
    },
    "roundtrip": {
        "case_id": ROUNDTRIP_CASE_ID,
        "validator": validate_workspace_roundtrip_profile,
        "adapter": RemoteWorkspaceRoundtripAdapter,
        "execute": execute_workspace_roundtrip_case,
        "fixture_effect": ROUNDTRIP_EFFECT,
    },
    "proposal": {
        "case_id": PROPOSAL_CASE_ID,
        "validator": validate_workspace_proposal_profile,
        "adapter": RemoteWorkspaceProposalAdapter,
        "execute": execute_workspace_proposal_case,
        "fixture_effect": PROPOSAL_EFFECT,
    },
}


def load_workspace_story_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "workspace_story_case must be incident-core, roundtrip or proposal")
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"workspace {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ConfigError):
        raise ConfigError(f"workspace {case_key} profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def dual_workspace_story_case_run(
    *,
    profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "workspace_story_case must be incident-core, roundtrip or proposal")
    run_id = identifier(run_id, f"workspace {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"workspace {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_workspace_story_case_profile(profile_path, case_key)
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
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = _CASES[case_key]["adapter"](profile)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"workspace {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": WORKSPACE_PARENT,
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
