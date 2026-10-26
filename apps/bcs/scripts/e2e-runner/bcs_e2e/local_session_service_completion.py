"""Owned-local Session Service completion and service-roundtrip slices."""

from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_operator_cli_contract import sessions_services_source_binding
from .local_session_service_cli import (
    SESSION_SERVICE_LIFECYCLE_PARENT,
    RemoteLocalSessionServiceLifecycleAdapter,
)
from .run_state import RunStateError
from .session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    validate_session_cli_complete_invoke_profile,
)

SESSION_SERVICE_COMPLETE_CASE_ID = "LOCAL-SESSION-SERVICE-COMPLETE-01"
SESSION_SERVICE_COMPLETE_CASE_METHOD = "execute_local_session_service_complete_case"
SESSION_SERVICE_COMPLETE_REQUIREMENT = "CLI-SESSION-SERVICE.session-complete"
SESSION_SERVICE_COMPLETE_EFFECT = "OWNED_SESSION_COMPLETION_THEN_GENERIC_GROUP_DELETE"
SESSION_SERVICE_ROUNDTRIP_CASE_ID = "LOCAL-SESSION-SERVICE-ROUNDTRIP-01"
SESSION_SERVICE_ROUNDTRIP_CASE_METHOD = "execute_local_session_service_roundtrip_case"
SESSION_SERVICE_ROUNDTRIP_REQUIREMENT = "CLI-SESSION-SERVICE.service-roundtrip"
SESSION_SERVICE_ROUNDTRIP_EFFECT = (
    "OWNED_SESSION_COMPLETION_AND_INVOCATION_THEN_CREATOR_SCOPED_CLEANUP")
SERVICE_TITLE = "CLI automated release audit"
COMPLETION_OUTPUT = {"summary": "approved by CLI"}


def _source_requirement(requirement_id: str) -> dict:
    binding = sessions_services_source_binding()
    row = next((item for item in binding["requirements"]
                if item["id"] == requirement_id
                and binding["parent"] == SESSION_SERVICE_LIFECYCLE_PARENT), None)
    if row is None:
        raise RunStateError("Session Service completion source obligation changed")
    return row


def _case_plan(*, case_id: str, requirement_id: str, function_name: str) -> dict:
    row = _source_requirement(requirement_id)
    return {
        "case_id": case_id,
        "parent_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_id": requirement_id,
        "source_function": function_name,
        "source_expectation": row["expectation"],
    }


def local_session_service_complete_case_plan() -> dict:
    return _case_plan(
        case_id=SESSION_SERVICE_COMPLETE_CASE_ID,
        requirement_id=SESSION_SERVICE_COMPLETE_REQUIREMENT,
        function_name="local_session_service_complete_case_plan",
    )


def local_session_service_roundtrip_case_plan() -> dict:
    return _case_plan(
        case_id=SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        requirement_id=SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
        function_name="local_session_service_roundtrip_case_plan",
    )


