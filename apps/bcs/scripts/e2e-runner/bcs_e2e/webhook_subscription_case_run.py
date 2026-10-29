"""Command lifecycle for the reviewed public-v1 Event Webhook subscription core."""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_webhook_subscription_contract import (
    CASE_ID,
    COMMUNITY_ONLY_CLOSURES,
    FIXTURE_EFFECT,
    execute_webhook_subscription_case,
    webhook_subscription_adaptability,
    webhook_subscription_case_plan,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_webhook_subscription_adapter import (
    RemoteWebhookSubscriptionAdapter,
    WEBHOOK_EDGE_OPERATIONS,
)
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-webhook-subscription-case-run-v1"
SUCCESS_RESOURCE_DISPOSITION = FIXTURE_EFFECT
BLOCKED_RESOURCE_DISPOSITION = "UNKNOWN_UNFINALIZED_WEBHOOK_SUBSCRIPTION_GROUP"
CREDENTIAL_KIND = "gateway_principal"
CASE_OPERATIONS = frozenset(WEBHOOK_EDGE_OPERATIONS)


def load_webhook_subscription_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("webhook-subscription profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != CASE_OPERATIONS:
        raise ConfigError(
            "webhook-subscription profile must bind exactly its reviewed lifecycle operations")
    if not profile.get("auth", {}).get("principal_signing_key_env"):
        raise ConfigError(
            "webhook-subscription profile requires an operator-provisioned principal signing key reference")
    return profile


def dual_webhook_subscription_case_run(
    *,
    profile_path: Path,
    receiver_origin: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "webhook-subscription case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "webhook-subscription run ID")
    profile = load_webhook_subscription_case_profile(profile_path)
    plan = webhook_subscription_case_plan()
    adaptability = webhook_subscription_adaptability()
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
        raise ConfigError("webhook-subscription case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("webhook-subscription case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "webhook-subscription-profile.json"
    binding_file = state_dir / "webhook-subscription-binding.json"
    report_file = state_dir / "webhook-subscription-run.json"
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
        "receiver_origin": receiver_origin,
        "planned_resource_disposition": SUCCESS_RESOURCE_DISPOSITION,
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_paths": list(adaptability["public_paths"]),
            "public_operations": sorted(CASE_OPERATIONS),
            "community_only_closures": list(COMMUNITY_ONLY_CLOSURES),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = RemoteWebhookSubscriptionAdapter(profile, receiver_origin=receiver_origin)
        execution = execute_webhook_subscription_case(adapter)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"webhook-subscription case execution is unavailable: {error}") from None

    resource_disposition = (
        SUCCESS_RESOURCE_DISPOSITION
        if execution.status == "PASS"
        else BLOCKED_RESOURCE_DISPOSITION
    )
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
        "receiver_origin": receiver_origin,
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "case": execution.snapshot(),
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
            "public_paths": list(adaptability["public_paths"]),
            "public_operations": sorted(CASE_OPERATIONS),
            "community_only_closures": list(COMMUNITY_ONLY_CLOSURES),
        },
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
        },
        "resource_disposition": resource_disposition,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report
