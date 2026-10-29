"""Bounded product-CLI runner tests for the dual Operator CLI slice.

Every case drives the frozen twenty-operation Operator CLI sequence (or an
ordered prefix of it) through a scripted capture double; no real process,
network or credential is ever used. Placeholder credential strings are
asserted absent from every journaled projection.
"""
from __future__ import annotations

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
    OPERATOR_CLI_ROUTES,
    OPERATOR_CLI_ROUTE_BINDINGS,
    OPERATOR_CLI_STATION_BINDINGS,
    OPERATOR_CLI_UPDATE_STATUS,
    OPERATOR_CLI_VISIBILITY,
    OperatorCliRunner,
    OperatorCliRunnerError,
    validate_operator_cli_profile,
)
from bcs_e2e.bounded_process import ProcessCaptureError
from bcs_e2e.profile import ConfigError

ROOT_GUARD = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
RUNNER_SOURCE = ROOT / "bcs_e2e/operator_cli_runner.py"

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
ONBOARD_TOKEN = "operator-onboard-token-private-value"
ROLE_TOKENS = {role: "operator-" + role + "-token-private" for role in ROLES}
COOKIE_ENV = "BCS_E2E_OPERATOR_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_OPERATOR_DRIVER_BOT_TOKEN"
MEMBER_TOKEN_ENV = "BCS_E2E_OPERATOR_MEMBER_BOT_TOKEN"
ENG_TOKEN_ENV = "BCS_E2E_OPERATOR_ENG_BOT_TOKEN"
QA_TOKEN_ENV = "BCS_E2E_OPERATOR_QA_BOT_TOKEN"
CS_TOKEN_ENV = "BCS_E2E_OPERATOR_CS_BOT_TOKEN"
TEMP_UUID = "operator-temp-bot-4177"
TEMP_NAME = "operator-cli-direct-4177af"
GROUP_ID = "operator-group-0001"
RUN_ID = "operator-run-0001"
SESSION_ID = "operator-session-0001"
REQUEST_ID = "operator-request-0001"
CONNECT_TOKEN = "operator-connect-token-private-value"

LOCAL_ORIGIN = "http://127.0.0.1:21300"
PREPROD_ORIGIN = "https://bcs-pre.alipay.com"
BROWSER_ORIGIN = "https://teamclaw-pre.alipay.com"

OP_INDEX = {operation: index for index, operation
            in enumerate(OPERATOR_CLI_CLI_OPERATIONS)}


