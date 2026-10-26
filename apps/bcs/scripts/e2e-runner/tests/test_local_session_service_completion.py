"""Source-bound Session Service completion and service-roundtrip tests."""

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
from bcs_e2e.local_session_service_completion import (
    SESSION_SERVICE_COMPLETE_CASE_ID,
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_COMPLETE_REQUIREMENT,
    SESSION_SERVICE_ROUNDTRIP_CASE_ID,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
    RemoteLocalSessionServiceCompleteAdapter,
    RemoteLocalSessionServiceRoundtripAdapter,
    execute_local_session_service_complete_case,
    execute_local_session_service_roundtrip_case,
    local_session_service_complete_adaptability,
    local_session_service_roundtrip_adaptability,
)
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION,
    SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION,
    SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION,
    SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION,
    SESSION_CLI_SERVICE_SESSION_READ_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_COMPLETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_READ_OPERATION,
    SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION,
    validate_session_cli_complete_invoke_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session_service_03"
SESSION = GROUP + ":0d0e0f10"
SERVICE = GROUP + ":1a1b1c1d"
TOKEN = "owned-local-token"
MEMBER_TOKEN = "owned-member-token"
COMPLETION_OUTPUT = {"summary": "approved by CLI"}


def _binding(method, path, auth="case_owned_driver"):
    return {"method": method, "path": path, "api_face": "legacy",
            "auth": auth, "response": "legacy-json",
            "traceparent": "disabled"}


def profile_value(*, preprod=False):
    return {
        "schema_version": 1,
        "contract": "dual-session-complete-invoke-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "session-service-preprod" if preprod else "session-service-local",
            "expected_bcs_env": "pre" if preprod else "local"},
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://gateway.example.com" if preprod else "http://127.0.0.1:21000",
            "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"}},
        "auth": {"kind": "owned-bot-tokens",
                 "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                 "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN"},
        "relationships": {
            "group": "bounded-owned-bots-cli-service-invoke",
            "initial_session": "deleted-with-group",
            "explicit_session": "completed-then-creator-deleted",
            "service_session": "invoked-then-creator-deleted",
            "cleanup": "exact-owned-group", "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "session-service-pool",
                   "exclusive_use_confirmed": True, "timeout_seconds": 10,
                   "max_response_bytes": 65536,
                   "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "session",
                "api_face": "legacy"},
        "route_bindings": {
            SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION: _binding(
                "GET", "/openapi/v1/collaboration/messages/not-a-message/"
                       "deliveries?session_id={session_id}"),
            SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION: _binding(
                "POST", "/openapi/v1/collaboration/sessions/{session_id}/"
                        "message-deliveries/query"),
            SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION: _binding(
                "POST", "/openapi/v1/collaboration/messages/not-a-message/"
                        "deliveries/not-a-delivery/cancel"),
            SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION: _binding(
                "POST", "/openapi/v1/collaboration/messages/not-a-message/"
                        "deliveries/cancel"),
            SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION: _binding(
                "POST", "/openapi/v1/collaboration/messages/not-a-message/"
                        "deliveries/not-a-delivery/resolve"),
            SESSION_CLI_SESSION_COMPLETE_OPERATION: _binding(
                "POST", "/sessions/{session_id}/complete"),
            SESSION_CLI_SESSION_READ_OPERATION: _binding(
                "GET", "/sessions/{session_id}"),
            SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION: _binding(
                "POST", "/sessions/{session_id}/complete"),
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION: _binding(
                "DELETE", "/sessions/{session_id}?bot_id={driver_actor_id}"),
            SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION: _binding(
                "DELETE", "/sessions/{session_id}?bot_id=bot:{driver_actor_id}"),
            SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION: _binding(
                "PATCH", "/groups/{group_id}/settings"),
            SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION: _binding(
                "POST", "/services/{group_id}/sessions"),
            SESSION_CLI_SERVICE_SESSION_READ_OPERATION: _binding(
                "GET", "/services/{group_id}/sessions/{session_id}"),
            SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION: _binding(
                "GET", "/services/{group_id}/sessions/{session_id}",
                auth="case_owned_member"),
            SESSION_CLI_GROUP_DELETE_OPERATION: _binding(
                "DELETE", "/groups/{group_id}?bot_id={driver_actor_id}"),
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: _binding(
                "GET", "/groups/{group_id}"),
        },
    }


def group_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n"
            f"  Session: {GROUP}:00000001\n").encode()


def session_output():
    return json.dumps({
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "Release sign-off",
    }).encode()


def completed_output():
    value = {
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "Release sign-off", "status": "completed",
        "output": COMPLETION_OUTPUT,
    }
    return json.dumps(value).encode()


