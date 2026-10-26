"""Dual-environment admission for the first complete Session obligation.

This is a source-bound mapping, not migration credit. It names the existing
shared PRE-02 executor so Singlebox and Gateway cannot fork private business
steps or independently weaken assertions.
"""
from __future__ import annotations

from .local_session_contract import PUBLIC_CASE as LOCAL_CASE, PARENT, REQUIREMENTS, session_source_binding
from .profile import ConfigError
from .remote_effects import remote_effect_admission
from .route_bindings import validate_route_bindings
from .run_state import RunStateError

CASE_ID = "PUBLIC-SESSION-JWT-WS-01"
REQUIREMENT_ID = REQUIREMENTS[0]
SOURCE_FUNCTION = "_story_connect_with_group_session_jwt"
EXECUTION_CASE_ID = "PRE-02"
LOCAL_EXECUTION_CASE_ID = LOCAL_CASE
ADAPTER_METHOD = "run_shared_session_public_case"
_REQUIRED_ADAPTER_METHODS = (
    "preflight", "group_create", "group_read", "session_create", "session_read",
    "session_update", "session_messages", "session_token", "session_ws_connect",
    "session_ws_close", "session_delete", "session_verify_deleted",
    "group_delete", "group_verify_deleted", "session_quiesce",
)
_SHARED_ROUTES = (
    "group.create", "group.read", "group.delete", "group.verify_deleted",
    "session.create", "session.read", "session.update", "session.messages",
    "session.token", "session.delete", "session.verify_deleted",
)


def _require_selected_source_row() -> dict:
    binding = session_source_binding()
    if binding["parent"] != PARENT:
        raise RunStateError("Session source parent changed")
    row = next((item for item in binding["requirements"]
                if item["id"] == REQUIREMENT_ID and item["function"] == SOURCE_FUNCTION), None)
    if row is None:
        raise RunStateError("public Session/JWT/WS source obligation changed")
    return {"requirement_id": row["id"], "source_function": row["function"],
            "source_expectation": row["expectation"]}


def session_public_case_plan() -> dict:
    source = _require_selected_source_row()
    return {
        "case_id": CASE_ID,
        "legacy_id": PARENT,
        "requirement_id": source["requirement_id"],
        "source_function": source["source_function"],
        "source_expectation": source["source_expectation"],
        "execution_case_id": EXECUTION_CASE_ID,
        "local_execution_case_id": LOCAL_EXECUTION_CASE_ID,
        "adapter_method": ADAPTER_METHOD,
        "shared_routes": list(_SHARED_ROUTES),
        "environments": ["owned-local-shared-pilot", "singlebox-public-v1", "preprod-public-v1"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def session_public_adaptability() -> dict:
    plan = session_public_case_plan()
    return {
        **plan,
        "blocker": (
            "The shared PRE-02 executor is the only admitted implementation. Singlebox can run it in "
            "an owned runtime once prepared; Gateway remains blocked by the reviewed group/session "
            "effect admission until explicit preproduction authorization. This slice does not "
            "migrate the other eight Session obligations or grant release credit."
        ),
    }


def session_public_case_admission(profile: dict) -> dict:
    """Off-line, request-free admission for a single environment."""
    if not isinstance(profile, dict):
        raise ConfigError("public Session/JWT/WS admission requires a validated profile")
    entrypoint = profile.get("entrypoint", {})
    environment = profile.get("environment", {})
    if (entrypoint.get("kind") != "singlebox" or environment.get("tier") != "local") and (
            entrypoint.get("kind") != "gateway" or environment.get("tier") != "preprod"):
        raise ConfigError("public Session/JWT/WS requires an owned local Singlebox or Gateway preprod profile")
    plan = session_public_case_plan()
    if entrypoint.get("api_face") != "public-v1":
        raise ConfigError("public Session/JWT/WS requires the exact public-v1 entrypoint")
    try:
        routes = validate_route_bindings(profile.get("route_bindings"), api_face=entrypoint["api_face"])
    except (KeyError, TypeError, ConfigError) as error:
        raise ConfigError(f"public Session/JWT/WS route binding is invalid: {error}") from None
    undeclared = [name for name in _SHARED_ROUTES if name not in routes]
    if undeclared:
        raise ConfigError("public Session/JWT/WS route declaration is incomplete")
    effect = remote_effect_admission(profile)
    status = "SINGLEBOX_OWNED_LOCAL_READY" if effect["status"] == "OWNED_LOCAL_SCOPE" else "GATEWAY_EFFECT_BLOCKED"
    return {
        "case_id": plan["case_id"],
        "requirement_id": plan["requirement_id"],
        "execution_case_id": EXECUTION_CASE_ID,
        "status": status,
        "ready_to_execute": False,
        "remote_validation": "NOT_RUN",
        "route_status": "PUBLIC_V1_BOUND",
        "effect_admission": effect,
        "grants_release_credit": False,
    }


def validate_session_public_adapter(adapter: object) -> None:
    required = (ADAPTER_METHOD, *_REQUIRED_ADAPTER_METHODS)
    missing = [name for name in required if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError("shared Session/JWT/WS adapter is missing methods: " + ", ".join(missing))


def project_session_public_result(report: dict) -> dict:
    """Project a PRE-02 report without extending its business credit."""
    if not isinstance(report, dict) or report.get("case_id") != EXECUTION_CASE_ID:
        raise RunStateError("shared Session/JWT/WS result is not the PRE-02 executor report")
    return {
        "case_id": CASE_ID,
        "parent_id": PARENT,
        "requirement_id": REQUIREMENT_ID,
        "execution_case_id": EXECUTION_CASE_ID,
        "functional": report.get("functional"),
        "cleanup": report.get("cleanup"),
        "status": report.get("status"),
        "ready_to_execute": False,
        "remote_validation": "NOT_RUN",
        "full_story_status": "NOT_MIGRATED",
        "grants_release_credit": False,
    }
