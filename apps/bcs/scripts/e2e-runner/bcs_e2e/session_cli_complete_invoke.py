"""Dual-environment Session completion-invocation product-CLI case."""

from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_session_contract import session_source_binding
from .run_state import RunStateError
from .session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION,
    SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION,
    SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION,
    SESSION_CLI_SERVICE_SESSION_READ_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_COMPLETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_READ_OPERATION,
    SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION,
    validate_session_cli_complete_invoke_profile,
)
from .write_transport import WriteClient


SESSION_CLI_COMPLETE_INVOKE_CASE_ID = "DUAL-SESSION-CLI-COMPLETE-INVOKE-01"
SESSION_CLI_COMPLETE_INVOKE_REQUIREMENT_ID = "SESSION.complete-invoke"
SESSION_CLI_COMPLETE_INVOKE_SOURCE_FUNCTION = "_story_complete_and_invoke_sessions"
SESSION_CLI_COMPLETE_INVOKE_CASE_METHOD = "execute_session_cli_complete_invoke_case"
SESSION_CLI_COMPLETE_INVOKE_EFFECT = (
    "OWNED_SERVICE_SIGNOFF_AND_INVOCATION_THEN_GENERIC_GROUP_DELETE")
SESSION_CLI_COMPLETE_INVOKE_CLI_OPERATIONS = frozenset({
    "session-cli.service.group.create",
    "session-cli.session.create",
})


def session_cli_complete_invoke_case_plan() -> dict:
    binding = session_source_binding()
    row = next((
        item for item in binding["requirements"]
        if item["id"] == SESSION_CLI_COMPLETE_INVOKE_REQUIREMENT_ID
        and item["function"] == SESSION_CLI_COMPLETE_INVOKE_SOURCE_FUNCTION
    ), None)
    if row is None:
        raise RunStateError("Session completion-invocation source obligation changed")
    return {
        "case_id": SESSION_CLI_COMPLETE_INVOKE_CASE_ID,
        "requirement_id": SESSION_CLI_COMPLETE_INVOKE_REQUIREMENT_ID,
        "source_function": SESSION_CLI_COMPLETE_INVOKE_SOURCE_FUNCTION,
        "source_expectation": row["expectation"],
    }


def session_cli_complete_invoke_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_COMPLETE_INVOKE_CASE_ID,
        "legacy_id": "story_user_runs_and_shares_sessions",
        "requirement_ids": [SESSION_CLI_COMPLETE_INVOKE_REQUIREMENT_ID],
        "source_function": SESSION_CLI_COMPLETE_INVOKE_SOURCE_FUNCTION,
        "selected_obligations": [
            "cli-service-group-create", "explicit-session-create",
            "delivery-observation", "delivery-query-observation",
            "delivery-cancel-rejection", "queue-resolution-rejection",
            "session-completion", "completion-readback",
            "service-concurrency-config", "service-invocation",
            "service-session-readback", "service-session-isolation",
            "service-completion-guardrail", "creator-scoped-session-delete",
            "bot-scoped-service-session-delete", "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-only; live preprod execution also requires the owned Human "
            "Cookie plus both dedicated pool Bot tokens (the reviewed ACE "
            "trio) because the service-invocation routes resolve Bot "
            "identity only [ACE-BOT-TOKEN-BEARER]."),
    }


