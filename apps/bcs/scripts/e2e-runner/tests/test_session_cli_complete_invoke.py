"""Cross-environment SESSION.complete-invoke product-CLI tests."""

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
from bcs_e2e.workspace_cli_commands import WorkspaceCliRunnerError
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner
from bcs_e2e.session_cli_complete_invoke import (
    SESSION_CLI_COMPLETE_INVOKE_CASE_ID,
    RemoteSessionCliCompleteInvokeAdapter,
    execute_session_cli_complete_invoke_case,
    session_cli_complete_invoke_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
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
GROUP = "bcs_grp_session_cli_09"
SESSION = GROUP + ":0d0e0f10"
SERVICE = GROUP + ":1a1b1c1d"
TOKEN = "owned-local-token"
MEMBER_TOKEN = "owned-member-token"
COOKIE = "owned-preprod-cookie"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-session-complete-invoke-profile-v1",
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
        "auth": ({"kind": "human-cookie-with-owned-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                  "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
                  "cookie_env": "BCS_E2E_HUMAN_COOKIE"} if preprod else
                 {"kind": "owned-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                  "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN"}),
        "relationships": {
            "group": "bounded-owned-bots-cli-service-invoke",
            "initial_session": "deleted-with-group",
            "explicit_session": "completed-then-creator-deleted",
            "service_session": "invoked-then-creator-deleted",
            "cleanup": "exact-owned-group", "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "session-cli-pool", "exclusive_use_confirmed": True,
            "timeout_seconds": 10, "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "session", "api_face": "legacy"},
        "route_bindings": {
            SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION: {
                "method": "GET",
                "path": ("/openapi/v1/collaboration/messages/not-a-message"
                         "/deliveries?session_id={session_id}"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION: {
                "method": "POST",
                "path": ("/openapi/v1/collaboration/sessions/{session_id}"
                         "/message-deliveries/query"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION: {
                "method": "POST",
                "path": ("/openapi/v1/collaboration/messages/not-a-message"
                         "/deliveries/not-a-delivery/cancel"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION: {
                "method": "POST",
                "path": ("/openapi/v1/collaboration/messages/not-a-message"
                         "/deliveries/cancel"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION: {
                "method": "POST",
                "path": ("/openapi/v1/collaboration/messages/not-a-message"
                         "/deliveries/not-a-delivery/resolve"),
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_COMPLETE_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/complete",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_READ_OPERATION: {
                "method": "GET", "path": "/sessions/{session_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION: {
                "method": "POST", "path": "/sessions/{session_id}/complete",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/sessions/{session_id}?bot_id=bot:{driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION: {
                "method": "PATCH", "path": "/groups/{group_id}/settings",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION: {
                "method": "POST", "path": "/services/{group_id}/sessions",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SERVICE_SESSION_READ_OPERATION: {
                "method": "GET",
                "path": "/services/{group_id}/sessions/{session_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION: {
                "method": "GET",
                "path": "/services/{group_id}/sessions/{session_id}",
                "api_face": "legacy", "auth": "case_owned_member",
                "response": "legacy-json", "traceparent": "disabled"},
            SESSION_CLI_GROUP_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
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
        "session_title": "Release sign-off",
    }).encode()


def completed_session():
    return {
        "id": SESSION, "session_id": SESSION, "group_id": GROUP,
        "session_title": "Release sign-off", "status": "completed",
        "output": {"summary": "release approved"},
        "participants": [
            {"bot_uuid": DRIVER, "role": "driver", "actor_kind": "bot",
             "mode": "auto"},
            {"bot_uuid": MEMBER, "role": "worker", "actor_kind": "bot"},
        ],
    }


def settings_output():
    return {"id": GROUP, "service_spec": {"max_concurrency": 2},
            "status": "ok"}


def invocation_output():
    return {
        "session_id": SERVICE, "group_id": GROUP,
        "session_kind": "service_invocation",
        "session_title": "Automated release audit", "status": "queued",
        "participants": completed_session()["participants"],
    }


def service_session_output():
    return {
        "session_id": SERVICE, "group_id": GROUP,
        "session_title": "Automated release audit", "status": "queued",
        "participants": completed_session()["participants"],
    }


class _StaticRunner:
    def execute(self, *args, **kwargs):
        raise AssertionError("product CLI must not run in a transport test")


class SessionCliCompleteInvokeTests(unittest.TestCase):
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
        profile = validate_session_cli_complete_invoke_profile(
            profile_value(preprod=preprod))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                      binary_sha256=self.digest,
                                      state_dir=self.state)
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
                SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION: [],
                SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION: [],
                SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION: None,
                SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION: None,
                SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION: None,
                SESSION_CLI_SESSION_COMPLETE_OPERATION: completed_session(),
                SESSION_CLI_SESSION_READ_OPERATION: completed_session(),
                SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION: {
                    "error": "forbidden",
                    "message": "service sessions cannot be completed via "
                               "this endpoint",
                },
                SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION: {
                    "deleted": True, "session_id": SESSION,
                },
                SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION: {
                    "deleted": True, "session_id": SERVICE,
                },
                SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION: settings_output(),
                SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION: invocation_output(),
                SESSION_CLI_SERVICE_SESSION_READ_OPERATION: service_session_output(),
                SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION: {
                    "error": "forbidden", "message": "caller_principal mismatch",
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

        def status_for(operation):
            if operation in {
                    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION,
                    SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION}:
                return 400
            if operation in {
                    SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION,
                    SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION,
                    SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION}:
                return 403
            if operation == SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION:
                return 202
            if operation == SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION:
                return 404
            return 200

        def fake_call(self_client, operation, **kwargs):
            data = responses()
            status = status_for(operation)
            payload = data[operation]
            if deform is not None:
                override = deform(data, operation, kwargs)
                if isinstance(override, dict):
                    if "data" in override:
                        payload = override["data"]
                    if "status" in override:
                        status = override["status"]
            return payload, {
                "operation": operation, "http_status": status,
                "path": "/complete-invoke",
                "submitted_request_id": "e2e-session-cli-complete-invoke",
            }

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
               "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.workspace_cli_runner.capture",
                   side_effect=fake_capture), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=fake_call, autospec=True):
            adapter = RemoteSessionCliCompleteInvokeAdapter(profile, runner)
            execution = execute_session_cli_complete_invoke_case(adapter)
        return execution, adapter, commands, contexts

    def test_local_and_preprod_completion_invocation_and_cleanup(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter, commands, contexts = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(adapter.group_id, GROUP)
                self.assertIsNone(adapter.service_session_id)
                self.assertIn("--manager", commands[0])
                self.assertEqual(commands[1][-2:], ["--title", "Release sign-off"])
                if preprod:
                    self.assertEqual(contexts[0][1], COOKIE)
                    self.assertEqual(contexts[1][1], COOKIE)
                else:
                    self.assertIsNone(contexts[0][1])
                    self.assertIsNone(contexts[1][1])
                snapshot = result.snapshot()
                requests = snapshot["steps"][0]["evidence"]["requests"]
                self.assertEqual(requests[0]["case_class"], "setup")
                self.assertEqual(requests[1]["case_class"], "session-create")
                self.assertEqual(requests[2]["case_class"],
                                 "message-deliveries-read")
                self.assertEqual(requests[7]["case_class"], "session-complete")
                self.assertEqual(requests[10]["case_class"], "service-invocation")
                self.assertEqual(requests[12]["case_class"],
                                 "service-session-isolation")
                self.assertEqual(requests[12]["caller_role"],
                                 "case_owned_member")
                self.assertEqual(requests[13]["case_class"],
                                 "service-completion-guardrail")
                expected_kinds = (
                    ["human_cookie_with_owned_bot_tokens"] * 2
                    + ["human_cookie_with_owned_driver_bot_token"] * 10
                    + ["human_cookie_with_owned_member_bot_token"]
                    + ["human_cookie_with_owned_driver_bot_token"] * 5
                ) if preprod else ["owned_local_bot_token"] * 18
                self.assertEqual(
                    [row["credential_kind"] for row in requests],
                    expected_kinds)
                self.assertEqual(snapshot["steps"][0]["data"]["group_absence_verified"], True)
                serialized = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(TOKEN, serialized)
                self.assertNotIn(MEMBER_TOKEN, serialized)

    def test_completion_output_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_COMPLETE_OPERATION:
                data[operation]["output"]["summary"] = "not-approved"
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_missing_delivery_cancel_not_rejected_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION:
                return {"status": 200}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_foreign_service_session_read_leak_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION:
                return {"status": 200, "data": service_session_output()}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_service_session_still_returns_guardrail_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION:
                return {"status": 200, "data": service_session_output()}
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_service_invocation_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION:
                data[operation]["session_kind"] = "chat"
        result, _, _, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_expected_credential_kind_partitions_cli_and_http_operations(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                profile = validate_session_cli_complete_invoke_profile(
                    profile_value(preprod=preprod))
                env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                       "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
                       "BCS_E2E_HUMAN_COOKIE": COOKIE}
                with patch.dict(os.environ, env):
                    adapter = RemoteSessionCliCompleteInvokeAdapter(
                        profile, _StaticRunner())
                cli_operations = ("session-cli.service.group.create",
                                  "session-cli.session.create")
                for operation in cli_operations:
                    if preprod:
                        self.assertEqual(
                            adapter.expected_credential_kind(operation),
                            "human_cookie_with_owned_bot_tokens")
                    else:
                        self.assertEqual(
                            adapter.expected_credential_kind(operation),
                            "owned_local_bot_token")
                if preprod:
                    self.assertEqual(
                        adapter.expected_credential_kind(
                            SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION),
                        "human_cookie_with_owned_member_bot_token")
                    self.assertEqual(
                        adapter.expected_credential_kind(
                            SESSION_CLI_SESSION_READ_OPERATION),
                        "human_cookie_with_owned_driver_bot_token")

    def test_preprod_bare_owned_bot_tokens_are_rejected(self):
        bare = profile_value(preprod=True)
        bare["auth"]["kind"] = "owned-bot-tokens"
        with self.assertRaisesRegex(ConfigError, "reviewed ACE trio"):
            validate_session_cli_complete_invoke_profile(bare)

    def test_preprod_http_actor_subprofiles_bind_the_ace_trio(self):
        profile = validate_session_cli_complete_invoke_profile(
            profile_value(preprod=True))
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
               "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env):
            adapter = RemoteSessionCliCompleteInvokeAdapter(
                profile, _StaticRunner())
            driver_auth = adapter.driver_client.profile["auth"]
            member_auth = adapter.member_client.profile["auth"]
            local_adapter = RemoteSessionCliCompleteInvokeAdapter(
                validate_session_cli_complete_invoke_profile(
                    profile_value(preprod=False)), _StaticRunner())
            local_auth = local_adapter.driver_client.profile["auth"]
        self.assertEqual(driver_auth["kind"],
                         "human-cookie-with-owned-bot-tokens")
        self.assertEqual(driver_auth["header_env"],
                         {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN",
                          "Cookie": "BCS_E2E_HUMAN_COOKIE"})
        self.assertEqual(driver_auth["authorization_scheme"], "bearer")
        self.assertEqual(member_auth["kind"],
                         "human-cookie-with-owned-bot-tokens")
        self.assertEqual(member_auth["header_env"]["Authorization"],
                         "BCS_E2E_MEMBER_BOT_TOKEN")
        self.assertEqual(local_auth, {
            "kind": "authenticated-product",
            "header_env": {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN"}})
        self.assertNotIn("authorization_scheme", local_auth)

    def test_complete_invoke_credentials_admit_the_preprod_ace_trio(self):
        profile = validate_session_cli_complete_invoke_profile(
            profile_value(preprod=True))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest,
                                    state_dir=self.state)
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN}):
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "ACE trio"):
                runner._credentials()
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env):
            self.assertEqual(
                runner._credentials(),
                ("human_cookie_with_owned_bot_tokens", TOKEN))
        local = validate_session_cli_complete_invoke_profile(
            profile_value(preprod=False))
        local_runner = WorkspaceCliRunner(local, binary=self.binary,
                                          binary_sha256=self.digest,
                                          state_dir=self.state)
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                                     "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN}):
            self.assertEqual(
                local_runner._credentials(),
                ("owned_local_bot_token", TOKEN))

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_cli_runner.py",
                 ROOT / "bcs_e2e/session_cli_complete_invoke.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_complete_invoke_route_drift(self):
        mutations = {
            "member-route": lambda value: value["route_bindings"][
                SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION].update(
                    path="/services/{group_id}/sessions"),
            "invocation-method": lambda value: value["route_bindings"][
                SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION].update(
                    method="GET"),
            "creator-delete-query": lambda value: value["route_bindings"][
                SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION].update(
                    path="/sessions/{session_id}"),
            "member-auth-owner": lambda value: value["route_bindings"][
                SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION].update(
                    auth="case_owned_driver"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_cli_complete_invoke_profile(
                        profile_value(mutate=mutate))

    def test_profile_rejects_cookie_auth_for_bot_identity_routes(self):
        def mutate(value):
            value["auth"] = {"kind": "human-cookie",
                             "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
        with self.assertRaises(ConfigError):
            validate_session_cli_complete_invoke_profile(
                profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = session_cli_complete_invoke_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"], ["SESSION.complete-invoke"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
