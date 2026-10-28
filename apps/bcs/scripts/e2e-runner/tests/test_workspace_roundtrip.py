"""Cross-environment WORKSPACE round trip legacy-HTTP tests; no live request."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_roundtrip_contract import (
    CASE_ID,
    RemoteWorkspaceRoundtripAdapter,
    execute_workspace_roundtrip_case,
    workspace_roundtrip_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION,
    WORKSPACE_ROUNDTRIP_CHAT_OPERATION,
    WORKSPACE_ROUNDTRIP_CREATE_OPERATION,
    WORKSPACE_ROUNDTRIP_DELETE_OPERATION,
    WORKSPACE_ROUNDTRIP_DETAIL_OPERATION,
    WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION,
    WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION,
    WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION,
    WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION,
    WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
    validate_workspace_roundtrip_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
QA = "qa-bot"
GROUP = "bcs_grp_workspace_roundtrip_01"
LABEL = "Workspace round trip"
DRIVER_TOKEN = "owned-roundtrip-driver-token"
COOKIE = "human-roundtrip-cookie"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-roundtrip-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": ("workspace-roundtrip-preprod" if preprod
                   else "workspace-roundtrip-local"),
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": ("https://gateway.example.com" if preprod
                       else "http://127.0.0.1:21000"),
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "39464"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "39464",
                       "dedicated": True, "visibility": "public"},
            "qa": {"bot_id": QA, "owner_id": "39464",
                   "dedicated": True, "visibility": "public"},
        },
        "auth": {"kind": "roundtrip-bundle",
                 "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                 "cookie_env": "BCS_E2E_HUMAN_COOKIE"},
        "relationships": {
            "creation": "finalized-bounded-owned-bots-incident-room",
            "workspace": "finalized-bounded-owned-bots-deprecated-workspace-404",
            "routing_service": (
                "finalized-bounded-owned-bots-structured-routing"),
            "messaging": "finalized-bounded-owned-bots-group-messaging",
            "finalization": "exact-owned-group-delete-and-not-found",
        },
        "policy": {
            "fixture_pool": "workspace-roundtrip-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
        "route_bindings": {
            WORKSPACE_ROUNDTRIP_CREATE_OPERATION: {
                "method": "POST", "path": "/groups",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_DETAIL_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION: {
                "method": "PUT", "path": "/groups/{group_id}/workspace",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}/workspace",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION: {
                "method": "PUT",
                "path": "/groups/{group_id}/routing-policy",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION: {
                "method": "PATCH", "path": "/groups/{group_id}/settings",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION: {
                "method": "POST", "path": "/groups/{group_id}/messages",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_CHAT_OPERATION: {
                "method": "POST", "path": "/groups/{group_id}/chat",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION: {
                "method": "POST", "path": "/groups/{group_id}/callback",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_ROUNDTRIP_DELETE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}?bot_id={driver_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


LEGACY_METHODS = {
    WORKSPACE_ROUNDTRIP_CREATE_OPERATION: "POST",
    WORKSPACE_ROUNDTRIP_DETAIL_OPERATION: "GET",
    WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION: "PUT",
    WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION: "GET",
    WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION: "PUT",
    WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION: "PATCH",
    WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION: "POST",
    WORKSPACE_ROUNDTRIP_CHAT_OPERATION: "POST",
    WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION: "POST",
    WORKSPACE_ROUNDTRIP_DELETE_OPERATION: "DELETE",
}
LEGACY_PATHS = {
    WORKSPACE_ROUNDTRIP_CREATE_OPERATION: "/groups",
    WORKSPACE_ROUNDTRIP_DETAIL_OPERATION: "/groups/" + GROUP,
    WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION:
        "/groups/" + GROUP + "/workspace",
    WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION:
        "/groups/" + GROUP + "/workspace",
    WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION:
        "/groups/" + GROUP + "/routing-policy",
    WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION:
        "/groups/" + GROUP + "/settings",
    WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION:
        "/groups/" + GROUP + "/messages",
    WORKSPACE_ROUNDTRIP_CHAT_OPERATION: "/groups/" + GROUP + "/chat",
    WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION:
        "/groups/" + GROUP + "/callback",
    WORKSPACE_ROUNDTRIP_DELETE_OPERATION:
        "/groups/" + GROUP + "?bot_id=" + DRIVER,
}


def created_roundtrip_group():
    return {"id": GROUP, "driver_bot": DRIVER,
            "participants": [DRIVER, MEMBER, QA],
            "group_kind": "normal", "created": True,
            "label": LABEL}


def roundtrip_detail():
    participants = [
        {"bot_uuid": DRIVER, "role": "driver"},
        {"bot_uuid": MEMBER, "role": "consultant"},
        {"bot_uuid": QA, "role": "consultant"},
    ]
    return {"id": GROUP, "label": LABEL, "status": "active",
            "context": None, "driver_bot": DRIVER,
            "participants": participants, "message_count": 0,
            "group_kind": "normal", "visibility": "public"}


def routing_policy_ack():
    return {"ok": True,
            "routing_policy": {"mode": "structured",
                               "default_bot_final_delivery":
                                   "inject_observers",
                               "sender_routes": {DRIVER: [MEMBER]}}}


def settings_ack():
    return {"id": GROUP,
            "service_spec": {"max_concurrency": 2},
            "status": "ok"}


def message_sent_ack():
    return {"message_id": "e2e-roundtrip-msg-01",
            "routed_to": [MEMBER, QA],
            "queue_admission": "admitted", "mentions": []}


def chat_delivered_ack():
    return {"delivered": True, "group_id": GROUP, "driver_bot": DRIVER,
            "delivered_count": 2, "queue_admission": "admitted",
            "failed_count": 0,
            "delivery_results": [{"bot_uuid": MEMBER, "success": True},
                                 {"bot_uuid": QA, "success": True}],
            "mentions": []}


def callback_delivered_ack():
    return {"delivered": True, "group_id": GROUP, "driver_bot": DRIVER,
            "delivered_count": 2, "failed_count": 0,
            "delivery_results": [{"bot_uuid": MEMBER, "success": True},
                                 {"bot_uuid": QA, "success": True}],
            "mentions": [DRIVER]}


def roundtrip_delete_ack():
    return {"deleted": True, "id": GROUP}


def standard_responses():
    detail_calls = {"count": 0}
    responses = {
        WORKSPACE_ROUNDTRIP_CREATE_OPERATION: created_roundtrip_group(),
        WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION:
            routing_policy_ack(),
        WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION: settings_ack(),
        WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION: message_sent_ack(),
        WORKSPACE_ROUNDTRIP_CHAT_OPERATION: chat_delivered_ack(),
        WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION:
            callback_delivered_ack(),
        WORKSPACE_ROUNDTRIP_DELETE_OPERATION: roundtrip_delete_ack(),
    }

    def fake_call(self_client, operation, **kwargs):
        if operation in {
            WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
            WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION,
        }:
            return None, {
                "operation": operation,
                "method": LEGACY_METHODS[operation],
                "http_status": 404,
                "path": LEGACY_PATHS[operation],
                "submitted_request_id": "e2e-workspace-roundtrip",
                "traceparent_mode": "disabled",
            }
        if operation == WORKSPACE_ROUNDTRIP_DETAIL_OPERATION:
            # call order: creation read-back, post-delete not-found
            # verification
            detail_calls["count"] += 1
            if detail_calls["count"] == 1:
                return roundtrip_detail(), {
                    "operation": operation,
                    "method": LEGACY_METHODS[operation],
                    "http_status": 200,
                    "path": LEGACY_PATHS[operation],
                    "submitted_request_id": "e2e-workspace-roundtrip",
                    "traceparent_mode": "disabled",
                }
            return None, {
                "operation": operation,
                "method": LEGACY_METHODS[operation],
                "http_status": 404,
                "path": LEGACY_PATHS[operation],
                "submitted_request_id": "e2e-workspace-roundtrip",
                "traceparent_mode": "disabled",
            }
        data = responses.get(operation)
        if data is None:
            raise AssertionError(operation)
        return data, {
            "operation": operation,
            "method": LEGACY_METHODS[operation],
            "http_status": 200,
            "path": LEGACY_PATHS[operation],
            "submitted_request_id": "e2e-workspace-roundtrip",
            "traceparent_mode": "disabled",
        }

    return fake_call, detail_calls


class WorkspaceRoundtripTests(unittest.TestCase):
    def run_case(self, *, preprod=False, deform=None):
        profile = profile_value(preprod=preprod)
        fake_call, _ = standard_responses()

        def deformed_call(self_client, operation, **kwargs):
            data, record = fake_call(self_client, operation, **kwargs)
            if deform is not None:
                deform(data, operation, kwargs)
            return data, record

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=deformed_call, autospec=True):
            adapter = RemoteWorkspaceRoundtripAdapter(profile)
            execution = execute_workspace_roundtrip_case(adapter)
        return execution, adapter

    def run_deprecated_workspace_drift(self, operation: str,
                                        data: object) -> None:
        profile = profile_value()
        fake_call, _ = standard_responses()

        def deformed_call(self_client, called_operation: str, **kwargs):
            if called_operation == operation:
                return data, {
                    "operation": called_operation,
                    "method": LEGACY_METHODS[called_operation],
                    "http_status": 200,
                    "path": LEGACY_PATHS[called_operation],
                    "submitted_request_id": "e2e-workspace-roundtrip",
                    "traceparent_mode": "disabled",
                }
            return fake_call(self_client, called_operation, **kwargs)

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=deformed_call, autospec=True):
            adapter = RemoteWorkspaceRoundtripAdapter(profile)
            execution = execute_workspace_roundtrip_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, "NOT_RUN")

    def test_local_and_preprod_roundtrip(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(result.completed_count, 3)
                self.assertEqual(adapter.group_id, GROUP)
                snapshot = result.snapshot()
                self.assertEqual(
                    [row["requirement_id"] for row in snapshot["steps"]],
                    ["WORKSPACE.workspace", "WORKSPACE.routing-service",
                     "WORKSPACE.messages"])
                self.assertEqual(
                    snapshot["steps"][0]["data"]
                    ["workspace_update_unavailable"], True)
                self.assertEqual(
                    snapshot["steps"][0]["data"]
                    ["workspace_read_unavailable"], True)
                self.assertEqual(
                    snapshot["steps"][1]["data"]["mode"], "structured")
                self.assertEqual(
                    snapshot["steps"][1]["data"]["max_concurrency"], 2)
                self.assertEqual(
                    snapshot["steps"][2]["data"]["persistent_message"],
                    True)
                self.assertEqual(
                    snapshot["steps"][2]["data"]["live_chat"], True)
                self.assertEqual(
                    snapshot["steps"][2]["data"]["agent_callback"], True)
                self.assertEqual(
                    snapshot["steps"][2]["data"]["not_found_verified"],
                    True)
                serialized = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(COOKIE, serialized)
                self.assertNotIn(DRIVER_TOKEN, serialized)
                driver_kind = ("human_cookie_with_owned_driver_bot_token"
                               if preprod else "owned_local_bot_token")
                self.assertEqual(
                    snapshot["steps"][0]["evidence"]["credential_kind"],
                    driver_kind)
                self.assertEqual(
                    snapshot["steps"][1]["evidence"]["credential_kind"],
                    driver_kind)
                self.assertEqual(
                    snapshot["steps"][2]["evidence"]["credential_kind"],
                    "human_cookie" if preprod else "owned_local_bot_token")
                if preprod:
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["credential_kinds"],
                        [driver_kind, "human_cookie", "human_cookie",
                         "human_cookie"])
                    self.assertEqual(
                        snapshot["steps"][1]["evidence"]["credential_kinds"],
                        [driver_kind, "human_cookie"])
                    self.assertEqual(
                        snapshot["steps"][2]["evidence"]["credential_kinds"],
                        ["human_cookie", driver_kind, "human_cookie",
                         "human_cookie", "human_cookie"])
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["requests"][0][
                            "route_caller_binding"], "route_bound")
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["requests"][2][
                            "route_caller_binding"], "documented_gap")
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["requests"][3][
                            "route_caller_binding"], "documented_gap")
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["requests"][1][
                            "route_caller_binding"], "open_read")
                    self.assertEqual(
                        snapshot["steps"][2]["evidence"]["requests"][0][
                            "route_caller_binding"], "route_bound")
                    self.assertEqual(
                        snapshot["steps"][2]["evidence"]["requests"][2][
                            "route_caller_binding"], "documented_gap")
                else:
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["credential_kinds"],
                        ["owned_local_bot_token"] * 4)
                    self.assertEqual(
                        snapshot["steps"][1]["evidence"]["credential_kinds"],
                        ["owned_local_bot_token", "owned_local_bot_token"])
                    self.assertEqual(
                        snapshot["steps"][2]["evidence"]["credential_kinds"],
                        ["owned_local_bot_token"] * 5)

    def test_preprod_bot_subprofile_binds_the_reviewed_ace_duo(self):
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env):
            adapter = RemoteWorkspaceRoundtripAdapter(
                profile_value(preprod=True))
            driver_auth = adapter.driver_client.profile["auth"]
            human_auth = adapter.human_client.profile["auth"]
            local_adapter = RemoteWorkspaceRoundtripAdapter(
                profile_value(preprod=False))
            local_auth = local_adapter.driver_client.profile["auth"]
        self.assertEqual(driver_auth["kind"],
                         "human-cookie-with-owned-driver-bot-token")
        self.assertEqual(driver_auth["header_env"],
                         {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN",
                          "Cookie": "BCS_E2E_HUMAN_COOKIE"})
        self.assertEqual(driver_auth["authorization_scheme"], "bearer")
        self.assertEqual(human_auth["kind"], "human-cookie")
        self.assertEqual(human_auth["header_env"],
                         {"Cookie": "BCS_E2E_HUMAN_COOKIE"})
        self.assertNotIn("authorization_scheme", human_auth)
        self.assertEqual(local_auth, {
            "kind": "authenticated-product",
            "header_env": {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN"}})
        self.assertNotIn("authorization_scheme", local_auth)

    def test_create_roster_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_CREATE_OPERATION:
                data["participants"] = [DRIVER, MEMBER, QA,
                                        "foreign-bot"]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 0)
        self.assertEqual(result.steps[0].status, "NOT_RUN")
        self.assertEqual(result.steps[1].status, "NOT_RUN")

    def test_create_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_CREATE_OPERATION:
                data["created"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_detail_label_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_ROUNDTRIP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                data["label"] = "foreign-label"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_detail_roster_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_ROUNDTRIP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                data["participants"] = [
                    {"bot_uuid": DRIVER, "role": "driver"},
                    {"bot_uuid": MEMBER, "role": "consultant"}]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_workspace_unavailability_drift_is_blocked(self):
        for operation, data in (
            (WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION,
             {"updated": True}),
            (WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION,
             {"decisions": ["unexpected persistence"]}),
        ):
            with self.subTest(operation=operation):
                self.run_deprecated_workspace_drift(operation, data)

    def test_routing_policy_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION:
                data["routing_policy"]["mode"] = "echoed"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 1)
        self.assertEqual(result.steps[1].status, "NOT_RUN")

    def test_settings_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION:
                data["service_spec"]["max_concurrency"] = 5
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 1)

    def test_message_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION:
                data["message_id"] = ""
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 2)
        self.assertEqual(result.steps[2].status, "NOT_RUN")

    def test_chat_delivery_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_CHAT_OPERATION:
                data["delivery_results"] = []
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 2)

    def test_callback_delivery_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION:
                data["group_id"] = "foreign-group"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 2)

    def test_delete_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_ROUNDTRIP_DELETE_OPERATION:
                data["deleted"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 2)
        self.assertEqual(result.steps[2].status, "NOT_RUN")

    def test_roundtrip_group_not_finalized_is_blocked(self):
        # the not-found verification reads a live Group instead of 404
        profile = profile_value()
        fake_call, _ = standard_responses()
        detail_calls = {"count": 0}

        def live_call(self_client, operation, **kwargs):
            if operation == WORKSPACE_ROUNDTRIP_DETAIL_OPERATION:
                detail_calls["count"] += 1
                if detail_calls["count"] >= 2:
                    return roundtrip_detail(), {
                        "operation": operation,
                        "method": "GET",
                        "http_status": 200,
                        "path": LEGACY_PATHS[operation],
                        "submitted_request_id": "e2e-workspace-roundtrip",
                        "traceparent_mode": "disabled",
                    }
            return fake_call(self_client, operation, **kwargs)

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=live_call, autospec=True):
            adapter = RemoteWorkspaceRoundtripAdapter(profile)
            execution = execute_workspace_roundtrip_case(adapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.steps[2].status, "NOT_RUN")

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_roundtrip_contract.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_group_route_drift(self):
        mutations = {
            "create-method": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_CREATE_OPERATION].update(method="PUT"),
            "detail-path": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_DETAIL_OPERATION].update(
                    path="/openapi/v1/collaboration/groups/{group_id}"),
            "workspace-method": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION].update(
                    method="POST"),
            "workspace-path": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION].update(
                    path="/groups/{group_id}/notes"),
            "routing-method": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION].update(
                    method="PATCH"),
            "settings-method": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION].update(
                    method="PUT"),
            "settings-path": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION].update(
                    path="/groups/{group_id}/service_spec"),
            "messages-path": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION].update(
                    path="/groups/{group_id}/message"),
            "chat-path": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_CHAT_OPERATION].update(
                    path="/groups/{group_id}/messages"),
            "callback-method": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION].update(
                    method="PUT"),
            "delete-target": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_DELETE_OPERATION].update(
                    path="/groups/{group_id}?bot_id={qa_actor_id}"),
            "delete-method": lambda value: value["route_bindings"][
                WORKSPACE_ROUNDTRIP_DELETE_OPERATION].update(
                    method="POST"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_roundtrip_profile(
                        profile_value(mutate=mutate))

    def test_profile_rejects_roundtrip_contract_drift(self):
        mutations = {
            "auth-kind": lambda value: value["auth"].update(
                kind="incident-bundle"),
            "missing-cookie": lambda value: value["auth"].pop(
                "cookie_env"),
            "missing-qa-actor": lambda value: value["actors"].pop("qa"),
            "effect-drift": lambda value: value["relationships"].update(
                creation="unreviewed-effect"),
            "family-drift": lambda value: value["cli"].update(
                command_family="session"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_roundtrip_profile(
                        profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = workspace_roundtrip_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"],
                         ["WORKSPACE.workspace", "WORKSPACE.routing-service",
                          "WORKSPACE.messages"])
        self.assertEqual(row["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
