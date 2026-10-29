"""Command lifecycle for the reviewed dual-environment network read Cases."""

from __future__ import annotations

from pathlib import Path

from .inventory import executor_digest
from .local_network_contract import (
    NETWORK_ACTOR_LIST_CASE_ID,
    NETWORK_ACTOR_SEARCH_CASE_ID,
    NETWORK_BOTS_MINE_CASE_ID,
    NETWORK_BOTS_PAGED_CASE_ID,
    NETWORK_BOTS_QUERY_CASE_ID,
    NETWORK_PLATFORM_ASSET_CASE_ID,
    PARENT,
    execute_network_actor_list_case,
    execute_network_actor_search_case,
    execute_network_bots_mine_case,
    execute_network_bots_paged_case,
    execute_network_bots_query_case,
    execute_network_platform_asset_case,
    network_actor_list_case_plan,
    network_actor_search_case_plan,
    network_bots_mine_case_plan,
    network_bots_paged_case_plan,
    network_bots_query_case_plan,
    network_platform_asset_case_plan,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_network_adapters import (
    RemoteNetworkActorListAdapter,
    RemoteNetworkActorSearchAdapter,
    RemoteNetworkBotsMineAdapter,
    RemoteNetworkBotsPagedAdapter,
    RemoteNetworkBotsQueryAdapter,
    RemoteNetworkPlatformAssetAdapter,
)
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-network-read-case-run-v1"
RESOURCE_DISPOSITION = "READ_ONLY_NO_BUSINESS_RESOURCE"
CREDENTIAL_KIND = "human_cookie"

CASE_KEYS = (
    "bots-query",
    "bots-mine",
    "actor-list",
    "actor-search",
    "bots-paged",
    "platform-asset",
)

_CASES = {
    "bots-query": {
        "case_id": NETWORK_BOTS_QUERY_CASE_ID,
        "binding_key": "network_bot_query.read",
        "plan": network_bots_query_case_plan,
    },
    "bots-mine": {
        "case_id": NETWORK_BOTS_MINE_CASE_ID,
        "binding_key": "network_bot_mine.read",
        "plan": network_bots_mine_case_plan,
    },
    "actor-list": {
        "case_id": NETWORK_ACTOR_LIST_CASE_ID,
        "binding_key": "network_actor_list.read",
        "plan": network_actor_list_case_plan,
    },
    "actor-search": {
        "case_id": NETWORK_ACTOR_SEARCH_CASE_ID,
        "binding_key": "network_actor_search.read",
        "plan": network_actor_search_case_plan,
    },
    "bots-paged": {
        "case_id": NETWORK_BOTS_PAGED_CASE_ID,
        "binding_key": "network_bots_paged.read",
        "plan": network_bots_paged_case_plan,
    },
    "platform-asset": {
        "case_id": NETWORK_PLATFORM_ASSET_CASE_ID,
        "binding_key": "network_platform_asset.read",
        "plan": network_platform_asset_case_plan,
    },
}


def _adapter(profile: dict, case_key: str):
    if case_key == "bots-query":
        return RemoteNetworkBotsQueryAdapter(profile)
    if case_key == "bots-mine":
        return RemoteNetworkBotsMineAdapter(profile)
    if case_key == "actor-list":
        return RemoteNetworkActorListAdapter(profile)
    if case_key == "actor-search":
        return RemoteNetworkActorSearchAdapter(profile)
    if case_key == "bots-paged":
        return RemoteNetworkBotsPagedAdapter(profile)
    if case_key == "platform-asset":
        return RemoteNetworkPlatformAssetAdapter(profile)
    raise ConfigError("unsupported network read case")


def _execute(adapter, case_key: str):
    if case_key == "bots-query":
        return execute_network_bots_query_case(adapter)
    if case_key == "bots-mine":
        return execute_network_bots_mine_case(adapter)
    if case_key == "actor-list":
        return execute_network_actor_list_case(adapter)
    if case_key == "actor-search":
        return execute_network_actor_search_case(adapter)
    if case_key == "bots-paged":
        return execute_network_bots_paged_case(adapter)
    if case_key == "platform-asset":
        return execute_network_platform_asset_case(adapter)
    raise ConfigError("unsupported network read case")


def load_network_read_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "network_read_case must be bots-query, bots-mine, actor-list, actor-search, bots-paged or platform-asset")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    expected = {_CASES[case_key]["binding_key"]}
    if not isinstance(bindings, dict) or set(bindings) != expected:
        raise ConfigError(
            f"network {case_key} profile must bind exactly its reviewed read operation")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            f"network {case_key} profile must be Cookie-bound and must not carry a Bot Authorization reference")
    return profile


def dual_network_read_case_run(
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
            "network_read_case must be bots-query, bots-mine, actor-list, actor-search, bots-paged or platform-asset")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"network {case_key} requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, f"network {case_key} run ID")
    profile = load_network_read_case_profile(profile_path, case_key)
    selected = _CASES[case_key]
    plan = selected["plan"]()
    selected_obligations = list(getattr(plan, "selected_obligations", (plan.requirement.requirement_id,)))
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
        raise ConfigError(f"network {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"network {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / f"network-{case_key}-profile.json"
    binding_file = state_dir / f"network-{case_key}-binding.json"
    report_file = state_dir / f"network-{case_key}-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": selected["case_id"],
        "parent_id": PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "resource_disposition": RESOURCE_DISPOSITION,
        "case_scope": {
            "requirement_id": plan.requirement.requirement_id,
            "selected_obligations": selected_obligations,
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = _adapter(profile, case_key)
        execution = _execute(adapter, case_key)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"network {case_key} case execution is unavailable: {error}") from None

    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
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
            "requirement_id": plan.requirement.requirement_id,
            "selected_obligations": selected_obligations,
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
