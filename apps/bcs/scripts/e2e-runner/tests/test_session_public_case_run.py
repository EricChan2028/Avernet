"""Command and receipt tests for the public Session JWT/WS lifecycle."""

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
sys.path.insert(0, str(ROOT))

from bcs_e2e.cli import main
from bcs_e2e.local_session_contract import PARENT, REQUIREMENTS
from bcs_e2e.local_session_public_case import (
    CASE_ID,
    LOCAL_EXECUTION_CASE_ID,
    session_public_case_plan,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from bcs_e2e.session_public_case_run import (
    REPORT_CONTRACT,
    dual_session_public_case_run,
)

RUN_ID = "session-public-run-1"
COOKIE_REF = "BCS_E2E_LOCAL_COOKIE"


def base_profile(*, gateway: bool = False) -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    if not gateway:
        return value
    value["environment"].update(
        id="preprod-session-public", expected_bcs_env="pre", tier="preprod",
        lifecycle="remote-attach", deployment="managed", provider="internal")
    value["entrypoint"].update(kind="gateway", origin="https://teamclawgw.example.com")
    value["observer"].update(kind="alisddbquery", server="observ",
                             database="agentclawdb",
                             environment_binding_confirmed=True)
    value["policy"].update(fixture_pool="preprod-session-public-pool",
                           exclusive_use_confirmed=True)
    return value


def expected_local_result(*, passed: bool = True) -> dict:
    return {
        "status": "LOCAL_SESSION_PUBLIC_PASS" if passed else "BLOCKED",
        "remaining_requirement_ids": list(REQUIREMENTS[1:]),
        "full_story_status": "NOT_MIGRATED",
        "full_release_status": "NOT_QUALIFIED",
    }


class SessionPublicCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def invoke_local(self, *, passed: bool = True):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        result = expected_local_result(passed=passed)
        with patch("bcs_e2e.session_public_case_run.local_session_public_run",
                   return_value=result) as execute:
            report = dual_session_public_case_run(
                profile_path=self.profile_path,
                binary=self.root / "approved-bcs",
                binary_sha256="a" * 64,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        execute.assert_called_once_with(
            binary=self.root / "approved-bcs",
            binary_sha256="a" * 64,
            state_dir=self.state_dir / "owned-local-session-public",
            run_id=RUN_ID,
            confirm_run_id=RUN_ID,
            allow_local_processes=True,
            allow_fixture_write=True,
            allow_write=True,
        )
        return report, profile, result

    def test_local_pass_writes_a_sanitized_dual_receipt(self):
        report, profile, result = self.invoke_local()
        plan = session_public_case_plan()
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["parent_id"], PARENT)
        self.assertEqual(report["requirement_id"], plan["requirement_id"])
        self.assertEqual(report["credential_kind"], "owned_local_principal_jwt")
        self.assertEqual(report["admission"]["status"], "SINGLEBOX_OWNED_LOCAL_READY")
        self.assertEqual(report["execution"]["status"], result["status"])
        self.assertEqual(report["execution"]["local_execution_case_id"],
                         LOCAL_EXECUTION_CASE_ID)
        self.assertEqual(report["execution"]["remaining_requirement_ids"],
                         list(REQUIREMENTS[1:]))
        self.assertEqual(report["resource_disposition"],
                         "OWNED_LOCAL_SESSION_AND_GROUP_FINALIZED")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / "session-public-profile.json"), profile)
        binding = read(self.state_dir / "session-public-binding.json")
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["runtime_dir"],
                         str(self.state_dir / "owned-local-session-public"))
        self.assertEqual(read(self.state_dir / "session-public-run.json"), report)
        self.assertNotIn(COOKIE_REF + "-private-value", json.dumps(report, sort_keys=True))

    def test_local_failure_keeps_the_result_blocked_without_credit(self):
        report, _, _ = self.invoke_local(passed=False)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["status"], "BLOCKED")
        self.assertEqual(report["resource_disposition"],
                         "UNKNOWN_OWNED_LOCAL_SESSION_GROUP_DISPOSITION")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / "session-public-run.json"), report)

    def test_gateway_reports_admission_blocked_without_a_request_or_executor(self):
        profile = base_profile(gateway=True)
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with patch("bcs_e2e.session_public_case_run.local_session_public_run") as execute:
            report = dual_session_public_case_run(
                profile_path=self.profile_path,
                binary=self.root / "approved-bcs",
                binary_sha256="a" * 64,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
            execute.assert_not_called()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["admission"]["status"], "GATEWAY_EFFECT_BLOCKED")
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertEqual(report["execution"]["execution_case_id"], "PRE-02")
        self.assertEqual(report["resource_disposition"],
                         "NO_REMOTE_BUSINESS_RESOURCE_CREATED")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertFalse((self.state_dir / "owned-local-session-public").exists())

    def test_non_cookie_auth_is_rejected_offline(self):
        profile = base_profile()
        profile["auth"] = {"header_env": {"Authorization": "BCS_E2E_BOT_TOKEN"}}
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Cookie-only"):
            dual_session_public_case_run(
                profile_path=self.profile_path,
                binary=self.root / "approved-bcs",
                binary_sha256="a" * 64,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertFalse(self.state_dir.exists())

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_session_public_case_run(
                profile_path=self.profile_path,
                binary=self.root / "approved-bcs",
                binary_sha256="a" * 64,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_session_public_case_run(
                profile_path=self.profile_path,
                binary=self.root / "approved-bcs",
                binary_sha256="a" * 64,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_session_public_lifecycle(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-session-public-run",
            "--profile", str(self.profile_path),
            "--binary", str(self.root / "approved-bcs"),
            "--binary-sha256", "a" * 64,
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_session_public_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            binary=self.root / "approved-bcs",
            binary_sha256="a" * 64,
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
