"""Cross-environment SESSION.cli-create-list-get product-CLI tests; no live request."""

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

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.session_file_core_contract import (
    SESSION_CLI_CREATE_GET_CASE_ID as CASE_ID,
    RemoteSessionCliCreateGetAdapter,
    execute_session_cli_create_get_case,
    session_cli_create_get_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
)
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_SESSION_INVITE_CREATE_OPERATION,
    SESSION_CLI_SESSION_INVITE_JOIN_OPERATION,
    SESSION_CLI_BOT_GROUPS_OPERATION,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_INVITE_CREATE_OPERATION,
    SESSION_CLI_GROUP_INVITE_JOIN_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    validate_session_cli_create_get_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session_cli_01"
SESSION = GROUP + ":01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False, mutate=None):
    tier = "preprod" if gateway else "local"
    value = {
        "schema_version": 1,
        "contract": "dual-session-cli-create-list-get-profile-v1",
        "environment": {"tier": tier,
                        "id": "session-cli-preprod" if gateway else "session-cli-local",
                        "expected_bcs_env": "pre" if gateway else "local"},
        "entrypoint": {"kind": "gateway" if gateway else "singlebox",
                        "origin": "https://gateway.example.com" if gateway else "http://127.0.0.1:21000",
                        "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {"driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"},
                    "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                                "dedicated": True, "visibility": "public"}},
        "auth": ({"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"} if gateway else
                  {"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}),
        "relationships": {"group": "bounded-owned-bots-cli-session-create-list-get",
                           "initial_session": "deleted-with-group",
                           "explicit_session": "created-listed-read-deleted-with-group",
                           "cleanup": "exact-owned-group", "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "session-cli-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 65536,
                    "failure_retention_hours": 24},
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
            f"  Participants: {DRIVER},{MEMBER}\n  Session: {GROUP}:00000001\n").encode()


def session_outputs(include_target=True):
    create = json.dumps({
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "cli-e2e-sess", "session_kind": "chat", "status": "active",
    }).encode()
    items = []
    if include_target:
        items.append({"id": SESSION, "session_id": SESSION, "group_id": GROUP,
                        "session_title": "cli-e2e-sess"})
    items.append({"id": "unrelated", "session_id": "unrelated:1",
                    "group_id": "unrelated"})
    listing = json.dumps({"group_id": GROUP, "items": items}).encode()
    read = json.dumps({
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "cli-e2e-sess",
    }).encode()
    return [create, listing, read]


class SessionCliCreateGetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}

    @staticmethod
    def cleanup_calls(group_id, operation):
        if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
            return ({"deleted": True, "id": group_id}, {
                "operation": operation, "http_status": 200, "path": f"/groups/{group_id}",
                "submitted_request_id": "e2e-session-cli-delete",
            })
        return ({"status": 404, "code": "group_not_found", "message": "Group not found",
                    "params": {"group_id": group_id}}, {
            "operation": operation, "http_status": 404, "path": f"/groups/{group_id}",
            "submitted_request_id": "e2e-session-cli-404",
        })

    def run_case(self, *, gateway=False, include_target=True):
        profile = validate_session_cli_create_get_profile(
            profile_value(gateway=gateway))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                     binary_sha256=self.digest, state_dir=self.state)
        commands = []
        contexts = []
        outputs = session_outputs(include_target)

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            environment = kwargs["environment"]
            context = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            contexts.append((json.loads(context.read_text()),
                              environment.get("BCS_COOKIE"), environment))
            raw = group_output() if len(commands) == 1 else outputs[len(commands) - 2]
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        def fake_cleanup(self_client, operation, **kwargs):
            return self.cleanup_calls(kwargs["group_id"], operation)

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_cleanup, autospec=True):
            adapter = RemoteSessionCliCreateGetAdapter(profile, runner)
            execution = execute_session_cli_create_get_case(adapter)
        return execution, adapter, commands, contexts

    def test_case_guard_accepts_the_shared_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/session_file_core_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                  text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = session_cli_create_get_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(value["case_id"], row["case_id"])
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])

    def test_local_sequence_creates_lists_reads_and_deletes(self):
        execution, adapter, commands, contexts = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(adapter.group_id, GROUP)
        self.assertEqual(adapter.session_id, SESSION)
        self.assertEqual([command[command.index("--url") + 1] for command in commands],
                          ["http://127.0.0.1:21000"] * 4)
        self.assertEqual(commands[1][-4:], ["create", "--group", GROUP, "--title", "cli-e2e-sess"][-4:])
        self.assertEqual(commands[2][-3:], ["list", "--group", GROUP])
        self.assertEqual(commands[3][-3:], ["session", "get", SESSION])
        for context, cookie, _environment in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertEqual(context["bot_uuid"], DRIVER)
            self.assertIsNone(cookie)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["group_id"], GROUP)
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 6)
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_gateway_cookie_is_not_evidence(self):
        execution, _, _, contexts = self.run_case(gateway=True)
        self.assertEqual(execution.status, "PASS")
        for context, cookie, environment in contexts:
            self.assertEqual(context["token"], "")
            self.assertEqual(cookie, COOKIE)
            self.assertEqual(environment["tc_sdb_nenv"], "production")
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))

    def test_missing_exact_session_blocks(self):
        execution, _, _, _ = self.run_case(include_target=False)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.error_type, "RunStateError")

    def test_profile_rejects_cleanup_and_identity_drift(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "route": lambda value: value["route_bindings"][
                SESSION_CLI_GROUP_DELETE_OPERATION].update(method="POST"),
            "borrowed-bot": lambda value: value["actors"]["member"].update(dedicated=False),
            "private-bot": lambda value: value["actors"]["driver"].update(visibility="protected"),
            "wrong-family": lambda value: value["cli"].update(command_family="group"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_cli_create_get_profile(profile_value(mutate=mutate))


if __name__ == "__main__":
    unittest.main()