def local_session_service_complete_adaptability() -> dict:
    return {
        "case_id": SESSION_SERVICE_COMPLETE_CASE_ID,
        "legacy_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_ids": [SESSION_SERVICE_COMPLETE_REQUIREMENT],
        "source_function": "_story_complete_and_invoke_sessions",
        "selected_obligations": [
            "cli-group-create",
            "explicit-session-create",
            "session-completion",
            "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-verified local and preprod contexts; live effect admission remains a separate gate.",
    }


def local_session_service_roundtrip_adaptability() -> dict:
    return {
        "case_id": SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        "legacy_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_ids": [SESSION_SERVICE_ROUNDTRIP_REQUIREMENT],
        "source_function": "_story_complete_and_invoke_sessions",
        "selected_obligations": [
            "cli-service-group-create",
            "explicit-session-create",
            "session-completion",
            "service-concurrency-config",
            "detached-service-invocation",
            "service-status-readback",
            "bounded-service-wait",
            "creator-scoped-session-delete",
            "bot-scoped-service-session-delete",
            "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-service",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-verified local and preprod contexts; live service effect admission remains a separate gate.",
    }


class RemoteLocalSessionServiceCompleteAdapter(
        RemoteLocalSessionServiceLifecycleAdapter):
    """Complete one exact owned Session, then remove its exact Group."""

    _profile_validator = staticmethod(validate_session_cli_complete_invoke_profile)

    @staticmethod
    def _credential_kind() -> str:
        return "owned_local_bot_token"

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        auth_kind = profile["auth"]["kind"]
        if auth_kind in {"owned-bot-tokens", "owned-local-bot-tokens"}:
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {
                    "Authorization": profile["auth"]["driver_token_env"],
                },
            }
        else:
            value["auth"] = {
                "kind": "human-cookie",
                "header_env": {"Cookie": profile["auth"]["cookie_env"]},
            }
        value["entrypoint"] = {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return value

    def __init__(self, profile: dict, runner: object):
        super().__init__(profile, runner)
        self.fixture_effect = SESSION_SERVICE_COMPLETE_EFFECT

    def create_owned_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError(
                "Session Service completion must follow Group setup once")
        call = self._run(
            "session-cli.session.create", group_id=self.group_id,
            session_title="Release sign-off")
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "Release sign-off"):
            raise RunStateError(
                "Session Service completion explicit Session is not exact")
        self.session_id = data["session_id"]
        return call

    def complete_owned_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError(
                "Session Service completion requires its exact Session")
        call = self._run(
            "session-cli.session.complete", session_id=self.session_id)
        data = call.data
        if (not isinstance(data, dict)
                or data.get("id") != self.session_id
                or (data.get("session_id") is not None
                    and data.get("session_id") != self.session_id)
                or data.get("group_id") != self.group_id
                or data.get("status") != "completed"
                or data.get("output") != COMPLETION_OUTPUT):
            raise RunStateError(
                "Session Service CLI completion acknowledgement is not exact")
        return call


class RemoteLocalSessionServiceRoundtripAdapter(
        RemoteLocalSessionServiceCompleteAdapter):
    """Complete a Session, invoke a detached service, and clean both exactly."""

    def __init__(self, profile: dict, runner: object):
        super().__init__(profile, runner)
        self.service_session_id: str | None = None
        self.fixture_effect = SESSION_SERVICE_ROUNDTRIP_EFFECT

    def create_service_cli_group(self) -> AdapterCall:
        call = self._run("session-cli.service.group.create")
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(data["group_id"] + ":")):
            raise RunStateError(
                "Session Service CLI service Group setup is not exact")
        self.group_id = data["group_id"]
        return call

    def _record(self, record: dict, *, case_class: str) -> dict:
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": self._credential_kind(),
            "case_class": case_class,
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return evidence

    def configure_service_concurrency(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError(
                "Session Service invocation config requires its exact Group")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
            group_id=self.group_id,
            body={"service_spec": {"max_concurrency": 2}}, expected=(200,))
        if (not isinstance(data, dict)
                or data.get("id") != self.group_id
                or data.get("status") != "ok"
                or not isinstance(data.get("service_spec"), dict)
                or data["service_spec"].get("max_concurrency") != 2):
            raise RunStateError(
                "Session Service concurrency acknowledgement is not exact")
        return AdapterCall(
            data={"group_id": self.group_id, "max_concurrency": 2},
            evidence=self._record(record, case_class="service-concurrency-config"))

    def invoke_detached_service(self) -> AdapterCall:
        if self.group_id is None or self.service_session_id is not None:
            raise RunStateError(
                "Session Service detached invocation must follow its exact Group")
        call = self._run("session-cli.service.invoke", group_id=self.group_id)
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("session_kind") != "service_invocation"
                or data.get("group_id") != self.group_id
                or data.get("session_title") != SERVICE_TITLE):
            raise RunStateError(
                "Session Service detached invocation acknowledgement is not exact")
        self.service_session_id = data["session_id"]
        return call

    def read_service_status(self) -> AdapterCall:
        if self.group_id is None or self.service_session_id is None:
            raise RunStateError(
                "Session Service status requires its exact invoked Session")
        call = self._run(
            "session-cli.service.status", group_id=self.group_id,
            session_id=self.service_session_id)
        data = call.data
        if (not isinstance(data, dict)
                or data.get("session_id") != self.service_session_id
                or data.get("group_id") != self.group_id
                or data.get("session_title") != SERVICE_TITLE
                or data.get("status") not in {"queued", "running", "completed"}):
            raise RunStateError(
                "Session Service status did not preserve its exact identity")
        return call

    def wait_for_service_terminal_contract(self) -> AdapterCall:
        if self.group_id is None or self.service_session_id is None:
            raise RunStateError(
                "Session Service bounded wait requires its exact invoked Session")
        call = self._run(
            "session-cli.service.wait", group_id=self.group_id,
            session_id=self.service_session_id)
        data = call.data
        if data == {
                "status": "timeout", "session_id": self.service_session_id,
                "timeout_ms": 1,
                "timeout_contract": "session-specific-bounded-timeout"}:
            return call
        if (not isinstance(data, dict)
                or data.get("session_id") != self.service_session_id
                or data.get("group_id") != self.group_id
                or data.get("session_title") != SERVICE_TITLE
                or data.get("status") != "completed"):
            raise RunStateError(
                "Session Service bounded wait did not return a reviewed terminal contract")
        return call

    def delete_owned_chat_session(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError(
                "Session Service cleanup requires its exact chat Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            session_id=self.session_id, expected=(200,))
        if (not isinstance(data, dict)
                or data.get("deleted") is not True
                or data.get("session_id") != self.session_id
                or record.get("http_status") != 200):
            raise RunStateError(
                "Session Service chat Session cleanup is not exact")
        return AdapterCall(
            data=data,
            evidence=self._record(record, case_class="chat-session-cleanup"))

    def delete_owned_service_session(self) -> AdapterCall:
        if self.service_session_id is None:
            raise RunStateError(
                "Session Service cleanup requires its exact service Session")
        data, record = self.cleanup_client.call(
            SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
            session_id=self.service_session_id, expected=(200,))
        if (not isinstance(data, dict)
                or data.get("deleted") is not True
                or data.get("session_id") != self.service_session_id
                or record.get("http_status") != 200):
            raise RunStateError(
                "Session Service service Session cleanup is not exact")
        return AdapterCall(
            data=data,
            evidence=self._record(record, case_class="service-session-cleanup"))


def _validate_requests(adapter: object, calls: tuple[AdapterCall, ...],
                       operations: tuple[str, ...]) -> list[dict]:
    records: list[dict] = []
    for call, operation in zip(calls, operations):
        if not isinstance(call, AdapterCall):
            raise RunStateError(
                f"Session Service completion path {operation} did not return AdapterCall")
        call.validate()
        if (call.evidence.get("credential_kind") != adapter._credential_kind()
                or call.evidence.get("remote_validation") != "NOT_RUN"
                or call.evidence.get("grants_release_credit") is not False
                or call.evidence.get("fixture_effect") != adapter.fixture_effect):
            raise RunStateError(
                f"Session Service completion path evidence for {operation} is invalid")
        records.append(dict(call.evidence))
    return records


def execute_local_session_service_complete_case(adapter: object) -> CaseExecution:
    local_session_service_complete_case_plan()
    if not isinstance(adapter, RemoteLocalSessionServiceCompleteAdapter):
        raise RunStateError(
            "Session Service completion requires its shared adapter")
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.complete_owned_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.complete",
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        records = _validate_requests(adapter, calls, operations)
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_COMPLETE_REQUIREMENT,
        method=SESSION_SERVICE_COMPLETE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "session_completed": True,
            "completion_output_preserved": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": adapter.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_SERVICE_COMPLETE_CASE_ID,
        parent_id=SESSION_SERVICE_LIFECYCLE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,),
        completed_count=1 if error_type is None else 0,
        total_count=1,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


def execute_local_session_service_roundtrip_case(adapter: object) -> CaseExecution:
    local_session_service_roundtrip_case_plan()
    if not isinstance(adapter, RemoteLocalSessionServiceRoundtripAdapter):
        raise RunStateError(
            "Session Service roundtrip requires its shared adapter")
    error_type: str | None = None
    group_id = ""
    session_id = ""
    service_session_id = ""
    try:
        calls = (
            adapter.create_service_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.complete_owned_session(),
            adapter.configure_service_concurrency(),
            adapter.invoke_detached_service(),
            adapter.read_service_status(),
            adapter.wait_for_service_terminal_contract(),
            adapter.delete_owned_chat_session(),
            adapter.delete_owned_service_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.service.group.create",
            "session-cli.session.create",
            "session-cli.session.complete",
            SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
            "session-cli.service.invoke",
            "session-cli.service.status",
            "session-cli.service.wait",
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        records = _validate_requests(adapter, calls, operations)
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
        service_session_id = adapter.service_session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
        method=SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "service_session_id": service_session_id,
            "service_identity_preserved": True,
            "bounded_wait_contract_exact": True,
            "chat_session_deleted": True,
            "service_session_deleted": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": adapter.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        parent_id=SESSION_SERVICE_LIFECYCLE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,),
        completed_count=1 if error_type is None else 0,
        total_count=1,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution

