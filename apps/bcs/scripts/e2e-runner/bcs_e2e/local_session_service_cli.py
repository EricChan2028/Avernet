"""Owned-local Session Service product-CLI lifecycle slice."""

from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_operator_cli_contract import sessions_services_source_binding
from .run_state import RunStateError
from .session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_lifecycle_profile,
)
from .write_transport import WriteClient


# Owned-local Session Service CLI lifecycle slice
SESSION_SERVICE_LIFECYCLE_CASE_ID = "LOCAL-SESSION-SERVICE-LIFECYCLE-01"
SESSION_SERVICE_LIFECYCLE_PARENT = "story_cli_operator_runs_sessions_and_services"
SESSION_SERVICE_LIFECYCLE_REQUIREMENT = "CLI-SESSION-SERVICE.session-lifecycle"
SESSION_SERVICE_LIFECYCLE_CASE_METHOD = "execute_local_session_service_lifecycle_case"
FIXTURE_EFFECT = "OWNED_SESSION_LIFECYCLE_THEN_GENERIC_GROUP_DELETE"


def local_session_service_lifecycle_case_plan() -> dict:
    binding = sessions_services_source_binding()
    row = next((
        item for item in binding["requirements"]
        if item["id"] == SESSION_SERVICE_LIFECYCLE_REQUIREMENT
        and binding["parent"] == SESSION_SERVICE_LIFECYCLE_PARENT
    ), None)
    if row is None:
        raise RunStateError("Session Service lifecycle source obligation changed")
    return {
        "case_id": SESSION_SERVICE_LIFECYCLE_CASE_ID,
        "parent_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_id": SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
        "source_expectation": row["expectation"],
    }