def profile_value(*, preprod=False, mutate=None):
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
        "human": {"user_id": "operator-human-1"},
        "actors": {role: {"bot_id": BOT_IDS[role], "owner_id": "operator-human-1",
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
        "route_bindings": json.loads(json.dumps(OPERATOR_CLI_ROUTE_BINDINGS)),
    }
    if preprod:
        value["browser_origin"] = BROWSER_ORIGIN
        value["edge_case_bindings"] = json.loads(json.dumps(OPERATOR_CLI_STATION_BINDINGS))
    if mutate is not None:
        mutate(value)
    return value


def full_environment(*, preprod=False):
    environment = {
        DRIVER_TOKEN_ENV: DRIVER_TOKEN,
        MEMBER_TOKEN_ENV: ROLE_TOKENS["member"],
        ENG_TOKEN_ENV: ROLE_TOKENS["eng"],
        QA_TOKEN_ENV: ROLE_TOKENS["qa"],
        CS_TOKEN_ENV: ROLE_TOKENS["cs"],
    }
    if preprod:
        environment[COOKIE_ENV] = COOKIE
    return environment


def list_text():
    return "Trusted-team fixture Bots:\n" + "".join(
        "  " + BOT_IDS[role] + "\n" for role in ROLES)


def group_create_text(driver=None, participants=None):
    return ("Group created:\n  ID: " + GROUP_ID + "\n  Driver: "
            + (driver if driver is not None else BOT_IDS["member"]) + "\n  Participants: "
            + (participants if participants is not None
               else BOT_IDS["member"] + "," + BOT_IDS["eng"]) + "\n")


def group_read_text():
    return ("Group: " + GROUP_ID + "\n  Driver: " + BOT_IDS["member"]
            + "\n  Participants: " + BOT_IDS["member"] + "," + BOT_IDS["eng"] + "\n")


def operation_tag(arguments, json_global=False):
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
    if head == ("visibility", "set"):
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
    if head == ("friend", "list"):
        return "friend_list_json" if json_global else "friend_list"
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
    if tag == "friend_list_json":
        return (0, json.dumps([{"bot_uuid": BOT_IDS["member"], "is_online": True},
                               {"bot_uuid": BOT_IDS["eng"], "is_online": True}]).encode(), b"")
    raise AssertionError("unexpected CLI tag " + tag)


class CliScript:
    """Record and script every bounded product-CLI capture."""

    def __init__(self):
        self.commands = []
        self.environments = []
        self.sessions = []
        self.cwds = []
        self.overrides = {}
        self.leak = None
        self.leak_cookie = False
        self.echo_connect_token = False
        self.nonzero = ()
        self.visibility_queue = []

    def __call__(self, command, **kwargs):
        log_level_index = command.index("--log-level")
        arguments = command[log_level_index + 2:]
        tag = operation_tag(arguments, "--json" in command[:log_level_index])
        self.commands.append(list(command))
        self.environments.append(dict(kwargs["environment"]))
        self.sessions.append(json.loads(
            (Path(kwargs["environment"]["BOT_DATA_DIR"]) / ".bcs"
             / "session.json").read_text()))
        self.cwds.append(kwargs["cwd"])
        exit_code, stdout, stderr = output_for(tag, arguments)
        if tag == "connect" and self.echo_connect_token:
            stdout = json.dumps({"bot_uuid": BOT_IDS["driver"], "is_new": False,
                                 "token": DRIVER_TOKEN}).encode()
        if tag in self.nonzero:
            exit_code, stdout, stderr = 1, b"", b"forced nonzero\n"
        if tag in self.overrides:
            exit_code, stdout, stderr = self.overrides[tag]
        if tag == "visibility_get" and self.visibility_queue:
            mode = self.visibility_queue.pop(0)
            if mode == "json":
                stdout, stderr = json.dumps(
                    {"visibility": "protected"}).encode(), b""
        if self.leak == tag:
            payload = COOKIE if self.leak_cookie else DRIVER_TOKEN
            stdout = stdout + b"\n" + payload.encode()
        return subprocess.CompletedProcess(command, exit_code, stdout, stderr)

    def capture_index(self, tag):
        return [operation_tag(tail) for tail in self.tails()].index(tag)

    def tails(self):
        return [command[command.index("--log-level") + 2:]
                for command in self.commands]


# The frozen once-only order; kwargs mirror the runner execute() contract.
OPERATIONS = [
    (OPERATOR_CLI_HEALTH, {}),
    (OPERATOR_CLI_LIST, {}),
    (OPERATOR_CLI_GET, {"target": BOT_IDS["driver"]}),
    (OPERATOR_CLI_DISCOVER, {"query": FIXTURE_NAMES["cs"]}),
    (OPERATOR_CLI_VISIBILITY, {}),
    (OPERATOR_CLI_CONNECT, {}),
    (OPERATOR_CLI_ONBOARD_WEB, {}),
    (OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD, {}),
    (OPERATOR_CLI_ONBOARD_DIRECT, {"name": TEMP_NAME,
                                   "onboard_token": ONBOARD_TOKEN,
                                   "expected_bot_uuid": TEMP_UUID}),
    (OPERATOR_CLI_ONBOARD_READBACK, {"bot_uuid": TEMP_UUID}),
    (OPERATOR_CLI_UPDATE_STATUS, {}),
    (OPERATOR_CLI_CREATE_GROUP, {}),
    (OPERATOR_CLI_GET_GROUP, {"group_id": GROUP_ID}),
    (OPERATOR_CLI_FRIEND_REQUEST_CHAT, {}),
    (OPERATOR_CLI_FRIEND_ACCEPT_CHAT, {"request_id": REQUEST_ID}),
    (OPERATOR_CLI_CHAT, {}),
    (OPERATOR_CLI_LIST_GROUPS, {}),
    (OPERATOR_CLI_FRIEND_REQUEST_LIST, {}),
    (OPERATOR_CLI_FRIEND_ACCEPT_LIST, {"request_id": REQUEST_ID}),
    (OPERATOR_CLI_FRIEND_LIST, {}),
]

# 25 scripted product-CLI captures for one full session (visibility is five,
# update-status is two).
CAPTURE_TAGS = [
    "health", "list", "get", "discover",
    "visibility_get", "visibility_set", "visibility_get",
    "visibility_set", "visibility_get",
    "connect", "onboard_web", "list", "onboard_direct", "get",
    "update_status", "update_status", "create_group", "get_group",
    "friend_request", "friend_accept", "chat_detach", "list_groups",
    "friend_request", "friend_accept", "friend_list",
]

# One row per frozen operation: (credential kind local, credential kind preprod).
OPERATION_KINDS = {
    OPERATOR_CLI_HEALTH: ("none", "none"),
    OPERATOR_CLI_LIST: ("owned_local_bot_token",
                        "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_GET: ("owned_local_bot_token",
                       "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_DISCOVER: ("owned_local_bot_token",
                            "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_VISIBILITY: ("owned_local_bot_token",
                              "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_CONNECT: ("owned_local_bot_token",
                           "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_ONBOARD_WEB: ("owned_local_bot_token",
                               "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_LIST_AFTER_WEB_ONBOARD: ("owned_local_bot_token",
                                          "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_ONBOARD_DIRECT: ("owned_local_bot_token",
                                  "human_cookie_with_owned_onboarded_bot_token"),
    OPERATOR_CLI_ONBOARD_READBACK: ("owned_local_bot_token",
                                    "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_UPDATE_STATUS: ("owned_local_bot_token",
                                 "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_CREATE_GROUP: ("owned_local_bot_token", "human_cookie"),
    OPERATOR_CLI_GET_GROUP: ("owned_local_bot_token", "human_cookie"),
    OPERATOR_CLI_FRIEND_REQUEST_CHAT: ("owned_local_bot_token",
                                       "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_FRIEND_ACCEPT_CHAT: ("owned_local_bot_token", "human_cookie"),
    OPERATOR_CLI_CHAT: ("owned_local_bot_token",
                        "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_LIST_GROUPS: ("owned_local_bot_token",
                               "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_FRIEND_REQUEST_LIST: ("owned_local_bot_token",
                                       "human_cookie_with_owned_driver_bot_token"),
    OPERATOR_CLI_FRIEND_ACCEPT_LIST: ("owned_local_bot_token", "human_cookie"),
    OPERATOR_CLI_FRIEND_LIST: ("owned_local_bot_token",
                               "human_cookie_with_owned_driver_bot_token"),
}


def make_runner(profile, root, script):
    binary = root / "bcs-cli"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    state = root / "state"
    state.mkdir(exist_ok=True)
    runner = OperatorCliRunner(profile, binary=binary, binary_sha256=digest,
                               state_dir=state)
    return runner, state


class OperatorCliHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)

    def run_prefix(self, last_operation, *, preprod=False, script=None):
        """Run the frozen prefix ending at last_operation, in order."""
        profile = validate_operator_cli_profile(profile_value(preprod=preprod))
        script = script if script is not None else CliScript()
        stop = OP_INDEX[last_operation] + 1
        runner, state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment(preprod=preprod)), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            results = [runner.execute(operation, **kwargs)
                       for operation, kwargs in OPERATIONS[:stop]]
        return runner, script, results, state

    def run_scripted(self, *, preprod=False, script=None, operations=None):
        profile = validate_operator_cli_profile(profile_value(preprod=preprod))
        script = script if script is not None else CliScript()
        runner, state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment(preprod=preprod)), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            results = []
            for operation, kwargs in (operations if operations is not None
                                      else OPERATIONS):
                results.append(runner.execute(operation, **kwargs))
        return runner, script, results, state


