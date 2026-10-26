"""Explicit fixture planning/apply boundary.

The planner is always offline. Apply is intentionally limited to the pure
LocalMock adapter in this increment; real Singlebox/remote fixture repair must
be implemented and reviewed separately.
"""
from __future__ import annotations

from pathlib import Path

from .observation_gate import validate_pilot_actor_scope
from .profile import fingerprint, identifier, require_resolved
from .run_state import RunStateError, read
from .run_state import write


def build_fixture_plan(profile: dict) -> dict:
    kind = profile["entrypoint"]["kind"]
    roles = {
        role: {
            "bot_id": binding["bot_id"],
            "owner_id": binding["owner_id"],
            "dedicated": binding["dedicated"],
            "friendship": "unchanged",
        }
        for role, binding in profile["actors"].items()
    }
    plan = {
        "schema_version": 1,
        "status": "BLOCKED",
        "ready_to_apply": False,
        "profile_digest": fingerprint(profile),
        "environment": profile["environment"],
        "human": profile["human"],
        "roles": roles,
        "required_relations": ["same Human owns driver and member Bots", "friendship is not mutated"],
        "planned_effects": ["local fixture declaration only"],
        "forbidden_effects": ["ensure-human", "register Bot", "change friendship", "bulk cleanup"],
    }
    if kind == "mock" and profile["environment"]["tier"] == "local":
        plan["status"] = "READY"
        plan["ready_to_apply"] = True
        plan["mode"] = "local-mock"
        plan["note"] = "Apply writes only a local fixture declaration; it opens no socket and contacts no product service."
    elif kind == "singlebox":
        plan["mode"] = "local-singlebox"
        plan["note"] = "Singlebox Bot discovery/apply requires a separate reviewed local fixture handler."
    else:
        plan["mode"] = "remote-attach"
        plan["note"] = "Remote fixture repair is never implicit and requires an approved environment-specific apply handler."
    return plan


def apply_fixture_plan(profile: dict, state_dir: Path, run_id: str) -> dict:
    require_resolved(profile)
    plan = build_fixture_plan(profile)
    try:
        actor_scope = validate_pilot_actor_scope(profile)
    except Exception:
        return {**plan, "status": "BLOCKED", "ready_to_apply": False,
                "reason": "pilot actor admission failed"}
    if plan["status"] != "READY":
        return {**plan, "status": "BLOCKED", "ready_to_apply": False}
    identifier(run_id, "run_id")
    if state_dir.is_symlink() or (state_dir.exists() and not state_dir.is_dir()):
        return {**plan, "status": "BLOCKED", "ready_to_apply": False,
                "reason": "fixture state directory is not a regular private directory"}
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fixture_path = state_dir / "fixture.json"
    if fixture_path.exists() or fixture_path.is_symlink():
        return {**plan, "status": "BLOCKED", "ready_to_apply": False,
                "reason": "fixture declaration already exists; explicit reconciliation is required"}
    run_path = state_dir / "run.json"
    if run_path.exists() or run_path.is_symlink():
        try:
            state = read(run_path)
        except RunStateError:
            return {**plan, "status": "BLOCKED", "ready_to_apply": False,
                    "reason": "fixture Run state is unreadable"}
        if state.get("run_id") != run_id or state.get("profile_digest") != plan["profile_digest"]:
            return {**plan, "status": "BLOCKED", "ready_to_apply": False,
                    "reason": "fixture Run/profile binding does not match"}
    fixture = {
        "schema_version": 1,
        "status": "APPLIED",
        "run_id": run_id,
        "profile_digest": plan["profile_digest"],
        "mode": "local-mock",
        "human": profile["human"],
        "roles": plan["roles"],
        "friendship": actor_scope["friendship"],
        "actor_scope": actor_scope,
        "network_contacted": False,
    }
    write(fixture_path, fixture)
    return fixture
