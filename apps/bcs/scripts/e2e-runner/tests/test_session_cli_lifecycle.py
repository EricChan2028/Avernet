"""Cross-environment SESSION.lifecycle product-CLI tests; no live request."""

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
from bcs_e2e.session_cli_shared import (
    SESSION_CLI_LIFECYCLE_CASE_ID,
    RemoteSessionCliLifecycleAdapter,
    execute_session_cli_lifecycle_case,
    session_cli_lifecycle_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
)
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
GROUP = "bcs_grp_session_cli_07"
SESSION = GROUP + ":090a0b0c"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-session-cli-lifecycle-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "session-cli-preprod" if preprod else "session-cli-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://gateway.example.com" if preprod else "http://127.0.0.1:21000",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
        },
        "auth": ({"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
                 if preprod else
                 {"kind": "owned-local-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}),
        "relationships": {
            "group": "bounded-owned-bots-cli-session-lifecycle",
            "initial_session": "deleted-with-group",
            "explicit_session": "renamed-muted-chatted-deleted",
            "cleanup": "exact-owned-group", "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "session-cli-pool", "exclusive_use_confirmed": True,
            "timeout_seconds": 10, "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "session", "api_face": "legacy"},
        "route_bindings": {
            SESSION_CLI_SESSION_PATCH_TITLE_OPERATION: {
                "method": "PATCH", "path": "/sessions/{session_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_MEMBER_ADD_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/members",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION: {
                "method": "PATCH",
                "path": "/sessions/{session_id}/members/{member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_CHAT_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/chat",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_MESSAGES_OPERATION: {
                "method": "GET", "path": "/sessions/{session_id}/messages",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}/members/{member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_GROUP_LISTING_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}/sessions",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE", "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


def group_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
             f"  Participants: {DRIVER},{MEMBER}\n"
             f"  Session: {GROUP}:00000001\n").encode()


def session_output():
    return json.dumps({
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "lifecycle-sess",
    }).encode()


def session_state(*, title="renamed-sess", member=None):
    participants = [
        {"bot_uuid": DRIVER, "role": "driver", "actor_kind": "bot",
         "mode": "auto"},
    ]
    if member is not False:
        entry = {"bot_uuid": MEMBER, "role": "consultant",
                 "actor_kind": "bot"}
        if member is not None:
            entry["mode"] = member
        participants.append(entry)
    return {
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": title, "participants": participants,
    }


class SessionCliLifecycleTests(unittest.TestCase):
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
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)
        commands, contexts = [], []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            result = group_output() if len(commands) == 1 else session_output()
            contexts.append((
                kwargs["environment"]["HOME"],
                kwargs["environment"].get("BCS_COOKIE"),
            ))
            return subprocess.CompletedProcess(cmd, 0, result, b"")

        def responses():
            return {
                SESSION_CLI_SESSION_PATCH_TITLE_OPERATION: session_state(),
                SESSION_CLI_SESSION_MEMBER_ADD_OPERATION: session_state(
                    member=None),
                SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION: session_state(
                    member="muted"),
                SESSION_CLI_SESSION_CHAT_OPERATION: {
                    "delivered": True, "session_id": SESSION,
                    "group_id": GROUP, "delivered_count": 1,
                },
                SESSION_CLI_SESSION_MESSAGES_OPERATION: [
                    {"message_id": "msg-1"},
                ],
                SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION: session_state(
                    member=False),
                SESSION_CLI_SESSION_DELETE_OPERATION: {
                    "deleted": True, "session_id": SESSION,
                },
                SESSION_CLI_SESSION_GROUP_LISTING_OPERATION: {
                    "items": [],
                },
                SESSION_CLI_GROUP_DELETE_OPERATION: {
                    "deleted": True, "id": GROUP,
                },
                SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                    "status": 404, "code": "group_not_found",
                    "message": "Group not found",
                    "params": {"group_id": GROUP},
                },
            }

        def fake_call(self_client, operation, **kwargs):
            data = responses()
            if deform is not None:
                deform(data, operation, kwargs)
            if operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                status = 404
            else:
                status = 200
            return data[operation], {
                "operation": operation, "http_status": status,
                "path": "/lifecycle",
                "submitted_request_id": "e2e-session-cli-lifecycle",
            }

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            adapter = RemoteSessionCliLifecycleAdapter(profile, runner)
            execution = execute_session_cli_lifecycle_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_lifecycle_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(commands[1][-2:], ["--title", "lifecycle-sess"])
                snapshot = result.snapshot()
                self.assertEqual(snapshot["steps"][0]["evidence"]["requests"][1]["case_class"],
                                 "session-create")
                self.assertEqual(snapshot["steps"][0]["data"]["member_muted"], True)
                self.assertEqual(snapshot["steps"][0]["data"]["session_absence_verified"], True)
                self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])

    def test_foreign_rename_title_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_PATCH_TITLE_OPERATION:
                data[operation]["session_title"] = "foreign-sess"
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_not_admitted_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_MEMBER_ADD_OPERATION:
                data[operation] = session_state(member=False)
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_not_muted_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION:
                data[operation] = session_state(member="auto")
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_chat_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_CHAT_OPERATION:
                data[operation]["delivered"] = "yes"
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_history_readback_shape_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_MESSAGES_OPERATION:
                data[operation] = {"messages": []}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_not_removed_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION:
                data[operation] = session_state(member="muted")
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_session_still_listed_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_GROUP_LISTING_OPERATION:
                data[operation] = {"items": [{"session_id": SESSION}]}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_case_guard_accepts_the_shared_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_cli_runner.py",
                 ROOT / "bcs_e2e/session_cli_shared.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_lifecycle_route_drift(self):
        mutations = {
            "member-route": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION].update(
                    path="/sessions/{session_id}/members"),
            "chat-method": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_CHAT_OPERATION].update(method="GET"),
            "delete-query": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_DELETE_OPERATION].update(
                    path="/sessions/{session_id}"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_cli_lifecycle_profile(profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = session_cli_lifecycle_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"], ["SESSION.lifecycle"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
