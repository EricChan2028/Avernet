"""Command lifecycle for the reviewed public-v1 actor-status slice.

The slice is a bounded, reversible mutation of the dedicated driver Bot's
status: read online, write hidden, read hidden, write online, and read the
restored online state back.
"""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_network_actor_status_contract import (
    CASE_ID,
    PARENT as PARENT_ID,
    network_actor_status_adaptability,
    network_actor_status_case_plan,
    execute_network_actor_status_case,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_network_adapters import RemoteNetworkActorStatusAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-network-actor-status-case-run-v1"
RESOURCE_DISPOSITION = "BOUNDED_OWNED_BOT_STATUS_RESTORE"
CREDENTIAL_KIND = "human_cookie"

BINDING_KEYS = frozenset(
    {
        "network_actor_status.read",
        "network_actor_status.write",
    }
)


def load_network_actor_status_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("actor-status profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "actor-status profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "actor-status profile must be Cookie-bound and must not carry a Bot Authorization reference")
    return profile


def dual_network_actor_status_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "actor-status case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "actor-status run ID")
    profile = load_network_actor_status_case_profile(profile_path)
    plan = network_actor_status_case_plan()
    adaptability = network_actor_status_adaptability()
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
        raise ConfigError("actor-status case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("actor-status case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "network-actor-status-profile.json"
    binding_file = state_dir / "network-actor-status-binding.json"
    report_file = state_dir / "network-actor-status-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT_ID,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "resource_disposition": RESOURCE_DISPOSITION,
        "case_scope": {
            "requirement_id": plan.requirement.requirement_id,
            "selected_obligations": list(adaptability["selected_obligations"]),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteNetworkActorStatusAdapter(profile)
        execution = execute_network_actor_status_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"actor-status case execution is unavailable: {error}") from None

    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT_ID,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "case": execution.snapshot(),
        "case_scope": {
            "requirement_id": plan.requirement.requirement_id,
            "selected_obligations": list(adaptability["selected_obligations"]),
        },
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
        },
        "resource_disposition": RESOURCE_DISPOSITION,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report
