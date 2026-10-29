"""Command lifecycle for the reviewed public-v1 register-onboard core.

The slice creates one temporary owned Bot with a Human-minted register token and
retains it because the public-v1 face has no Bot delete route. Community-only
registration closures stay explicitly blocked instead of being counted as PASS.
"""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_network_register_contract import (
    CASE_ID,
    FIXTURE_EFFECT,
    PUBLIC_OPERATIONS,
    PARENT as PARENT_ID,
    execute_network_register_case,
    network_register_adaptability,
    network_register_case_plan,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_network_register_adapter import RemoteNetworkRegisterAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-network-register-case-run-v1"
RESOURCE_DISPOSITION = FIXTURE_EFFECT
CREDENTIAL_KIND = "human_cookie"

BINDING_KEYS = frozenset(PUBLIC_OPERATIONS)


def load_network_register_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("register profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "register profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "register profile must be Cookie-bound and must not carry a Bot Authorization reference")
    return profile


def dual_network_register_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "register case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "register run ID")
    profile = load_network_register_case_profile(profile_path)
    plan = network_register_case_plan()
    adaptability = network_register_adaptability()
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
        raise ConfigError("register case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("register case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "network-register-profile.json"
    binding_file = state_dir / "network-register-binding.json"
    report_file = state_dir / "network-register-run.json"
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
            "public_operations": list(plan.requirement.public_operations),
            "community_only_closures": list(plan.requirement.community_only_closures),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteNetworkRegisterAdapter(profile)
        execution = execute_network_register_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"register case execution is unavailable: {error}") from None

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
            "public_operations": list(plan.requirement.public_operations),
            "community_only_closures": list(plan.requirement.community_only_closures),
            "selected_obligations": list(adaptability["adapter_methods"]),
        },
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "resource_disposition": RESOURCE_DISPOSITION,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report
