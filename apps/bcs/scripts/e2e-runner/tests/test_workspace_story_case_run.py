"""Command and receipt tests for the remaining dual Workspace stories."""

from __future__ import annotations

from contextlib import redirect_stdout
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
from bcs_e2e.cli import main
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from bcs_e2e.workspace_story_case_run import (
    CASE_KEYS,
    WORKSPACE_PARENT,
    _CASES,
    dual_workspace_story_case_run,
)

from test_workspace_incident_core import profile_value as incident_profile
from test_workspace_proposal import profile_value as proposal_profile
from test_workspace_roundtrip import profile_value as roundtrip_profile

RUN_ID = "workspace-story-run-1"
PROFILE_BUILDERS = {
    "incident-core": incident_profile,
    "roundtrip": roundtrip_profile,
    "proposal": proposal_profile,
}


def execution(case_key: str, *, preprod=False) -> CaseExecution:
    step = StepOutcome(
        requirement_id=f"WORKSPACE.{case_key}",
        method=_CASES[case_key]["case_id"],
        status="PASS",
        data={"command_entry": case_key},
        evidence={"credential_kind": "human_cookie" if preprod else "owned_local_bot_token"},
        error_type=None,
    )
    case = CaseExecution(
        case_id=_CASES[case_key]["case_id"],
        parent_id=WORKSPACE_PARENT,
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    case.validate()
    return case


class WorkspaceStoryCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def invoke_case(self, case_key: str, *, preprod=False):
        profile_path = self.root / f"profile-{case_key}.json"
        profile = PROFILE_BUILDERS[case_key](preprod=preprod)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / f"state-{case_key}"
        expected = execution(case_key, preprod=preprod)
        with patch.dict(_CASES[case_key], {
            "adapter": lambda profile: object(),
            "execute": lambda adapter: expected,
        }):
            report = dual_workspace_story_case_run(
                profile_path=profile_path,
                case_key=case_key,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, state_dir, expected

    def test_case_registry_covers_the_remaining_workspace_slices(self):
        inventory = migration_plan()["cross_environment_product_cli_case_slices"]
        rows = {row["case_id"] for row in inventory}
        case_ids = {item["case_id"] for item in _CASES.values()}
        self.assertEqual(case_ids, {
            "DUAL-WORKSPACE-INCIDENT-CORE-01",
            "DUAL-WORKSPACE-ROUNDTRIP-01",
            "DUAL-WORKSPACE-PROPOSAL-01",
        })
        self.assertTrue(case_ids <= rows)
        self.assertEqual(tuple(CASE_KEYS), CASE_KEYS)

    def test_all_local_cases_write_bound_sanitized_receipts(self):
        for case_key in PROFILE_BUILDERS:
            with self.subTest(case_key):
                report, profile, state_dir, expected = self.invoke_case(case_key)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], expected.case_id)
                self.assertEqual(report["parent_id"], WORKSPACE_PARENT)
                self.assertEqual(report["credential_kind"], "owned_local_bot_token")
                self.assertEqual(report["remote_validation"], "NOT_RUN")
                self.assertFalse(report["grants_release_credit"])
                self.assertEqual(report["resource_disposition"], _CASES[case_key]["fixture_effect"])
                self.assertEqual(
                    read(state_dir / f"workspace-{case_key}-profile.json"), profile)
                self.assertEqual(
                    read(state_dir / f"workspace-{case_key}-binding.json")["case_id"],
                    expected.case_id)
                self.assertEqual(
                    read(state_dir / f"workspace-{case_key}-run.json"), report)
                self.assertNotIn("owned-driver-token", json.dumps(report, sort_keys=True))

    def test_preprod_proposal_records_cookie_kind_without_cookie_secret(self):
        report, _, _, _ = self.invoke_case("proposal", preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        raw = json.dumps(report, sort_keys=True)
        self.assertNotIn("human-proposal-cookie", raw)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE=Cookie:", raw)

    def test_permissions_and_fresh_state_are_enforced(self):
        profile_path = self.root / "guard-profile.json"
        profile_path.write_text(
            json.dumps(PROFILE_BUILDERS["roundtrip"]()), encoding="utf-8")
        state_dir = self.root / "guard"
        with self.assertRaisesRegex(ConfigError, "workspace_story_case must be"):
            dual_workspace_story_case_run(
                profile_path=profile_path, case_key="lifecycle", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_workspace_story_case_run(
                profile_path=profile_path, case_key="roundtrip", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id="other")
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_workspace_story_case_run(
                profile_path=profile_path, case_key="roundtrip", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_workspace_story_runner(self):
        profile_path = self.root / "dispatch-profile.json"
        state_dir = self.root / "dispatch"
        profile_path.write_text(
            json.dumps(PROFILE_BUILDERS["incident-core"]()), encoding="utf-8")
        arguments = [
            "dual-workspace-story-run",
            "--profile", str(profile_path),
            "--case", "incident-core",
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_workspace_story_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            case_key="incident-core",
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
