"""Cross-environment WORKSPACE group governance legacy-HTTP tests."""

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
from bcs_e2e.workspace_group_governance_contract import (
    CASE_ID,
    GOVERNANCE_LABEL,
    GOVERNANCE_VISIBILITY,
    RemoteWorkspaceGroupGovernanceAdapter,
    execute_workspace_group_governance_case,
    workspace_group_governance_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    WORKSPACE_GROUP_CREATE_OPERATION,
    WORKSPACE_GROUP_DETAIL_OPERATION,
    WORKSPACE_GROUP_LABEL_OPERATION,
    WORKSPACE_GROUP_VISIBILITY_OPERATION,
    validate_workspace_group_governance_profile,
)
from bcs_e2e.migration_inventory import migration_plan

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace_governance_01"
TOKEN = "owned-local-token"
COOKIE = "human-cookie"

LEGACY_METHODS = {
    WORKSPACE_GROUP_CREATE_OPERATION: "POST",
    WORKSPACE_GROUP_DETAIL_OPERATION: "GET",
    WORKSPACE_GROUP_LABEL_OPERATION: "PUT",
    WORKSPACE_GROUP_VISIBILITY_OPERATION: "PUT",
}
LEGACY_PATHS = {
    WORKSPACE_GROUP_CREATE_OPERATION: "/groups",
    WORKSPACE_GROUP_DETAIL_OPERATION: "/groups/{group_id}",
    WORKSPACE_GROUP_LABEL_OPERATION: "/groups/{group_id}/label",
    WORKSPACE_GROUP_VISIBILITY_OPERATION:
        "/groups/{group_id}/visibility",
}


def profile_value(*, preprod=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-group-governance-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": ("workspace-group-governance-preprod" if preprod
                   else "workspace-group-governance-local"),
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
            "group": "bounded-owned-bots-retained-group-governance",
            "initial_session": "retained", "cleanup": "none",
            "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "workspace-group-governance-pool",
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
            WORKSPACE_GROUP_LABEL_OPERATION: {
                "method": "PUT", "path": "/groups/{group_id}/label",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
            WORKSPACE_GROUP_VISIBILITY_OPERATION: {
                "method": "PUT", "path": "/groups/{group_id}/visibility",
                "api_face": "legacy", "auth": "case_owned_driver",
                "response": "legacy-json", "traceparent": "disabled"},
        },
    }
    if mutate is not None:
        mutate(value)
    return value


def created_group():
    return {"id": GROUP, "driver_bot": DRIVER,
            "participants": [DRIVER], "group_kind": "normal",
            "created": True, "context": None, "context_injected": False}


def group_detail(label=None, visibility="public"):
    return {"id": GROUP, "label": label, "status": "active",
            "context": None, "driver_bot": DRIVER,
            "participants": [{"bot_uuid": DRIVER, "role": "driver"}],
            "message_count": 0, "group_kind": "normal",
            "visibility": visibility}


def standard_responses():
    detail_calls = {"count": 0}

    def responses():
        return {
            WORKSPACE_GROUP_CREATE_OPERATION: created_group(),
            WORKSPACE_GROUP_DETAIL_OPERATION: group_detail(),
            WORKSPACE_GROUP_LABEL_OPERATION: {
                "updated": True, "group_id": GROUP,
                "label": GOVERNANCE_LABEL, "changed_by": DRIVER},
            WORKSPACE_GROUP_VISIBILITY_OPERATION: {
                "updated": True, "group_id": GROUP,
                "visibility": GOVERNANCE_VISIBILITY,
                "changed_by": DRIVER},
        }

    def fake_call(self_client, operation, **kwargs):
        data = responses().get(operation)
        if operation == WORKSPACE_GROUP_DETAIL_OPERATION:
            # call order: label read-back, visibility read-back
            detail_calls["count"] += 1
            if detail_calls["count"] == 1:
                data = group_detail(label=GOVERNANCE_LABEL)
            else:
                data = group_detail(
                    label=GOVERNANCE_LABEL,
                    visibility=GOVERNANCE_VISIBILITY)
        if data is None:
            raise AssertionError(operation)
        return data, {
            "operation": operation,
            "method": LEGACY_METHODS[operation],
            "http_status": 200,
            "path": LEGACY_PATHS[operation],
            "submitted_request_id": "e2e-workspace-governance",
            "traceparent_mode": "disabled",
        }

    return fake_call, detail_calls


