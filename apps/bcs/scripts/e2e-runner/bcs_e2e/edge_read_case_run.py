"""Command entry for the reviewed dual-environment Edge read slices.

The admission, friend-request list, friend-connection list and Bot-search
candidate slices already have environment-neutral Case contracts and public
cross-environment adapters. This entry binds one admitted Profile to a fresh
private Run, executes only one selected slice, and writes the same three-file
receipt contract used by the other command lifecycles.
"""

from __future__ import annotations

from pathlib import Path
from .adapters import RemoteEdgeReadAdapter, RemoteEdgeBotSearchCandidatesAdapter
from .edge_read_case import (
    ADMISSION_PUBLIC_CASE_ID,
    FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
    FRIEND_REQUESTS_PUBLIC_CASE_ID,
    execute_edge_read_case,
)
from .edge_search_case import (
    EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
    execute_edge_bot_search_candidates_case,
)
from .remote_edge_admission_adapter import RemoteEdgeAdmissionAdapter
from .inventory import executor_digest
from .run_state import RunStateError
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-edge-read-case-run-v1"
CASE_KEYS = ("admission", "friend-requests", "friend-connections", "bot-search-candidates")
READ_ONLY_RESOURCE_DISPOSITION = "READ_ONLY_NO_BUSINESS_RESOURCE"

_CASES = {
    "admission": {
        "case_id": ADMISSION_PUBLIC_CASE_ID,
        "binding_keys": frozenset({
            "edge_admission_human_actor.read",
            "edge_admission_actor_kind.read",
        }),
    },
    "friend-requests": {
        "case_id": FRIEND_REQUESTS_PUBLIC_CASE_ID,
        "binding_keys": frozenset({"friend_requests.read"}),
    },
    "friend-connections": {
        "case_id": FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
        "binding_keys": frozenset({"friend_connections.read"}),
    },
    "bot-search-candidates": {
        "case_id": EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
        "binding_keys": frozenset({
            "edge_bot_search_name.read",
            "edge_bot_search_empty.read",
        }),
    },
}


def load_edge_read_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("edge_read_case must be admission, friend-requests, friend-connections or bot-search-candidates")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    expected = _CASES[case_key]["binding_keys"]
    if not isinstance(bindings, dict) or set(bindings) != set(expected):
        raise ConfigError(
            f"edge {case_key} profile must bind exactly its reviewed case operations")
    return profile


def _credential_kind(profile: dict, case_key: str) -> str:
    """Return only the credential kind, never a credential value."""
    if case_key == "admission":
        return "unauthenticated"
    headers = profile["auth"].get("header_env", {})
    if case_key == "bot-search-candidates":
        return "human_cookie" if "Cookie" in headers else "none"
    if "Authorization" in headers:
        return "owned_local_bot_token"
    if "Cookie" in headers:
        return "human_cookie"
    return "none"


def _adapter(profile: dict, case_key: str):
    if case_key == "admission":
        return RemoteEdgeAdmissionAdapter(profile)
    if case_key == "bot-search-candidates":
        return RemoteEdgeBotSearchCandidatesAdapter(profile)
    return RemoteEdgeReadAdapter(profile, _CASES[case_key]["case_id"])


def dual_edge_read_case_run(
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
            "edge_read_case must be admission, friend-requests, friend-connections or bot-search-candidates")
    run_id = identifier(run_id, f"edge {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"edge {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_edge_read_case_profile(profile_path, case_key)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute()
        or requested_state_dir.is_symlink()
        or requested_state_dir.exists()
        or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError(f"edge {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"edge {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / f"edge-{case_key}-profile.json"
    binding_file = state_dir / f"edge-{case_key}-binding.json"
    report_file = state_dir / f"edge-{case_key}-run.json"
    from .run_state import write
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": _CASES[case_key]["case_id"],
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "state_dir": str(state_dir),
        "credential_kind": _credential_kind(profile, case_key),
    }
    write(binding_file, binding)
    try:
        adapter = _adapter(profile, case_key)
        if case_key == "bot-search-candidates":
            execution = execute_edge_bot_search_candidates_case(adapter)
        else:
            execution = execute_edge_read_case(
                _CASES[case_key]["case_id"], adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"edge {case_key} case execution is unavailable: {error}") from None

    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": execution.parent_id,
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
        "resource_disposition": READ_ONLY_RESOURCE_DISPOSITION,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": _credential_kind(profile, case_key),
    }
    write(report_file, report)
    return report
