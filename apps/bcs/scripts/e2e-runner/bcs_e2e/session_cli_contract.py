"""Reviewed product-CLI Session slices of the Session-file contract module.

Split out of :mod:`bcs_e2e.session_file_core_contract` to keep every executor
source file within the reviewed 1,000-line limit.  The CLI create/list/get and
Bot-group slices below are unchanged; the parent module re-exports them so the
command registry and the shared case guard keep their existing imports.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass

from .file_transport import RemoteFileClient

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_session_contract import PARENT, session_source_binding
from .run_state import RunStateError
from .write_transport import WriteClient
from .workspace_cli_runner import SESSION_CLI_CREATE_GET_EFFECT
from .session_cli_profile import (
    SESSION_CLI_BOT_GROUPS_OPERATION,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    validate_session_cli_create_get_profile,
)


# Dual product-CLI Session create/list/get case
CASE_CLASSES = {
    "session-cli.group.create": "setup",
    "session-cli.session.create": "create",
    "session-cli.session.list": "list",
    "session-cli.session.get": "get",
}
FINAL_EFFECT = "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET"


class RemoteSessionCliCreateGetAdapter:
    """Execute one owned CLI Session lifecycle and exact parent cleanup."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_create_get_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Session CLI create/list/get requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.initial_session_id = ""
        self.session_id: str | None = None
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        """Use the reviewed framework transport only for the two cleanup routes."""
        value = copy.deepcopy(profile)
        if profile["environment"]["tier"] == "local":
            value["auth"] = {"kind": "authenticated-product",
                             "header_env": {
                                 "Authorization": profile["auth"]["driver_token_env"]}}
        else:
            value["auth"] = {"kind": "human-cookie",
                             "header_env": {"Cookie": profile["auth"]["cookie_env"]}}
        value["entrypoint"] = {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return json.loads(json.dumps(value, allow_nan=False))

    def _call(self, operation: str, *, group_id: str | None = None,
              session_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id, session_id=session_id)
        if (not isinstance(result, dict)
                or set(result) != {"data", "evidence"}
                or not isinstance(result["evidence"], dict)):
            raise RunStateError("Session product-CLI result or evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_owned_cli_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("Session CLI Group creation is a one-shot operation")
        result = self._call("session-cli.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str) or not data["group_id"]
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(data["group_id"] + ":")):
            raise RunStateError("Session CLI group setup is not exact")
        self.group_id = data["group_id"]
        self.initial_session_id = data["session_id"]
        return result

    def create_listed_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError("Session CLI explicit create must follow Group setup once")
        result = self._call("session-cli.session.create", group_id=self.group_id)
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data["session_id"] == self.initial_session_id
                or data.get("id") != data["session_id"]
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "cli-e2e-sess"):
            raise RunStateError("Session CLI explicit create is not exact")
        self.session_id = data["session_id"]
        return result

    def read_session_list(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session CLI list must follow explicit Session create")
        result = self._call("session-cli.session.list", group_id=self.group_id)
        data = result.data
        items = data.get("items") if isinstance(data, dict) else None
        exact = [row for row in items if isinstance(row, dict)
                 and (row.get("session_id") or row.get("id")) == self.session_id] \
            if isinstance(items, list) else []
        if (not isinstance(data, dict) or set(data) != {"group_id", "items"}
                or data.get("group_id") != self.group_id
                or not isinstance(items, list) or not items or len(items) > 50
                or len(exact) != 1 or exact[0].get("group_id") != self.group_id):
            raise RunStateError("Session CLI list did not contain the exact created Session")
        return result

    def read_session_exact(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session CLI get must follow list")
        result = self._call("session-cli.session.get", session_id=self.session_id)
        data = result.data
        if (not isinstance(data, dict)
                or data.get("id") != self.session_id
                or data.get("session_id") != self.session_id
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "cli-e2e-sess"):
            raise RunStateError("Session CLI get did not return the exact Session")
        return result

    def _cleanup(self, operation: str, *, expected: tuple[int, ...],
                 parse_error: bool = False) -> tuple[object, dict]:
        if self.group_id is None:
            raise RunStateError("Session CLI cleanup requires the exact owned Group ID")
        return self.cleanup_client.call(
            operation, group_id=self.group_id, expected=expected,
            parse_error=parse_error, request_id=f"e2e-session-cli-{operation}-{os.urandom(8).hex()}")

    def delete_owned_group(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI Group cleanup must follow list/get")
        data, record = self._cleanup(SESSION_CLI_GROUP_DELETE_OPERATION, expected=(200,))
        if (not isinstance(data, dict)
                or data != {"deleted": True, "id": self.group_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session CLI Group delete acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": "cleanup-delete",
            "fixture_effect": FINAL_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def verify_group_deleted(self) -> AdapterCall:
        data, record = self._cleanup(
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION, expected=(404,), parse_error=True)
        if (not isinstance(data, dict)
                or data.get("status") != 404
                or data.get("code") != "group_not_found"
                or data.get("params") != {"group_id": self.group_id}
                or not isinstance(data.get("message"), str) or not data["message"]
                or record.get("http_status") != 404):
            raise RunStateError("Session CLI deleted Group was not returned as an exact 404")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": "cleanup-404-proof",
            "fixture_effect": FINAL_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)
from dataclasses import dataclass

from .case_executor import CaseExecution, StepOutcome
from .local_session_contract import PARENT, session_source_binding
from .workspace_cli_runner import SESSION_CLI_CREATE_GET_EFFECT

SESSION_CLI_CREATE_GET_CASE_ID = "DUAL-SESSION-CLI-CREATE-LIST-GET-01"
SESSION_CLI_CREATE_GET_REQUIREMENT_ID = "SESSION.cli-create-list-get"
SOURCE_FUNCTION = "test_group_session_via_cli"
CASE_METHOD = "execute_session_cli_create_list_get_case"


@dataclass(frozen=True)
class SessionCliCreateGetCasePlan:
    case_id: str
    parent_id: str
    requirement_id: str
    source_function: str
    source_expectation: str

    def validate(self) -> None:
        if (self.case_id != SESSION_CLI_CREATE_GET_CASE_ID or self.parent_id != PARENT
                or self.requirement_id != SESSION_CLI_CREATE_GET_REQUIREMENT_ID
                or self.source_function != SOURCE_FUNCTION
                or not self.source_expectation):
            raise RunStateError("Session CLI create/list/get case plan changed without review")


def session_cli_create_get_case_plan() -> SessionCliCreateGetCasePlan:
    binding = session_source_binding()
    row = next((item for item in binding["requirements"]
                if item["id"] == SESSION_CLI_CREATE_GET_REQUIREMENT_ID
                and item["function"] == SOURCE_FUNCTION), None)
    if row is None:
        raise RunStateError("Session CLI create/list/get source obligation changed")
    plan = SessionCliCreateGetCasePlan(
        case_id=SESSION_CLI_CREATE_GET_CASE_ID, parent_id=PARENT,
        requirement_id=SESSION_CLI_CREATE_GET_REQUIREMENT_ID,
        source_function=SOURCE_FUNCTION,
        source_expectation=row["expectation"])
    plan.validate()
    return plan


def session_cli_create_get_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_CREATE_GET_CASE_ID,
        "legacy_id": PARENT,
        "requirement_ids": [SESSION_CLI_CREATE_GET_REQUIREMENT_ID],
        "source_function": SOURCE_FUNCTION,
        "selected_obligations": ["cli-create", "cli-list", "cli-get", "exact-group-delete"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The case is mock-verified against both product-CLI credential contexts, "
            "but real Singlebox/preprod effect admission and execution remain separate gates."
        ),
    }


def validate_session_cli_create_get_adapter(adapter: object) -> None:
    if not isinstance(adapter, RemoteSessionCliCreateGetAdapter):
        raise RunStateError("Session CLI create/list/get requires its shared adapter")
    for name in (
        "create_owned_cli_group", "create_listed_cli_session",
        "read_session_list", "read_session_exact",
        "delete_owned_group", "verify_group_deleted",
    ):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"Session CLI adapter is missing {name}")


def _validate_cli_evidence(result: AdapterCall, operation: str,
                            credential_kind: str) -> None:
    evidence = result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_CLI"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "driver"
            or evidence.get("credential_kind") != credential_kind
            or evidence.get("fixture_effect") not in {
                "OWNED_GROUP_SESSIONS_READ_THEN_GENERIC_GROUP_DELETE",
                "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET",
            }
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"Session CLI evidence for {operation} is outside the contract")


def execute_session_cli_create_get_case(adapter: object) -> CaseExecution:
    session_cli_create_get_case_plan()
    validate_session_cli_create_get_adapter(adapter)
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_listed_cli_session(),
            adapter.read_session_list(),
            adapter.read_session_exact(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create", "session-cli.session.create",
            "session-cli.session.list", "session-cli.session.get",
            "session_cli.group.delete", "session_cli.group.verify_deleted",
        )
        credential_kind = ("owned_local_bot_token"
                           if adapter.profile["environment"]["tier"] == "local"
                           else "human_cookie")
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(f"Session CLI {operation} did not return AdapterCall")
            call.validate()
            if operation.startswith("session-cli."):
                _validate_cli_evidence(call, operation, credential_kind)
            else:
                evidence = call.evidence
                if (evidence.get("transport") != "PRODUCT_HTTP"
                        or evidence.get("operation") != operation
                        or evidence.get("caller_role") != "driver"
                        or evidence.get("credential_kind") != credential_kind
                        or evidence.get("http_status") not in {200, 404}
                        or evidence.get("fixture_effect") != "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET"):
                    raise RunStateError(f"Session cleanup evidence for {operation} is invalid")
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__

    step = StepOutcome(
        requirement_id=SESSION_CLI_CREATE_GET_REQUIREMENT_ID, method=CASE_METHOD,
        status="PASS" if error_type is None else "NOT_RUN",
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "explicit_session_listed": True,
            "exact_session_read": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET",
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    completed = 1 if error_type is None else 0
    execution = CaseExecution(
        case_id=SESSION_CLI_CREATE_GET_CASE_ID, parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Dual product-CLI Session Bot-groups case
SESSION_CLI_BOT_GROUPS_CASE_ID = "DUAL-SESSION-CLI-BOT-GROUPS-01"
SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID = "SESSION.bot-groups"
SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION = "test_bot_groups_of_bot"
SESSION_CLI_BOT_GROUPS_CASE_METHOD = "execute_session_cli_bot_groups_case"

def session_cli_bot_groups_case_plan() -> dict:
    binding = session_source_binding()
    row = next((x for x in binding["requirements"]
                if x["id"] == SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID
                and x["function"] == SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION), None)
    if row is None:
        raise RunStateError("Session CLI Bot-groups source obligation changed")
    return {"case_id": SESSION_CLI_BOT_GROUPS_CASE_ID, "parent_id": PARENT,
            "requirement_id": SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID,
            "source_function": SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION,
            "source_expectation": row["expectation"]}

def session_cli_bot_groups_adaptability() -> dict:
    return {"case_id": SESSION_CLI_BOT_GROUPS_CASE_ID, "legacy_id": PARENT,
            "requirement_ids": [SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID],
            "source_function": SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION,
            "selected_obligations": ["cli-group-create", "bot-groups-list", "exact-group-delete"],
            "environments": ["singlebox-product-cli", "preprod-product-cli"],
            "local_executor_status": "LOCAL_FULL_EXECUTOR",
            "cli_surface": "product-bcs-cli-session", "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "blocker": "Mock-only coverage; real Singlebox/preprod execution remain separate gates."}

class RemoteSessionCliBotGroupsAdapter(RemoteSessionCliCreateGetAdapter):
    def create_owned_cli_group(self) -> AdapterCall:
        result = super().create_owned_cli_group()
        self.session_id = self.initial_session_id
        return result

    def read_driver_bot_groups(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI Bot-groups must follow an exact Group create")
        data, record = self.cleanup_client.call(
            SESSION_CLI_BOT_GROUPS_OPERATION, expected=(200,),
            request_id=f"e2e-session-cli-bot-groups-{os.urandom(8).hex()}")
        items = data.get("items") if isinstance(data, dict) else None
        exact = [x for x in items if isinstance(x, dict)
                 and (x.get("group_id") or x.get("id")) == self.group_id] \
            if isinstance(items, list) else []
        if not (isinstance(data, dict) and data.get("bot_uuid") == self.driver_id
                and data.get("offset") == 0 and data.get("limit") == 50
                and isinstance(items, list) and len(items) <= 50
                and type(data.get("total")) is int and data["total"] >= len(items)
                and len(exact) == 1):
            raise RunStateError("Session CLI Bot-groups listing is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP", "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": "bot-groups", "fixture_effect": FINAL_EFFECT,
            "remote_validation": "NOT_RUN", "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def delete_owned_group(self) -> AdapterCall:
        result = super().delete_owned_group()
        result.evidence.update({"remote_validation": "NOT_RUN", "grants_release_credit": False})
        return result

    def verify_group_deleted(self) -> AdapterCall:
        result = super().verify_group_deleted()
        result.evidence.update({"remote_validation": "NOT_RUN", "grants_release_credit": False})
        return result

def execute_session_cli_bot_groups_case(adapter: object) -> CaseExecution:
    session_cli_bot_groups_case_plan()
    if not isinstance(adapter, RemoteSessionCliBotGroupsAdapter):
        raise RunStateError("Session CLI Bot-groups requires its shared adapter")
    records, error_type, group_id = [], None, ""
    try:
        calls = (adapter.create_owned_cli_group(), adapter.read_driver_bot_groups(),
                 adapter.delete_owned_group(), adapter.verify_group_deleted())
        operations = ("session-cli.group.create", SESSION_CLI_BOT_GROUPS_OPERATION,
                     SESSION_CLI_GROUP_DELETE_OPERATION, SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION)
        credential_kind = ("owned_local_bot_token"
                           if adapter.profile["environment"]["tier"] == "local"
                           else "human_cookie")
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(f"Session CLI Bot-groups {operation} did not return AdapterCall")
            call.validate()
            evidence = call.evidence
            if not (evidence.get("caller_role") == "driver"
                    and evidence.get("credential_kind") == credential_kind
                    and evidence.get("fixture_effect") in {
                        SESSION_CLI_CREATE_GET_EFFECT, FINAL_EFFECT}
                    and evidence.get("remote_validation") == "NOT_RUN"
                    and evidence.get("grants_release_credit") is False):
                raise RunStateError(f"Session CLI Bot-groups evidence for {operation} is invalid")
            records.append(dict(evidence))
        group_id = adapter.group_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID,
        method=SESSION_CLI_BOT_GROUPS_CASE_METHOD, status=status,
        data=None if error_type is not None else {
            "group_id": group_id, "bot_groups_exact": True,
            "group_deleted": True, "group_absence_verified": True},
        evidence={} if error_type is not None else {
            "requests": records, "fixture_effect": FINAL_EFFECT,
            "remote_validation": "NOT_RUN", "grants_release_credit": False},
        error_type=error_type)
    execution = CaseExecution(
        case_id=SESSION_CLI_BOT_GROUPS_CASE_ID, parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED", steps=(step,),
        completed_count=1 if error_type is None else 0, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None)
    execution.validate()
    return execution
