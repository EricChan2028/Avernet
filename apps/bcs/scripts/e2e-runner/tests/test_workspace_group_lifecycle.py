"""Cross-environment WORKSPACE group lifecycle legacy-HTTP tests; no live request."""

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
from bcs_e2e.workspace_group_lifecycle_contract import (
    CASE_ID,
    RemoteWorkspaceGroupLifecycleAdapter,
    execute_workspace_group_lifecycle_case,
    workspace_group_lifecycle_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    WORKSPACE_GROUP_CREATE_OPERATION,
    WORKSPACE_GROUP_DETAIL_OPERATION,
    WORKSPACE_GROUP_LIST_OPERATION,
    WORKSPACE_GROUP_MEMBER_ADD_OPERATION,
    WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION,
    WORKSPACE_GROUP_PATCH_OPERATION,
    validate_workspace_group_lifecycle_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace_lifecycle_01"
LABEL = "dual-group-lifecycle-e2e"
CONTEXT = "dual-group-context-e2e"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-group-lifecycle-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": ("workspace-group-lifecycle-preprod" if preprod
                   else "workspace-group-lifecycle-local"),
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
        },
        "auth": ({"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
                 if preprod else
                 {"kind": "owned-local-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}),
        "relationships": {
            "group": "bounded-owned-bots-retained-group-lifecycle",
            "initial_session": "retained", "cleanup": "none",
            "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "workspace-group-lifecycle-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
        "route_bindings": {
            WORKSPACE_GROUP_CREATE_OPERATION: {
                "method": "POST", "path": "/groups",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_DETAIL_OPERATION: {
                "method": "GET", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_PATCH_OPERATION: {
                "method": "PATCH", "path": "/groups/{group_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_LIST_OPERATION: {
                "method": "GET", "path": "/groups?limit=50&group_kind=all",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_MEMBER_ADD_OPERATION: {
                "method": "POST", "path": "/groups/{group_id}/members",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION: {
                "method": "DELETE",
                "path": "/groups/{group_id}/members/{member_actor_id}",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


LEGACY_METHODS = {
    WORKSPACE_GROUP_CREATE_OPERATION: "POST",
    WORKSPACE_GROUP_DETAIL_OPERATION: "GET",
    WORKSPACE_GROUP_PATCH_OPERATION: "PATCH",
    WORKSPACE_GROUP_LIST_OPERATION: "GET",
    WORKSPACE_GROUP_MEMBER_ADD_OPERATION: "POST",
    WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION: "DELETE",
}
LEGACY_PATHS = {
    WORKSPACE_GROUP_CREATE_OPERATION: "/groups",
    WORKSPACE_GROUP_DETAIL_OPERATION: "/groups/{group_id}",
    WORKSPACE_GROUP_PATCH_OPERATION: "/groups/{group_id}",
    WORKSPACE_GROUP_LIST_OPERATION: "/groups?limit=50&group_kind=all",
    WORKSPACE_GROUP_MEMBER_ADD_OPERATION: "/groups/{group_id}/members",
    WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION:
        "/groups/{group_id}/members/{member_actor_id}",
}


def created_group():
    return {"id": GROUP, "driver_bot": DRIVER,
            "participants": [DRIVER], "group_kind": "normal",
            "created": True, "context": None, "context_injected": False}


def group_detail(extra=None):
    participants = [{"bot_uuid": DRIVER, "role": "driver"}]
    if extra == "member":
        participants.append({"bot_uuid": MEMBER, "role": "participant"})
    return {"id": GROUP, "label": None, "status": "active",
            "context": None, "driver_bot": DRIVER,
            "participants": participants, "message_count": 0,
            "group_kind": "normal", "visibility": "public"}


def standard_responses():
    detail_calls = {"count": 0}

    def responses():
        return {
            WORKSPACE_GROUP_CREATE_OPERATION: created_group(),
            WORKSPACE_GROUP_PATCH_OPERATION: {"name": LABEL,
                                              "context": CONTEXT},
            WORKSPACE_GROUP_LIST_OPERATION: {
                "items": [{"id": GROUP, "driver_bot": DRIVER,
                           "label": LABEL, "group_kind": "normal",
                           "visibility": "public"}],
                "total": 1, "offset": 0, "limit": 50,
                "group_kind": "all"},
            WORKSPACE_GROUP_MEMBER_ADD_OPERATION: {
                "added": True, "session_id": GROUP,
                "member": {"bot_uuid": MEMBER, "role": "participant"}},
            WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION: {
                "removed": True, "group_id": GROUP,
                "removed_bot_uuid": MEMBER},
        }

    def fake_call(self_client, operation, **kwargs):
        data = responses().get(operation)
        if operation == WORKSPACE_GROUP_DETAIL_OPERATION:
            # call order: initial detail, patch read-back, post-add
            # read-back, post-remove read-back
            detail_calls["count"] += 1
            if detail_calls["count"] == 2:
                data = group_detail()
                data["label"] = LABEL
                data["context"] = CONTEXT
            elif detail_calls["count"] == 3:
                data = group_detail(extra="member")
            else:
                data = group_detail()
        if data is None:
            raise AssertionError(operation)
        return data, {
            "operation": operation,
            "method": LEGACY_METHODS[operation],
            "http_status": 200,
            "path": LEGACY_PATHS[operation],
            "submitted_request_id": "e2e-workspace-group",
            "traceparent_mode": "disabled",
        }

    return fake_call, detail_calls


class WorkspaceGroupLifecycleTests(unittest.TestCase):
    def run_case(self, *, preprod=False, deform=None):
        profile = profile_value(preprod=preprod)
        fake_call, _ = standard_responses()

        def deformed_call(self_client, operation, **kwargs):
            data, record = fake_call(self_client, operation, **kwargs)
            if deform is not None:
                deform(data, operation, kwargs)
            return data, record

        env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
               "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict(os.environ, env), \
             patch("bcs_e2e.write_transport.WriteClient.call",
                   side_effect=deformed_call, autospec=True):
            adapter = RemoteWorkspaceGroupLifecycleAdapter(profile)
            execution = execute_workspace_group_lifecycle_case(adapter)
        return execution, adapter

    def test_local_and_preprod_lifecycle(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(result.completed_count, 5)
                self.assertEqual(adapter.group_id, GROUP)
                snapshot = result.snapshot()
                self.assertEqual(
                    [row["requirement_id"] for row in snapshot["steps"]],
                    ["WORKSPACE.create", "WORKSPACE.detail",
                     "WORKSPACE.patch", "WORKSPACE.list",
                     "WORKSPACE.members"])
                self.assertEqual(
                    snapshot["steps"][0]["data"]["participants"], [DRIVER])
                self.assertEqual(
                    snapshot["steps"][2]["data"]["label"], LABEL)
                self.assertEqual(
                    snapshot["steps"][3]["data"]["bounded_list_match"], True)
                self.assertEqual(
                    snapshot["steps"][4]["data"]["final_participants"],
                    [DRIVER])
                serialized = json.dumps(snapshot, sort_keys=True)
                self.assertNotIn(COOKIE, serialized)
                self.assertNotIn(TOKEN, serialized)
                if preprod:
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["credential_kind"],
                        "human_cookie")
                else:
                    self.assertEqual(
                        snapshot["steps"][0]["evidence"]["credential_kind"],
                        "owned_local_bot_token")

    def test_create_roster_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_CREATE_OPERATION:
                data["participants"] = [DRIVER, "foreign-bot"]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 0)
        self.assertEqual(result.steps[0].status, "NOT_RUN")
        self.assertEqual(result.steps[1].status, "NOT_RUN")

    def test_detail_identity_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_DETAIL_OPERATION:
                data["driver_bot"] = "foreign-bot"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_patch_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_PATCH_OPERATION:
                data["name"] = "foreign-label"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_patch_readback_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_DETAIL_OPERATION and kwargs.get(
                    "group_id") == GROUP:
                data["context"] = "foreign-context"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_list_missing_exact_group_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_LIST_OPERATION:
                data["items"] = [{"id": "unrelated-group",
                                  "driver_bot": "unrelated-driver"}]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_not_added_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_MEMBER_ADD_OPERATION:
                data["added"] = False
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_member_still_present_after_removal_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_DETAIL_OPERATION:
                data["participants"] = [
                    {"bot_uuid": DRIVER, "role": "driver"},
                    {"bot_uuid": MEMBER, "role": "participant"}]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_remove_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION:
                data["removed_bot_uuid"] = "foreign-bot"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_group_lifecycle_contract.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_group_route_drift(self):
        mutations = {
            "create-method": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_CREATE_OPERATION].update(method="PUT"),
            "detail-path": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_DETAIL_OPERATION].update(
                    path="/openapi/v1/collaboration/groups/{group_id}"),
            "patch-face": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_PATCH_OPERATION].update(api_face="public-v1"),
            "list-query": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_LIST_OPERATION].update(path="/groups"),
            "member-route": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION].update(
                    path="/groups/{group_id}/members/{driver_actor_id}"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_group_lifecycle_profile(
                        profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = workspace_group_lifecycle_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"],
                         ["WORKSPACE.create", "WORKSPACE.detail",
                          "WORKSPACE.patch", "WORKSPACE.list",
                          "WORKSPACE.members"])
        self.assertEqual(row["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
