"""Source-bound Session Service member-chat lifecycle tests; no live request."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner
from bcs_e2e.local_session_service_cli import (
    SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
    SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
    SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
    RemoteLocalSessionServiceMemberChatAdapter,
    execute_local_session_service_member_chat_case,
    local_session_service_member_chat_adaptability,
)
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_CHAT_OPERATION,
    SESSION_CLI_SESSION_MEMBER_ADD_OPERATION,
    SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION,
    SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION,
    SESSION_CLI_SESSION_MESSAGES_OPERATION,
    SESSION_CLI_SESSION_PATCH_TITLE_OPERATION,
    SESSION_CLI_SESSION_DELETE_OPERATION,
    SESSION_CLI_SESSION_GROUP_LISTING_OPERATION,
    validate_session_cli_lifecycle_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session_service_02"
SESSION = GROUP + ":01020304"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"
TITLE = "CLI full session"
CHAT_MESSAGE = "CLI member-chat lifecycle check"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-session-cli-lifecycle-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "session-service-preprod" if preprod else "session-service-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://gateway.example.com" if preprod else "http://127.0.0.1:21001",
            "api_face": "legacy",
            "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {
                "bot_id": DRIVER, "owner_id": "39464",
                "dedicated": True, "visibility": "public",
            },
            "member": {
                "bot_id": MEMBER, "owner_id": "39464",
                "dedicated": True, "visibility": "public",
            },
        },
        "auth": (
            {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
            if preprod else
            {"kind": "owned-local-bot-tokens",
             "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
        ),
        "relationships": {
            "group": "bounded-owned-bots-cli-session-lifecycle",
            "initial_session": "deleted-with-group",
            "explicit_session": "renamed-muted-chatted-deleted",
            "cleanup": "exact-owned-group",
            "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "session-service-lifecycle-pool",
            "exclusive_use_confirmed": True,
            "timeout_seconds": 10,
            "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {
            "transport": "product-cli", "command_family": "session",
            "api_face": "legacy",
        },
        "route_bindings": {
            SESSION_CLI_SESSION_PATCH_TITLE_OPERATION: {
                "method": "PATCH", "path": "/sessions/{session_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MEMBER_ADD_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/members",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION: {
                "method": "PATCH",
                "path": "/sessions/{session_id}/members/{member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_CHAT_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/chat",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MESSAGES_OPERATION: {
                "method": "GET", "path": "/sessions/{session_id}/messages",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}/members/{member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_GROUP_LISTING_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}/sessions",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled",
            },
        },
    }
    if mutate is not None:
        mutate(value)
    return value


def group_output():
    return (
        f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
        f"  Participants: {DRIVER},{MEMBER}\n  Session: {GROUP}:00000001\n"
    ).encode()


def session_data():
    return {
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": TITLE,
    }


def participant_rows(mode="auto"):
    row = {"bot_uuid": MEMBER, "role": "consultant", "actor_kind": "bot"}
    if mode is not None:
        row["mode"] = mode
    return [
        {"bot_uuid": DRIVER, "role": "driver", "actor_kind": "bot",
         "mode": "auto"},
        row,
    ]


def session_state(mode="auto", member=True):
    return {
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": TITLE,
        "participants": participant_rows(mode) if member else [
            {"bot_uuid": DRIVER, "role": "driver", "actor_kind": "bot",
             "mode": "auto"},
        ],
    }


def chat_output():
    return {
        "delivered": True, "session_id": SESSION, "group_id": GROUP,
        "driver_bot": DRIVER, "delivered_count": 1,
        "queue_admission": True, "failed_count": 0,
        "delivery_results": [
            {"bot_uuid": MEMBER, "delivery_type": "send", "success": True},
        ],
        "mentions": [],
    }


def invite_output(profile):
    token = "member-chat-token"
    scheme = "https" if profile["environment"]["tier"] == "preprod" else "http"
    return {
        "invite_token": token,
        "join_url": (
            f"{scheme}://workstation.invalid/bcn/chat/invite/sessions/"
            + token),
        "expires_at": 20261025000000,
    }


class SessionServiceMemberChatTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_case(self, *, preprod=False, deform=None):
        profile = validate_session_cli_lifecycle_profile(profile_value(preprod=preprod))
        runner = WorkspaceCliRunner(
            profile, binary=self.binary, binary_sha256=self.digest,
            state_dir=self.state,
        )
        commands = []
        contexts = []

        def outputs(index: int) -> bytes:
            if index == 1:
                return group_output()
            if index == 2:
                value = session_data()
            elif index == 3:
                value = session_state(member=True)
            elif index == 4:
                value = session_state(mode="muted", member=True)
            elif index == 5:
                value = session_state(mode="muted", member=True)
            elif index == 6:
                value = chat_output()
            elif index == 7:
                value = [
                    {"id": "msg-1", "timestamp": 20261024000001,
                     "sender": DRIVER, "content": CHAT_MESSAGE,
                     "message_type": "human", "role": "user"},
                ]
            elif index == 8:
                value = invite_output(profile)
            elif index in (9, 10):
                value = session_state(member=False)
            else:
                raise AssertionError("unexpected command index")
            if deform is not None:
                value = deform(index, value)
            if index == 7:
                return json.dumps(value).encode()
            return json.dumps(value).encode()

        def fake_capture(cmd, **kwargs):
            # Keep the command list explicit for evidence assertions.
            commands.append(cmd)
            contexts.append((
                kwargs["environment"]["HOME"],
                kwargs["environment"].get("BCS_COOKIE"),
            ))
            return subprocess.CompletedProcess(cmd, 0, outputs(len(commands)), b"")

        def fake_client(self_client, operation, **kwargs):
            if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                return {"deleted": True, "id": GROUP}, {
                    "operation": operation, "http_status": 200,
                    "path": "/session-service-member-chat",
                }
            if operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                return {
                    "status": 404, "code": "group_not_found",
                    "message": "Group not found",
                    "params": {"group_id": GROUP},
                }, {
                    "operation": operation, "http_status": 404,
                    "path": "/session-service-member-chat",
                }
            raise AssertionError("unexpected cleanup operation")

        env = {
            "BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
            "BCS_E2E_HUMAN_COOKIE": COOKIE,
        }
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_client, autospec=True):
            adapter = RemoteLocalSessionServiceMemberChatAdapter(profile, runner)
            execution = execute_local_session_service_member_chat_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_member_chat_lifecycle_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.session_id, SESSION)
                self.assertEqual(len(commands), 10)
                self.assertEqual(commands[0][-1], "bcs-e2e-session-cli-create-list-get")
                self.assertEqual(commands[2][-3:], [MEMBER, "--role", "consultant"])
                self.assertEqual(commands[3][-4:], [SESSION, MEMBER, "--mode", "muted"])
                self.assertIn(CHAT_MESSAGE, commands[5])
                self.assertEqual(commands[6][-5:], [SESSION, "--view-bot", DRIVER, "--limit", "50"])
                snapshot = result.snapshot()
                self.assertEqual(snapshot["case_id"], SESSION_SERVICE_MEMBER_CHAT_CASE_ID)
                self.assertEqual(len(snapshot["steps"]), 18)
                self.assertEqual(snapshot["steps"][0]["method"], SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD)
                self.assertEqual(snapshot["steps"][15]["data"]["member_absent"], True)
                self.assertEqual(snapshot["steps"][12]["data"]["fields"],
                                 ["expires_at", "invite_token", "join_url"])
                self.assertTrue(snapshot["steps"][13]["data"]["token_present"])
                self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])

    def test_foreign_member_role_is_blocked(self):
        def deform(index, value):
            if index == 3:
                value["participants"][1]["role"] = "manager"
        result, _ = self._blocked(deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_not_muted_is_blocked(self):
        def deform(index, value):
            if index == 4:
                for row in value["participants"]:
                    if row["bot_uuid"] == MEMBER:
                        row["mode"] = "auto"
        result, _ = self._blocked(deform)
        self.assertEqual(result.status, "BLOCKED")

    def test_chat_history_drift_is_blocked(self):
        def deform(index, value):
            if index == 7:
                value[0]["content"] = "foreign message"
        result, _ = self._blocked(deform)
        self.assertEqual(result.status, "BLOCKED")

    def test_invite_link_drift_is_blocked(self):
        def deform(index, value):
            if index == 8:
                value["join_url"] = "https://foreign.example.com/sessions/join/x"
        result, _ = self._blocked(deform)
        self.assertEqual(result.status, "BLOCKED")

    def test_removed_member_drift_is_blocked(self):
        def deform(index, value):
            if index == 10:
                value["participants"].append(participant_rows("auto")[1])
        result, _ = self._blocked(deform)
        self.assertEqual(result.status, "BLOCKED")

    def test_adaptability_is_source_bound_and_without_parent_credit(self):
        value = local_session_service_member_chat_adaptability()
        self.assertEqual(value["case_id"], SESSION_SERVICE_MEMBER_CHAT_CASE_ID)
        self.assertEqual(value["requirement_ids"], [SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        plan = migration_plan()
        self.assertEqual(plan["migrated_story_count"], 0)

    def test_full_parent_executor_replaces_the_superseded_slice_registration(self):
        plan = migration_plan()
        row = next(item for item in plan["local_registered_executors"]
                   if item["case_id"] == "LOCAL-SESSION-SERVICE-PARENT-01")
        self.assertEqual(row["legacy_id"], "story_cli_operator_runs_sessions_and_services")
        self.assertEqual(row["required_assertion_count"], 44)
        self.assertNotIn(SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
                         [item["case_id"] for item in plan["local_partial_executors"]])
        self.assertEqual(row["execution_command"], "execute_local_session_service_parent_case")
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [
            ROOT / "bcs_e2e/local_session_service_cli.py",
            ROOT / "bcs_e2e/workspace_cli_runner.py",
            ROOT / "bcs_e2e/workspace_cli_commands.py",
        ]
        result = subprocess.run(
            [sys.executable, str(guard), *map(str, files)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def _blocked(self, deform):
        result, adapter, _commands, _contexts = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        return result, adapter
