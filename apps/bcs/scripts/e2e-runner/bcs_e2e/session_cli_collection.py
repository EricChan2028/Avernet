"""Dual-environment Session collection product-CLI case.

Migrates the legacy ``test_session_collection`` obligation
(收藏): the driver Bot collects and uncollects its own explicit
sign-off Session, the collected listing is observed before and
after, per-Bot collection isolation is verified for the member
Bot, and a non-participant QA Bot collect is rejected with an
exact 404. All routes are legacy-face product routes whose
collector is resolved from the caller credential: a Singlebox Bot
token resolves to itself, while the preprod Human Cookie names the
owned pool Bot it acts for through the request participant.
"""

from __future__ import annotations

import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_session_contract import session_source_binding
from .run_state import RunStateError
from .session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_COLLECT_OPERATION,
    SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
    SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION,
    SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_UNCOLLECT_OPERATION,
    validate_session_cli_collection_profile,
)
from .write_transport import WriteClient


SESSION_CLI_COLLECTION_CASE_ID = "DUAL-SESSION-CLI-COLLECTION-01"
SESSION_CLI_COLLECTION_REQUIREMENT_ID = "SESSION.collection"
SESSION_CLI_COLLECTION_SOURCE_FUNCTION = "test_session_collection"
SESSION_CLI_COLLECTION_CASE_METHOD = "execute_session_cli_collection_case"
SESSION_CLI_COLLECTION_EFFECT = (
    "OWNED_SESSION_COLLECTION_THEN_GENERIC_GROUP_DELETE")


def session_cli_collection_case_plan() -> dict:
    binding = session_source_binding()
    row = next((
        item for item in binding["requirements"]
        if item["id"] == SESSION_CLI_COLLECTION_REQUIREMENT_ID
        and item["function"] == SESSION_CLI_COLLECTION_SOURCE_FUNCTION
    ), None)
    if row is None:
        raise RunStateError("Session collection source obligation changed")
    return {
        "case_id": SESSION_CLI_COLLECTION_CASE_ID,
        "requirement_id": SESSION_CLI_COLLECTION_REQUIREMENT_ID,
        "source_function": SESSION_CLI_COLLECTION_SOURCE_FUNCTION,
        "source_expectation": row["expectation"],
    }


def session_cli_collection_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_COLLECTION_CASE_ID,
        "legacy_id": "story_user_runs_and_shares_sessions",
        "requirement_ids": [SESSION_CLI_COLLECTION_REQUIREMENT_ID],
        "source_function": SESSION_CLI_COLLECTION_SOURCE_FUNCTION,
        "selected_obligations": [
            "cli-group-create", "explicit-session-create",
            "collected-list-empty-precondition",
            "collected-filter-requires-participant",
            "participant-collect", "collect-idempotent",
            "collected-list-contains",
            "per-bot-collected-isolation",
            "non-participant-collect-rejected",
            "participant-uncollect", "uncollect-idempotent",
            "collected-list-empty-postcondition",
            "creator-scoped-session-delete", "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-only; live Singlebox/preprod effect admission "
            "remains a separate gate, and the preprod Human "
            "principal must own all three dedicated pool Bots "
            "(driver collector, member isolation subject, "
            "non-participant QA rejector)."),
    }