class OperatorCliRunnerTests(OperatorCliHarness):

    def test_case_guard_accepts_the_runner_source(self):
        result = subprocess.run(
            [sys.executable, str(ROOT_GUARD), str(RUNNER_SOURCE)],
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_both_tier_profiles_validate_with_the_frozen_contract(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                profile = validate_operator_cli_profile(
                    profile_value(preprod=preprod))
                self.assertEqual(profile["contract"], "dual-operator-cli-profile-v1")
                self.assertEqual(set(profile["route_bindings"]),
                                 set(OPERATOR_CLI_ROUTE_BINDINGS))
                self.assertEqual(
                    profile["environment"]["expected_bcs_env"],
                    "pre" if preprod else "local")
        local_profile = validate_operator_cli_profile(profile_value(preprod=False))
        self.assertNotIn("browser_origin", local_profile)
        self.assertNotIn("edge_case_bindings", local_profile)
        preprod_profile = validate_operator_cli_profile(profile_value(preprod=True))
        self.assertEqual(preprod_profile["browser_origin"], BROWSER_ORIGIN)
        self.assertEqual(set(preprod_profile["edge_case_bindings"]),
                         set(OPERATOR_CLI_STATION_BINDINGS))

    def test_local_full_sequence_matches_the_frozen_map_and_creds(self):
        runner, script, results, _state = self.run_scripted(preprod=False)
        self.assertEqual(runner.executed, list(OPERATOR_CLI_CLI_OPERATIONS))
        self.assertEqual(len(script.commands), 25)
        self.assertEqual([operation_tag(tail) for tail in script.tails()],
                         CAPTURE_TAGS)
        self.assertEqual(script.tails()[4:9], [
            ["visibility", "get"], ["visibility", "set", "--value", "public"],
            ["visibility", "get"], ["visibility", "set", "--value", "public"],
            ["visibility", "get"]])
        self.assertEqual(results[0]["data"], {"status": "healthy",
                                              "credential_free": True})
        self.assertEqual(results[1]["data"]["fixture_count"], 5)
        self.assertEqual(results[1]["data"]["fixture_uuids"],
                         [BOT_IDS[role] for role in ROLES])
        self.assertEqual(results[8]["data"],
                         {"bot_uuid": TEMP_UUID, "onboarded": True,
                          "bot_name": TEMP_NAME})
        self.assertTrue(results[7]["data"]["probe_absent"])
        self.assertEqual(results[10]["data"],
                         {"statuses": ["idle", "busy"], "acknowledged": True})
        self.assertEqual(results[11]["data"]["participants"],
                         [BOT_IDS["member"], BOT_IDS["eng"]])
        self.assertFalse(results[15]["data"]["detach_fallback"])
        for result, (operation, _) in zip(results, OPERATIONS):
            evidence = result["evidence"]
            self.assertEqual(evidence["operation"], operation)
            self.assertEqual(evidence["transport"], "PRODUCT_CLI")
            self.assertEqual(evidence["fixture_effect"], OPERATOR_CLI_EFFECT)
            self.assertEqual(evidence["remote_validation"], "NOT_RUN")
            self.assertFalse(evidence["grants_release_credit"])
            self.assertEqual(evidence["exit_status"], 0)
            self.assertIsInstance(evidence["elapsed_ms"], float)
        expected_roles = {
            OPERATOR_CLI_HEALTH: "health",
            OPERATOR_CLI_ONBOARD_DIRECT: "onboard",
            OPERATOR_CLI_CREATE_GROUP: "member",
            OPERATOR_CLI_GET_GROUP: "member",
            OPERATOR_CLI_FRIEND_ACCEPT_CHAT: "member",
            OPERATOR_CLI_FRIEND_ACCEPT_LIST: "eng",
        }
        for result, (operation, _) in zip(results, OPERATIONS):
            self.assertEqual(result["evidence"]["caller_role"],
                             expected_roles.get(operation, "driver"))
        self.assertEqual(
            [row["evidence"]["credential_kind"] for row in results],
            ["none"] + ["owned_local_bot_token"] * 19)
        for environment in script.environments:
            self.assertEqual(
                set(environment),
                {"PATH", "HOME", "BOT_DATA_DIR", "TMPDIR", "LANG",
                 "SERVER_ENV", "tc_sdb_nenv"})
            self.assertEqual(environment["SERVER_ENV"], "local")
            self.assertEqual(environment["tc_sdb_nenv"], "production")
            for name in (COOKIE_ENV, DRIVER_TOKEN_ENV, MEMBER_TOKEN_ENV,
                         ENG_TOKEN_ENV, QA_TOKEN_ENV, CS_TOKEN_ENV, "BCS_COOKIE"):
                self.assertNotIn(name, environment)
        driver_session = script.sessions[1]
        self.assertEqual(driver_session["token"], DRIVER_TOKEN)
        self.assertEqual(driver_session["bot_uuid"], BOT_IDS["driver"])
        self.assertEqual(driver_session["api_base_url"], LOCAL_ORIGIN)
        health_session = script.sessions[0]
        self.assertEqual(health_session, {"token": "", "bot_uuid": "",
                                          "api_base_url": LOCAL_ORIGIN})
        onboard_session = script.sessions[12]
        self.assertEqual(onboard_session["token"], ONBOARD_TOKEN)
        self.assertEqual(onboard_session["bot_uuid"], "")
        member_session = script.sessions[16]
        self.assertEqual(member_session["token"], ROLE_TOKENS["member"])
        self.assertEqual(member_session["bot_uuid"], BOT_IDS["member"])
        self.assertEqual(member_session["api_base_url"], LOCAL_ORIGIN)
        serialized = json.dumps(results, sort_keys=True, default=str)
        for placeholder in (COOKIE, DRIVER_TOKEN, ONBOARD_TOKEN, CONNECT_TOKEN,
                            *ROLE_TOKENS.values()):
            self.assertNotIn(placeholder, serialized)

    def test_preprod_full_sequence_uses_the_duo_cookie_and_no_token_env(self):
        runner, script, results, _state = self.run_scripted(preprod=True)
        self.assertEqual(runner.executed, list(OPERATOR_CLI_CLI_OPERATIONS))
        self.assertEqual(len(script.commands), 25)
        self.assertEqual([operation_tag(tail) for tail in script.tails()],
                         CAPTURE_TAGS)
        duo = "human_cookie_with_owned_driver_bot_token"
        onboard_duo = "human_cookie_with_owned_onboarded_bot_token"
        human_cookie = "human_cookie"
        self.assertEqual(
            [row["evidence"]["credential_kind"] for row in results],
            ["none", duo, duo, duo, duo, duo, duo, duo, onboard_duo, duo, duo,
             human_cookie, human_cookie, duo, human_cookie, duo, duo, duo,
             human_cookie, duo])
        capture_roles = (
            ["health"] + ["driver"] * 11 + ["onboard", "driver", "driver",
                                            "driver", "member", "member",
                                            "driver", "member", "driver",
                                            "driver", "driver", "eng", "driver"])
        self.assertEqual(len(capture_roles), 25)
        for index, role in enumerate(capture_roles):
            environment = script.environments[index]
            self.assertNotIn("SERVER_ENV", environment)
            base = {"PATH", "HOME", "BOT_DATA_DIR", "TMPDIR", "LANG"}
            if role == "health":
                self.assertEqual(set(environment), base)
            else:
                self.assertEqual(set(environment),
                                 base | {"BCS_COOKIE", "tc_sdb_nenv"})
                self.assertEqual(environment["BCS_COOKIE"], COOKIE)
                self.assertEqual(environment["tc_sdb_nenv"], "production")
            for name in (DRIVER_TOKEN_ENV, MEMBER_TOKEN_ENV, ENG_TOKEN_ENV,
                         QA_TOKEN_ENV, CS_TOKEN_ENV, COOKIE_ENV):
                self.assertNotIn(name, environment)
            session = script.sessions[index]
            self.assertEqual(session["api_base_url"], PREPROD_ORIGIN)
            if role == "health":
                self.assertEqual(session, {"token": "", "bot_uuid": "",
                                           "api_base_url": PREPROD_ORIGIN})
            elif role == "onboard":
                self.assertEqual(session["token"], ONBOARD_TOKEN)
                self.assertEqual(session["bot_uuid"], "")
            elif role == "driver":
                self.assertEqual(session["token"], DRIVER_TOKEN)
                self.assertEqual(session["bot_uuid"], BOT_IDS["driver"])
            else:
                self.assertEqual(session["token"], "")
                self.assertEqual(session["bot_uuid"], BOT_IDS[role])
        self.assertEqual(results[7]["evidence"]["credential_kind"], duo)
        self.assertEqual(results[8]["evidence"]["credential_kind"], onboard_duo)
        serialized = json.dumps(results, sort_keys=True, default=str)
        for placeholder in (COOKIE, DRIVER_TOKEN, ONBOARD_TOKEN, CONNECT_TOKEN,
                            *ROLE_TOKENS.values()):
            self.assertNotIn(placeholder, serialized)


class OperatorCliCommandTests(OperatorCliHarness):
    def test_command_argv_shapes_for_every_declared_route_family(self):
        checks = (
            (OPERATOR_CLI_HEALTH, {}, ["health"], False),
            (OPERATOR_CLI_LIST, {}, ["list"], False),
            (OPERATOR_CLI_GET, {"target": BOT_IDS["driver"]},
             ["get", BOT_IDS["driver"]], False),
            (OPERATOR_CLI_DISCOVER, {"query": FIXTURE_NAMES["cs"]},
             ["discover", "--query", FIXTURE_NAMES["cs"]], False),
            (OPERATOR_CLI_CONNECT, {}, ["connect"], True),
            (OPERATOR_CLI_FRIEND_REQUEST_CHAT, {},
             ["friend", "request", "--bot-uuid", BOT_IDS["member"]], True),
            (OPERATOR_CLI_FRIEND_ACCEPT_CHAT, {"request_id": REQUEST_ID},
             ["friend", "accept", "--request-id", REQUEST_ID], False),
            (OPERATOR_CLI_CHAT, {},
             ["chat", "--bot-uuid", BOT_IDS["member"], "--message",
              "operator e2e chat", "--detach"], True),
            (OPERATOR_CLI_LIST_GROUPS, {}, ["list-groups"], True),
            (OPERATOR_CLI_FRIEND_LIST, {}, ["friend", "list"], False),
        )
        for operation, kwargs, expected_tail, json_mode in checks:
            with self.subTest(operation=operation):
                prefix = list(OPERATIONS[:OP_INDEX[operation] + 1])
                prefix[-1] = (operation, kwargs)
                _runner, script, _results, _state = self.run_scripted(operations=prefix)
                tails = script.tails()
                index = tails.index(expected_tail)
                command = script.commands[index]
                self.assertEqual(command[1:3], ["--url", LOCAL_ORIGIN])
                self.assertEqual(command[3], "--json" if json_mode
                                 else "--log-level")
                self.assertEqual(command[command.index("--log-level") + 2:],
                                 expected_tail)
                self.assertEqual(script.sessions[index]["api_base_url"],
                                 LOCAL_ORIGIN)

    def test_visibility_round_trip_journals_the_exact_five_capture_order(self):
        _runner, script, results, _state = self.run_prefix(OPERATOR_CLI_VISIBILITY)
        self.assertEqual(script.tails()[4:9], [
            ["visibility", "get"], ["visibility", "set", "--value", "public"],
            ["visibility", "get"], ["visibility", "set", "--value", "public"],
            ["visibility", "get"]])
        self.assertEqual(results[-1]["data"],
                         {"original": "public", "round_trip_verified": True})

    def test_visibility_accepts_the_json_projection_and_restores_exactly(self):
        script = CliScript()
        script.visibility_queue = ["json", "line", "json"]
        _runner, script, results, _state = self.run_prefix(
            OPERATOR_CLI_VISIBILITY, script=script)
        self.assertEqual(results[-1]["data"],
                         {"original": "protected", "round_trip_verified": True})
        self.assertEqual(script.tails()[7],
                         ["visibility", "set", "--value", "protected"])

    def test_create_group_and_page_shapes_are_strict(self):
        script = CliScript()
        script.overrides = {"create_group": (
            0, b"Group created:\n  ID: " + GROUP_ID.encode() + b"\n", b"")}
        with self.assertRaisesRegex(OperatorCliRunnerError,
                                    "bounded line output"):
            self.run_prefix(OPERATOR_CLI_CREATE_GROUP, script=script)
        script = CliScript()
        script.overrides = {"list_groups": (0, json.dumps(
            {"items": [{"group_id": GROUP_ID}], "returned": 2,
             "total": 1, "has_more": False}).encode(), b"")}
        with self.assertRaisesRegex(OperatorCliRunnerError, "page shape"):
            self.run_prefix(OPERATOR_CLI_LIST_GROUPS, script=script)

    def test_connect_chat_and_request_id_contracts_are_strict(self):
        script = CliScript()
        script.overrides = {"connect": (0, json.dumps(
            {"bot_uuid": BOT_IDS["driver"], "is_new": False}).encode(), b"")}
        with self.assertRaisesRegex(OperatorCliRunnerError, "contract differs"):
            self.run_prefix(OPERATOR_CLI_CONNECT, script=script)
        script = CliScript()
        script.overrides = {"chat_detach": (0, json.dumps(
            {"run_id": "run with spaces", "session_id": SESSION_ID}).encode(), b"")}
        with self.assertRaisesRegex(OperatorCliRunnerError, "CLI chat run ID"):
            self.run_prefix(OPERATOR_CLI_CHAT, script=script)
        script = CliScript()
        script.overrides = {"friend_request": (0, json.dumps(
            {"data": {"id": ""}}).encode(), b"")}
        with self.assertRaisesRegex(OperatorCliRunnerError, "friend request ID"):
            self.run_prefix(OPERATOR_CLI_FRIEND_REQUEST_CHAT, script=script)

    def test_chat_detach_fallback_retries_the_blocking_command(self):
        script = CliScript()
        script.overrides = {"chat_detach": (1, b"", b"detach unsupported\n")}
        runner, script, results, _state = self.run_prefix(OPERATOR_CLI_CHAT, script=script)
        self.assertTrue(results[-1]["data"]["detach_fallback"])
        self.assertEqual(script.tails()[20:22], [
            ["chat", "--bot-uuid", BOT_IDS["member"], "--message",
             "operator e2e chat", "--detach"],
            ["chat", "--bot-uuid", BOT_IDS["member"], "--message",
             "operator e2e chat"]])
        self.assertEqual(runner.executed, list(OPERATOR_CLI_CLI_OPERATIONS[:16]))

    def test_private_context_is_removed_and_session_zeroed_before_return(self):
        _runner, script, _results, _state = self.run_prefix(OPERATOR_CLI_LIST)
        context = Path(script.cwds[-1])
        self.assertFalse(context.exists())
        run_root = Path(script.cwds[0])
        self.assertFalse(run_root.exists())
        self.assertEqual(sorted(item.name for item in run_root.parent.iterdir()),
                         [])

    def test_once_only_reorder_and_unknown_leaf_rejections(self):
        runner, _script, _results, _state = self.run_prefix(OPERATOR_CLI_GET)
        with self.assertRaisesRegex(OperatorCliRunnerError, "already executed"):
            runner.execute(OPERATOR_CLI_LIST)
        with self.assertRaisesRegex(OperatorCliRunnerError,
                                    "cannot be reordered or skipped"):
            runner.execute(OPERATOR_CLI_VISIBILITY)
        with self.assertRaisesRegex(OperatorCliRunnerError,
                                    "not a reviewed operator CLI leaf"):
            runner.execute("operator_cli.nope")

    def test_nonzero_exit_blocks_the_case(self):
        script = CliScript()
        script.nonzero = ("list",)
        with self.assertRaisesRegex(OperatorCliRunnerError, "nonzero"):
            self.run_prefix(OPERATOR_CLI_LIST, script=script)

    def test_leaked_credentials_are_scrubbed_before_projection(self):
        script = CliScript()
        script.leak = "discover"
        script.leak_cookie = True
        _runner, _script, results, _state = self.run_prefix(
            OPERATOR_CLI_DISCOVER, preprod=True, script=script)
        discover = results[OP_INDEX[OPERATOR_CLI_DISCOVER]]
        self.assertEqual(discover["evidence"]["operation"], OPERATOR_CLI_DISCOVER)
        self.assertEqual(discover["data"]["bot_uuid"], BOT_IDS["cs"])
        self.assertEqual(script.tails()[-1],
                         ["discover", "--query", FIXTURE_NAMES["cs"]])

        script = CliScript()
        script.leak = "list"
        script.leak_cookie = False
        _runner, _script, results, _state = self.run_prefix(
            OPERATOR_CLI_LIST, script=script)
        listing = results[OP_INDEX[OPERATOR_CLI_LIST]]
        self.assertEqual(listing["evidence"]["operation"], OPERATOR_CLI_LIST)
        self.assertEqual(listing["data"]["fixture_count"], 5)

        serialized = json.dumps(results, sort_keys=True, default=str)
        for placeholder in (COOKIE, DRIVER_TOKEN, *ROLE_TOKENS.values()):
            self.assertNotIn(placeholder, serialized)

    def test_connect_credential_echo_projects_without_journaling_the_secret(self):
        script = CliScript()
        script.echo_connect_token = True
        _runner, _script, results, _state = self.run_prefix(
            OPERATOR_CLI_CONNECT, preprod=True, script=script)
        connect = results[OP_INDEX[OPERATOR_CLI_CONNECT]]
        self.assertEqual(connect["evidence"]["operation"], OPERATOR_CLI_CONNECT)
        self.assertTrue(connect["data"]["token_present"])
        self.assertEqual(connect["evidence"]["credential_kind"],
                         "human_cookie_with_owned_driver_bot_token")
        serialized = json.dumps(results, sort_keys=True, default=str)
        for placeholder in (COOKIE, DRIVER_TOKEN, ONBOARD_TOKEN, CONNECT_TOKEN,
                            *ROLE_TOKENS.values()):
            self.assertNotIn(placeholder, serialized)

    def test_invoke_scrubs_the_echoed_credential_from_raw_output(self):
        profile = validate_operator_cli_profile(profile_value(preprod=True))
        script = CliScript()
        script.echo_connect_token = True
        runner, _state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment(preprod=True)), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            raw, _code, _elapsed, _kind = runner._invoke(
                "driver", arguments=("connect",), json_mode=True)
        self.assertIn(b"<redacted-credential>", raw)
        self.assertNotIn(DRIVER_TOKEN.encode(), raw)
        self.assertNotIn(COOKIE.encode(), raw)

    def test_missing_credentials_block_before_any_process_start(self):
        profile = validate_operator_cli_profile(profile_value(preprod=True))
        script = CliScript()
        runner, _state = make_runner(profile, self.root, script)
        environment = {DRIVER_TOKEN_ENV: DRIVER_TOKEN}
        with patch.dict(os.environ, environment), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            runner.execute(OPERATOR_CLI_HEALTH)
            self.assertEqual(len(script.commands), 1)
            with self.assertRaisesRegex(OperatorCliRunnerError,
                                        "Human Cookie is absent"):
                runner.execute(OPERATOR_CLI_LIST)
        self.assertEqual(len(script.commands), 1)
        profile = validate_operator_cli_profile(profile_value(preprod=False))
        script = CliScript()
        runner, _state = make_runner(profile, self.root, script)
        environment = {name: value for name, value in full_environment().items()
                       if name != DRIVER_TOKEN_ENV}
        with patch.dict(os.environ, environment), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            runner.execute(OPERATOR_CLI_HEALTH)
            with self.assertRaisesRegex(OperatorCliRunnerError,
                                        "owned driver Bot credential is absent"):
                runner.execute(OPERATOR_CLI_LIST)
        self.assertEqual(len(script.commands), 1)
        script = CliScript()
        runner, _state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            for operation, kwargs in OPERATIONS[:8]:
                runner.execute(operation, **kwargs)
            capture_count = len(script.commands)
            with self.assertRaisesRegex(OperatorCliRunnerError,
                                        "requires the registered Bot token"):
                runner.execute(OPERATOR_CLI_ONBOARD_DIRECT, name=TEMP_NAME,
                               onboard_token="")
        self.assertEqual(len(script.commands), capture_count)

    def test_process_failure_and_unconfirmed_cleanup_map_to_case_errors(self):
        profile = validate_operator_cli_profile(profile_value(preprod=False))
        script = CliScript()

        def hard_failure(command, **kwargs):
            raise ProcessCaptureError("boom")

        runner, _state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            runner.execute(OPERATOR_CLI_HEALTH)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture",
                   side_effect=hard_failure):
            with self.assertRaisesRegex(OperatorCliRunnerError, "process failed"):
                runner.execute(OPERATOR_CLI_LIST)

        def unconfirmed_shutdown(command, **kwargs):
            error = ProcessCaptureError("deadline")
            error.child_shutdown_status = "UNCONFIRMED"
            raise error

        runner, _state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            runner.execute(OPERATOR_CLI_HEALTH)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture",
                   side_effect=unconfirmed_shutdown):
            with self.assertRaisesRegex(OperatorCliRunnerError,
                                        "process failed") as caught:
                runner.execute(OPERATOR_CLI_LIST)
            self.assertEqual(caught.exception.child_shutdown_status,
                             "UNCONFIRMED")

        import bcs_e2e.operator_cli_runner as runner_module
        real_write = runner_module.write

        def failing_cleanup_write(path, value):
            if (isinstance(value, dict)
                    and value and all(item == "" for item in value.values())):
                raise OSError("disk full")
            return real_write(path, value)

        runner, _state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            runner.execute(OPERATOR_CLI_HEALTH)
        with patch.dict(os.environ, full_environment()), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script), \
             patch("bcs_e2e.operator_cli_runner.write",
                   side_effect=failing_cleanup_write):
            with self.assertRaisesRegex(OperatorCliRunnerError,
                                        "cleanup is unconfirmed") as caught:
                runner.execute(OPERATOR_CLI_LIST)
            self.assertEqual(caught.exception.credential_cleanup_status,
                             "UNCONFIRMED")


