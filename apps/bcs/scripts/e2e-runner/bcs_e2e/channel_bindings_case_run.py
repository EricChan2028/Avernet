"""Command lifecycle for the reviewed public-v1 Channel-binding slice.

The slice freezes four obligations from the disabled-channel parent story:
the rejected create attempt for an uninstalled channel, a list-before read,
an exact by-target absence read, and a list-after read that proves no ghost
binding was created for the dedicated driver.
"""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_channel_public_contract import (
    channel_bindings_case_plan,
    execute_channel_bindings_case,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_network_adapters import RemoteChannelBindingsAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-channel-bindings-case-run-v1"
CASE_ID = "PUBLIC-CHANNEL-BINDINGS-01"
PARENT_ID = "story_user_validates_external_channel_setup"
RESOURCE_DISPOSITION = "NO_BINDING_CREATED"
CREDENTIAL_KIND = "human_cookie"

BINDING_KEYS = frozenset(
    {
        "channel_bindings.create_denied",
        "channel_bindings.list",
        "channel_bindings.by_target",
    }
)


def load_channel_bindings_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("channel-bindings profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "channel-bindings profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "channel-bindings profile must be Cookie-bound and must not carry a Bot Authorization reference")
    return profile


def dual_channel_bindings_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "channel-bindings case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "channel-bindings run ID")
    profile = load_channel_bindings_case_profile(profile_path)
    plan = channel_bindings_case_plan()
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
        raise ConfigError("channel-bindings case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("channel-bindings case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "channel-bindings-profile.json"
    binding_file = state_dir / "channel-bindings-binding.json"
    report_file = state_dir / "channel-bindings-run.json"
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
            "requirement_ids": [row.requirement_id for row in plan.requirements],
            "blocked_requirement_ids": list(plan.blocked_requirements),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteChannelBindingsAdapter(profile)
        execution = execute_channel_bindings_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"channel-bindings case execution is unavailable: {error}") from None

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
            "requirement_ids": [row.requirement_id for row in plan.requirements],
            "blocked_requirement_ids": list(plan.blocked_requirements),
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
