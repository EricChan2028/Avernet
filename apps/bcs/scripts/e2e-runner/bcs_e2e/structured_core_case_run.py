"""Command lifecycle for the reviewed public-v1 structured-collaboration core.

The slice creates one state-machine Group, patches and drives its definition,
asserts the three reviewed negative responses, and finalizes the Group on
success. A blocked run never receives a successful cleanup claim.
"""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_structured_core_contract import (
    CASE_ID,
    FIXTURE_EFFECT,
    PUBLIC_OPERATIONS,
    PARENT as PARENT_ID,
    execute_structured_core_case,
    structured_core_adaptability,
    structured_core_case_plan,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_structured_core_adapter import RemoteStructuredCoreAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-structured-core-case-run-v1"
SUCCESS_RESOURCE_DISPOSITION = FIXTURE_EFFECT
BLOCKED_RESOURCE_DISPOSITION = "UNKNOWN_UNFINALIZED_STRUCTURED_COLLABORATION_GROUP"
CREDENTIAL_KIND = "human_cookie_and_driver_bot_token"

BINDING_KEYS = frozenset(PUBLIC_OPERATIONS)


def load_structured_core_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("structured-core profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "structured-core profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" not in headers:
        raise ConfigError(
            "structured-core profile must bind its reviewed Human Cookie and driver Bot Authorization references")
    return profile


def dual_structured_core_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "structured-core case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "structured-core run ID")
    profile = load_structured_core_case_profile(profile_path)
    plan = structured_core_case_plan()
    adaptability = structured_core_adaptability()
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
        raise ConfigError("structured-core case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("structured-core case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "structured-core-profile.json"
    binding_file = state_dir / "structured-core-binding.json"
    report_file = state_dir / "structured-core-run.json"
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
        "planned_resource_disposition": SUCCESS_RESOURCE_DISPOSITION,
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_operations": list(PUBLIC_OPERATIONS),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteStructuredCoreAdapter(profile)
        execution = execute_structured_core_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"structured-core case execution is unavailable: {error}") from None

    resource_disposition = (
        SUCCESS_RESOURCE_DISPOSITION
        if execution.status == "PASS"
        else BLOCKED_RESOURCE_DISPOSITION
    )
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
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_operations": list(PUBLIC_OPERATIONS),
        },
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "resource_disposition": resource_disposition,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report