class WorkspaceGroupGovernanceTests(unittest.TestCase):
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
            adapter = RemoteWorkspaceGroupGovernanceAdapter(profile)
            execution = execute_workspace_group_governance_case(adapter)
        return execution, adapter

    def test_local_and_preprod_governance(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                result, adapter = self.run_case(preprod=preprod)
                self.assertEqual(result.status, "PASS")
                self.assertEqual(result.completed_count, 2)
                self.assertEqual(adapter.group_id, GROUP)
                self.assertEqual(adapter.caller_actor_id, DRIVER)
                snapshot = result.snapshot()
                self.assertEqual(
                    [row["requirement_id"] for row in snapshot["steps"]],
                    ["WORKSPACE.label", "WORKSPACE.visibility"])
                self.assertEqual(
                    snapshot["steps"][0]["data"]["label"], GOVERNANCE_LABEL)
                self.assertEqual(
                    snapshot["steps"][0]["data"]["changed_by"], DRIVER)
                self.assertEqual(
                    snapshot["steps"][1]["data"]["visibility"],
                    GOVERNANCE_VISIBILITY)
                self.assertEqual(
                    snapshot["steps"][1]["data"]["changed_by"], DRIVER)
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

    def test_setup_create_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_CREATE_OPERATION:
                data["participants"] = [DRIVER, "foreign-bot"]
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")
        self.assertEqual(result.completed_count, 0)
        self.assertEqual(result.steps[0].status, "NOT_RUN")
        self.assertEqual(result.steps[1].status, "NOT_RUN")

    def test_label_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_LABEL_OPERATION:
                data["label"] = "foreign-label"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_label_caller_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_LABEL_OPERATION:
                data["changed_by"] = "foreign-bot"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_label_readback_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_GROUP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                data["label"] = "foreign-label"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_visibility_acknowledgement_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if operation == WORKSPACE_GROUP_VISIBILITY_OPERATION:
                data["visibility"] = "protected"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_visibility_readback_drift_is_blocked(self):
        def deform(data, operation, kwargs):
            if (operation == WORKSPACE_GROUP_DETAIL_OPERATION
                    and kwargs.get("group_id") == GROUP):
                data["visibility"] = "protected"
        result, _ = self.run_case(deform=deform)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.error_type, "RunStateError")

    def test_case_guard_accepts_the_case_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = [ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_group_governance_contract.py"]
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_profile_rejects_governance_route_drift(self):
        mutations = {
            "label-method": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_LABEL_OPERATION].update(method="PATCH"),
            "visibility-path": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_VISIBILITY_OPERATION].update(
                    path="/groups/{group_id}"),
            "create-face": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_CREATE_OPERATION].update(api_face="public-v1"),
            "detail-auth": lambda value: value["route_bindings"][
                WORKSPACE_GROUP_DETAIL_OPERATION].update(auth="anonymous"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_group_governance_profile(
                        profile_value(mutate=mutate))

    def test_adaptability_is_registered_and_does_not_grant_credit(self):
        value = workspace_group_governance_adaptability()
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        row = next((row for row in rows
                    if row["case_id"] == value["case_id"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(value["requirement_ids"], row["requirement_ids"])
        self.assertEqual(value["requirement_ids"],
                         ["WORKSPACE.label", "WORKSPACE.visibility"])
        self.assertEqual(row["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
