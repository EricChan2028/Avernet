"""Command and report tests for the dual Session product-CLI cases."""

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
from bcs_e2e import session_cli_case_run as session_case_runner
from bcs_e2e.session_cli_case_run import _CASES, SESSION_PARENT, dual_session_cli_case_run

from test_session_cli_bot_groups import profile_value as bot_groups_profile
from test_session_cli_collection import profile_value as collection_profile
from test_session_cli_complete_invoke import profile_value as complete_invoke_profile
from test_session_cli_create_get import profile_value as create_get_profile
from test_session_cli_group_invite import profile_value as group_invite_profile
from test_session_cli_invite_join import profile_value as invite_join_profile
from test_session_cli_lifecycle import profile_value as lifecycle_profile
from test_session_cli_session_invite import profile_value as session_invite_profile

RUN_ID = "session-cli-run-1"
PROFILE_BUILDERS = {
    "create-get": lambda: create_get_profile(),
    "bot-groups": lambda: bot_groups_profile(),
    "group-invite": lambda: group_invite_profile(),
    "session-invite": lambda: session_invite_profile(),
    "invite-join": lambda: invite_join_profile(),
    "lifecycle": lambda: lifecycle_profile(),
    "complete-invoke": lambda: complete_invoke_profile(),
    "collection": lambda: collection_profile(),
}


def execution(case_key: str, *, gateway=False) -> CaseExecution:
    item = _CASES[case_key]
    step = StepOutcome(
        requirement_id=f"SESSION.{case_key}",
        method=item["case_id"],
        status="PASS",
        data={"case_entry": case_key},
        evidence={"credential_kind": "human_cookie" if gateway else "owned_local_bot_token"},
        error_type=None,
    )
    execution = CaseExecution(
        case_id=item["case_id"],
        parent_id=SESSION_PARENT,
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
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = Path(state_dir).resolve()


class FakeAdapter:
    def __init__(self, profile, runner):
        self.profile = profile
        self.runner = runner


class SessionCliCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = __import__("hashlib").sha256(self.cli_binary.read_bytes()).hexdigest()

    def invoke_case(self, case_key: str, *, gateway=False):
        profile_path = self.root / f"profile-{case_key}.json"
        profile = PROFILE_BUILDERS[case_key]()
        if gateway and case_key == "create-get":
            profile = create_get_profile(gateway=True)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / f"state-{case_key}"
        expected = execution(case_key, gateway=gateway)
        case_patch = {
            "adapter": FakeAdapter,
            "execute": lambda adapter: expected,
        }
        with patch.object(session_case_runner, "WorkspaceCliRunner", FakeRunner), \
             patch.dict(_CASES[case_key], case_patch):
            report = dual_session_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                case_key=case_key,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, state_dir

    def test_case_registry_covers_the_reviewed_session_inventory(self):
        inventory = migration_plan()["cross_environment_product_cli_case_slices"]
        rows = {row["case_id"] for row in inventory}
        case_ids = {item["case_id"] for item in _CASES.values()}
        self.assertEqual(case_ids, {
            "DUAL-SESSION-CLI-CREATE-LIST-GET-01",
            "DUAL-SESSION-CLI-BOT-GROUPS-01",
            "DUAL-SESSION-CLI-GROUP-INVITE-01",
            "DUAL-SESSION-CLI-SESSION-INVITE-01",
            "DUAL-SESSION-CLI-INVITE-JOIN-01",
            "DUAL-SESSION-CLI-LIFECYCLE-01",
            "DUAL-SESSION-CLI-COMPLETE-INVOKE-01",
            "DUAL-SESSION-CLI-COLLECTION-01",
        })
        self.assertTrue(case_ids <= rows)

    def test_all_reviewed_cases_write_local_receipts(self):
        for case_key in PROFILE_BUILDERS:
            with self.subTest(case_key):
                report, profile, state_dir = self.invoke_case(case_key)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], _CASES[case_key]["case_id"])
                self.assertEqual(report["parent_id"], SESSION_PARENT)
                self.assertEqual(report["credential_kind"], "owned_local_bot_token")
                self.assertEqual(report["remote_validation"], "NOT_RUN")
                self.assertFalse(report["grants_release_credit"])
                self.assertEqual(
                    read(state_dir / f"session-{case_key}-profile.json"), profile)
                self.assertEqual(
                    read(state_dir / f"session-{case_key}-binding.json")["case_id"],
                    _CASES[case_key]["case_id"])
                self.assertEqual(
                    read(state_dir / f"session-{case_key}-run.json"), report)

    def test_gateway_reports_cookie_kind_but_no_cookie_secret(self):
        report, _, _ = self.invoke_case("create-get", gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        raw = json.dumps(report, sort_keys=True)
        self.assertNotIn("cookie-secret", raw)
        self.assertNotIn("driver-token-secret", raw)

    def test_case_permissions_and_fresh_state_are_enforced(self):
        state_dir = self.root / "guard"
        profile_path = self.root / "guard-profile.json"
        profile_path.write_text(json.dumps(PROFILE_BUILDERS["lifecycle"]()), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "session_case must be"):
            dual_session_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="file", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_session_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="lifecycle", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id="other")
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_session_cli_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="lifecycle", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_session_case_runner(self):
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(PROFILE_BUILDERS["collection"]()), encoding="utf-8")
        arguments = [
            "dual-session-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--case", "collection",
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_session_cli_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            case_key="collection",
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


    def test_preprod_complete_invoke_receipt_labels_the_ace_trio(self):
        profile = complete_invoke_profile(preprod=True)
        self.assertEqual(profile["auth"]["kind"],
                         "human-cookie-with-owned-bot-tokens")
        profile_path = self.root / "trio-profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / "trio-state"
        expected = execution("complete-invoke", gateway=True)
        case_patch = {"adapter": FakeAdapter,
                      "execute": lambda adapter: expected}
        with patch.object(session_case_runner, "WorkspaceCliRunner",
                          FakeRunner), \
             patch.dict(_CASES["complete-invoke"], case_patch):
            report = dual_session_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                case_key="complete-invoke",
                state_dir=state_dir,
                run_id=RUN_ID + "-trio",
                allow_write=True,
                confirm_run_id=RUN_ID + "-trio",
            )
        self.assertEqual(report["credential_kind"],
                         "human_cookie_with_owned_bot_tokens")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertNotIn("owned-member-token",
                         json.dumps(report, sort_keys=True))

    def test_preprod_collection_receipt_stays_cookie_only(self):
        profile = collection_profile(preprod=True)
        self.assertEqual(profile["auth"]["kind"], "human-cookie")
        profile_path = self.root / "collection-profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / "collection-state"
        expected = execution("collection", gateway=True)
        case_patch = {"adapter": FakeAdapter,
                      "execute": lambda adapter: expected}
        with patch.object(session_case_runner, "WorkspaceCliRunner",
                          FakeRunner), \
             patch.dict(_CASES["collection"], case_patch):
            report = dual_session_cli_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                case_key="collection",
                state_dir=state_dir,
                run_id=RUN_ID + "-collection",
                allow_write=True,
                confirm_run_id=RUN_ID + "-collection",
            )
        self.assertEqual(report["credential_kind"], "human_cookie")


if __name__ == "__main__":
    unittest.main()
