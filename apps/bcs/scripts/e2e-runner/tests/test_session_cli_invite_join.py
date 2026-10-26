"""Cross-environment SESSION.invite-join product-CLI tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner
from bcs_e2e.session_cli_shared import (
    SESSION_CLI_INVITE_JOIN_CASE_ID,
    RemoteSessionCliInviteJoinAdapter,
    execute_session_cli_invite_join_case,
    session_cli_invite_join_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
)
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_BOT_GROUPS_OPERATION,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
    SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_INVITE_CREATE_OPERATION,
    SESSION_CLI_SESSION_INVITE_JOIN_OPERATION,
    validate_session_cli_create_get_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session_cli_04"
SESSION = GROUP + ":05060708"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"
INVITE_TOKEN = "session-join-private-token"
EXPIRES_AT = int(time.time()) + 300


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-session-cli-create-list-get-profile-v1",
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
            "group": "bounded-owned-bots-cli-session-create-list-get",
            "initial_session": "deleted-with-group",
            "explicit_session": "created-listed-read-deleted-with-group",
            "cleanup": "exact-owned-group", "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "session-cli-pool", "exclusive_use_confirmed": True,
            "timeout_seconds": 10, "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "session", "api_face": "legacy"},
        "route_bindings": {
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE", "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_BOT_GROUPS_OPERATION: {
                "method": "GET", "path": "/bots/{driver_actor_id}/groups?limit=50&group_kind=all",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_INVITE_CREATE_OPERATION: {
                "method": "POST", "path": "/groups/{group_id}/invite-link",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_INVITE_JOIN_OPERATION: {
                "method": "POST", "path": "/groups/join/{invite_token}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_INVITE_CREATE_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/invite-link",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_INVITE_JOIN_OPERATION: {
                "method": "POST", "path": "/sessions/join/{invite_token}",
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
        "session_title": "share-sess",
    }).encode()


def invite_output(origin, *, foreign=False, missing=False):
    join_url = ("https://foreign.invalid/sessions/join/" + INVITE_TOKEN
                if foreign else origin + "/sessions/join/" + INVITE_TOKEN)
    data = {"invite_token": INVITE_TOKEN, "expires_at": EXPIRES_AT,
            "join_url": join_url}
    return json.dumps(data).encode() if not missing else b"{}"


class SessionCliInviteJoinTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_case(self, *, preprod=False, foreign=False, missing=False,
                 wrong_target=False):
        profile = validate_session_cli_create_get_profile(profile_value(preprod=preprod))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                   binary_sha256=self.digest, state_dir=self.state)
        commands, contexts = [], []
        outputs = [
            session_output(),
            invite_output(profile["entrypoint"]["origin"],
                          foreign=foreign, missing=missing),
        ]

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            result = group_output() if len(commands) == 1 else outputs[len(commands) - 2]
            contexts.append((
                kwargs["environment"]["HOME"],
                kwargs["environment"].get("BCS_COOKIE"),
            ))
            return subprocess.CompletedProcess(cmd, 0, result, b"")

        def fake_call(self_client, operation, **kwargs):
            if operation == SESSION_CLI_SESSION_INVITE_JOIN_OPERATION:
                target_id = SESSION + ":wrong" if wrong_target else SESSION
                return ({"joined": True, "already_member": False,
                         "target_type": "session", "target_id": target_id,
                         "actor_id": "human_39464"}, {
                    "operation": operation, "http_status": 200,
                    "path": "/sessions/join/[REDACTED]",
                    "submitted_request_id": "e2e-session-cli-join"})
            if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                return ({"deleted": True, "id": kwargs["group_id"]}, {
                    "operation": operation, "http_status": 200,
                    "path": "/groups/" + kwargs["group_id"],
                    "submitted_request_id": "e2e-session-cli-delete"})
            return ({"status": 404, "code": "group_not_found",
                     "message": "Group not found",
                     "params": {"group_id": kwargs["group_id"]}}, {
                "operation": operation, "http_status": 404,
                "path": "/groups/" + kwargs["group_id"],
                "submitted_request_id": "e2e-session-cli-404"})

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            adapter = RemoteSessionCliInviteJoinAdapter(profile, runner)
            execution = execute_session_cli_invite_join_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_invite_join_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.session_id, SESSION)
                self.assertEqual(adapter.invite_token, "")
                self.assertEqual(commands[1][-3:], ["--group", GROUP, "--title", "share-sess"][-3:])
                self.assertEqual(commands[2][-3:], [SESSION, "--ttl-seconds", "300"])
                snapshot = result.snapshot()
                self.assertEqual(snapshot["steps"][0]["evidence"]["requests"][3]["case_class"],
                                 "session-join")
                self.assertEqual(snapshot["steps"][0]["data"]["joined"], True)
                self.assertNotIn(INVITE_TOKEN, json.dumps(snapshot, sort_keys=True))
                self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])

    def test_foreign_invite_url_is_blocked(self):
        result, _, _, _ = self.run_case(foreign=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_missing_invite_fields_are_blocked(self):
        result, _, _, _ = self.run_case(missing=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_wrong_join_target_is_blocked(self):
        result, _, _, _ = self.run_case(wrong_target=True)
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

    def test_profile_rejects_invite_route_drift(self):
        mutations = {
            "join-route": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_INVITE_JOIN_OPERATION].update(method="GET"),
            "join-path": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_INVITE_JOIN_OPERATION].update(path="/sessions/join"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_cli_create_get_profile(profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = session_cli_invite_join_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"], ["SESSION.invite-join"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
