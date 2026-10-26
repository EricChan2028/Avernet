"""Command, report, guard, and CLI dispatch tests; no live request."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.cli import main
from bcs_e2e.group_context_case_run import (
    BINDING_FILE,
    REPORT_CONTRACT,
    PROFILE_FILE,
    REPORT_FILE,
    dual_group_context_cli_case_run,
)
from bcs_e2e.group_context_contract import (
    ADAPTER_METHODS,
    CASE_ID,
    PARENT,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read

from test_collaboration_team import (
    COOKIE,
    DRIVER_TOKEN,
    profile_value,
)

RUN_ID = "group-context-cli-run-1"

REQUIREMENT_IDS = (
    "CLI-GROUP-CONTEXT.workspace",
    "CLI-GROUP-CONTEXT.add-group-entry",
    "CLI-GROUP-CONTEXT.add-session-entry",
    "CLI-GROUP-CONTEXT.add-round-entry",
    "CLI-GROUP-CONTEXT.get-initial-entry",
    "CLI-GROUP-CONTEXT.update-entry",
    "CLI-GROUP-CONTEXT.list-entries",
    "CLI-GROUP-CONTEXT.get-updated-entry",
    "CLI-GROUP-CONTEXT.cleanup-workspace",
)


def obligation_pass() -> CaseExecution:
    steps = tuple(StepOutcome(
        requirement_id=requirement_id, method=method, status="PASS",
        data={"obligation": requirement_id},
        evidence={"transport": "PRODUCT_CLI", "operation": operation,
                  "remote_validation": "NOT_RUN",
                  "grants_release_credit": False},
        error_type=None)
        for requirement_id, method, operation in zip(
            REQUIREMENT_IDS, ADAPTER_METHODS, (
                "group_context.workspace", "group_context.add.group",
                "group_context.add.session", "group_context.add.round",
                "group_context.get.initial", "group_context.update.group",
                "group_context.list.entries", "group_context.get.updated",
                "group_context.cleanup")))
    value = CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status="PASS", steps=steps,
        completed_count=9, total_count=9, error_type=None,
        ready_to_execute=True)
    value.validate()
    return value


def obligation_blocked() -> CaseExecution:
    steps = tuple(StepOutcome(
        requirement_id=requirement_id, method=method, status="NOT_RUN",
        data=None, evidence={}, error_type=None)
        for requirement_id, method in zip(REQUIREMENT_IDS, ADAPTER_METHODS))
    value = CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status="BLOCKED", steps=steps,
        completed_count=0, total_count=9, error_type="RunStateError",
        ready_to_execute=False)
    value.validate()
    return value


class GroupContextCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(
            self.cli_binary.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *, gateway=False):
        profile_path = self.root / "profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=gateway)),
            encoding="utf-8")
        state_dir = self.root / "group-context-state"
        expected = obligation_pass()

        class FakeRunner:
            def __init__(self, profile, *, binary, binary_sha256, state_dir):
                self.profile = profile
                self.binary = binary
                self.binary_sha256 = binary_sha256
                self.state_dir = state_dir

        class FakeAdapter:
            def __init__(self, runner):
                self.runner = runner
                self.profile = runner.profile

        with patch(
                "bcs_e2e.group_context_case_run.CollaborationTeamRunner",
                FakeRunner), patch(
                "bcs_e2e.group_context_case_run.RemoteGroupContextAdapter",
                FakeAdapter), patch(
                "bcs_e2e.group_context_case_run.execute_group_context_case",
                lambda adapter: expected):
            report = dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, expected, state_dir

    def test_local_case_writes_a_bound_sanitized_report(self):
        report, expected, state_dir = self.invoke(gateway=False)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["parent_id"], PARENT)
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["run_id"], RUN_ID)
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["resource_disposition"],
                         "REQUEST_AND_GROUP_HISTORY_RETAINED")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["environment"], {
            "tier": "local", "id": "local",
            "entrypoint_kind": "singlebox"})
        self.assertEqual(report["cli_binary_sha256"], self.cli_sha256)
        self.assertEqual(report["case"], expected.snapshot())
        self.assertEqual(report["case"]["completed_count"], 9)
        binding = read(state_dir / BINDING_FILE)
        self.assertEqual(binding["contract"], REPORT_CONTRACT)
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["parent_id"], PARENT)
        self.assertEqual(binding["run_id"], RUN_ID)
        self.assertEqual(binding["cli_binary_sha256"], self.cli_sha256)
        self.assertEqual(binding["state_dir"], str(state_dir))
        self.assertTrue(binding["profile_digest"])
        self.assertTrue(binding["executor_digest"])
        self.assertEqual(read(state_dir / PROFILE_FILE),
                         profile_value(gateway=False))
        self.assertEqual(read(state_dir / REPORT_FILE), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn(DRIVER_TOKEN, serialized)
        self.assertNotIn("private-driver-token", serialized)

    def test_gateway_reports_cookie_kind_without_cookie_secret(self):
        report, _, _ = self.invoke(gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"],
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["environment"], {
            "tier": "preprod", "id": "preprod",
            "entrypoint_kind": "gateway"})
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn(DRIVER_TOKEN, serialized)
        self.assertNotIn("private-human-cookie-value", serialized)

    def test_blocked_execution_is_reported_without_release_credit(self):
        profile_path = self.root / "blocked-profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "blocked-state"
        blocked = obligation_blocked()
        with patch(
                "bcs_e2e.group_context_case_run.CollaborationTeamRunner"), patch(
                "bcs_e2e.group_context_case_run.RemoteGroupContextAdapter"), patch(
                "bcs_e2e.group_context_case_run.execute_group_context_case",
                lambda adapter: blocked):
            report = dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["error_type"], "RunStateError")
        self.assertEqual(report["case"]["ready_to_execute"], False)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_write_permission_and_run_confirmation_are_enforced(self):
        profile_path = self.root / "profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "guard-state"
        with self.assertRaisesRegex(
                ConfigError, "explicit write permission"):
            dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.assertFalse(state_dir.exists())
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id="other-run",
            )
        self.assertFalse(state_dir.exists())

    def test_fresh_absolute_private_state_directory_is_enforced(self):
        profile_path = self.root / "profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        existing = self.root / "existing-state"
        existing.mkdir()
        with self.assertRaisesRegex(
                ConfigError, "fresh private state directory"):
            dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=existing,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        with self.assertRaisesRegex(
                ConfigError, "fresh private state directory"):
            dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=Path("relative-group-context-state"),
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        target = self.root / "symlink-target"
        target.mkdir()
        link = self.root / "symlink-state"
        link.symlink_to(target)
        with self.assertRaisesRegex(
                ConfigError, "fresh private state directory"):
            dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=link,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertEqual(list(existing.iterdir()), [])

    def test_unreadable_and_invalid_profiles_are_rejected(self):
        broken = self.root / "broken-profile.json"
        broken.write_text("{not json", encoding="utf-8")
        state_dir = self.root / "broken-state"
        with self.assertRaisesRegex(ConfigError, "unreadable JSON"):
            dual_group_context_cli_case_run(
                profile_path=broken,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        invalid = profile_value(gateway=False)
        del invalid["actors"]
        invalid_path = self.root / "invalid-profile.json"
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(ConfigError):
            dual_group_context_cli_case_run(
                profile_path=invalid_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertFalse(state_dir.exists())

    def test_run_id_identifier_is_enforced(self):
        profile_path = self.root / "profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "id-state"
        with self.assertRaisesRegex(ConfigError, "invalid identifier"):
            dual_group_context_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id="bad group context run id!",
                allow_write=True,
                confirm_run_id="bad group context run id!",
            )
        self.assertFalse(state_dir.exists())

    def test_cli_dispatch_uses_the_group_context_runner(self):
        self.assertIn("dual-group-context-run", CASE_COMMANDS)
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "dispatch-state"
        arguments = [
            "dual-group-context-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_group_context_cli_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")

    def test_cli_dispatch_reports_blocked_with_nonzero_status(self):
        profile_path = self.root / "blocked-dispatch-profile.json"
        profile_path.write_text(
            json.dumps(profile_value(gateway=False)), encoding="utf-8")
        state_dir = self.root / "blocked-dispatch-state"
        arguments = [
            "dual-group-context-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_group_context_cli_case_run",
                return_value={"status": "BLOCKED"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 2)
        execute.assert_called_once()
        self.assertEqual(json.loads(stdout.getvalue())["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
