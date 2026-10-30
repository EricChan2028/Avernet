"""Owned-local parent case for the complete Session Service CLI Story.

The four focused Session Service slices are independent, exact, and cleanup
their own resources. This parent case composes them in the frozen source order
and preserves every parsed field-level assertion point; a failure stops later
sibling execution instead of charging forward on an unhealthy or mis-scoped
environment.
"""
from __future__ import annotations

from .case_executor import CaseExecution, StepOutcome
from .local_operator_cli_contract import (
    SESSIONS_SERVICES_PARENT,
    SESSIONS_SERVICES_REQUIREMENTS,
    sessions_services_source_binding,
)
from .local_session_service_cli import (
    LIFECYCLE_ASSERTION_IDS,
    MEMBER_CHAT_ASSERTION_IDS,
    SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
    SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
    SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
    SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
    RemoteLocalSessionServiceMemberChatAdapter,
    RemoteLocalSessionServiceLifecycleAdapter,
    execute_local_session_service_lifecycle_case,
    execute_local_session_service_member_chat_case,
)
from .local_session_service_completion import (
    COMPLETE_ASSERTION_IDS,
    ROUNDTRIP_ASSERTION_IDS,
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_COMPLETE_REQUIREMENT,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
    SESSION_SERVICE_ASSERTION_IDS,
    RemoteLocalSessionServiceCompleteAdapter,
    RemoteLocalSessionServiceRoundtripAdapter,
    execute_local_session_service_complete_case,
    execute_local_session_service_roundtrip_case,
)
from .run_state import RunStateError

PARENT_CASE_ID = "LOCAL-SESSION-SERVICE-PARENT-01"
PARENT_CASE_SUITE = "owned-local-session-service-parent-v1"
PARENT_CASE_METHOD = "execute_local_session_service_parent_case"
_REQUIREMENTS = SESSIONS_SERVICES_REQUIREMENTS
_METHODS = (
    SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
    SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
)
_ASSERTION_GROUPS = (
    (LIFECYCLE_ASSERTION_IDS, SESSION_SERVICE_LIFECYCLE_CASE_METHOD),
    (MEMBER_CHAT_ASSERTION_IDS, SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD),
    (COMPLETE_ASSERTION_IDS, SESSION_SERVICE_COMPLETE_CASE_METHOD),
    (ROUNDTRIP_ASSERTION_IDS, SESSION_SERVICE_ROUNDTRIP_CASE_METHOD),
)


def session_service_parent_case_plan() -> dict:
    """Bind the four reviewed Session Service obligations to one parent case."""
    binding = sessions_services_source_binding()
    if (binding["parent"] != SESSIONS_SERVICES_PARENT
            or tuple(row["id"] for row in binding["requirements"]) != _REQUIREMENTS):
        raise RunStateError("Session Service parent source binding changed")
    if tuple(assertion_id for group, _ in _ASSERTION_GROUPS for assertion_id in group) \
            != SESSION_SERVICE_ASSERTION_IDS:
        raise RunStateError("Session Service parsed assertion inventory changed")
    return {
        "case_id": PARENT_CASE_ID,
        "legacy_id": SESSIONS_SERVICES_PARENT,
        "requirement_ids": list(_REQUIREMENTS),
        "parent_requirement_count": 4,
        "assertion_ids": list(SESSION_SERVICE_ASSERTION_IDS),
        "assertion_count": len(SESSION_SERVICE_ASSERTION_IDS),
    }


def session_service_parent_adaptability() -> dict:
    plan = session_service_parent_case_plan()
    return {
        "case_id": plan["case_id"],
        "legacy_id": plan["legacy_id"],
        "requirement_ids": plan["requirement_ids"],
        "assertion_ids": plan["assertion_ids"],
        "assertion_count": plan["assertion_count"],
        "selected_obligations": [
            "session-lifecycle",
            "member-chat-lifecycle",
            "session-complete",
            "service-roundtrip",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_PARENT_EXECUTOR",
        "cli_surface": "product-bcs-cli-session-service",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "All four local contract slices are implemented and mock-verified "
            "at 44 parsed assertion points; preprod effect admission and live "
            "execution remain separate gates."
        ),
    }


def _not_run_group(index: int) -> list[StepOutcome]:
    assertion_ids, method = _ASSERTION_GROUPS[index]
    return [StepOutcome(requirement_id=assertion_id, method=method,
                        status="NOT_RUN", data=None, evidence={},
                        error_type=None)
            for assertion_id in assertion_ids]


def execute_local_session_service_parent_case(profile: dict, runner: object) -> CaseExecution:
    """Compose the four reviewed Session Service slices into one exact case."""
    session_service_parent_case_plan()
    adapters = (
        RemoteLocalSessionServiceLifecycleAdapter(profile, runner),
        RemoteLocalSessionServiceMemberChatAdapter(profile, runner),
        RemoteLocalSessionServiceCompleteAdapter(profile, runner),
        RemoteLocalSessionServiceRoundtripAdapter(profile, runner),
    )
    children = (
        execute_local_session_service_lifecycle_case,
        execute_local_session_service_member_chat_case,
        execute_local_session_service_complete_case,
        execute_local_session_service_roundtrip_case,
    )
    steps: list[StepOutcome] = []
    failed_error: str | None = None
    for index, child in enumerate(children):
        if failed_error is not None:
            steps.extend(_not_run_group(index))
            continue
        execution = child(adapters[index])
        steps.extend(execution.steps)
        if execution.status != "PASS":
            failed_error = execution.error_type or type(execution).__name__
    execution = CaseExecution(
        case_id=PARENT_CASE_ID,
        parent_id=SESSIONS_SERVICES_PARENT,
        status="PASS" if failed_error is None else "BLOCKED",
        steps=tuple(steps),
        completed_count=sum(step.status == "PASS" for step in steps),
        total_count=len(steps),
        error_type=failed_error,
        ready_to_execute=failed_error is None,
    )
    execution.validate()
    return execution
