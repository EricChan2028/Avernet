"""Command and report tests for the Workspace status/termination entry."""

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
from bcs_e2e.workspace_cli_status_run import dual_workspace_cli_status_run

DRIVER = "driver-bot"
MEMBER = "member-bot"
COMPLETION_GROUP = "bcs_grp_status-completion"
TERMINATION_GROUP = "bcs_grp_status-termination"
COMPLETION_SESSION = COMPLETION_GROUP + ":01020304"
TERMINATION_SESSION = TERMINATION_GROUP + ":05060708"
LOCAL_TOKEN = "local-driver-token-secret"
PREPROD_COOKIE = "preprod-human-cookie-secret"
REPORT_FILE = "workspace-cli-status-run.json"
BINDING_FILE = "workspace-cli-status-binding.json"
PROFILE_FILE = "workspace-cli-status-profile.json"


def profile_value(*, gateway=False):
    local = not gateway
    return {
        "schema_version": 1,
        "contract": "dual-workspace-cli-status-profile-v1",
        "environment": {
            "tier": "local" if local else "preprod",
            "id": "workspace-cli-status-local" if local else "workspace-cli-status-preprod",
            "expected_bcs_env": "local" if local else "pre",
        },
        "entrypoint": {
            "kind": "singlebox" if local else "gateway",
            "origin": "http://127.0.0.1:21000" if local else "https://gateway.example.com",
            "api_face": "legacy",
            "manifest_face": "legacy",
        },
        "human": {"user_id": "owner-1"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                       "dedicated": True, "visibility": "public"},
        },
        "auth": (
            {"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
            if local else {"kind": "owned-bot-tokens",
                           "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
        ),
        "relationships": {
            "group": "bounded-owned-bots-retained-cli-status-termination",
            "initial_session": "retained",
            "cleanup": "none",
            "participant_mode": "driver-member",
        },
        "policy": {
            "fixture_pool": "workspace-cli-status-pool",
            "exclusive_use_confirmed": True,
            "timeout_seconds": 10,
            "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-cli", "command_family": "group",
                "api_face": "legacy"},
    }


def create_output(group_id: str, session_id: str) -> bytes:
    return (
        f"Group created:\n  ID: {group_id}\n  Driver: {DRIVER}\n"
        f"  Participants: {DRIVER},{MEMBER}\n  Session: {session_id}\n"
    ).encode()


def group_read(group_id: str, status: str) -> dict:
    return {"id": group_id, "driver_bot": DRIVER, "status": status,
            "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}


def fake_complete_process(argument, operation: str, group_id: str | None):
    if operation.endswith(".create"):
        group_id, session_id = (
            (COMPLETION_GROUP, COMPLETION_SESSION)
            if operation == "status.group.create"
            else (TERMINATION_GROUP, TERMINATION_SESSION)
        )
        raw = create_output(group_id, session_id)
    elif operation == "status.group.status":
        raw = json.dumps({"updated": True, "group_id": group_id,
                          "status": "completed", "changed_by": DRIVER}).encode()
    elif operation == "termination.group.terminate":
        raw = json.dumps({"terminated": True, "group_id": group_id,
                          "status": "completed", "terminated_by": DRIVER}).encode()
    else:
        status = "active" if operation.endswith(".active") else "completed"
        raw = json.dumps(group_read(group_id, status)).encode()
    return subprocess.CompletedProcess(argument, 0, raw, b"")


class WorkspaceCliStatusRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"
        self.run_id = "workspace-status-run-1"

    def write_profile(self, *, gateway=False, mutate=None):
        value = profile_value(gateway=gateway)
        if mutate is not None:
            mutate(value)
        self.profile_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def invoke_case(self, *, gateway=False):
        profile = self.write_profile(gateway=gateway)
        environment = {
            "BCS_E2E_DRIVER_BOT_TOKEN": LOCAL_TOKEN,
            "BCS_E2E_HUMAN_COOKIE": PREPROD_COOKIE,
        }
        calls = []
        expected = [
            ("status.group.create", None),
            ("status.group.read.active", COMPLETION_GROUP),
            ("status.group.status", COMPLETION_GROUP),
            ("status.group.read.completed", COMPLETION_GROUP),
            ("termination.group.create", None),
            ("termination.group.read.active", TERMINATION_GROUP),
            ("termination.group.terminate", TERMINATION_GROUP),
            ("termination.group.read.completed", TERMINATION_GROUP),
        ]

        def fake_capture(command, **kwargs):
            operation, group_id = expected[len(calls)]
            calls.append(operation)
            return fake_complete_process(command, operation, group_id)

        with patch.dict(os.environ, environment), patch(
                "bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            report = dual_workspace_cli_status_run(
                profile_path=self.profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=self.state_dir,
                run_id=self.run_id,
                allow_write=True,
                confirm_run_id=self.run_id,
            )
        return report, profile, calls

    def test_local_singlebox_case_is_command_invokable_and_reported(self):
        report, profile, calls = self.invoke_case()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case"]["status"], "PASS")
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["case"]["steps"][0]["requirement_id"], "WORKSPACE.cli-status")
        self.assertEqual(len(report["case"]["steps"][0]["evidence"]["requests"]), 8)
        self.assertEqual(calls[-1], "termination.group.read.completed")
        self.assertEqual(read(self.state_dir / PROFILE_FILE), profile)
        self.assertEqual(read(self.state_dir / BINDING_FILE)["case_id"], "DUAL-WORKSPACE-CLI-STATUS-01")
        self.assertEqual(read(self.state_dir / REPORT_FILE), report)
        self.assertNotIn(LOCAL_TOKEN, json.dumps(report, sort_keys=True))

    def test_bad_cli_exit_produces_a_blocked_report_without_credit(self):
        self.write_profile()
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN": LOCAL_TOKEN}), patch(
                "bcs_e2e.workspace_cli_runner.capture",
                return_value=subprocess.CompletedProcess([], 1, b"", b"bad exit")):
            report = dual_workspace_cli_status_run(
                profile_path=self.profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=self.state_dir,
                run_id=self.run_id,
                allow_write=True,
                confirm_run_id=self.run_id,
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / REPORT_FILE), report)
        self.assertNotIn(LOCAL_TOKEN, json.dumps(report, sort_keys=True))
    def test_preprod_gateway_case_is_command_invokable_and_reported(self):
        report, _, calls = self.invoke_case(gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "owned_bot_token")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertNotIn(PREPROD_COOKIE, json.dumps(report, sort_keys=True))
        self.assertEqual(calls[-1], "termination.group.read.completed")

    def test_exact_confirmation_and_fresh_state_are_required(self):
        self.write_profile()
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_workspace_cli_status_run(
                profile_path=self.profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=self.state_dir,
                run_id=self.run_id,
                allow_write=True,
                confirm_run_id="other",
            )
        self.assertFalse(self.state_dir.exists())
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_workspace_cli_status_run(
                profile_path=self.profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=self.state_dir,
                run_id=self.run_id,
                allow_write=True,
                confirm_run_id=self.run_id,
            )

    def test_cli_dispatch_uses_the_dual_workspace_runner(self):
        arguments = [
            "dual-workspace-cli-status-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", self.run_id,
            "--confirm-run-id", self.run_id,
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_workspace_cli_status_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            state_dir=self.state_dir,
            run_id=self.run_id,
            allow_write=True,
            confirm_run_id=self.run_id,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
