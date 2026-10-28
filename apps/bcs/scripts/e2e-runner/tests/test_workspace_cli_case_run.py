"""Command and report tests for the remaining dual Workspace product-CLI cases."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from bcs_e2e.workspace_cli_case_run import dual_workspace_cli_case_run

DRIVER = "driver-bot"
MEMBER = "member-bot"
LOCAL_TOKEN = "local-driver-token-secret"
PREPROD_COOKIE = "preprod-human-cookie-secret"
RUN_ID = "workspace-cli-run-1"

CONTRACTS = {
    "create-get": "dual-workspace-cli-profile-v1",
    "detail": "dual-workspace-cli-detail-profile-v1",
    "list": "dual-workspace-cli-list-profile-v1",
    "fuse": "dual-workspace-cli-fuse-profile-v1",
    "member": "dual-workspace-cli-member-profile-v1",
}
GROUPS = {
    "create-get": "bcs_grp_workspace-create-get",
    "detail": "bcs_grp_workspace-detail",
    "list": "bcs_grp_workspace-list",
    "fuse": "bcs_grp_workspace-fuse",
    "member": "bcs_grp_workspace-member",
}
CASE_IDS = {
    "create-get": "DUAL-WORKSPACE-CLI-CREATE-GET-01",
    "detail": "DUAL-WORKSPACE-CLI-DETAIL-01",
    "list": "DUAL-WORKSPACE-CLI-LIST-01",
    "fuse": "DUAL-WORKSPACE-CLI-FUSE-01",
    "member": "DUAL-WORKSPACE-CLI-MEMBER-01",
}
DISPOSITIONS = {
    "create-get": "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION",
    "detail": "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION",
    "list": "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION",
    "fuse": "RETAINED_DYNAMIC_GROUP_INITIAL_SESSION_AND_FUSION",
    "member": "RETAINED_DYNAMIC_GROUP_INITIAL_SESSION_AND_MEMBER",
}


def profile_value(case_key: str, *, gateway=False) -> dict:
    group = GROUPS[case_key]
    session = group + ":01020304"
    local = not gateway
    return {
        "schema_version": 1,
        "contract": CONTRACTS[case_key],
        "environment": {
            "tier": "preprod" if gateway else "local",
            "id": f"workspace-{case_key}-{'preprod' if gateway else 'local'}",
            "expected_bcs_env": "pre" if gateway else "local",
        },
        "entrypoint": {
            "kind": "gateway" if gateway else "singlebox",
            "origin": "https://gateway.example.com" if gateway
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
            {"kind": "human-cookie-with-owned-driver-bot-token",
             "cookie_env": "BCS_E2E_HUMAN_COOKIE",
             "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
            if gateway and case_key == "list" else
            {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
            if gateway else
            {"kind": "owned-local-bot-tokens",
             "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
        ),
        "relationships": {
            "group": ({
                "create-get": "bounded-owned-bots-retained-cli-create-get",
                "member": "bounded-owned-bots-retained-cli-create-add-member",
            }.get(case_key, f"bounded-owned-bots-retained-cli-create-{case_key}")),
            "initial_session": "retained", "cleanup": "none",
            "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": f"workspace-cli-{case_key}-pool",
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
    }


def fake_process(argument: object, operation: str) -> subprocess.CompletedProcess:
    case_key = {"group": "create-get", "detail": "detail", "list": "list",
                "fuse": "fuse", "member": "member"}[operation.split(".", 1)[0]]
    if operation == "list.group.list":
        value = {"items": [
            {"group_id": GROUPS["list"], "label": "bcs-e2e-workspace-cli-list",
             "coordinator_bot": DRIVER,
             "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}],
             "created_at": 1, "updated_at": 1, "group_kind": "chat",
             "group_strategy": "cooperative", "visibility": "private",
             "human_mention_notify_mode": "driver_bot_only"},
            {"group_id": "bcs_grp_unrelated", "label": "unrelated",
             "coordinator_bot": "unrelated",
             "participants": [{"bot_uuid": "unrelated"}],
             "created_at": 1, "updated_at": 1, "group_kind": "chat",
             "group_strategy": "cooperative", "visibility": "private",
             "human_mention_notify_mode": "driver_bot_only"},
        ], "offset": 0, "returned": 2, "total": 10, "has_more": False}
        raw = json.dumps(value).encode()
    elif operation == "fuse.group.fuse":
        value = {"perspectives": [
            {"bot_uuid": DRIVER, "summary": "driver workspace context"},
            {"bot_uuid": MEMBER, "summary": "member workspace context"},
        ], "conflicts": [], "alignment_points": [],
         "recommendation": "Simple fusion for: how to align?",
         "key_insights": [], "extra": {"fusion_id": "fus-workspace-cli-01",
                                        "fusion_mode": "agent"}}
        raw = b"Fusion result:\n" + json.dumps(value).encode()
    elif operation == "member.group.add-member":
        raw = (f"✓ Member added to group:\n  Group: {GROUPS['member']}\n"
               f"  Bot: {MEMBER}\n  Role: participant\n").encode()
    else:
        group = GROUPS[case_key]
        session = group + ":01020304"
        if operation in {"group.create", "detail.group.create", "list.group.create",
                         "fuse.group.create", "member.group.create"}:
            raw = (f"Group created:\n  ID: {group}\n  Driver: {DRIVER}\n"
                   f"  Participants: {DRIVER},{MEMBER}\n  Session: {session}\n").encode()
        else:
            value = {"id": group, "driver_bot": DRIVER, "status": "active",
                     "group_kind": "chat", "visibility": "private",
                     "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}
            raw = json.dumps(value).encode()
    return subprocess.CompletedProcess(argument, 0, raw, b"")


class WorkspaceCliCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def invoke_case(self, case_key: str, *, gateway=False, return_process=None):
        profile = profile_value(case_key, gateway=gateway)
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        environment = {
            "BCS_E2E_DRIVER_BOT_TOKEN": LOCAL_TOKEN,
            "BCS_E2E_HUMAN_COOKIE": PREPROD_COOKIE,
        }
        expected = {
            "create-get": ("group.create", "group.get"),
            "detail": ("detail.group.create", "detail.group.get"),
            "list": ("list.group.create", "list.group.list"),
            "fuse": ("fuse.group.create", "fuse.group.fuse"),
            "member": ("member.group.create", "member.group.add-member",
                        "member.group.read"),
        }[case_key]
        calls: list[str] = []

        def fake_capture(argument, **kwargs):
            operation = expected[len(calls)]
            calls.append(operation)
            if return_process is None:
                return fake_process(argument, operation)
            return return_process(argument, operation)

        with patch.dict(os.environ, environment), patch(
                "bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            report = dual_workspace_cli_case_run(
                profile_path=self.profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                case_key=case_key,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile

    def test_all_five_local_cases_write_bound_sanitized_reports(self):
        for case_key in CASE_IDS:
            with self.subTest(case_key):
                self.state_dir = self.root / f"state-{case_key}"
                report, profile = self.invoke_case(case_key)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], CASE_IDS[case_key])
                self.assertEqual(report["resource_disposition"], DISPOSITIONS[case_key])
                self.assertEqual(report["credential_kind"], "owned_local_bot_token")
                self.assertEqual(report["remote_validation"], "NOT_RUN")
                self.assertFalse(report["grants_release_credit"])
                self.assertEqual(read(self.state_dir / f"workspace-{case_key}-profile.json"), profile)
                self.assertEqual(read(self.state_dir / f"workspace-{case_key}-binding.json")["case_id"],
                                 CASE_IDS[case_key])
                self.assertEqual(read(self.state_dir / f"workspace-{case_key}-run.json"), report)
                self.assertNotIn(LOCAL_TOKEN, json.dumps(report, sort_keys=True))

    def test_preprod_gateway_case_reports_cookie_kind_without_the_secret(self):
        self.state_dir = self.root / "state-preprod"
        report, _ = self.invoke_case("create-get", gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertNotIn(PREPROD_COOKIE, json.dumps(report, sort_keys=True))

    def test_preprod_gateway_list_case_reports_the_station_duo_kind(self):
        self.state_dir = self.root / "state-preprod-list"
        report, _ = self.invoke_case("list", gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"],
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertNotIn(LOCAL_TOKEN, json.dumps(report, sort_keys=True))

    def test_bad_cli_output_produces_a_blocked_report_without_credit(self):
        report, _ = self.invoke_case("detail", return_process=lambda argument, operation:
                                     subprocess.CompletedProcess(argument, 1, b"", b"bad exit"))
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / "workspace-detail-run.json"), report)

    def test_case_permissions_and_fresh_state_are_enforced(self):
        self.state_dir = self.root / "state-guard"
        self.profile_path.write_text(json.dumps(profile_value("detail")), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "workspace_case must be"):
            dual_workspace_cli_case_run(profile_path=self.profile_path,
                                          cli_binary=self.cli_binary,
                                          cli_binary_sha256=self.cli_sha256,
                                          case_key="status", state_dir=self.state_dir,
                                          run_id=RUN_ID, allow_write=True,
                                          confirm_run_id=RUN_ID)
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_workspace_cli_case_run(profile_path=self.profile_path,
                                          cli_binary=self.cli_binary,
                                          cli_binary_sha256=self.cli_sha256,
                                          case_key="detail", state_dir=self.state_dir,
                                          run_id=RUN_ID, allow_write=True,
                                          confirm_run_id="other")
        self.assertFalse(self.state_dir.exists())
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_workspace_cli_case_run(profile_path=self.profile_path,
                                          cli_binary=self.cli_binary,
                                          cli_binary_sha256=self.cli_sha256,
                                          case_key="detail", state_dir=self.state_dir,
                                          run_id=RUN_ID, allow_write=True,
                                          confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_workspace_case_runner(self):
        self.state_dir = self.root / "state-dispatch"
        arguments = [
            "dual-workspace-run",
            "--profile", str(self.profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--case", "member",
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_workspace_cli_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            case_key="member",
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
