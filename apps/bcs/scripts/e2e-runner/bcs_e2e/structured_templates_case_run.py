"""Command lifecycle for the reviewed public-v1 Structured template catalog."""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_structured_public_contract import (
    CASE_ID,
    execute_structured_templates_case,
    structured_templates_adaptability,
    structured_templates_case_plan,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_network_adapters import RemoteStructuredTemplatesAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-structured-templates-case-run-v1"
RESOURCE_DISPOSITION = "READ_ONLY_NO_BUSINESS_RESOURCE"
CREDENTIAL_KIND = "human_cookie"
CASE_OPERATIONS = frozenset({
    "structured_templates.list",
    "structured_templates.detail",
})


def load_structured_templates_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("structured-template profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != CASE_OPERATIONS:
        raise ConfigError(
            "structured-template profile must bind exactly its reviewed catalog operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "structured-template profile must be Cookie-bound and must not carry a Bot Authorization reference")
    return profile


def dual_structured_templates_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "structured-template case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "structured-template run ID")
    profile = load_structured_templates_case_profile(profile_path)
    plan = structured_templates_case_plan()
    adaptability = structured_templates_adaptability()
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
        raise ConfigError("structured-template case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("structured-template case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "structured-templates-profile.json"
    binding_file = state_dir / "structured-templates-binding.json"
    report_file = state_dir / "structured-templates-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": plan.parent_id,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "resource_disposition": RESOURCE_DISPOSITION,
        "case_scope": {
            "requirement_ids": [row.requirement_id for row in plan.requirements],
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_paths": list(adaptability["public_paths"]),
            "public_operations": sorted(CASE_OPERATIONS),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteStructuredTemplatesAdapter(profile)
        execution = execute_structured_templates_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"structured-template case execution is unavailable: {error}") from None

    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": execution.parent_id,
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
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_paths": list(adaptability["public_paths"]),
            "public_operations": sorted(CASE_OPERATIONS),
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
