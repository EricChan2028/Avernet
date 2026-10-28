"""Dual-environment Operator CLI adapter contract tests; no live request.

The preprod leg is driven once through a real bounded product-CLI runner
(scripted process captures only) plus class-level station transport doubles,
and every scripted leg freezes the same obligation map through a runner
double. The Singlebox leg freezes at the owned-local direct-onboard
boundary. Every AdapterCall carries remote_validation=NOT_RUN,
grants_release_credit=false and the reviewed fixture effect; placeholder
credential strings are asserted absent from every snapshot.
"""
from __future__ import annotations

import contextlib
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_operator_cli_contract import COMMANDS, PARENT, REQUIREMENTS
from bcs_e2e.operator_cli_runner import (
    OPERATOR_CLI_CHAT,
    OPERATOR_CLI_CLI_OPERATIONS,
    OPERATOR_CLI_CONNECT,
    OPERATOR_CLI_CREATE_GROUP,
    OPERATOR_CLI_DISCOVER,
    OPERATOR_CLI_EFFECT,
    OPERATOR_CLI_FRIEND_ACCEPT_CHAT,
    OPERATOR_CLI_FRIEND_ACCEPT_LIST,
    OPERATOR_CLI_FRIEND_LIST,
    OPERATOR_CLI_FRIEND_REQUEST_CHAT,
    OPERATOR_CLI_FRIEND_REQUEST_LIST,
    OPERATOR_CLI_GET,
    OPERATOR_CLI_GET_GROUP,
    OPERATOR_CLI_HEALTH,
    OPERATOR_CLI_LIST,
    OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD,
    OPERATOR_CLI_LIST_GROUPS,
    OPERATOR_CLI_ONBOARD_DIRECT,
    OPERATOR_CLI_ONBOARD_READBACK,
    OPERATOR_CLI_ONBOARD_WEB,
    OPERATOR_CLI_UPDATE_STATUS,
    OPERATOR_CLI_VISIBILITY,
    OPERATOR_CLI_ROUTES,
    OperatorCliRunner,
    OperatorCliRunnerError,
    validate_operator_cli_profile,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.remote_operator_cli_adapter import (
    OPERATOR_CLI_ADAPTER_METHODS,
    OPERATOR_CLI_BLOCKER,
    OPERATOR_CLI_CASE_ID,
    OPERATOR_CLI_CLI_OBLIGATIONS,
    RemoteOperatorCliAdapter,
    execute_operator_cli_case,
    operator_cli_adaptability,
    operator_cli_case_plan,
    validate_operator_cli_adapter,
)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import Observation, ReadClient
from bcs_e2e.write_transport import WriteClient

ROOT_GUARD = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
RUNNER_SOURCE = ROOT / "bcs_e2e/operator_cli_runner.py"
ADAPTER_SOURCE = ROOT / "bcs_e2e/remote_operator_cli_adapter.py"

ROLES = ("driver", "member", "eng", "qa", "cs")
BOT_IDS = {
    "driver": "operator-ceo-bot-01",
    "member": "operator-pm-bot-01",
    "eng": "operator-eng-bot-01",
    "qa": "operator-qa-bot-01",
    "cs": "operator-cs-bot-01",
}
FIXTURE_NAMES = {role: role + "-trusted-fixture" for role in ROLES}
COOKIE = "operator-cookie-private-value"
DRIVER_TOKEN = "operator-driver-token-private-value"
REGISTER_TOKEN = "operator-register-token-private-value"
BOT_TOKEN = "operator-onboard-bot-token-private-value"
CONNECT_TOKEN = "operator-connect-token-private-value"
ROLE_TOKENS = {role: "operator-" + role + "-token-private" for role in ROLES}
COOKIE_ENV = "BCS_E2E_OPERATOR_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_OPERATOR_DRIVER_BOT_TOKEN"
MEMBER_TOKEN_ENV = "BCS_E2E_OPERATOR_MEMBER_BOT_TOKEN"
ENG_TOKEN_ENV = "BCS_E2E_OPERATOR_ENG_BOT_TOKEN"
QA_TOKEN_ENV = "BCS_E2E_OPERATOR_QA_BOT_TOKEN"
CS_TOKEN_ENV = "BCS_E2E_OPERATOR_CS_BOT_TOKEN"
TEMP_UUID = "operator-temp-bot-4177"
GROUP_ID = "operator-group-0001"
RUN_ID = "operator-run-0001"
SESSION_ID = "operator-session-0001"
REQUEST_ID = "operator-request-0001"
PROBE_NAME = "operator-cli-web-probe"
LOCAL_ORIGIN = "http://127.0.0.1:21300"
PREPROD_ORIGIN = "https://bcs-pre.alipay.com"
BROWSER_ORIGIN = "https://teamclaw-pre.alipay.com"

DUO = "human_cookie_with_owned_driver_bot_token"
ONBOARD_DUO = "human_cookie_with_owned_onboarded_bot_token"
HUMAN_COOKIE = "human_cookie"
OWNED = "owned_local_bot_token"

REGISTRY_READ = "operator_cli.bot_registry.read"
GROUP_DELETE = "operator_cli.group.delete"
GROUP_DELETED_VERIFY = "operator_cli.group.deleted_verify"
DRIVER_RECORD = {"bot_uuid": BOT_IDS["driver"], "owner_id": "39464",
                 "visibility": "public", "dedicated": True}
OTHER_RECORD = dict(DRIVER_RECORD, visibility="protected")

PLACEHOLDERS = (COOKIE, DRIVER_TOKEN, REGISTER_TOKEN, BOT_TOKEN, CONNECT_TOKEN,
                *ROLE_TOKENS.values())


def profile_value(*, preprod=False):
    tier = "preprod" if preprod else "local"
    value = {
        "schema_version": 1,
        "contract": "dual-operator-cli-profile-v1",
        "environment": {"tier": tier, "id": "operator-cli-" + tier,
                        "expected_bcs_env": "pre" if preprod else "local"},
        "entrypoint": {
            "kind": "bcs-direct" if preprod else "singlebox",
            "origin": PREPROD_ORIGIN if preprod else LOCAL_ORIGIN,
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {role: {"bot_id": BOT_IDS[role], "owner_id": "39464",
                          "dedicated": True,
                          "visibility": "public" if role == "driver" else "protected"}
                   for role in ROLES},
        "fixture_names": dict(FIXTURE_NAMES),
        "auth": (
            {"kind": "human-cookie-with-owned-driver-bot-token",
             "cookie_env": COOKIE_ENV, "driver_token_env": DRIVER_TOKEN_ENV}
            if preprod else
            {"kind": "owned-local-bot-tokens",
             "driver_token_env": DRIVER_TOKEN_ENV,
             "member_token_env": MEMBER_TOKEN_ENV,
             "eng_token_env": ENG_TOKEN_ENV,
             "qa_token_env": QA_TOKEN_ENV,
             "cs_token_env": CS_TOKEN_ENV}),
        "relationships": {
            "fixture_pool": "operator-cli-trusted-team",
            "roles": "CEO-PM-ENG-QA-CS",
            "group_cleanup": "creator-scoped-exact-once",
            "temporary_agent": "register-onboard-read-delete-404",
            "friendships": "two-in-case-admissions-retained",
            "web_onboard": "non-mutating",
        },
        "policy": {"fixture_pool": "operator-cli-pool", "exclusive_use_confirmed": True,
                   "timeout_seconds": 10, "max_response_bytes": 65536,
                   "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "operator",
                "api_face": "legacy"},
        "route_bindings": {
            REGISTRY_READ: {"method": "GET", "path": "/bots/{driver_actor_id}",
                            "api_face": "legacy", "auth": "case_owned_driver",
                            "response": "legacy-json", "traceparent": "disabled"},
            GROUP_DELETE: {"method": "DELETE",
                           "path": "/groups/{group_id}?bot_id={member_actor_id}",
                           "api_face": "legacy", "auth": "case_owned_member",
                           "response": "legacy-json", "traceparent": "disabled"},
            GROUP_DELETED_VERIFY: {"method": "GET", "path": "/groups/{group_id}",
                                   "api_face": "legacy", "auth": "case_owned_member",
                                   "response": "legacy-json",
                                   "traceparent": "disabled"},
        },
    }
    if preprod:
        value["browser_origin"] = BROWSER_ORIGIN
        value["edge_case_bindings"] = {
            "friend_connection_mutation.register_token.read": {
                "method": "GET", "path": "/register/token",
                "api_face": "station-legacy", "auth": "human_cookie",
                "response": "plain-json", "traceparent": "disabled"},
            "friend_connection_mutation.register_bot.create": {
                "method": "POST",
                "path": "/register?token={register_token}&bot-name={bot_name}",
                "api_face": "station-legacy", "auth": "register_token",
                "response": "station-register-json", "traceparent": "disabled"},
            "friend_connection_mutation.station_bot_delete": {
                "method": "DELETE", "path": "/bots/{bot_id}",
                "api_face": "station-legacy", "auth": "human_cookie_owned_bot",
                "response": "station-bot-delete-json", "traceparent": "disabled"},
            "friend_connection_mutation.station_bot_read": {
                "method": "GET", "path": "/bots/{bot_id}",
                "api_face": "station-legacy", "auth": "human_cookie_owned_bot",
                "response": "station-bot-read-json", "traceparent": "disabled"},
        }
    return validate_operator_cli_profile(value)


def preprod_environment():
    return {COOKIE_ENV: COOKIE, DRIVER_TOKEN_ENV: DRIVER_TOKEN}


def local_environment():
    return {DRIVER_TOKEN_ENV: DRIVER_TOKEN,
            MEMBER_TOKEN_ENV: ROLE_TOKENS["member"],
            ENG_TOKEN_ENV: ROLE_TOKENS["eng"],
            QA_TOKEN_ENV: ROLE_TOKENS["qa"],
            CS_TOKEN_ENV: ROLE_TOKENS["cs"]}


def list_text():
    return "Trusted-team fixture Bots:\n" + "".join(
        "  " + BOT_IDS[role] + "\n" for role in ROLES)


def group_create_text():
    return ("Group created:\n  ID: " + GROUP_ID + "\n  Driver: "
            + BOT_IDS["member"] + "\n  Participants: "
            + BOT_IDS["member"] + "," + BOT_IDS["eng"] + "\n")


def group_read_text():
    return ("Group: " + GROUP_ID + "\n  Driver: " + BOT_IDS["member"]
            + "\n  Participants: " + BOT_IDS["member"] + "," + BOT_IDS["eng"]
            + "\n")


def operation_tag(arguments):
    head = tuple(arguments[:2])
    if arguments == ["health"]:
        return "health"
    if arguments == ["list"]:
        return "list"
    if head[0] == "get":
        return "get"
    if head[0] == "discover":
        return "discover"
    if head == ("visibility", "get"):
        return "visibility_get"
    if head[0] == "visibility":
        return "visibility_set"
    if arguments == ["connect"]:
        return "connect"
    if head[0] == "onboard" and "--web" in arguments:
        return "onboard_web"
    if head[0] == "onboard":
        return "onboard_direct"
    if head[0] == "update-status":
        return "update_status"
    if head[0] == "create-group":
        return "create_group"
    if head[0] == "get-group":
        return "get_group"
    if head == ("friend", "request"):
        return "friend_request"
    if head == ("friend", "accept"):
        return "friend_accept"
    if head[0] == "chat":
        return "chat_detach" if "--detach" in arguments else "chat_blocking"
    if arguments == ["list-groups"]:
        return "list_groups"
    if arguments == ["friend", "list"]:
        return "friend_list"
    raise AssertionError("unexpected CLI arguments: " + " ".join(arguments))


def output_for(tag, arguments):
    if tag == "health":
        return (0, b"status: healthy\n", b"")
    if tag == "list":
        return (0, list_text().encode(), b"")
    if tag == "get":
        return (0, ("Bot: " + arguments[1] + "\n").encode(), b"")
    if tag == "discover":
        return (0, ("Fixture: " + BOT_IDS["cs"] + "\n").encode(), b"")
    if tag == "visibility_get":
        return (0, b"Visibility: public\n", b"")
    if tag == "visibility_set":
        return (0, b"Status: ok\n", b"")
    if tag == "connect":
        return (0, json.dumps({"bot_uuid": BOT_IDS["driver"], "is_new": False,
                               "token": CONNECT_TOKEN}).encode(), b"")
    if tag == "onboard_web":
        probe = arguments[arguments.index("--name") + 1]
        return (0, ("Registration URL: https://register.example?name="
                    + probe + "\n").encode(), b"")
    if tag == "onboard_direct":
        name = arguments[arguments.index("--name") + 1]
        return (0, json.dumps({"bot_uuid": TEMP_UUID, "onboarded": True,
                               "name": name}).encode(), b"")
    if tag == "update_status":
        return (0, ("Status: " + arguments[-1] + "\n").encode(), b"")
    if tag == "create_group":
        return (0, group_create_text().encode(), b"")
    if tag == "get_group":
        return (0, group_read_text().encode(), b"")
    if tag == "friend_request":
        return (0, json.dumps({"request_id": REQUEST_ID}).encode(), b"")
    if tag == "friend_accept":
        return (0, b"Accepted\n", b"")
    if tag in {"chat_detach", "chat_blocking"}:
        return (0, json.dumps({"run_id": RUN_ID, "session_id": SESSION_ID,
                               "detach": tag == "chat_detach"}).encode(), b"")
    if tag == "list_groups":
        return (0, json.dumps({"items": [{"group_id": GROUP_ID}], "returned": 1,
                               "total": 1, "has_more": False}).encode(), b"")
    if tag == "friend_list":
        return (0, ("Friends:\n  " + BOT_IDS["eng"] + "\n").encode(), b"")
    raise AssertionError("unexpected CLI tag " + tag)


class CliScript:
    """Record and script every bounded product-CLI capture."""

    def __init__(self):
        self.commands = []
        self.count = 0

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        arguments = command[command.index("--log-level") + 2:]
        tag = operation_tag(arguments)
        exit_code, stdout, stderr = output_for(tag, arguments)
        self.count += 1
        return subprocess.CompletedProcess(command, exit_code, stdout, stderr)


def make_binary(root):
    binary = root / "bcs-cli"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def http_record(operation, path, status, method="GET"):
    return {"operation": operation, "method": method, "path": path,
            "http_status": status, "traceparent_mode": "disabled",
            "credentials_sent": True, "placeholder_free": True}


class ScriptedRunner:
    """Order-recording Operator CLI runner double with drift switches."""

    def __init__(self, *, preprod=True, outputs=None, failures=None):
        self.preprod = preprod
        self.calls = []
        self.executed = []
        self.outputs = outputs if outputs is not None else {}
        self.failures = failures if failures is not None else {}

    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        self.executed.append(operation)
        if operation in self.failures:
            raise self.failures[operation]
        return {"data": self._data(operation),
                "evidence": self._evidence(operation)}

    def _data(self, operation):
        if operation in self.outputs:
            return self.outputs[operation]
        if operation == OPERATOR_CLI_HEALTH:
            return {"status": "healthy", "credential_free": True}
        if operation == OPERATOR_CLI_LIST:
            return {"fixture_count": 5,
                    "fixture_uuids": [BOT_IDS[r] for r in ROLES]}
        if operation == OPERATOR_CLI_GET:
            return {"target": BOT_IDS["driver"], "echoed": True}
        if operation == OPERATOR_CLI_DISCOVER:
            return {"query": FIXTURE_NAMES["cs"], "bot_uuid": BOT_IDS["cs"]}
        if operation == OPERATOR_CLI_VISIBILITY:
            return {"original": "public", "round_trip_verified": True}
        if operation == OPERATOR_CLI_CONNECT:
            return {"bot_uuid": BOT_IDS["driver"], "is_new": False,
                    "token_present": True}
        if operation == OPERATOR_CLI_ONBOARD_WEB:
            return {"probe_name": PROBE_NAME, "registered": False}
        if operation == OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD:
            return {"probe_absent": True, "fixture_uuids_present": True}
        if operation == OPERATOR_CLI_ONBOARD_DIRECT:
            return {"bot_uuid": TEMP_UUID, "onboarded": True,
                    "bot_name": "operator-cli-direct-x"}
        if operation == OPERATOR_CLI_ONBOARD_READBACK:
            return {"bot_uuid": TEMP_UUID, "readback_verified": True}
        if operation == OPERATOR_CLI_UPDATE_STATUS:
            return {"statuses": ["idle", "busy"], "acknowledged": True}
        if operation == OPERATOR_CLI_CREATE_GROUP:
            return {"group_id": GROUP_ID, "driver_bot": BOT_IDS["member"],
                    "participants": [BOT_IDS["member"], BOT_IDS["eng"]],
                    "topic": "operator cli e2e group"}
        if operation == OPERATOR_CLI_GET_GROUP:
            return {"group_id": GROUP_ID, "echoed": True}
        if operation in {OPERATOR_CLI_FRIEND_REQUEST_CHAT,
                         OPERATOR_CLI_FRIEND_REQUEST_LIST}:
            return {"request_id": REQUEST_ID, "target_bot": BOT_IDS["member"]}
        if operation in {OPERATOR_CLI_FRIEND_ACCEPT_CHAT,
                         OPERATOR_CLI_FRIEND_ACCEPT_LIST}:
            return {"request_id": REQUEST_ID, "accepted": True}
        if operation == OPERATOR_CLI_CHAT:
            return {"run_id": RUN_ID, "session_id": SESSION_ID,
                    "detach_fallback": False}
        if operation == OPERATOR_CLI_LIST_GROUPS:
            return {"returned": 1, "total": 1, "has_more": False,
                    "item_count": 1}
        if operation == OPERATOR_CLI_FRIEND_LIST:
            return {"friend_list_verified": True, "target_bot": BOT_IDS["eng"]}
        raise AssertionError("unexpected operator CLI operation " + operation)

    def _evidence(self, operation):
        role = self._role(operation)
        kind = "none" if role == "health" else self._kind(role)
        method, route = OPERATOR_CLI_ROUTES[operation]
        return {"transport": "PRODUCT_CLI", "operation": operation,
                "caller_role": role,
                "command_arguments": self._argv(operation),
                "http_method_declared": method, "route_declared": route,
                "credential_kind": kind, "exit_status": 0,
                "elapsed_ms": 1.0, "fixture_effect": OPERATOR_CLI_EFFECT,
                "remote_validation": "NOT_RUN", "grants_release_credit": False}

    def _role(self, operation):
        if operation == OPERATOR_CLI_HEALTH:
            return "health"
        if operation == OPERATOR_CLI_ONBOARD_DIRECT:
            return "onboard"
        if operation in {OPERATOR_CLI_CREATE_GROUP, OPERATOR_CLI_GET_GROUP,
                         OPERATOR_CLI_FRIEND_ACCEPT_CHAT}:
            return "member"
        if operation == OPERATOR_CLI_FRIEND_ACCEPT_LIST:
            return "eng"
        return "driver"

    def _kind(self, role):
        if self.preprod:
            if role == "onboard":
                return ONBOARD_DUO
            if role in {"member", "eng"}:
                return HUMAN_COOKIE
            return DUO
        return OWNED

    def _argv(self, operation):
        return {"health": ["health"], "list": ["list"],
                "friend_request": ["friend", "request"]}.get(
            operation, ["operator", operation.rsplit(".", 1)[-1].replace("_", "-")])

class OperatorCliPlanTests(unittest.TestCase):
    def test_case_plan_freezes_the_thirteen_obligations(self):
        plan = operator_cli_case_plan()
        self.assertEqual(plan["case_id"], OPERATOR_CLI_CASE_ID)
        self.assertEqual(plan["parent_id"], PARENT)
        self.assertEqual(plan["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(len(plan["requirement_ids"]), 13)
        self.assertEqual(plan["source_commands"], list(COMMANDS))
        self.assertEqual(plan["adapter_methods"], list(OPERATOR_CLI_ADAPTER_METHODS))
        self.assertEqual(len(set(plan["adapter_methods"])), 13)
        self.assertEqual(plan["frozen_operations"], list(OPERATOR_CLI_CLI_OPERATIONS))
        self.assertEqual(len(plan["frozen_operations"]), 20)

    def test_adaptability_claims_no_credit_and_names_the_station_gap(self):
        value = operator_cli_adaptability()
        self.assertEqual(value["case_id"], OPERATOR_CLI_CASE_ID)
        self.assertEqual(value["legacy_id"], PARENT)
        self.assertEqual(value["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(value["selected_obligations"],
                         list(OPERATOR_CLI_CLI_OBLIGATIONS))
        self.assertEqual(value["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(value["local_executor_status"], "LOCAL_FULL_EXECUTOR")
        self.assertEqual(value["cli_surface"], "product-bcs-cli-operator")
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        self.assertIn("station_bot_delete", value["blocker"])
        self.assertIn("station_bot_read", value["blocker"])
        self.assertIn(OPERATOR_CLI_BLOCKER, value["blocker"])

    def test_case_guard_accepts_both_operator_cli_sources(self):
        result = subprocess.run(
            [sys.executable, str(ROOT_GUARD), str(RUNNER_SOURCE),
             str(ADAPTER_SOURCE)],
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class OperatorCliAdapterAdmissionTests(unittest.TestCase):
    def test_constructor_requires_a_product_cli_runner(self):
        profile = profile_value(preprod=True)
        with patch.dict(os.environ, preprod_environment()):
            adapter = RemoteOperatorCliAdapter(profile, ScriptedRunner())
            validate_operator_cli_adapter(adapter)
            with self.assertRaisesRegex(RunStateError, "product-CLI runner"):
                RemoteOperatorCliAdapter(profile, object())
        with self.assertRaisesRegex(RunStateError,
                                   "adapter is missing methods"):
            validate_operator_cli_adapter(object())

    def test_preprod_duo_profile_requires_both_credentials(self):
        profile = profile_value(preprod=True)
        with patch.dict(os.environ, {DRIVER_TOKEN_ENV: DRIVER_TOKEN}):
            with self.assertRaisesRegex(RunStateError, "duo credentials"):
                RemoteOperatorCliAdapter(profile, ScriptedRunner())

    def test_constructor_rejects_unvalidated_profiles(self):
        value = json.loads(json.dumps(profile_value(preprod=True)))
        value["environment"]["tier"] = "staging"
        with self.assertRaises(ConfigError):
            RemoteOperatorCliAdapter(value, ScriptedRunner())


class OperatorCliCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.binary, self.digest = make_binary(self.root)
        self.state = self.root / "state"
        self.state.mkdir()

    # -- station transport doubles --------------------------------------
    def station_dispatcher(self, calls, *, registry_drift=False,
                           delete_ack=False, verify_status=404):
        def fake_call(self, operation, **kwargs):
            calls.append((operation, kwargs))
            if operation == REGISTRY_READ:
                reads = sum(1 for item in calls if item[0] == REGISTRY_READ)
                payload = (OTHER_RECORD if (registry_drift and reads > 1)
                           else DRIVER_RECORD)
                return (dict(payload), http_record(
                    REGISTRY_READ, "/bots/" + BOT_IDS["driver"], 200))
            if operation == GROUP_DELETE:
                if delete_ack:
                    return ({"deleted": False}, http_record(
                        GROUP_DELETE, "/groups/" + GROUP_ID, 200, "DELETE"))
                return ({"deleted": True}, http_record(
                    GROUP_DELETE, "/groups/" + kwargs["group_id"], 200,
                    "DELETE"))
            if operation == GROUP_DELETED_VERIFY:
                return (None, http_record(
                    GROUP_DELETED_VERIFY, "/groups/" + kwargs["group_id"],
                    verify_status))
            raise AssertionError("unexpected transport operation: " + operation)
        return fake_call

    # -- scripted case harness ------------------------------------------
    def run_scripted_case(self, *, preprod=True, outputs=None, failures=None,
                          registry_drift=False, delete_ack=False, dispose_drift=False,
                          verify_status=404, register_payload=None,
                          read_token=True, station_methods=True):
        profile = profile_value(preprod=preprod)
        runner = ScriptedRunner(preprod=preprod, outputs=outputs,
                                failures=failures)
        calls = []
        station = {"register": 0, "delete": 0, "read": 0}
        token_reads = {"count": 0}

        def fake_register(self, token, bot_name):
            station["register"] += 1
            payload = (register_payload if register_payload is not None else
                       {"bot_uuid": TEMP_UUID, "bot_token": BOT_TOKEN,
                        "bot_name": bot_name})
            return (dict(payload), http_record(
                "friend_connection_mutation.register_bot.create",
                "/register?token=[REDACTED-register-token]&bot-name=" + bot_name,
                200, "POST"))

        def fake_delete(bot_uuid):
            station["delete"] += 1
            payload = ({"left": False, "bot_uuid": bot_uuid} if dispose_drift
                       else {"left": True, "bot_uuid": bot_uuid})
            return (payload, http_record(
                "friend_connection_mutation.station_bot_delete",
                "/bots/" + bot_uuid, 200, "DELETE"))

        def fake_read(bot_uuid):
            station["read"] += 1
            return ({"code": "bot_not_found", "status": 404}, http_record(
                "friend_connection_mutation.station_bot_read",
                "/bots/" + bot_uuid, 404))

        def fake_token_read(self, operation, **kwargs):
            token_reads["count"] += 1
            token_reads["operation"] = operation
            return Observation(data={"token": REGISTER_TOKEN} if read_token else {},
                               evidence=http_record(
                                   "station.register_token.read",
                                   "/register/token", 200))

        environment = preprod_environment() if preprod else local_environment()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, environment))
            if preprod:
                stack.enter_context(patch.object(
                    WriteClient, "call", autospec=True,
                    side_effect=self.station_dispatcher(
                        calls, registry_drift=registry_drift, delete_ack=delete_ack,
                        verify_status=verify_status)))
                if station_methods:
                    stack.enter_context(patch.object(
                        WriteClient, "station_register_bot", autospec=True,
                        side_effect=fake_register))
                    stack.enter_context(patch.object(
                        WriteClient, "station_bot_delete", create=True,
                        side_effect=fake_delete))
                    stack.enter_context(patch.object(
                        WriteClient, "station_bot_read", create=True,
                        side_effect=fake_read))
                stack.enter_context(patch.object(
                    ReadClient, "read", autospec=True, side_effect=fake_token_read))
            adapter = RemoteOperatorCliAdapter(profile, runner)
            execution = execute_operator_cli_case(adapter)
        return execution, runner, calls, station, token_reads

    # -- assertions ------------------------------------------------------
    def assert_no_placeholder(self, execution):
        rendered = json.dumps(execution.snapshot(), sort_keys=True)
        for placeholder in PLACEHOLDERS:
            self.assertNotIn(placeholder, rendered)

    def test_preprod_lifecycle_passes_all_thirteen_obligations(self):
        execution, runner, calls, station, token_reads = self.run_scripted_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 13)
        self.assertEqual(execution.total_count, 13)
        self.assertTrue(execution.ready_to_execute)
        self.assertIsNone(execution.error_type)
        self.assertEqual(
            [step.method for step in execution.steps],
            list(OPERATOR_CLI_ADAPTER_METHODS))
        self.assertEqual(
            [step.requirement_id for step in execution.steps],
            list(REQUIREMENTS))
        self.assertEqual([item[0] for item in runner.calls],
                         list(OPERATOR_CLI_CLI_OPERATIONS))
        kinds = ["none", DUO, DUO, DUO, DUO, DUO, DUO, ONBOARD_DUO, DUO,
                 HUMAN_COOKIE, DUO, DUO, DUO]
        for index, step in enumerate(execution.steps):
            self.assertEqual(step.status, "PASS", step.method)
            self.assertIsNone(step.error_type)
            evidence = step.evidence
            self.assertEqual(evidence["remote_validation"], "NOT_RUN")
            self.assertFalse(evidence["grants_release_credit"])
            self.assertEqual(evidence["fixture_effect"], OPERATOR_CLI_EFFECT)
            self.assertIn(evidence["transport"], {"PRODUCT_CLI", "PRODUCT_HTTP"})
            self.assertEqual(evidence["credential_kind"], kinds[index])
            self.assertEqual(evidence["gaps"], [])
            self.assertEqual(evidence["defects"], [])
        for index in (0, 1, 2, 3, 4, 5, 6, 10, 11, 12):
            self.assertEqual(execution.steps[index].evidence["transport"],
                             "PRODUCT_CLI")
        for index in (7, 8, 9):
            evidence = execution.steps[index].evidence
            self.assertEqual(evidence["transport"], "PRODUCT_HTTP")
        direct = execution.steps[7]
        self.assertEqual(direct.evidence["case_class"], "direct-onboard")
        self.assertEqual(direct.evidence["http_case_caller_role"],
                         "case_owned_driver")
        self.assertEqual(len(direct.evidence["requests"]), 3)
        self.assertIn("register_token_read", direct.evidence)
        self.assertTrue(direct.evidence["driver_token_restored"])
        self.assertEqual(direct.data["disposition"], "DELETED_404_VERIFIED")
        self.assertEqual(direct.data["onboarded_token_sha256"],
                         hashlib.sha256(BOT_TOKEN.encode()).hexdigest())
        self.assertEqual(direct.data["register_token_sha256"],
                         hashlib.sha256(REGISTER_TOKEN.encode()).hexdigest())
        update = execution.steps[8]
        self.assertEqual(update.evidence["case_class"], "update-status")
        self.assertEqual(len(update.evidence["requests"]), 2)
        self.assertTrue(update.data["registry_unchanged"])
        group = execution.steps[9]
        self.assertEqual(group.evidence["case_class"], "create-group")
        self.assertEqual(group.evidence["http_case_caller_role"],
                         "case_owned_member")
        self.assertEqual(len(group.evidence["requests"]), 2)
        self.assertEqual(group.data["disposition"],
                         "CREATOR_SCOPED_DELETE_404_VERIFIED")
        chat = execution.steps[10]
        self.assertEqual(chat.data["prerequisite_admission"]["caller"],
                         BOT_IDS["driver"])
        self.assertEqual(chat.data["prerequisite_admission"]["target"],
                         BOT_IDS["member"])
        self.assertEqual(chat.data["run_id"], RUN_ID)
        self.assertFalse(chat.data["detach_fallback"])
        friend = execution.steps[12]
        self.assertEqual(friend.data["prerequisite_admission"]["target"],
                         BOT_IDS["eng"])
        self.assertEqual(token_reads["count"], 1)
        self.assertEqual(token_reads["operation"],
                         "station.register_token.read")
        self.assertEqual(station["register"], 1)
        self.assertEqual(station["delete"], 1)
        self.assertEqual(station["read"], 1)
        self.assertEqual([item[0] for item in calls],
                         [REGISTRY_READ, REGISTRY_READ, GROUP_DELETE,
                          GROUP_DELETED_VERIFY])
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["case_id"], OPERATOR_CLI_CASE_ID)
        self.assertEqual(snapshot["parent_id"], PARENT)
        self.assertEqual(snapshot["status"], "PASS")
        self.assert_no_placeholder(execution)

    def test_singlebox_passes_seven_then_freezes_at_the_owned_local_boundary(self):
        execution, runner, calls, station, token_reads = self.run_scripted_case(
            preprod=False)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 7)
        self.assertFalse(execution.ready_to_execute)
        self.assertEqual(execution.error_type, "RunStateError")
        kinds = ["none", OWNED, OWNED, OWNED, OWNED, OWNED, OWNED]
        for index, step in enumerate(execution.steps[:7]):
            self.assertEqual(step.status, "PASS", step.method)
            self.assertEqual(step.evidence["credential_kind"], kinds[index])
            self.assertEqual(step.evidence["transport"], "PRODUCT_CLI")
        boundary = execution.steps[7]
        self.assertEqual(boundary.status, "NOT_RUN")
        self.assertEqual(boundary.error_type, "RunStateError")
        self.assertIn("LOCAL-OPERATOR-CLI-01", boundary.error_gap or "")
        for step in execution.steps[8:]:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertIsNone(step.error_type)
            self.assertEqual(step.evidence, {})
        self.assertEqual([item[0] for item in runner.calls],
                         list(OPERATOR_CLI_CLI_OPERATIONS[:8]))
        self.assertEqual(calls, [])
        self.assertEqual(station["register"], 0)
        self.assertEqual(station["delete"], 0)
        self.assertEqual(station["read"], 0)
        self.assertEqual(token_reads["count"], 0)
        self.assert_no_placeholder(execution)

    def test_station_dispose_ack_drift_fail_closes_direct_onboard_on_preprod(self):
        execution, runner, calls, station, token_reads = self.run_scripted_case(
            dispose_drift=True)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 7)
        boundary = execution.steps[7]
        self.assertEqual(boundary.status, "NOT_RUN")
        self.assertEqual(boundary.error_type, "RunStateError")
        self.assertIn("did not acknowledge exact disposal", boundary.error_gap or "")
        for step in execution.steps[8:]:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertIsNone(step.error_type)
            self.assertEqual(step.evidence, {})
        self.assertEqual(station["register"], 1)
        self.assertEqual(station["delete"], 1)
        self.assertEqual(station["read"], 0)
        self.assertEqual(token_reads["count"], 1)
        self.assertEqual(token_reads["operation"],
                         "station.register_token.read")
        self.assertEqual([item[0] for item in calls], [])
        self.assert_no_placeholder(execution)

    def test_runner_failure_freezes_the_remaining_obligations(self):
        failures = {OPERATOR_CLI_LIST: OperatorCliRunnerError("nonzero")}
        execution, _runner, _calls, _station, _reads = self.run_scripted_case(
            failures=failures)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 1)
        self.assertFalse(execution.ready_to_execute)
        self.assertEqual(execution.error_type, "OperatorCliRunnerError")
        frozen = execution.steps[1]
        self.assertEqual(frozen.status, "NOT_RUN")
        self.assertEqual(frozen.error_type, "OperatorCliRunnerError")
        for step in execution.steps[2:]:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertIsNone(step.error_type)
            self.assertEqual(step.evidence, {})

    def test_drift_matrix_blocks_without_partial_credit(self):
        foreign = "operator-foreign-bot-999"
        cases = {
            "registry-record-drift": (
                {"registry_drift": True}, 8, "RunStateError"),
            "group-delete-ack-drift": (
                {"delete_ack": True}, 9, "RunStateError"),
            "group-deleted-verify-drift": (
                {"verify_status": 200}, 9, "RunStateError"),
            "connect-handle-drift": (
                {"outputs": {OPERATOR_CLI_CONNECT: {
                    "bot_uuid": foreign, "is_new": False,
                    "token_present": True}}}, 5, "RunStateError"),
            "discover-cs-drift": (
                {"outputs": {OPERATOR_CLI_DISCOVER: {
                    "query": FIXTURE_NAMES["cs"], "bot_uuid": foreign}}},
                3, "RunStateError"),
            "web-onboard-registered": (
                {"outputs": {OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD: {
                    "probe_absent": False, "fixture_uuids_present": True}}},
                6, "RunStateError"),
            "web-onboard-mutating-registration": (
                {"outputs": {OPERATOR_CLI_ONBOARD_WEB: {
                    "probe_name": PROBE_NAME, "registered": True}}},
                6, "RunStateError"),
            "register-token-empty": ({"read_token": False}, 7,
                                     "RunStateError"),
            "station-register-missing-bot-token": (
                {"register_payload": {"bot_uuid": TEMP_UUID,
                                      "bot_name": "operator-cli-direct-x"}},
                7, "RunStateError"),
            "create-group-driver-drift": (
                {"outputs": {OPERATOR_CLI_CREATE_GROUP: {
                    "group_id": GROUP_ID, "driver_bot": foreign,
                    "participants": [BOT_IDS["member"], BOT_IDS["eng"]],
                    "topic": "operator cli e2e group"}}}, 9, "RunStateError"),
            "chat-handle-drift": (
                {"outputs": {OPERATOR_CLI_CHAT: {"run_id": 12,
                                                 "session_id": SESSION_ID,
                                                 "detach_fallback": False}}},
                10, "RunStateError"),
            "friend-list-missing-eng": (
                {"outputs": {OPERATOR_CLI_FRIEND_LIST: {
                    "friend_list_verified": False,
                    "target_bot": foreign}}}, 12, "RunStateError"),
        }
        for name, (overrides, completed, error_type) in cases.items():
            with self.subTest(drift=name):
                execution, _runner, _calls, _station, _reads = (
                    self.run_scripted_case(**overrides))
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, completed)
                self.assertEqual(execution.error_type, error_type)
                self.assertFalse(execution.ready_to_execute)
                for index, step in enumerate(execution.steps):
                    if index < completed:
                        self.assertEqual(step.status, "PASS", step.method)
                    else:
                        self.assertEqual(step.status, "NOT_RUN", step.method)
                        if index == completed:
                            self.assertEqual(step.error_type, error_type,
                                             step.method)
                        else:
                            self.assertIsNone(step.error_type, step.method)
                self.assert_no_placeholder(execution)

    def test_real_runner_plays_the_twenty_frozen_captures_on_preprod(self):
        profile = profile_value(preprod=True)
        script = CliScript()
        calls = []
        station = {"register": 0, "delete": 0, "read": 0,
                   "register_authorization": "", "register_cookie": "",
                   "register_token": "", "register_bot_name": "",
                   "deleted_uuid": "", "read_uuid": ""}
        registry = dict(DRIVER_RECORD)

        def fake_call(self, operation, **kwargs):
            calls.append((operation, kwargs))
            if operation == REGISTRY_READ:
                return (dict(registry), http_record(
                    REGISTRY_READ, "/bots/" + BOT_IDS["driver"], 200))
            if operation == GROUP_DELETE:
                return ({"deleted": True}, http_record(
                    GROUP_DELETE, "/groups/" + GROUP_ID, 200, "DELETE"))
            if operation == GROUP_DELETED_VERIFY:
                return (None, http_record(
                    GROUP_DELETED_VERIFY, "/groups/" + GROUP_ID, 404))
            raise AssertionError("unexpected transport operation: " + operation)

        def fake_register(self, token, bot_name):
            station["register"] += 1
            station["register_authorization"] = self.headers.get("Authorization")
            station["register_cookie"] = self.headers.get("Cookie")
            station["register_token"] = token
            station["register_bot_name"] = bot_name
            return ({"bot_uuid": TEMP_UUID, "bot_token": BOT_TOKEN,
                     "bot_name": bot_name}, http_record(
                "friend_connection_mutation.register_bot.create",
                "/register?token=[REDACTED-register-token]&bot-name=" + bot_name,
                200, "POST"))

        def fake_delete(bot_uuid):
            station["delete"] += 1
            station["deleted_uuid"] = bot_uuid
            return ({"left": True, "bot_uuid": bot_uuid}, http_record(
                "friend_connection_mutation.station_bot_delete",
                "/bots/" + bot_uuid, 200, "DELETE"))

        def fake_read(bot_uuid):
            station["read"] += 1
            station["read_uuid"] = bot_uuid
            return ({"code": "bot_not_found", "status": 404}, http_record(
                "friend_connection_mutation.station_bot_read",
                "/bots/" + bot_uuid, 404))

        token_reads = {"count": 0}

        def fake_token_read(self, operation, **kwargs):
            token_reads["count"] += 1
            return Observation(data={"token": REGISTER_TOKEN},
                               evidence=http_record(
                                   "station.register_token.read",
                                   "/register/token", 200))

        environment = preprod_environment()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, environment))
            stack.enter_context(patch(
                "bcs_e2e.operator_cli_runner.capture", side_effect=script))
            stack.enter_context(patch.object(
                WriteClient, "call", autospec=True, side_effect=fake_call))
            stack.enter_context(patch.object(
                WriteClient, "station_register_bot", autospec=True,
                side_effect=fake_register))
            stack.enter_context(patch.object(
                WriteClient, "station_bot_delete", create=True,
                side_effect=fake_delete))
            stack.enter_context(patch.object(
                WriteClient, "station_bot_read", create=True,
                side_effect=fake_read))
            stack.enter_context(patch.object(
                ReadClient, "read", autospec=True, side_effect=fake_token_read))
            runner_instance = OperatorCliRunner(
                profile, binary=self.binary, binary_sha256=self.digest,
                state_dir=self.state)
            adapter = RemoteOperatorCliAdapter(profile, runner_instance)
            execution = execute_operator_cli_case(adapter)
            self.assertEqual(os.environ[DRIVER_TOKEN_ENV], DRIVER_TOKEN)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 13)
        self.assertIsNone(execution.error_type)
        self.assertEqual(script.count, 25)
        self.assertEqual(len(script.commands), 25)
        self.assertEqual(runner_instance.executed,
                         list(OPERATOR_CLI_CLI_OPERATIONS))
        tag_counts = Counter(
            operation_tag(command[command.index("--log-level") + 2:])
            for command in script.commands)
        self.assertEqual(tag_counts["visibility_get"], 3)
        self.assertEqual(tag_counts["visibility_set"], 2)
        self.assertEqual(tag_counts["update_status"], 2)
        self.assertEqual(tag_counts["chat_detach"], 1)
        self.assertEqual(station["register"], 1)
        self.assertEqual(station["delete"], 1)
        self.assertEqual(station["read"], 1)
        self.assertEqual([item[0] for item in calls],
                         [REGISTRY_READ, REGISTRY_READ, GROUP_DELETE,
                          GROUP_DELETED_VERIFY])
        self.assertEqual(station["register_authorization"], REGISTER_TOKEN)
        self.assertEqual(station["register_cookie"], COOKIE)
        self.assertEqual(station["register_token"], REGISTER_TOKEN)
        self.assertTrue(station["register_bot_name"].startswith(
            "operator-cli-direct-"))
        self.assertEqual(station["deleted_uuid"], TEMP_UUID)
        self.assertEqual(station["read_uuid"], TEMP_UUID)
        direct = execution.steps[7]
        self.assertEqual(direct.data["bot_id"], TEMP_UUID)
        self.assertEqual(direct.evidence["credential_kind"], ONBOARD_DUO)
        self.assertTrue(direct.evidence["driver_token_restored"])
        self.assertEqual(direct.evidence["cli_operations"],
                         [OPERATOR_CLI_ONBOARD_DIRECT,
                          OPERATOR_CLI_ONBOARD_READBACK])
        chat = execution.steps[10]
        self.assertEqual(chat.data["run_id"], RUN_ID)
        self.assertFalse(chat.data["detach_fallback"])
        self.assert_no_placeholder(execution)

    def test_web_onboard_step_keeps_the_non_mutating_projection(self):
        execution, _runner, _calls, _station, _reads = self.run_scripted_case()
        web = execution.steps[6]
        self.assertEqual(web.evidence["case_class"], "web-onboard")
        self.assertEqual(web.evidence["transport"], "PRODUCT_CLI")
        self.assertEqual(web.data["registered"], False)
        self.assertEqual(web.data["probe_absent"], True)
        self.assertEqual(web.data["fixture_uuids_present"], True)


if __name__ == "__main__":
    unittest.main()
