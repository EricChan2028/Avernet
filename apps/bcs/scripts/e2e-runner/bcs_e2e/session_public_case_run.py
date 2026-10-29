"""Command lifecycle for the reviewed public-v1 Session JWT/WS slice."""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_session_contract import PARENT, REQUIREMENTS
from .local_session_public_case import (
    CASE_ID,
    LOCAL_EXECUTION_CASE_ID,
    session_public_case_admission,
    session_public_case_plan,
)
from .local_session_run import local_session_public_run
from .profile import ConfigError, fingerprint, identifier, load, require_resolved
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-session-public-case-run-v1"


def load_session_public_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("session-public profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    session_public_case_admission(profile)
    headers = profile["auth"].get("header_env", {}) if isinstance(profile["auth"], dict) else {}
    if set(headers) != {"Cookie"}:
        raise ConfigError("session-public requires the reviewed Cookie-only auth contract")
    return profile


def _fresh_state_dir(state_dir: Path) -> Path:
    requested = Path(state_dir)
    if (
        not requested.is_absolute()
        or requested.is_symlink()
        or requested.exists()
        or not requested.parent.is_dir()
        or requested.parent.is_symlink()
    ):
        raise ConfigError("session-public case requires a fresh private state directory")
    resolved = requested.resolve()
    if resolved.is_symlink() or resolved.exists():
        raise ConfigError("session-public case requires a fresh private state directory")
    resolved.mkdir(mode=0o700, parents=False)
    return resolved


def dual_session_public_case_run(
    *,
    profile_path: Path,
    binary: Path,
    binary_sha256: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "session-public case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "session-public run ID")
    profile = load_session_public_case_profile(profile_path)
    plan = session_public_case_plan()
    admission = session_public_case_admission(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    root = _fresh_state_dir(state_dir)
    runtime_dir = root / "owned-local-session-public"

    profile_file = root / "session-public-profile.json"
    binding_file = root / "session-public-binding.json"
    report_file = root / "session-public-run.json"
    write(profile_file, profile)
    credential_kind = (
        "owned_local_principal_jwt" if profile["environment"]["tier"] == "local"
        else "human_cookie"
    )
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT,
        "requirement_id": plan["requirement_id"],
        "execution_case_id": plan["execution_case_id"],
        "local_execution_case_id": LOCAL_EXECUTION_CASE_ID,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": credential_kind,
        "runtime_dir": str(runtime_dir),
        "case_scope": {
            "shared_routes": list(plan["shared_routes"]),
            "remaining_requirement_ids": list(REQUIREMENTS[1:]),
            "full_story_status": "NOT_MIGRATED",
        },
        "state_dir": str(root),
    }
    write(binding_file, binding)

    execution: dict = {
        "status": "NOT_RUN",
        "reason": "gateway Session effect admission has no reviewed executor",
        "execution_case_id": plan["execution_case_id"],
        "local_execution_case_id": LOCAL_EXECUTION_CASE_ID,
    }
    status = "BLOCKED"
    resource_disposition = "NO_REMOTE_BUSINESS_RESOURCE_CREATED"
    execution_digest: str | None = None
    if admission["status"] == "SINGLEBOX_OWNED_LOCAL_READY":
        try:
            result = local_session_public_run(
                binary=binary,
                binary_sha256=binary_sha256,
                state_dir=runtime_dir,
                run_id=run_id,
                confirm_run_id=run_id,
                allow_local_processes=True,
                allow_fixture_write=True,
                allow_write=True,
            )
        except (ConfigError, RunStateError, OSError) as error:
            raise ConfigError(
                f"session-public owned-local execution is unavailable: {error}") from None
        execution_digest = fingerprint(result)
        passed = result.get("status") == "LOCAL_SESSION_PUBLIC_PASS"
        status = "PASS" if passed else "BLOCKED"
        execution = {
            "status": result.get("status", "BLOCKED"),
            "execution_case_id": plan["execution_case_id"],
            "local_execution_case_id": LOCAL_EXECUTION_CASE_ID,
            "result_digest": execution_digest,
            "remaining_requirement_ids": list(result.get("remaining_requirement_ids", REQUIREMENTS[1:])),
            "full_story_status": result.get("full_story_status", "NOT_MIGRATED"),
            "full_release_status": result.get("full_release_status", "NOT_QUALIFIED"),
        }
        if result.get("error_type") is not None:
            execution["error_type"] = result["error_type"]
        resource_disposition = (
            "OWNED_LOCAL_SESSION_AND_GROUP_FINALIZED" if passed
            else "UNKNOWN_OWNED_LOCAL_SESSION_GROUP_DISPOSITION"
        )

    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": PARENT,
        "requirement_id": plan["requirement_id"],
        "run_id": run_id,
        "status": status,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": credential_kind,
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "admission": admission,
        "execution": execution,
        "resource_disposition": resource_disposition,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "full_story_status": "NOT_MIGRATED",
        "full_release_status": "NOT_QUALIFIED",
        "state_dir": str(root),
    }
    write(report_file, report)
    return report