class RemoteSessionCliCollectionAdapter:
    """Collect, isolate, reject, and uncollect one owned Session."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_collection_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError(
                "Session collection requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.qa_id = self.profile["actors"]["qa"]["bot_id"]
        self.group_id: str | None = None
        self.session_id: str | None = None
        self.cookie_client = (
            WriteClient(self._cookie_profile())
            if self.profile["environment"]["tier"] == "preprod"
            else None)
        self._actor_clients: dict[str, WriteClient] = {}
        if self.profile["environment"]["tier"] == "local":
            self._actor_clients = {
                "driver": WriteClient(
                    self._actor_profile("driver_token_env")),
                "member": WriteClient(
                    self._actor_profile("member_token_env")),
                "qa": WriteClient(self._actor_profile("qa_token_env")),
            }

    @staticmethod
    def _entrypoint(profile: dict) -> dict:
        return {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }

    def _actor_profile(self, credential_ref: str) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "authenticated-product",
            "header_env": {
                "Authorization": self.profile["auth"][credential_ref],
            },
        }
        value["entrypoint"] = self._entrypoint(self.profile)
        return value

    def _cookie_profile(self) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "human-cookie",
            "header_env": {"Cookie": self.profile["auth"]["cookie_env"]},
        }
        value["entrypoint"] = self._entrypoint(self.profile)
        return value

    def _collection_client(self, actor: str) -> WriteClient:
        if self.profile["environment"]["tier"] == "local":
            return self._actor_clients[actor]
        return self.cookie_client

    def _credential_kind(self) -> str:
        return ("owned_local_bot_token"
                if self.profile["environment"]["tier"] == "local"
                else "human_cookie")

    def create_collection_cli_group(self) -> AdapterCall:
        result = self.runner.execute("session-cli.group.create")
        data = result.get("data") if isinstance(result, dict) else None
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id,
                                                self.member_id]
                or not isinstance(data.get("session_id"), str)):
            raise RunStateError(
                "Session collection Group setup is not exact")
        self.group_id = data["group_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "setup",
            "fixture_effect": SESSION_CLI_COLLECTION_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def create_collect_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError(
                "Session collection must follow Group setup")
        result = self.runner.execute(
            "session-cli.session.create", group_id=self.group_id,
            session_title="collect-sess",
        )
        data = result["data"]
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data.get("id") != data.get("session_id")
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "collect-sess"):
            raise RunStateError(
                "Session collection explicit Session is not exact")
        self.session_id = data["session_id"]
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": "session-create",
            "fixture_effect": SESSION_CLI_COLLECTION_EFFECT,
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
                f"Session collection {operation} requires its "
                "exact bound Group")
        data, record = client.call(
            operation, session_id=session_id or self.session_id or "",
            group_id=group_id or self.group_id, body=body,
            expected=expected, parse_error=parse_error)
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": caller_role,
            "credential_kind": self._credential_kind(),
            "case_class": case_class,
            "fixture_effect": SESSION_CLI_COLLECTION_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def _collected_listing(self, actor: str, operation: str, *,
                           case_class: str,
                           caller_role: str = "case_owned_driver"
                           ) -> AdapterCall:
        call = self._http(self._collection_client(actor), operation,
                          case_class=case_class, caller_role=caller_role)
        data = call.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("items"), list)
                or data.get("group_id") != self.group_id):
            raise RunStateError(
                "Session collection collected listing is not exact")
        return call

    def _collected_session_ids(self, call: AdapterCall) -> list[str]:
        return [
            item.get("session_id") for item in call.data["items"]
            if isinstance(item, dict)
        ]

    def read_collected_list_before(self) -> AdapterCall:
        call = self._collected_listing(
            "driver", SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
            case_class="collected-list-precondition")
        if call.data["items"] != []:
            raise RunStateError(
                "Session collection precondition collected list is "
                "not empty")
        return call

    def reject_collected_filter_without_participant(self) -> AdapterCall:
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION,
            expected=(400,), parse_error=True,
            case_class="collected-filter-rejection")
        if (call.data != {"error": "invalid_params",
                          "message": "collected filter requires "
                                     "participant"}
                or call.evidence.get("http_status") != 400):
            raise RunStateError(
                "Session collection collected filter without "
                "participant was not the exact 400 rejection")
        return AdapterCall(data={"rejected": True}, evidence=call.evidence)

    def collect_signoff_session(self) -> AdapterCall:
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_SESSION_COLLECT_OPERATION,
            body={"participant": self.driver_id},
            case_class="session-collect")
        data = call.data
        if (data != {"collected": True, "session_id": self.session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session collection collect acknowledgement is "
                "not exact")
        return AdapterCall(data=dict(data), evidence=call.evidence)

    def repeat_collect_idempotent(self) -> AdapterCall:
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_SESSION_COLLECT_OPERATION,
            body={"participant": self.driver_id},
            case_class="session-collect-idempotent")
        data = call.data
        if (data != {"collected": True, "session_id": self.session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session collection repeat collect was not an "
                "idempotent 200")
        return AdapterCall(data=dict(data), evidence=call.evidence)

    def read_collected_list_contains(self) -> AdapterCall:
        call = self._collected_listing(
            "driver", SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
            case_class="collected-list-contains")
        items = call.data["items"]
        if not any(isinstance(item, dict)
                   and item.get("session_id") == self.session_id
                   and item.get("collected") is True
                   for item in items):
            raise RunStateError(
                "Session collection collected list is missing the "
                "collected Session")
        return call

    def verify_per_bot_collection_isolation(self) -> AdapterCall:
        call = self._collected_listing(
            "member", SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION,
            case_class="collected-list-isolation",
            caller_role="case_owned_member")
        if self.session_id in self._collected_session_ids(call):
            raise RunStateError(
                "Session collection member collected list leaked the "
                "driver collection")
        return call

    def reject_non_participant_collect(self) -> AdapterCall:
        call = self._http(
            self._collection_client("qa"),
            SESSION_CLI_SESSION_COLLECT_OPERATION,
            body={"participant": self.qa_id},
            expected=(404,), parse_error=True,
            caller_role="case_owned_qa",
            case_class="non-participant-collect-rejection")
        if (call.data != {"error": "participant {} not in session {}"
                          .format(self.qa_id, self.session_id)}
                or call.evidence.get("http_status") != 404):
            raise RunStateError(
                "Session collection non-participant collect was not "
                "the exact 404 rejection")
        return AdapterCall(
            data={"session_id": self.session_id,
                  "non_participant_rejected": True},
            evidence=call.evidence)

    def uncollect_signoff_session(self) -> AdapterCall:
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_SESSION_UNCOLLECT_OPERATION,
            case_class="session-uncollect")
        data = call.data
        if (data != {"collected": False, "session_id": self.session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session collection uncollect acknowledgement is "
                "not exact")
        return AdapterCall(data=dict(data), evidence=call.evidence)

    def repeat_uncollect_idempotent(self) -> AdapterCall:
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_SESSION_UNCOLLECT_OPERATION,
            case_class="session-uncollect-idempotent")
        data = call.data
        if (data != {"collected": False, "session_id": self.session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session collection repeat uncollect was not an "
                "idempotent 200")
        return AdapterCall(data=dict(data), evidence=call.evidence)

    def read_collected_list_empty_after(self) -> AdapterCall:
        call = self._collected_listing(
            "driver", SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
            case_class="collected-list-postcondition")
        if call.data["items"] != []:
            raise RunStateError(
                "Session collection postcondition collected list is "
                "not empty")
        return call

    def delete_created_session(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError(
                "Session collection creator delete requires its exact "
                "Session")
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            case_class="session-creator-delete")
        if (call.data != {"deleted": True, "session_id": self.session_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session collection creator Session delete "
                "acknowledgement is not exact")
        evidence = dict(call.evidence)
        self.session_id = ""
        return AdapterCall(
            data={"deleted": True, "session_id": ""},
            evidence=evidence)

    def delete_collection_group(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError(
                "Session collection cleanup requires the exact Group ID")
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_GROUP_DELETE_OPERATION, session_id="",
            case_class="cleanup-delete")
        if (call.data != {"deleted": True, "id": self.group_id}
                or call.evidence.get("http_status") != 200):
            raise RunStateError(
                "Session collection Group delete acknowledgement "
                "is not exact")
        return AdapterCall(data=dict(call.data), evidence=call.evidence)

    def verify_collection_group_absent(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError(
                "Session collection cleanup requires the exact Group ID")
        call = self._http(
            self._collection_client("driver"),
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION, session_id="",
            expected=(404,), parse_error=True,
            case_class="cleanup-404-proof")
        data = call.data
        if (not isinstance(data, dict)
                or data.get("status") != 404
                or data.get("code") != "group_not_found"
                or data.get("params") != {"group_id": self.group_id}
                or not isinstance(data.get("message"), str)
                or not data["message"]
                or call.evidence.get("http_status") != 404):
            raise RunStateError(
                "Session collection deleted Group was not an exact "
                "404")
        return AdapterCall(data=data, evidence=call.evidence)


def execute_session_cli_collection_case(adapter: object) -> CaseExecution:
    session_cli_collection_case_plan()
    if not isinstance(adapter, RemoteSessionCliCollectionAdapter):
        raise RunStateError("Session collection requires its shared adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    try:
        calls = (
            adapter.create_collection_cli_group(),
            adapter.create_collect_cli_session(),
            adapter.read_collected_list_before(),
            adapter.reject_collected_filter_without_participant(),
            adapter.collect_signoff_session(),
            adapter.repeat_collect_idempotent(),
            adapter.read_collected_list_contains(),
            adapter.verify_per_bot_collection_isolation(),
            adapter.reject_non_participant_collect(),
            adapter.uncollect_signoff_session(),
            adapter.repeat_uncollect_idempotent(),
            adapter.read_collected_list_empty_after(),
            adapter.delete_created_session(),
            adapter.delete_collection_group(),
            adapter.verify_collection_group_absent(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
            SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION,
            SESSION_CLI_SESSION_COLLECT_OPERATION,
            SESSION_CLI_SESSION_COLLECT_OPERATION,
            SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
            SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION,
            SESSION_CLI_SESSION_COLLECT_OPERATION,
            SESSION_CLI_SESSION_UNCOLLECT_OPERATION,
            SESSION_CLI_SESSION_UNCOLLECT_OPERATION,
            SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION,
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        credential_kind = adapter._credential_kind()
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(
                    f"Session collection {operation} did not return "
                    "AdapterCall")
            call.validate()
            if (call.evidence.get("credential_kind") != credential_kind
                    or call.evidence.get("fixture_effect")
                    != SESSION_CLI_COLLECTION_EFFECT
                    or call.evidence.get("remote_validation") != "NOT_RUN"
                    or call.evidence.get("grants_release_credit") is not False):
                raise RunStateError(
                    f"Session collection evidence for {operation} "
                    "is invalid")
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_COLLECTION_REQUIREMENT_ID,
        method=SESSION_CLI_COLLECTION_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "service_group_created": True,
            "signoff_session_created": True,
            "collected_list_empty_before": True,
            "collected_filter_requires_participant": True,
            "session_collected": True,
            "collect_idempotent": True,
            "collected_list_contains_session": True,
            "member_collection_isolation": True,
            "non_participant_collect_rejected": True,
            "session_uncollected": True,
            "uncollect_idempotent": True,
            "collected_list_empty_after": True,
            "chat_session_deleted": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": SESSION_CLI_COLLECTION_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=SESSION_CLI_COLLECTION_CASE_ID,
        parent_id="story_user_runs_and_shares_sessions",
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=1 if error_type is None else 0,
        total_count=1, error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
