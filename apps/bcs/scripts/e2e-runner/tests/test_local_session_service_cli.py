"""Source-bound Session Service CLI lifecycle tests; no live request."""
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
    SESSION_SERVICE_LIFECYCLE_CASE_ID,
    SESSION_SERVICE_LIFECYCLE_PARENT,
    SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
    RemoteLocalSessionServiceLifecycleAdapter,
    execute_local_session_service_lifecycle_case,
    local_session_service_lifecycle_adaptability,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_CHAT_OPERATION,
    SESSION_CLI_SESSION_DELETE_OPERATION,
    SESSION_CLI_SESSION_GROUP_LISTING_OPERATION,
    SESSION_CLI_SESSION_MEMBER_ADD_OPERATION,
    SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION,
    SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION,
    SESSION_CLI_SESSION_MESSAGES_OPERATION,
    SESSION_CLI_SESSION_PATCH_TITLE_OPERATION,
    validate_session_cli_lifecycle_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session_service_01"
SESSION = GROUP + ":0f0e0d0c"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"
TITLE = "CLI full session"
NEW_TITLE = "CLI release sign-off"


def profile_value(*, preprod=False):
    return {
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
            {"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
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
            "transport": "product-cli",
            "command_family": "session",
            "api_face": "legacy",
        },
        "route_bindings": {
            SESSION_CLI_SESSION_PATCH_TITLE_OPERATION: {
                "method": "PATCH",
                "path": "/sessions/{session_id}",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MEMBER_ADD_OPERATION: {
                "method": "POST",
                "path": "/sessions/{session_id}/members",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION: {
                "method": "PATCH",
                "path": "/sessions/{session_id}/members/{member_actor_id}",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_CHAT_OPERATION: {
                "method": "POST",
                "path": "/sessions/{session_id}/chat",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MESSAGES_OPERATION: {
                "method": "GET",
                "path": "/sessions/{session_id}/messages",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}/members/{member_actor_id}",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_SESSION_GROUP_LISTING_OPERATION: {
                "method": "GET",
                "path": "/groups/{group_id}/sessions",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                "method": "GET",
                "path": "/groups/{group_id}",
                "api_face": "legacy",
                "auth": "case_owned_driver",
                "response": "legacy-json",
                "traceparent": "disabled",
            },
        },
    }


def group_output():
    return (
        f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
        f"  Participants: {DRIVER},{MEMBER}\n  Session: {GROUP}:00000001\n"
    ).encode()


def session_data(title=TITLE):
    return {
        "id": SESSION,
        "session_id": SESSION,
        "group_id": GROUP,
        "session_title": title,
    }


def initial_session_data():
    return {
        "id": GROUP + ":00000000",
        "session_id": GROUP + ":00000000",
        "group_id": GROUP,
        "session_title": "新会话",
    }


def session_list():
    return {
        "items": [initial_session_data(), session_data()],
        "group_id": GROUP,
    }


class RemoteLocalSessionServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def execute_case(self, *, preprod=False, deform=None):
        profile = validate_session_cli_lifecycle_profile(profile_value(preprod=preprod))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)

        commands = []
        contexts = []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            if len(commands) == 1:
                result = group_output()
            elif len(commands) == 2:
                result = json.dumps(session_data()).encode()
            elif len(commands) == 3:
                value = session_list()
                if deform:
                    value = deform(value, "session-cli.session.list")
                result = json.dumps(value).encode()
            elif len(commands) == 4:
                value = session_data()
                if deform:
                    value = deform(value, "session-cli.session.get")
                result = json.dumps(value).encode()
            else:
                value = session_data(title=NEW_TITLE)
                if deform:
                    value = deform(value, "session-cli.session.patch")
                result = json.dumps(value).encode()
            contexts.append((kwargs["environment"]["HOME"], kwargs["environment"].get("BCS_COOKIE")))
            return subprocess.CompletedProcess(cmd, 0, result, b"")

        def fake_client(self_client, operation, **kwargs):
            if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                data = {"deleted": True, "id": GROUP}
                status = 200
            elif operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                data = {
                    "status": 404,
                    "code": "group_not_found",
                    "message": "Group not found",
                    "params": {"group_id": GROUP},
                }
                status = 404
            else:
                raise AssertionError("unexpected cleanup operation")
            return data, {
                "operation": operation,
                "http_status": status,
                "path": "/session-service-lifecycle",
            }

        env = {
            "BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
            "BCS_E2E_HUMAN_COOKIE": COOKIE,
        }
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call", side_effect=fake_client, autospec=True):
            adapter = RemoteLocalSessionServiceLifecycleAdapter(profile, runner)
            execution = execute_local_session_service_lifecycle_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_summary_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.execute_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.session_id, SESSION)
                self.assertEqual(commands[0][-1], "bcs-e2e-session-cli-create-list-get")
                self.assertEqual(commands[1][-1], "CLI full session")
                self.assertEqual(commands[-1][-1], "CLI release sign-off")
                snapshot = result.snapshot()
                self.assertEqual(snapshot["case_id"], SESSION_SERVICE_LIFECYCLE_CASE_ID)
                self.assertEqual(snapshot["steps"][0]["evidence"]["requests"][4]["case_class"],
                                 "session-cli.session.patch")
                self.assertTrue(snapshot["steps"][0]["data"]["group_absence_verified"])
                self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])

    def test_foreign_patch_title_is_rejected(self):
        def deform(value, operation):
            if operation == "session-cli.session.patch":
                value["session_title"] = "foreign-sess"
        result, _, _, _ = self.execute_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_list_missing_exact_session_is_rejected(self):
        def deform(value, operation):
            if operation == "session-cli.session.list":
                value["items"] = []
        result, _, _, _ = self.execute_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_list_foreign_group_envelope_is_rejected(self):
        def deform(value, operation):
            if operation == "session-cli.session.list":
                value["group_id"] = "foreign-group"
        result, _, _, _ = self.execute_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_patch_accepts_canonical_id_without_legacy_alias(self):
        def deform(value, operation):
            if operation == "session-cli.session.patch":
                value.pop("session_id", None)
            return value
        result, _, _, _ = self.execute_case(deform=deform)
        self.assertEqual(result.status, "PASS")
        self.assertIsNone(result.error_type)

    def test_get_title_drift_is_rejected(self):
        def deform(value, operation):
            if operation == "session-cli.session.get":
                value["session_title"] = "foreign-sess"
        result, _, _, _ = self.execute_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_adaptability_is_source_bound_and_unusable_for_full_parent_credit(self):
        value = local_session_service_lifecycle_adaptability()
        self.assertEqual(value["case_id"], SESSION_SERVICE_LIFECYCLE_CASE_ID)
        self.assertEqual(value["legacy_id"], SESSION_SERVICE_LIFECYCLE_PARENT)
        self.assertEqual(value["requirement_ids"], [SESSION_SERVICE_LIFECYCLE_REQUIREMENT])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        plan = migration_plan()
        self.assertEqual(plan["migrated_story_count"], 0)

    def test_full_parent_executor_replaces_the_superseded_slice_registration(self):
        plan = migration_plan()
        row = next(item for item in plan["local_registered_executors"]
                   if item["case_id"] == "LOCAL-SESSION-SERVICE-PARENT-01")
        self.assertEqual(row["legacy_id"], SESSION_SERVICE_LIFECYCLE_PARENT)
        self.assertEqual(row["required_assertion_count"], 4)
        self.assertNotIn(SESSION_SERVICE_LIFECYCLE_CASE_ID,
                         [item["case_id"] for item in plan["local_partial_executors"]])
        self.assertEqual(row["execution_command"], "execute_local_session_service_parent_case")
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])

    def test_case_is_exposed_as_a_cross_environment_product_cli_slice(self):
        value = local_session_service_lifecycle_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next(item for item in rows if item["case_id"] == value["case_id"])
        self.assertEqual(row["requirement_ids"], value["requirement_ids"])
        self.assertEqual(row["local_executor_status"], "LOCAL_FULL_EXECUTOR")
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
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
