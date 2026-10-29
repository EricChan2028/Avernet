"""Command and report tests for the dual Workspace Group runner entry."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from bcs_e2e.workspace_cli_profile import (
    WORKSPACE_GROUP_CREATE_OPERATION,
    WORKSPACE_GROUP_DELETE_OPERATION,
    WORKSPACE_GROUP_DETAIL_OPERATION,
    WORKSPACE_GROUP_LABEL_OPERATION,
    WORKSPACE_GROUP_LIST_OPERATION,
    WORKSPACE_GROUP_MEMBER_ADD_OPERATION,
    WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION,
    WORKSPACE_GROUP_PATCH_OPERATION,
    WORKSPACE_GROUP_VISIBILITY_OPERATION,
)
from bcs_e2e.workspace_group_case_run import (
    _CASES,
    dual_workspace_group_case_run,
)

DRIVER = "driver-bot"
MEMBER = "member-bot"
LOCAL_TOKEN = "local-driver-token-secret"
PREPROD_COOKIE = "preprod-human-cookie-secret"
RUN_ID = "workspace-group-run-1"


def route(operation: str, method: str, path: str) -> dict:
    return {"method": method, "path": path, "api_face": "legacy",
            "auth": "case_owned_driver", "response": "legacy-json",
            "traceparent": "disabled"}


def lifecycle_bindings() -> dict:
    return {
        WORKSPACE_GROUP_CREATE_OPERATION: route(
            WORKSPACE_GROUP_CREATE_OPERATION, "POST", "/groups"),
        WORKSPACE_GROUP_DETAIL_OPERATION: route(
            WORKSPACE_GROUP_DETAIL_OPERATION, "GET", "/groups/{group_id}"),
        WORKSPACE_GROUP_PATCH_OPERATION: route(
            WORKSPACE_GROUP_PATCH_OPERATION, "PATCH", "/groups/{group_id}"),
        WORKSPACE_GROUP_LIST_OPERATION: route(
            WORKSPACE_GROUP_LIST_OPERATION, "GET",
            "/groups?limit=50&group_kind=all"),
        WORKSPACE_GROUP_MEMBER_ADD_OPERATION: route(
            WORKSPACE_GROUP_MEMBER_ADD_OPERATION, "POST",
            "/groups/{group_id}/members"),
        WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION: route(
            WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION, "DELETE",
            "/groups/{group_id}/members/{member_actor_id}"),
        WORKSPACE_GROUP_DELETE_OPERATION: route(
            WORKSPACE_GROUP_DELETE_OPERATION, "DELETE",
            "/groups/{group_id}?bot_id={driver_actor_id}"),
    }


def governance_bindings() -> dict:
    return {
        WORKSPACE_GROUP_CREATE_OPERATION: route(
            WORKSPACE_GROUP_CREATE_OPERATION, "POST", "/groups"),
        WORKSPACE_GROUP_DETAIL_OPERATION: route(
            WORKSPACE_GROUP_DETAIL_OPERATION, "GET", "/groups/{group_id}"),
        WORKSPACE_GROUP_LABEL_OPERATION: route(
            WORKSPACE_GROUP_LABEL_OPERATION, "PUT",
            "/groups/{group_id}/label"),
        WORKSPACE_GROUP_VISIBILITY_OPERATION: route(
            WORKSPACE_GROUP_VISIBILITY_OPERATION, "PUT",
            "/groups/{group_id}/visibility"),
    }


def profile_value(case: str, *, preprod=False) -> dict:
    local = not preprod
    return {
        "schema_version": 1,
        "contract": (f"dual-workspace-group-{case}-profile-v1"),
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": f"workspace-group-{case}-{'preprod' if preprod else 'local'}",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": "https://gateway.example.com" if preprod
                      else "http://127.0.0.1:21000",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": "owner-1"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                       "dedicated": True, "visibility": "public"},
        },
        "auth": (
            {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
            if preprod else
            {"kind": "owned-local-bot-tokens",
             "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
        ),
        "relationships": (
            {"group": "bounded-owned-bots-group-lifecycle-delete",
             "initial_session": "retained",
             "cleanup": "exact-owned-group-delete-and-not-found",
             "participant_mode": "driver-member"}
            if case == "lifecycle" else
            {"group": "bounded-owned-bots-retained-group-governance",
             "initial_session": "retained", "cleanup": "none",
             "participant_mode": "driver-member"}
        ),
        "policy": {
            "fixture_pool": f"workspace-group-{case}-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
        "route_bindings": lifecycle_bindings() if case == "lifecycle"
                          else governance_bindings(),
    }


def execution(case_id: str, requirement_id: str) -> CaseExecution:
    step = StepOutcome(
        requirement_id=requirement_id, method="case-method",
        status="PASS", data={"guarded": True},
        evidence={"transport": "PRODUCT_HTTP", "requests": [
            {"method": "POST", "path": "/groups",
             "submitted_request_id": "request-1"}]},
        error_type=None,
    )
    result = CaseExecution(
        case_id=case_id, parent_id="story_user_operates_group_workspace",
        status="PASS", steps=(step,), completed_count=1, total_count=1,
        error_type=None, ready_to_execute=True,
    )
    result.validate()
    return result


class WorkspaceGroupCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def invoke(self, case: str, *, preprod=False, cleanup=None):
        self.profile_path.write_text(
            json.dumps(profile_value(case, preprod=preprod)), encoding="utf-8")
        case_id, requirement_id = (
            ("DUAL-WORKSPACE-GROUP-LIFECYCLE-01", "WORKSPACE.create")
            if case == "lifecycle" else
            ("DUAL-WORKSPACE-GROUP-GOVERNANCE-01", "WORKSPACE.label")
        )
        result = execution(case_id, requirement_id)
        adapter = Mock()
        if cleanup is not None:
            adapter.cleanup_receipt = cleanup
        factory = Mock(return_value=adapter)
        with patch.dict(_CASES, {case: {**_CASES[case], "adapter": factory,
                                        "execute": Mock(return_value=result)}}):
            report = dual_workspace_group_case_run(
                profile_path=self.profile_path, case_key=case,
                state_dir=self.state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID,
            )
        return report, result, case_id

    def test_lifecycle_case_entry_writes_a_bound_sanitized_report(self):
        cleanup = {
            "status": "PASS", "group_id": "bcs_grp_workspace_lifecycle_01",
            "deleted": True, "not_found_verified": True,
            "resource_disposition":
                "EXACT_OWNED_GROUP_DELETED_HISTORY_RETAINED",
            "retained_groups": [], "requests": [],
        }
        report, result, case_id = self.invoke("lifecycle", cleanup=cleanup)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], case_id)
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["resource_disposition"],
                         "EXACT_OWNED_GROUP_DELETED_HISTORY_RETAINED")
        self.assertEqual(report["cleanup"], cleanup)
        self.assertEqual(report["retained_groups"], [])
        self.assertEqual(report["case"], result.snapshot())
        self.assertEqual(read(self.state_dir / "workspace-lifecycle-profile.json"),
                         profile_value("lifecycle"))
        self.assertEqual(read(self.state_dir / "workspace-lifecycle-binding.json")["case_id"], case_id)
        self.assertEqual(read(self.state_dir / "workspace-lifecycle-run.json"), report)
        self.assertNotIn(LOCAL_TOKEN, json.dumps(report, sort_keys=True))

    def test_lifecycle_cleanup_failure_is_disclosed_without_masking_case(self):
        cleanup = {
            "status": "BLOCKED", "error_type": "RunStateError",
            "error_detail": "delete unconfirmed",
            "resource_disposition":
                "EXACT_OWNED_GROUP_RETAINED_DELETE_UNCONFIRMED",
            "retained_groups": [{
                "kind": "group", "id": "bcs_grp_workspace_lifecycle_01",
                "driver_bot": DRIVER,
                "disposition": "RETAINED_DELETE_UNCONFIRMED"}],
            "requests": [],
        }
        report, _, _ = self.invoke("lifecycle", cleanup=cleanup)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["status"], "PASS")
        self.assertEqual(report["cleanup"]["status"], "BLOCKED")
        self.assertEqual(report["resource_disposition"],
                         "EXACT_OWNED_GROUP_RETAINED_DELETE_UNCONFIRMED")
        self.assertEqual(len(report["retained_groups"]), 1)

    def test_preprod_governance_case_entry_writes_a_bound_sanitized_report(self):
        report, result, case_id = self.invoke("governance", preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["case_id"], case_id)
        self.assertEqual(read(self.state_dir / "workspace-governance-run.json"), report)
        self.assertNotIn(PREPROD_COOKIE, json.dumps(report, sort_keys=True))

    def test_lifecycle_case_rejects_deformed_case_names_and_state_paths(self):
        self.profile_path.write_text(
            json.dumps(profile_value("lifecycle")), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "workspace_case must be"):
            dual_workspace_group_case_run(
                profile_path=self.profile_path, case_key="membership",
                state_dir=self.state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_workspace_group_case_run(
                profile_path=self.profile_path, case_key="lifecycle",
                state_dir=self.state_dir, run_id=RUN_ID,
                allow_write=True, confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_workspace_group_runner(self):
        arguments = [
            "dual-workspace-group-run",
            "--profile", str(self.profile_path),
            "--case", "governance",
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_workspace_group_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path, case_key="governance",
            state_dir=self.state_dir, run_id=RUN_ID,
            allow_write=True, confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
