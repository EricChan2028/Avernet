"""Owned-local parent case for the complete Session Service CLI Story.

The four focused Session Service slices are independent, exact, and cleanup
their own resources. This parent case only composes them in the frozen
source order; a failure stops later sibling execution instead of charging
forward on an unhealthy or mis-scoped environment.
"""
from __future__ import annotations

from .case_executor import CaseExecution, StepOutcome
from .local_operator_cli_contract import (
    SESSIONS_SERVICES_PARENT,
    SESSIONS_SERVICES_REQUIREMENTS,
    sessions_services_source_binding,
)
from .local_session_service_cli import (
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
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_COMPLETE_REQUIREMENT,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
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


def session_service_parent_case_plan() -> dict:
    """Bind the four reviewed Session Service obligations to one parent case."""
    binding = sessions_services_source_binding()
    if (binding["parent"] != SESSIONS_SERVICES_PARENT
            or tuple(row["id"] for row in binding["requirements"]) != _REQUIREMENTS):
        raise RunStateError("Session Service parent source binding changed")
    return {
        "case_id": PARENT_CASE_ID,
        "legacy_id": SESSIONS_SERVICES_PARENT,
        "requirement_ids": list(_REQUIREMENTS),
        "parent_requirement_count": 4,
    }


def session_service_parent_adaptability() -> dict:
    plan = session_service_parent_case_plan()
    return {
        "case_id": plan["case_id"],
        "legacy_id": plan["legacy_id"],
        "requirement_ids": plan["requirement_ids"],
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
            "All four local contract slices are implemented and mock-verified; "
            "preprod effect admission and live execution remain separate gates."
        ),
    }


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
    completed = 0
    failed_error: str | None = None
    for index, child in enumerate(children):
        execution = child(adapters[index])
        if execution.status != "PASS":
            failed_error = execution.error_type or type(execution).__name__
            steps.append(execution.steps[0])
            for requirement_id, method in zip(
                    _REQUIREMENTS[index+1:], _METHODS[index+1:]):
                steps.append(StepOutcome(
                    requirement_id=requirement_id, method=method,
                    status="NOT_RUN", data=None, evidence={},
                    error_type=None,
                ))
            break
        steps.append(execution.steps[0])
        completed += 1
    result = CaseExecution(
        case_id=PARENT_CASE_ID,
        parent_id=SESSIONS_SERVICES_PARENT,
        status="PASS" if failed_error is None else "BLOCKED",
        steps=tuple(steps),
        completed_count=completed,
        total_count=len(_REQUIREMENTS),
        error_type=failed_error,
        ready_to_execute=failed_error is None,
    )
    result.validate()
    return result