class RemoteSessionCliCompleteInvokeAdapter:
    """Complete a service sign-off Session, then invoke one service Session."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_complete_invoke_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError(
                "Session completion-invocation requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.session_id: str | None = None
        self.service_session_id: str | None = None
        self.driver_client = WriteClient(self._actor_profile("driver_token_env"))
        self.member_client = WriteClient(self._actor_profile("member_token_env"))

    def _actor_profile(self, credential_ref: str) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        if self.profile["environment"]["tier"] == "preprod":
            # The station legacy face admits an owned Bot principal only as
            # the reviewed ACE trio: the owned Human Cookie session plus the
            # two owned Bot Bearer tokens (live evidence
            # ACE-BOT-TOKEN-BEARER; a bare Bot Bearer returns USER_NOT_LOGIN
            # / decisionBy=ACE).
            value["auth"] = {
                "kind": "human-cookie-with-owned-bot-tokens",
                "header_env": {
                    "Authorization": self.profile["auth"][credential_ref],
                    "Cookie": self.profile["auth"]["cookie_env"],
                },
                "authorization_scheme": "bearer",
            }
        else:
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {"Authorization": self.profile["auth"][credential_ref]},
            }
        value["entrypoint"] = {
            "kind": self.profile["entrypoint"]["kind"],
            "origin": self.profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return value

    def _credential_kind(self, role: str = "driver") -> str:
        if self.profile["environment"]["tier"] == "local":
            return "owned_local_bot_token"
        if role == "member":
            return "human_cookie_with_owned_member_bot_token"
        return "human_cookie_with_owned_driver_bot_token"

    def expected_credential_kind(self, operation: str) -> str:
        """Expected per-request credential label for one reviewed operation."""
        if operation in SESSION_CLI_COMPLETE_INVOKE_CLI_OPERATIONS:
            if self.profile["environment"]["tier"] == "local":
                return "owned_local_bot_token"
            return "human_cookie_with_owned_bot_tokens"
        if operation == SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION:
            return self._credential_kind("member")
        return self._credential_kind("driver")

    def create_service_cli_group(self) -> AdapterCall:
        result = self.runner.execute("session-cli.service.group.create")
        data = result.get("data") if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)):
            raise RunStateError(
                "Session completion-invocation service Group setup is not exact")
        self.group_id = data["group_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "setup",
            "fixture_effect": SESSION_CLI_COMPLETE_INVOKE_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def create_signoff_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError(
                "Session completion-invocation must follow service Group setup")
        result = self.runner.execute(
            "session-cli.session.create", group_id=self.group_id,
            session_title="Release sign-off",
        )
        data = result["data"]
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "Release sign-off"):
            raise RunStateError(
                "Session completion-invocation explicit Session is not exact")
        self.session_id = data["session_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "session-create",
            "fixture_effect": SESSION_CLI_COMPLETE_INVOKE_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def _http(self, client: WriteClient, operation: str, *,
              session_id: str = "", group_id: str = "",
              body: dict | None = None, expected: tuple[int, ...] = (200,),
              parse_error: bool = False,
              caller_role: str = "case_owned_driver",
              case_class: str) -> AdapterCall:
        if not self.group_id:
            raise RunStateError(
                f"Session completion-invocation {operation} requires its "
                "exact bound Group")
        data, record = client.call(
            operation, session_id=session_id or self.session_id or "",
            group_id=group_id or self.group_id, body=body,
            expected=expected, parse_error=parse_error)
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": caller_role,
            "credential_kind": self._credential_kind(
                "member" if caller_role == "case_owned_member" else "driver"),
            "case_class": case_class,
            "fixture_effect": SESSION_CLI_COMPLETE_INVOKE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def read_unmanaged_message_deliveries(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION,
            case_class="message-deliveries-read")
        if call.data != []:
            raise RunStateError(
                "Session completion-invocation unmanaged delivery read is "
                "not an empty array")
        return call

    def query_unmanaged_message_deliveries(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION,
            body={"message_ids": ["not-a-message"]},
            case_class="message-deliveries-query")
        if call.data != []:
            raise RunStateError(
                "Session completion-invocation delivery batch query is not "
                "an empty array")
        return call

    def reject_missing_delivery_cancel(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION,
            body={"session_id": self.session_id}, expected=(400,),
            case_class="message-delivery-cancel-rejection")
        if call.evidence.get("http_status") != 400:
            raise RunStateError(
                "Session completion-invocation missing delivery cancel was "
                "not rejected with 400")
        return call

    def reject_missing_message_cancel(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION,
            body={"session_id": self.session_id}, expected=(400,),
            case_class="message-cancel-rejection")
        if call.evidence.get("http_status") != 400:
            raise RunStateError(
                "Session completion-invocation missing message cancel was "
                "not rejected with 400")
        return call

    def reject_bot_queue_resolution(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION,
            body={
                "session_id": self.session_id,
                "expected_state_version": 3,
                "resolution": "confirmed_not_sent",
                "reason": "Bot must not manually release uncertain work",
            },
            expected=(403,),
            case_class="queue-resolution-rejection")
        if call.evidence.get("http_status") != 403:
            raise RunStateError(
                "Session completion-invocation Bot queue resolution was not "
                "rejected with 403")
        return call

    def complete_signoff_session(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_SESSION_COMPLETE_OPERATION,
            body={"output": {"summary": "release approved"}},
            case_class="session-complete")
        data = call.data
        if (not isinstance(data, dict)
                or data.get("session_id") != self.session_id
                or data.get("status") != "completed"
                or data.get("output") != {"summary": "release approved"}):
            raise RunStateError(
                "Session completion-invocation completion acknowledgement is "
                "not exact")
        return AdapterCall(
            data={
                "session_id": self.session_id,
                "status": "completed",
                "output": {"summary": "release approved"},
            },
            evidence=call.evidence)

    def read_completed_session(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_SESSION_READ_OPERATION,
            case_class="session-readback")
        data = call.data
        if (not isinstance(data, dict)
                or data.get("session_id") != self.session_id
                or data.get("status") != "completed"
                or data.get("output") != {"summary": "release approved"}):
            raise RunStateError(
                "Session completion-invocation completion read-back is not "
                "exact")
        return AdapterCall(
            data={
                "session_id": self.session_id,
                "status": "completed",
                "output_preserved": True,
            },
            evidence=call.evidence)

    def configure_service_concurrency(self) -> AdapterCall:
        call = self._http(
            self.driver_client, SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
            body={"service_spec": {"max_concurrency": 2}},
            case_class="service-concurrency-config")
        data = call.data
        if (not isinstance(data, dict)
                or data.get("id") != self.group_id
                or data.get("status") != "ok"
                or not isinstance(data.get("service_spec"), dict)
                or data["service_spec"].get("max_concurrency") != 2):
            raise RunStateError(
                "Session completion-invocation service concurrency setting "
                "is not exact")
        return AdapterCall(
            data={"group_id": self.group_id, "max_concurrency": 2},
            evidence=call.evidence)

    def invoke_service_session(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError(
                "Session completion-invocation invocation requires its exact "
                "Session")
        call = self._http(
            self.driver_client, SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION,
            body={
                "caller_id": "release-pipeline-1",
                "session_title": "Automated release audit",
                "input": {"commit": "abc123"},
                "meta": {"source": "e2e"},
            },
            expected=(202,),
            case_class="service-invocation")
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"]
                or data.get("session_kind") != "service_invocation"
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "Automated release audit"):
            raise RunStateError(
                "Session completion-invocation service invocation "
                "acknowledgement is not exact")
        self.service_session_id = data["session_id"]
        return AdapterCall(
            data={
                "session_id": self.service_session_id,
                "session_kind": "service_invocation",
                "group_id": self.group_id,
                "session_title": "Automated release audit",
            },
            evidence=call.evidence)

    def read_own_service_session(self) -> AdapterCall:
        if self.service_session_id is None:
            raise RunStateError(
                "Session completion-invocation service read-back requires its "
                "exact invoked Session")
        call = self._http(
            self.driver_client, SESSION_CLI_SERVICE_SESSION_READ_OPERATION,
            session_id=self.service_session_id,
            case_class="service-session-readback")
        data = call.data
        if (not isinstance(data, dict)
                or data.get("session_id") != self.service_session_id
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "Automated release audit"):
            raise RunStateError(
                "Session completion-invocation service session read-back is "
                "not exact")
        return AdapterCall(
            data={
                "session_id": self.service_session_id,
                "group_id": self.group_id,
                "session_title": "Automated release audit",
            },
            evidence=call.evidence)

    def reject_foreign_service_session_read(self) -> AdapterCall:
        if self.service_session_id is None:
            raise RunStateError(
                "Session completion-invocation isolation requires its exact "
                "invoked Session")
        call = self._http(
            self.member_client, SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION,
            session_id=self.service_session_id,
            expected=(403,), parse_error=True,
            caller_role="case_owned_member",
            case_class="service-session-isolation")
        if (call.data != {"error": "forbidden",
                          "message": "caller_principal mismatch"}
                or call.evidence.get("http_status") != 403):
            raise RunStateError(
                "Session completion-invocation foreign service session read "
                "was not an exact forbidden mismatch")
        return AdapterCall(
            data={"session_id": self.service_session_id,
                  "foreign_read_rejected": True},
            evidence=call.evidence)

    def reject_service_session_chat_completion(self) -> AdapterCall:
        if self.service_session_id is None:
            raise RunStateError(
                "Session completion-invocation guardrail requires its exact "
                "invoked Session")
        call = self._http(
            self.driver_client,
            SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION,
            session_id=self.service_session_id,
            body={"output": {"summary": "wrong endpoint"}},
            expected=(403,), parse_error=True,
            case_class="service-completion-guardrail")
        if (call.data != {"error": "forbidden",
                          "message": "service sessions cannot be completed "
                                     "via this endpoint"}
                or call.evidence.get("http_status") != 403):
            raise RunStateError(
                "Session completion-invocation service session chat "
                "completion was not the exact guardrail rejection")
        return AdapterCall(
            data={"session_id": self.service_session_id,
                  "guardrail_rejected": True},
            evidence=call.evidence)

    def delete_completed_session(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError(
                "Session completion-invocation creator delete requires its "
                "exact Session")
        call = self._http(
            self.driver_client, SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            session_id=self.session_id,
            case_class="session-creator-delete")
        if (call.data != {"deleted": True, "session_id": self.session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session completion-invocation creator Session delete "
                "acknowledgement is not exact")
        self.session_id = ""
        evidence = dict(call.evidence)
        return AdapterCall(
            data={"deleted": True, "session_id": ""},
            evidence=evidence)

    def delete_service_session(self) -> AdapterCall:
        if self.service_session_id is None:
            raise RunStateError(
                "Session completion-invocation service delete requires its "
                "exact invoked Session")
        call = self._http(
            self.driver_client, SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
            session_id=self.service_session_id,
            case_class="service-session-creator-delete")
        if (call.data != {"deleted": True,
                          "session_id": self.service_session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session completion-invocation service Session delete "
                "acknowledgement is not exact")
        self.service_session_id = None
        return AdapterCall(
            data={"deleted": True},
            evidence=call.evidence)

    def delete_owned_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError(
                "Session completion-invocation cleanup requires the exact "
                "Group ID")
        call = self._http(
            self.driver_client, SESSION_CLI_GROUP_DELETE_OPERATION,
            session_id="",
            case_class="cleanup-delete")
        if (call.data != {"deleted": True, "id": self.group_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session completion-invocation Group delete acknowledgement "
                "is not exact")
        return AdapterCall(
            data={"deleted": True, "id": self.group_id},
            evidence=call.evidence)

    def verify_group_deleted(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError(
                "Session completion-invocation cleanup requires the exact "
                "Group ID")
        call = self._http(
            self.driver_client, SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
            session_id="", expected=(404,), parse_error=True,
            case_class="cleanup-404-proof")
        if (not isinstance(call.data, dict)
                or call.data.get("status") != 404
                or call.data.get("code") != "group_not_found"
                or call.data.get("params") != {"group_id": self.group_id}
                or not isinstance(call.data.get("message"), str)
                or not call.data["message"]
                or call.evidence.get("http_status") != 404):
            raise RunStateError(
                "Session completion-invocation deleted Group was not an "
                "exact 404")
        return AdapterCall(
            data=call.data,
            evidence=call.evidence)


def execute_session_cli_complete_invoke_case(adapter: object) -> CaseExecution:
    session_cli_complete_invoke_case_plan()
    if not isinstance(adapter, RemoteSessionCliCompleteInvokeAdapter):
        raise RunStateError(
            "Session completion-invocation requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    try:
        calls = (
            adapter.create_service_cli_group(),
            adapter.create_signoff_cli_session(),
            adapter.read_unmanaged_message_deliveries(),
            adapter.query_unmanaged_message_deliveries(),
            adapter.reject_missing_delivery_cancel(),
            adapter.reject_missing_message_cancel(),
            adapter.reject_bot_queue_resolution(),
            adapter.complete_signoff_session(),
            adapter.read_completed_session(),
            adapter.configure_service_concurrency(),
            adapter.invoke_service_session(),
            adapter.read_own_service_session(),
            adapter.reject_foreign_service_session_read(),
            adapter.reject_service_session_chat_completion(),
            adapter.delete_completed_session(),
            adapter.delete_service_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.service.group.create",
            "session-cli.session.create",
            SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION,
            SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION,
            SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION,
            SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION,
            SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION,
            SESSION_CLI_SESSION_COMPLETE_OPERATION,
            SESSION_CLI_SESSION_READ_OPERATION,
            SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
            SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION,
            SESSION_CLI_SERVICE_SESSION_READ_OPERATION,
            SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION,
            SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION,
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(
                    f"Session completion-invocation {operation} did not "
                    "return AdapterCall")
            call.validate()
            if (call.evidence.get("credential_kind")
                    != adapter.expected_credential_kind(operation)
                    or call.evidence.get("fixture_effect") != SESSION_CLI_COMPLETE_INVOKE_EFFECT
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False):
                raise RunStateError(
                    f"Session completion-invocation evidence for {operation} "
                    "is invalid")
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_COMPLETE_INVOKE_REQUIREMENT_ID,
        method=SESSION_CLI_COMPLETE_INVOKE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "service_group_created": True,
            "signoff_session_created": True,
            "unmanaged_delivery_reads_empty": True,
            "delivery_batch_query_empty": True,
            "missing_delivery_cancel_rejected": True,
            "missing_message_cancel_rejected": True,
            "bot_queue_resolution_rejected": True,
            "signoff_session_completed": True,
            "completion_output_preserved": True,
            "service_concurrency_configured": True,
            "service_session_invoked": True,
            "own_service_session_read": True,
            "foreign_service_session_read_rejected": True,
            "service_chat_completion_rejected": True,
            "chat_session_deleted": True,
            "service_session_deleted": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": SESSION_CLI_COMPLETE_INVOKE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_CLI_COMPLETE_INVOKE_CASE_ID,
        parent_id="story_user_runs_and_shares_sessions",
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=1 if error_type is None else 0, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
