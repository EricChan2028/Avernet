"""Command lifecycle for the reviewed public friend auto-acceptance slice.

Each run first provisions a fresh Human-owned driver/member Bot pair through
the reviewed public-v1 register path, then creates one exact owned-Bot
friendship and reads it back.  The pair is single-run only and its retention is
disclosed in the receipt; the slice does not broaden the effect admission."""
from __future__ import annotations

from pathlib import Path

from .fresh_fixture_pair import (
    CONTRACT as FRESH_FIXTURE_CONTRACT,
    RETAINED_DISPOSITION as FRESH_FIXTURE_DISPOSITION,
    FreshFixtureProvisioningError,
    initial_fresh_fixture_receipt,
    provision_fresh_owned_bot_pair,
)
from .inventory import executor_digest
from .local_friend_public_accept_contract import (
    CASE_ID,
    FRIENDSHIP_PARENT,
    friend_public_accept_adaptability,
    friend_public_accept_case_plan,
    execute_friend_public_accept_case,
    RESOURCE_DISPOSITION,
)
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    load,
    require_resolved,
)
from .remote_friend_public_accept_adapter import RemoteFriendPublicAcceptAdapter
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-friend-public-accept-case-run-v1"
CREDENTIAL_KIND = "human_cookie"
FRESH_FIXTURE_PURPOSE = "friend-public-accept"
BINDING_KEYS = frozenset(
    {
        "friend_connection_public_accept.create",
        "friend_connection_public_accept.driver_connections.read",
    }
)


def load_friend_public_accept_case_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("friend public acceptance profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(BINDING_KEYS):
        raise ConfigError(
            "friend public acceptance profile must bind exactly its reviewed case operations")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "friend public acceptance profile must be Cookie-bound and must not carry a Bot Authorization reference")
    relationships = profile.get("relationships", {}).get("friendship")
    if relationships != "bounded-owned-bot-public-acceptance":
        raise ConfigError(
            "friend public acceptance profile must explicitly admit only bounded owned-Bot public acceptance")
    return profile


def _blocked_case_snapshot(plan, error_type: str, error_detail: str) -> dict:
    return {
        "case_id": CASE_ID,
        "parent_id": FRIENDSHIP_PARENT,
        "status": "BLOCKED",
        "completed_count": 0,
        "total_count": len(plan.requirement_ids),
        "ready_to_execute": False,
        "error_type": error_type,
        "error_detail": error_detail,
        "steps": [
            {
                "requirement_id": requirement_id,
                "method": "fresh_fixture_pair_provisioning",
                "status": "NOT_RUN",
                "data": None,
                "evidence": {},
                "error_type": None,
                "error_gap": None,
                "error_detail": None,
            }
            for requirement_id in plan.requirement_ids
        ],
    }


def dual_friend_public_accept_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "friend public acceptance case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "friend public acceptance run ID")
    profile = load_friend_public_accept_case_profile(profile_path)
    plan = friend_public_accept_case_plan()
    adaptability = friend_public_accept_adaptability()
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
        raise ConfigError("friend public acceptance case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("friend public acceptance case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "friend-public-accept-profile.json"
    binding_file = state_dir / "friend-public-accept-binding.json"
    fresh_fixture_file = state_dir / "friend-public-accept-fresh-fixture.json"
    effective_profile_file = state_dir / "friend-public-accept-effective-profile.json"
    report_file = state_dir / "friend-public-accept-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": FRIENDSHIP_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "planned_resource_disposition": RESOURCE_DISPOSITION,
        "fresh_fixture_provisioning": {
            "contract": FRESH_FIXTURE_CONTRACT,
            "roles": ["driver", "member"],
            "disposition": FRESH_FIXTURE_DISPOSITION,
        },
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
        },
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)

    fresh_fixture = initial_fresh_fixture_receipt(
        run_id=run_id, purpose=FRESH_FIXTURE_PURPOSE)
    write(fresh_fixture_file, fresh_fixture)
    effective_profile = None
    execution = None
    execution_snapshot = None
    error_type = None
    error_detail = None
    try:
        fresh = provision_fresh_owned_bot_pair(
            profile=profile,
            run_id=run_id,
            purpose=FRESH_FIXTURE_PURPOSE,
            on_progress=lambda receipt: write(fresh_fixture_file, receipt),
        )
        fresh_fixture = fresh.evidence
        effective_profile = fresh.profile
        write(fresh_fixture_file, fresh_fixture)
        write(effective_profile_file, effective_profile)
        adapter = RemoteFriendPublicAcceptAdapter(effective_profile)
        execution = execute_friend_public_accept_case(adapter)
        execution_snapshot = execution.snapshot()
        error_type = execution.error_type
        error_detail = execution.error_detail
    except FreshFixtureProvisioningError as error:
        fresh_fixture = error.evidence
        write(fresh_fixture_file, fresh_fixture)
        execution_snapshot = _blocked_case_snapshot(
            plan, type(error).__name__, str(error))
        error_type = type(error).__name__
        error_detail = str(error)
    except (ConfigError, RunStateError, OSError) as error:
        if fresh_fixture.get("status") != "PASS":
            fresh_fixture.update({
                "status": "BLOCKED",
                "error_type": type(error).__name__,
                "error_detail": str(error)[:800],
            })
        else:
            fresh_fixture["post_provisioning_error"] = {
                "type": type(error).__name__,
                "detail": str(error)[:800],
            }
        write(fresh_fixture_file, fresh_fixture)
        execution_snapshot = _blocked_case_snapshot(
            plan, type(error).__name__, str(error)[:800])
        error_type = type(error).__name__
        error_detail = str(error)[:800]

    success = execution is not None and execution.status == "PASS"
    disposition = RESOURCE_DISPOSITION if success else "UNKNOWN_OWNED_BOT_PUBLIC_ACCEPTANCE"
    if fresh_fixture.get("status") != "PASS":
        disposition = fresh_fixture.get(
            "resource_disposition", "UNKNOWN_FRESH_OWNED_BOT_PAIR")
    execution_report = (
        {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        }
        if execution is not None
        else {
            "status": "BLOCKED",
            "completed_count": 0,
            "total_count": len(plan.requirement_ids),
            "error_type": error_type,
            "error_detail": error_detail,
        }
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": FRIENDSHIP_PARENT,
        "run_id": run_id,
        "status": "PASS" if success else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "fresh_fixture": fresh_fixture,
        "case": execution_snapshot,
        "case_scope": {
            "requirement_ids": list(plan.requirement_ids),
            "selected_obligations": list(adaptability["selected_obligations"]),
        },
        "execution": execution_report,
        "resource_disposition": disposition,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report