def invocation_output():
    return json.dumps({
        "session_id": SERVICE, "group_id": GROUP,
        "session_kind": "service_invocation",
        "session_title": "CLI automated release audit", "status": "queued",
    }).encode()


def service_status_output():
    return json.dumps({
        "session_id": SERVICE, "group_id": GROUP,
        "session_title": "CLI automated release audit", "status": "queued",
    }).encode()


def service_terminal_output():
    return json.dumps({
        "session_id": SERVICE, "group_id": GROUP,
        "session_title": "CLI automated release audit", "status": "completed",
    }).encode()


class LocalSessionServiceCompletionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.state.mkdir()
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _runner(self, *, preprod=False):
        profile = validate_session_cli_complete_invoke_profile(
            profile_value(preprod=preprod))
        return profile, WorkspaceCliRunner(
            profile, binary=self.binary, binary_sha256=self.digest,
            state_dir=self.state)

    def _run_complete(self, *, preprod=False, deform=None):
        profile, runner = self._runner(preprod=preprod)
        commands = []
        stderr_selections = []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            stderr_selections.append(kwargs.get('collect_stderr'))
            raw = completed_output() if len(commands) == 3 else (
                group_output() if len(commands) == 1 else session_output())
            if deform is not None:
                raw = deform(len(commands), raw)
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        def fake_client(_client, operation, **kwargs):
            if operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                return {"deleted": True, "id": GROUP}, {
                    "operation": operation, "http_status": 200, "path": "/complete"}
            if operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                return {"status": 404, "code": "group_not_found",
                        "message": "Group not found",
                        "params": {"group_id": GROUP}}, {
                    "operation": operation, "http_status": 404,
                    "path": "/complete"}
            raise AssertionError("unexpected completion cleanup operation")

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
               "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture",
                   side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_client, autospec=True):
            adapter = RemoteLocalSessionServiceCompleteAdapter(profile, runner)
            execution = execute_local_session_service_complete_case(adapter)
        return execution, adapter, commands

    def _run_roundtrip(self, *, preprod=False, timeout=False, deform=None,
                       http_deform=None):
        profile, runner = self._runner(preprod=preprod)
        commands = []
        stderr_selections = []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            stderr_selections.append(kwargs.get('collect_stderr'))
            if len(commands) == 1:
                raw = group_output()
            elif len(commands) == 2:
                raw = session_output()
            elif len(commands) == 3:
                raw = completed_output()
            elif len(commands) == 4:
                raw = invocation_output()
            elif len(commands) == 5:
                raw = service_status_output()
            elif len(commands) == 6:
                if timeout:
                    message = f"Timed out after 1ms waiting for {SERVICE}\n".encode()
                    if deform is not None:
                        message = deform(len(commands), message)
                    return subprocess.CompletedProcess(cmd, 1, b"", message)
                raw = service_terminal_output()
            else:
                raise AssertionError("unexpected service command index")
            if deform is not None:
                raw = deform(len(commands), raw)
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        def fake_client(_client, operation, *args, **kwargs):
            if operation == SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION:
                data = {"id": GROUP, "status": "ok",
                        "service_spec": {"max_concurrency": 2}}
                status = 200
            elif operation == SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION:
                data = {"deleted": True, "session_id": SESSION}
                status = 200
            elif operation == SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION:
                data = {"deleted": True, "session_id": SERVICE}
                status = 200
            elif operation == SESSION_CLI_GROUP_DELETE_OPERATION:
                data = {"deleted": True, "id": GROUP}
                status = 200
            elif operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                data = {"status": 404, "code": "group_not_found",
                        "message": "Group not found",
                        "params": {"group_id": GROUP}}
                status = 404
            else:
                raise AssertionError("unexpected service cleanup operation")
            if http_deform is not None:
                override = http_deform(operation, data, status)
                if isinstance(override, dict):
                    data = override.get("data", data)
                    status = override.get("status", status)
            return data, {"operation": operation, "http_status": status,
                          "path": "/service-roundtrip"}

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
               "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture",
                   side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_client, autospec=True):
            adapter = RemoteLocalSessionServiceRoundtripAdapter(profile, runner)
            execution = execute_local_session_service_roundtrip_case(adapter)
        return execution, adapter, commands, stderr_selections

    def test_local_and_preprod_session_completion_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                execution, adapter, commands = self._run_complete(preprod=preprod)
                self.assertEqual(execution.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.session_id, SESSION)
                self.assertEqual(commands[2][-6:],
                                 ["error", "session", "complete", SESSION, "--output",
                                  '{"summary":"approved by CLI"}'])
                snapshot = execution.snapshot()
                self.assertEqual(snapshot["case_id"], SESSION_SERVICE_COMPLETE_CASE_ID)
                self.assertEqual(snapshot["steps"][0]["method"],
                                 SESSION_SERVICE_COMPLETE_CASE_METHOD)
                self.assertEqual(snapshot["steps"][0]["data"]["session_completed"], True)
                self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_local_and_preprod_service_roundtrip_accepts_terminal_or_timeout(self):
        for preprod in (False, True):
            for timeout in (False, True):
                with self.subTest(preprod=preprod, timeout=timeout):
                    execution, adapter, commands, stderr_selections = self._run_roundtrip(
                        preprod=preprod, timeout=timeout)
                    self.assertEqual(execution.status, "PASS")
                    self.assertEqual(adapter.service_session_id, SERVICE)
                    self.assertIn("--manager", commands[0])
                    self.assertIn("cli-release-pipeline", commands[3])
                    self.assertIn("--detach", commands[3])
                    self.assertEqual(commands[4][-1], SERVICE)
                    self.assertEqual(commands[5][-3:], [SERVICE, "--timeout-ms", "1"])
                    self.assertEqual(stderr_selections,
                                     [False, False, False, False, False, True])
                    snapshot = execution.snapshot()
                    requests = snapshot["steps"][0]["evidence"]["requests"]
                    self.assertEqual(requests[6]["case_class"], "session-cli.service.wait")
                    self.assertEqual(requests[6]["exit_status"], 1 if timeout else 0)
                    self.assertEqual(snapshot["steps"][0]["data"]["bounded_wait_contract_exact"], True)
                    self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_completion_accepts_canonical_id_without_legacy_alias(self):
        def deform(index, raw):
            if index == 3:
                value = json.loads(raw)
                value.pop("session_id", None)
            return raw
        execution, _, _ = self._run_complete(deform=deform)
        self.assertEqual(execution.status, "PASS")
        self.assertIsNone(execution.error_type)

    def test_completion_output_drift_is_blocked(self):
        def deform(index, raw):
            if index == 3:
                value = json.loads(raw)
                value["output"]["summary"] = "foreign"
                return json.dumps(value).encode()
            return raw
        execution, _, _ = self._run_complete(deform=deform)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")

    def test_service_identity_drift_is_blocked(self):
        def deform(index, raw):
            if index == 4:
                value = json.loads(raw)
                value["session_kind"] = "chat"
                return json.dumps(value).encode()
            return raw
        execution, *_ = self._run_roundtrip(deform=deform)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")

    def test_unbounded_service_wait_error_is_blocked(self):
        def deform(index, raw):
            if index == 6:
                return b"network error\n"
            return raw
        execution, *_ = self._run_roundtrip(timeout=True, deform=deform)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")

    def test_service_cleanup_drift_is_blocked(self):
        def http_deform(operation, data, status):
            if operation == SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION:
                return {"data": {"deleted": False, "session_id": SERVICE}}
        execution, *_ = self._run_roundtrip(http_deform=http_deform)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")

    def test_adaptabilities_claim_no_parent_or_release_credit(self):
        for adaptability, case_id, requirement in (
                (local_session_service_complete_adaptability(),
                 SESSION_SERVICE_COMPLETE_CASE_ID, SESSION_SERVICE_COMPLETE_REQUIREMENT),
                (local_session_service_roundtrip_adaptability(),
                 SESSION_SERVICE_ROUNDTRIP_CASE_ID, SESSION_SERVICE_ROUNDTRIP_REQUIREMENT)):
            with self.subTest(case_id=case_id):
                self.assertEqual(adaptability["case_id"], case_id)
                self.assertEqual(adaptability["requirement_ids"], [requirement])
                self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
                self.assertFalse(adaptability["grants_release_credit"])

    def test_full_parent_executor_replaces_the_superseded_slice_registration(self):
        plan = migration_plan()
        parent = next(item for item in plan["local_registered_executors"]
                      if item["case_id"] == "LOCAL-SESSION-SERVICE-PARENT-01")
        self.assertEqual(parent["required_assertion_count"], 4)
        self.assertEqual(parent["execution_command"], "execute_local_session_service_parent_case")
        partial_ids = {item["case_id"] for item in plan["local_partial_executors"]}
        self.assertNotIn(SESSION_SERVICE_COMPLETE_CASE_ID, partial_ids)
        self.assertNotIn(SESSION_SERVICE_ROUNDTRIP_CASE_ID, partial_ids)
        self.assertEqual(parent["remote_validation"], "NOT_RUN")
        self.assertFalse(parent["grants_release_credit"])
        self.assertEqual(plan["migrated_story_count"], 0)

    def test_case_guard_accepts_the_new_completion_module(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [
            ROOT / "bcs_e2e/local_session_service_completion.py",
            ROOT / "bcs_e2e/workspace_cli_runner.py",
            ROOT / "bcs_e2e/workspace_cli_commands.py",
        ]
        result = subprocess.run(
            [sys.executable, str(guard), *map(str, files)],
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