def local_session_service_lifecycle_adaptability() -> dict:
    return {
        "case_id": SESSION_SERVICE_LIFECYCLE_CASE_ID,
        "legacy_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_ids": [SESSION_SERVICE_LIFECYCLE_REQUIREMENT],
        "selected_obligations": [
            "cli-group-create",
            "explicit-session-create",
            "session-list",
            "session-get",
            "session-patch",
            "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-verified local and preprod credential contexts; live effect admission remains a separate gate.",
    }


class RemoteLocalSessionServiceLifecycleAdapter:
    """Run one exact Session create/list/get/patch lifecycle, then clean up its Group."""

    _profile_validator = staticmethod(validate_session_cli_lifecycle_profile)

    def __init__(self, profile: dict, runner: object):
        self.profile = self._profile_validator(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Session Service lifecycle requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.session_id: str | None = None
        self.fixture_effect = FIXTURE_EFFECT
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        if profile["environment"]["tier"] == "local":
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {"Authorization": profile["auth"]["driver_token_env"]},
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

    def _credential_kind(self) -> str:
        return ("owned_local_bot_token"
                if self.profile["environment"]["tier"] == "local"
                else "human_cookie")

    def _run(self, operation: str, **kwargs) -> AdapterCall:
        try:
            result = self.runner.execute(operation, **kwargs)
        except Exception as error:
            raise RunStateError(
                f"Session Service product-CLI execution for {operation} failed"
            ) from error
        if (not isinstance(result, dict)
                or set(result) != {"data", "evidence"}
                or not isinstance(result["evidence"], dict)):
            raise RunStateError(f"Session Service CLI result for {operation} is invalid")
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": operation,
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_owned_cli_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("Session Service CLI Group creation is a one-shot operation")
        call = self._run("session-cli.group.create")
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(data["group_id"] + ":")):
            raise RunStateError("Session Service CLI Group setup is not exact")
        self.group_id = data["group_id"]
        return call

    def create_owned_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError("Session Service CLI must follow Group setup once")
        call = self._run(
            "session-cli.session.create", group_id=self.group_id,
            session_title="CLI full session")
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "CLI full session"):
            raise RunStateError("Session Service CLI explicit Session create is not exact")
        self.session_id = data["session_id"]
        return call

    def read_session_list(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session Service CLI list must follow explicit Session create")
        call = self._run("session-cli.session.list", group_id=self.group_id)
        data = call.data
        items = data.get("items") if isinstance(data, dict) else None
        exact = [row for row in items if isinstance(row, dict)
                 and (row.get("session_id") or row.get("id")) == self.session_id] \
            if isinstance(items, list) else []
        if (not isinstance(data, dict)
                or set(data) != {"items", "group_id"}
                or data.get("group_id") != self.group_id
                or not isinstance(items, list)
                or len(exact) != 1
                or exact[0].get("group_id") != self.group_id):
            raise RunStateError("Session Service CLI list did not contain the exact Session")
        return call

    def read_session_exact(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session Service CLI get must follow list")
        call = self._run("session-cli.session.get", session_id=self.session_id)
        data = call.data
        if (not isinstance(data, dict)
                or data.get("id") != self.session_id
                or data.get("session_id") != self.session_id
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "CLI full session"):
            raise RunStateError("Session Service CLI get did not return the exact Session")
        return call

    def rename_session_title(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session Service CLI patch requires its exact Session")
        call = self._run(
            "session-cli.session.patch", session_id=self.session_id,
            session_title="CLI release sign-off")
        data = call.data
        if (not isinstance(data, dict)
                or data.get("id") != self.session_id
                or (data.get("session_id") is not None
                    and data.get("session_id") != self.session_id)
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "CLI release sign-off"):
            raise RunStateError("Session Service CLI patch did not return the exact renamed Session")
        return call

    def delete_owned_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session Service CLI cleanup requires the exact Group ID")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_DELETE_OPERATION,
            group_id=self.group_id, expected=(200,))
        if (not isinstance(data, dict)
                or data != {"deleted": True, "id": self.group_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session Service CLI Group delete acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": self._credential_kind(),
            "case_class": "cleanup-delete",
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def verify_group_deleted(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session Service CLI cleanup requires the exact Group ID")
        data, record = self.cleanup_client.call(
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
            group_id=self.group_id, expected=(404,), parse_error=True)
        if (not isinstance(data, dict)
                or data.get("status") != 404
                or data.get("code") != "group_not_found"
                or data.get("params") != {"group_id": self.group_id}
                or not isinstance(data.get("message"), str)
                or not data["message"]
                or record.get("http_status") != 404):
            raise RunStateError("Session Service CLI deleted Group was not an exact 404")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": self._credential_kind(),
            "case_class": "cleanup-404-proof",
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)


def execute_local_session_service_lifecycle_case(adapter: object) -> CaseExecution:
    local_session_service_lifecycle_case_plan()
    if not isinstance(adapter, RemoteLocalSessionServiceLifecycleAdapter):
        raise RunStateError("Session Service lifecycle requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.read_session_list(),
            adapter.read_session_exact(),
            adapter.rename_session_title(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.list",
            "session-cli.session.get",
            "session-cli.session.patch",
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(f"Session Service CLI {operation} did not return AdapterCall")
            call.validate()
            if (call.evidence.get("credential_kind") != adapter._credential_kind()
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False
                    or call.evidence.get("fixture_effect") != adapter.fixture_effect):
                raise RunStateError(f"Session Service CLI evidence for {operation} is invalid")
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
        method=SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": adapter.session_id or "",
            "session_created": True,
            "session_listed": True,
            "session_read": True,
            "session_renamed": True,
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
        case_id=SESSION_SERVICE_LIFECYCLE_CASE_ID,
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

# Owned-local Session Service member-chat lifecycle slice
SESSION_SERVICE_MEMBER_CHAT_CASE_ID = "LOCAL-SESSION-SERVICE-MEMBER-CHAT-01"
SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD = "execute_local_session_service_member_chat_case"
SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT = "CLI-SESSION-SERVICE.member-chat-lifecycle"
SESSION_SERVICE_MEMBER_CHAT_FIXTURE_EFFECT = (
    "OWNED_SESSION_MEMBER_CHAT_THEN_GENERIC_GROUP_DELETE"
)


def local_session_service_member_chat_case_plan() -> dict:
    binding = sessions_services_source_binding()
    row = next((
        item for item in binding["requirements"]
        if item["id"] == SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT
        and binding["parent"] == SESSION_SERVICE_LIFECYCLE_PARENT
    ), None)
    if row is None:
        raise RunStateError("Session Service member-chat source obligation changed")
    return {
        "case_id": SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
        "parent_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_id": SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
        "source_expectation": row["expectation"],
    }


def local_session_service_member_chat_adaptability() -> dict:
    return {
        "case_id": SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
        "legacy_id": SESSION_SERVICE_LIFECYCLE_PARENT,
        "requirement_ids": [SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT],
        "selected_obligations": [
            "cli-group-create",
            "explicit-session-create",
            "session-member-add",
            "session-member-mute",
            "session-get",
            "session-chat",
            "session-message-history",
            "session-invite-link",
            "session-member-remove",
            "session-get",
            "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": "Mock-verified local and preprod product-CLI contexts; live effect admission remains a separate gate.",
    }


class RemoteLocalSessionServiceMemberChatAdapter(
        RemoteLocalSessionServiceLifecycleAdapter):
    """Run one exact Session member/chat/invite/remove lifecycle, then clean up."""

    def __init__(self, profile: dict, runner: object):
        super().__init__(profile, runner)
        self.fixture_effect = SESSION_SERVICE_MEMBER_CHAT_FIXTURE_EFFECT
        self.chat_message = "CLI member-chat lifecycle check"

    def _member_state(self, data: object, *, present: bool,
                      mode: str | None = None) -> dict:
        if (not isinstance(data, dict)
                or data.get("id") != self.session_id
                or data.get("session_id") != self.session_id
                or data.get("group_id") != self.group_id):
            raise RunStateError(
                "Session Service member-chat response is not bound to the exact Session")
        participants = data.get("participants")
        if not isinstance(participants, list):
            raise RunStateError("Session Service member-chat participants are invalid")
        rows = [row for row in participants if isinstance(row, dict)
                and row.get("bot_uuid") == self.member_id]
        if present:
            if (len(rows) != 1
                    or rows[0].get("actor_kind") != "bot"
                    or rows[0].get("role") != "consultant"):
                raise RunStateError(
                    "Session Service member-chat participant is not exact")
            if mode is not None and rows[0].get("mode") != mode:
                raise RunStateError(
                    "Session Service member-chat participant mode is not exact")
        elif rows:
            raise RunStateError(
                "Session Service removed participant is still present")
        return data

    def add_session_member(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError(
                "Session Service member-chat requires Group and Session setup")
        call = self._run(
            "session-cli.session.member.add", session_id=self.session_id)
        self._member_state(call.data, present=True)
        return call

    def mute_session_member(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError(
                "Session Service member-chat mute requires its exact Session")
        call = self._run(
            "session-cli.session.member.patch_mode", session_id=self.session_id)
        self._member_state(call.data, present=True, mode="muted")
        return call

    def read_muted_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session Service member-chat read requires its Session")
        call = self._run(
            "session-cli.session.get.muted", session_id=self.session_id)
        self._member_state(call.data, present=True, mode="muted")
        return call

    def send_session_chat(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session Service member-chat send requires its Session")
        call = self._run(
            "session-cli.session.chat", session_id=self.session_id,
            message=self.chat_message)
        data = call.data
        if (not isinstance(data, dict)
                or data.get("session_id") != self.session_id
                or data.get("group_id") != self.group_id
                or not isinstance(data.get("delivery_results"), list)):
            raise RunStateError(
                "Session Service member-chat acknowledgement is not exact")
        for result in data["delivery_results"]:
            if (not isinstance(result, dict)
                    or not isinstance(result.get("bot_uuid"), str)
                    or not result["bot_uuid"]
                    or not isinstance(result.get("delivery_type"), str)
                    or not result["delivery_type"]
                    or not isinstance(result.get("success"), bool)):
                raise RunStateError(
                    "Session Service member-chat delivery result is not exact")
        return call

    def read_session_history(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError(
                "Session Service member-chat history requires its exact Session")
        call = self._run(
            "session-cli.session.messages", session_id=self.session_id)
        data = call.data
        messages = data.get("items") if isinstance(data, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(messages, list)
                or not all(isinstance(item, dict) for item in messages)
                or not any(item.get("content") == self.chat_message
                           for item in messages)):
            raise RunStateError(
                "Session Service member-chat history did not contain the exact message")
        return call

    def create_session_invite(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError(
                "Session Service member-chat invite requires its exact Session")
        call = self._run(
            "session-cli.session.invite", session_id=self.session_id)
        data = call.data
        if (not isinstance(data, dict)
                or set(data) != {"invite_token", "expires_at", "join_url"}
                or not isinstance(data.get("invite_token"), str)
                or not data["invite_token"].isascii()
                or not 1 <= len(data["invite_token"]) <= 4096
                or any(ord(char) <= 32 or ord(char) == 127
                       for char in data["invite_token"])
                or type(data.get("expires_at")) is not int
                or data["expires_at"] <= 0
                or not isinstance(data.get("join_url"), str)
                or len(data["join_url"]) > 8192):
            raise RunStateError(
                "Session Service member-chat invitation fields are invalid")
        origin = self.profile["entrypoint"]["origin"].rstrip("/")
        expected_join_urls = {
            origin + "/sessions/join/" + data["invite_token"],
            origin + "/sessions/{session_id}/" + data["invite_token"],
        }
        if data["join_url"] not in expected_join_urls:
            raise RunStateError(
                "Session Service member-chat invitation URL is not exact")
        return call

    def remove_session_member(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError(
                "Session Service member-chat removal requires its exact Session")
        call = self._run(
            "session-cli.session.member.remove", session_id=self.session_id)
        self._member_state(call.data, present=False)
        return call

    def read_removed_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session Service member-chat removed read requires its Session")
        call = self._run(
            "session-cli.session.get.removed", session_id=self.session_id)
        self._member_state(call.data, present=False)
        return call


def execute_local_session_service_member_chat_case(adapter: object) -> CaseExecution:
    local_session_service_member_chat_case_plan()
    if not isinstance(adapter, RemoteLocalSessionServiceMemberChatAdapter):
        raise RunStateError(
            "Session Service member-chat requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.add_session_member(),
            adapter.mute_session_member(),
            adapter.read_muted_session(),
            adapter.send_session_chat(),
            adapter.read_session_history(),
            adapter.create_session_invite(),
            adapter.remove_session_member(),
            adapter.read_removed_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.member.add",
            "session-cli.session.member.patch_mode",
            "session-cli.session.get.muted",
            "session-cli.session.chat",
            "session-cli.session.messages",
            "session-cli.session.invite",
            "session-cli.session.member.remove",
            "session-cli.session.get.removed",
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(
                    f"Session Service member-chat {operation} did not return AdapterCall"
                )
            call.validate()
            if (call.evidence.get("credential_kind") != adapter._credential_kind()
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False
                    or call.evidence.get("fixture_effect")
                    != adapter.fixture_effect):
                raise RunStateError(
                    f"Session Service member-chat evidence for {operation} is invalid"
                )
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
        method=SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "member_admitted": True,
            "member_muted": True,
            "chat_delivered": True,
            "history_observable": True,
            "invite_minted": True,
            "member_removed": True,
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
        case_id=SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
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
