"""Command and report tests for the dual Operator product-CLI case."""

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
from bcs_e2e import operator_cli_case_run as operator_case_runner
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.local_operator_cli_contract import PARENT
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.operator_cli_case_run import dual_operator_cli_case_run
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from test_remote_operator_cli_adapter import PLACEHOLDERS, profile_value

RUN_ID = "operator-cli-run-1"


def local_profile() -> dict:
    return profile_value()


def preprod_profile() -> dict:
    return profile_value(preprod=True)


def fake_execution() -> CaseExecution:
    step = StepOutcome(
        requirement_id="OPERATOR-CLI.bot-registry.read",
        method="read_cli_bot",
        status="PASS",
        data={"bot_registry_read": True},
        evidence={"credential_kind": "owned_local_bot_token"},
        error_type=None,
    )
    execution = CaseExecution(
        case_id="DUAL-OPERATOR-CLI-01",
        parent_id=PARENT,
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    execution.validate()
    return execution


class FakeRunner:
    def __init__(self, profile, *, binary, binary_sha256, state_dir):
        self.profile = profile
        self.state_dir = Path(state_dir).resolve()


class FakeAdapter:
    def __init__(self, profile, runner):
        self.profile = profile
        self.runner = runner


class OperatorCliCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = hashlib.sha256(self.cli_binary.read_bytes()).hexdigest()

    def invoke(self, *, preprod=False):
        profile = preprod_profile() if preprod else local_profile()
        profile_path = self.root / f"profile-{'preprod' if preprod else 'local'}.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / f"state-{'preprod' if preprod else 'local'}"
        expected = fake_execution()
        with patch.object(operator_case_runner, "OperatorCliRunner", FakeRunner), \
             patch.object(operator_case_runner, "RemoteOperatorCliAdapter", FakeAdapter), \
             patch.object(operator_case_runner, "execute_operator_cli_case",
                          lambda adapter: expected):
            report = dual_operator_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, state_dir

    def test_report_is_registered_in_the_case_inventory(self):
        rows = {row["case_id"] for row in migration_plan()[
            "cross_environment_product_cli_case_slices"]}
        self.assertIn("DUAL-OPERATOR-CLI-01", rows)

    def test_local_run_writes_bound_sanitized_receipts(self):
        report, profile, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], "DUAL-OPERATOR-CLI-01")
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(state_dir / "operator-cli-profile.json"), profile)
        self.assertEqual(read(state_dir / "operator-cli-binding.json")["case_id"],
                         "DUAL-OPERATOR-CLI-01")
        self.assertEqual(read(state_dir / "operator-cli-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        for placeholder in PLACEHOLDERS:
            self.assertNotIn(placeholder, serialized)

    def test_preprod_report_records_credential_kind_without_secret(self):
        report, _, _ = self.invoke(preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"],
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "bcs-direct")
        serialized = json.dumps(report, sort_keys=True)
        for placeholder in PLACEHOLDERS:
            self.assertNotIn(placeholder, serialized)

    def test_permissions_and_fresh_state_are_enforced(self):
        state_dir = self.root / "state"
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_operator_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id="other")
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_operator_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_operator_runner(self):
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        arguments = [
            "dual-operator-cli-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_operator_cli_case_run",
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


if __name__ == "__main__":
    unittest.main()