class OperatorCliProfileTests(unittest.TestCase):
    def profile_mutations(self):
        mutations = {
            "local-browser-origin": lambda value: value.update(
                {"browser_origin": "http://127.0.0.1:21300"}),
            "local-edge-bindings": lambda value: value.update(
                {"edge_case_bindings": json.loads(
                    json.dumps(OPERATOR_CLI_STATION_BINDINGS))}),
            "preprod-missing-browser-origin": lambda value: value.pop(
                "browser_origin"),
            "preprod-missing-edge-bindings": lambda value: value.pop(
                "edge_case_bindings"),
            "preprod-station-path-drift": lambda value: value[
                "edge_case_bindings"]["friend_connection_mutation.register_bot.create"
                ].update({"path": "/register?token={register_token}"}),
            "preprod-station-extra-binding": lambda value: value[
                "edge_case_bindings"].update(
                {"friend_connection_mutation.legacy_revoke": {
                    "method": "DELETE", "path": "/x", "api_face": "station-legacy",
                    "auth": "human_cookie_owned_bot", "response": "station-revoke-json",
                    "traceparent": "disabled"}}),
            "tier": lambda value: value["environment"].update({"tier": "staging"}),
            "environment-binding": lambda value: value["environment"].update(
                {"expected_bcs_env": "pre"}),
            "schema": lambda value: value.update({"schema_version": 2}),
            "contract": lambda value: value.update(
                {"contract": "dual-operator-cli-profile-v2"}),
            "driver-visibility": lambda value: value["actors"]["driver"].update(
                {"visibility": "protected"}),
            "duplicate-bot": lambda value: value["actors"]["cs"].update(
                {"bot_id": BOT_IDS["driver"]}),
            "cross-owner": lambda value: value["actors"]["cs"].update(
                {"owner_id": "operator-human-2"}),
            "shared-bot": lambda value: value["actors"]["qa"].update(
                {"dedicated": False}),
            "lowercase-env-ref": lambda value: value["auth"].update(
                {"driver_token_env": "bcs_e2e_driver"}),
            "local-auth-kind": lambda value: value["auth"].update(
                {"kind": "human-cookie", "cookie_env": COOKIE_ENV}),
            "preprod-auth-kind": lambda value: value["auth"].update(
                {"kind": "human-cookie", "cookie_env": COOKIE_ENV}),
            "local-origin-https": lambda value: value["entrypoint"].update(
                {"origin": "https://127.0.0.1:21300"}),
            "preprod-origin-http": lambda value: value["entrypoint"].update(
                {"origin": "http://bcs-pre.example.com"}),
            "local-entrypoint-kind": lambda value: value["entrypoint"].update(
                {"kind": "gateway"}),
            "preprod-entrypoint-kind": lambda value: value["entrypoint"].update(
                {"kind": "gateway"}),
            "route-binding-drift": lambda value: value["route_bindings"][
                "operator_cli.bot_registry.read"].update({"method": "POST"}),
            "route-binding-missing": lambda value: value["route_bindings"].pop(
                "operator_cli.group.deleted_verify"),
            "relationships-drift": lambda value: value["relationships"].update(
                {"web_onboard": "mutating"}),
            "exclusive-use": lambda value: value["policy"].update(
                {"exclusive_use_confirmed": False}),
            "policy-bound": lambda value: value["policy"].update(
                {"timeout_seconds": 45}),
            "cli-binding": lambda value: value["cli"].update(
                {"command_family": "channel"}),
            "actor-missing": lambda value: value["actors"].pop("qa"),
            "fixture-name-missing": lambda value: value["fixture_names"].pop("cs"),
            "human-id": lambda value: value["human"].update(
                {"user_id": "not an identifier"}),
            "browser-origin-unvalidated": lambda value: value.update(
                {"browser_origin": "http://insecure.origin"}),
        }
        self.assertEqual(len(mutations), 31)
        return mutations

    def test_validator_matrix_rejects_drifted_profiles(self):
        for name, mutate in self.profile_mutations().items():
            with self.subTest(mutation=name):
                preprod = name.startswith("preprod") or name == (
                    "browser-origin-unvalidated")
                value = profile_value(preprod=preprod)
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_operator_cli_profile(value)

    def test_declared_routes_match_the_reviewed_contract_surface(self):
        self.assertEqual(len(OPERATOR_CLI_ROUTES), 20)
        self.assertEqual(OPERATOR_CLI_ROUTES[OPERATOR_CLI_LIST],
                         ("GET", "/bots"))
        self.assertEqual(OPERATOR_CLI_ROUTES[OPERATOR_CLI_ONBOARD_DIRECT],
                         ("POST",
                          "/register?token={register_token}&bot-name={bot_name}"))
        self.assertEqual(len(OPERATOR_CLI_ROUTE_BINDINGS), 3)


if __name__ == "__main__":
    unittest.main()
