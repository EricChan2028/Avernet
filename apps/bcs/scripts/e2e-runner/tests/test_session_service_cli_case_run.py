"""Command and report tests for the dual Session Service product-CLI cases."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
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
from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import read
from bcs_e2e.session_cli_profile import (
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
)
from bcs_e2e.session_cli_profile import (
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_lifecycle_profile,
)
from bcs_e2e import session_service_cli_case_run as session_service_case_runner
from bcs_e2e.session_service_cli_case_run import (
    _CASES,
    CASE_KEYS,
    DUAL_SESSION_SERVICE_COMPLETE_CASE_ID,
    DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID,
    DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
    DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID,
    DualSessionServiceCompleteAdapter,
    DualSessionServiceLifecycleAdapter,
    DualSessionServiceRoundtripAdapter,
    expected_step_transport,
    session_service_credential_kind,
    dual_session_service_case_run,
)

from test_session_cli_complete_invoke import profile_value as complete_invoke_profile
from test_session_cli_lifecycle import profile_value as lifecycle_profile

RUN_ID = "session-service-run-1"
PROFILE_BUILDERS = {
    "service-lifecycle": lambda preprod=False: lifecycle_profile(preprod=preprod),
    "service-member-chat": lambda preprod=False: lifecycle_profile(preprod=preprod),
    "service-complete": lambda preprod=False: complete_invoke_profile(preprod=preprod),
    "service-roundtrip": lambda preprod=False: complete_invoke_profile(preprod=preprod),
}
REQUIREMENTS = {
    "service-lifecycle": "CLI-SESSION-SERVICE.session-lifecycle",
    "service-member-chat": "CLI-SESSION-SERVICE.member-chat-lifecycle",
    "service-complete": "CLI-SESSION-SERVICE.session-complete",
    "service-roundtrip": "CLI-SESSION-SERVICE.service-roundtrip",
}
CASE_IDS = {
    "service-lifecycle": DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID,
    "service-member-chat": DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
    "service-complete": DUAL_SESSION_SERVICE_COMPLETE_CASE_ID,
    "service-roundtrip": DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID,
}
FIXTURE_EFFECTS = {
    "service-lifecycle": "OWNED_SESSION_LIFECYCLE_THEN_GENERIC_GROUP_DELETE",
    "service-member-chat": "OWNED_SESSION_MEMBER_CHAT_THEN_GENERIC_GROUP_DELETE",
    "service-complete": "OWNED_SESSION_COMPLETION_THEN_GENERIC_GROUP_DELETE",
    "service-roundtrip": (
        "OWNED_SESSION_COMPLETION_AND_INVOCATION_THEN_CREATOR_SCOPED_CLEANUP"),
}


def execution(case_key: str, *, gateway=False) -> CaseExecution:
    item = _CASES[case_key]
    step = StepOutcome(
        requirement_id=REQUIREMENTS[case_key],
        method=item["execute"].__name__,
        status="PASS",
        data={"case_entry": case_key},
        evidence={"credential_kind": "human_cookie" if gateway else "owned_local_bot_token"},
        error_type=None,
    )
    result = CaseExecution(
        case_id=item["case_id"],
        parent_id="story_cli_operator_runs_sessions_and_services",
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    result.validate()
    return result


class FakeRunner:
    def __init__(self, profile, *, binary, binary_sha256, state_dir):
        self.profile = profile
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.state_dir = Path(state_dir).resolve()

    def execute(self, operation, **kwargs):
        raise AssertionError("fake product CLI runner must not execute")


class FakeAdapter:
    def __init__(self, profile, runner):
        self.profile = profile
        self.runner = runner


class SessionServiceCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.cli_binary = self.root / "bcs-cli"
        self.cli_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.cli_binary.chmod(0o700)
        self.cli_sha256 = __import__("hashlib").sha256(self.cli_binary.read_bytes()).hexdigest()

    def invoke_case(self, case_key: str, *, gateway=False):
        profile_path = self.root / f"profile-{case_key}.json"
        profile = PROFILE_BUILDERS[case_key](preprod=gateway)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        state_dir = self.root / f"state-{case_key}"
        expected = execution(case_key, gateway=gateway)
        case_patch = {"adapter": FakeAdapter, "execute": lambda adapter: expected}
        with patch.object(session_service_case_runner, "WorkspaceCliRunner", FakeRunner), \
             patch.dict(_CASES[case_key], case_patch):
            report = dual_session_service_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                case_key=case_key,
                state_dir=state_dir,
                run_id=RUN_ID + "-" + case_key,
                allow_write=True,
                confirm_run_id=RUN_ID + "-" + case_key,
            )
        return report, profile, state_dir

    def test_case_registry_covers_the_reviewed_session_service_inventory(self):
        self.assertEqual(set(CASE_KEYS), {
            "service-lifecycle", "service-member-chat",
            "service-complete", "service-roundtrip"})
        inventory = migration_plan()["cross_environment_product_cli_case_slices"]
        rows = {row["case_id"] for row in inventory}
        case_ids = {item["case_id"] for item in _CASES.values()}
        self.assertEqual(case_ids, {
            DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID,
            DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
            DUAL_SESSION_SERVICE_COMPLETE_CASE_ID,
            DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        })
        self.assertTrue(case_ids <= rows)

    def test_all_reviewed_cases_write_local_receipts(self):
        for case_key in PROFILE_BUILDERS:
            with self.subTest(case_key=case_key):
                report, profile, state_dir = self.invoke_case(case_key)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], _CASES[case_key]["case_id"])
                self.assertEqual(report["parent_id"],
                                 "story_cli_operator_runs_sessions_and_services")
                self.assertEqual(report["credential_kind"], "owned_local_bot_token")
                self.assertEqual(report["remote_validation"], "NOT_RUN")
                self.assertFalse(report["grants_release_credit"])
                self.assertEqual(report["resource_disposition"], FIXTURE_EFFECTS[case_key])
                self.assertEqual(report["environment"]["tier"], "local")
                self.assertEqual(read(state_dir / f"session-service-{case_key}-profile.json"),
                                 profile)
                self.assertEqual(
                    read(state_dir / f"session-service-{case_key}-binding.json")["case_id"],
                    _CASES[case_key]["case_id"])
                self.assertEqual(read(state_dir / f"session-service-{case_key}-run.json"),
                                 report)

    def test_gateway_lifecycle_reports_cookie_kind_but_no_cookie_secret(self):
        report, _, _ = self.invoke_case("service-lifecycle", gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        raw = json.dumps(report, sort_keys=True)
        self.assertNotIn("cookie-secret", raw)
        self.assertNotIn("driver-token-secret", raw)

    def test_preprod_complete_invoke_receipt_labels_the_ace_trio(self):
        profile = complete_invoke_profile(preprod=True)
        self.assertEqual(profile["auth"]["kind"], "human-cookie-with-owned-bot-tokens")
        self.assertEqual(session_service_credential_kind(profile),
                         "human_cookie_with_owned_bot_tokens")
        report, _, state_dir = self.invoke_case("service-complete", gateway=True)
        self.assertEqual(report["credential_kind"], "human_cookie_with_owned_bot_tokens")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(read(state_dir / "session-service-service-complete-run.json"),
                         report)

    def test_preprod_lifecycle_receipt_stays_cookie_only(self):
        report, _, _ = self.invoke_case("service-member-chat", gateway=True)
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertNotIn("human_cookie_with_owned_bot_tokens",
                         json.dumps(report, sort_keys=True))

    def test_dual_adapters_label_the_tier_aware_credential_kind(self):
        lifecycle_preprod = lifecycle_profile(preprod=True)
        complete_preprod = complete_invoke_profile(preprod=True)
        proxy = FakeRunner(lifecycle_profile(),
                           binary=self.cli_binary,
                           binary_sha256=self.cli_sha256,
                           state_dir=self.root)
        env = {"BCS_E2E_DRIVER_BOT_TOKEN": "unit-driver-token",
               "BCS_E2E_MEMBER_BOT_TOKEN": "unit-member-token",
               "BCS_E2E_HUMAN_COOKIE": "unit-human-cookie"}
        with patch.dict(os.environ, env):
            lifecycle_adapter = DualSessionServiceLifecycleAdapter(lifecycle_preprod, proxy)
            self.assertEqual(lifecycle_adapter._credential_kind(), "human_cookie")
            complete_adapter = DualSessionServiceCompleteAdapter(complete_preprod, proxy)
            self.assertEqual(complete_adapter._credential_kind(),
                             "human_cookie_with_owned_bot_tokens")
            roundtrip_adapter = DualSessionServiceRoundtripAdapter(complete_preprod, proxy)
            self.assertEqual(roundtrip_adapter._credential_kind(),
                             "human_cookie_with_owned_bot_tokens")
            lifecycle_local = DualSessionServiceLifecycleAdapter(lifecycle_profile(), proxy)
            self.assertEqual(lifecycle_local._credential_kind(), "owned_local_bot_token")

    def test_expected_step_transport_splits_product_cli_from_cleanup_http(self):
        self.assertEqual(expected_step_transport("session-cli.session.chat"), "PRODUCT_CLI")
        self.assertEqual(expected_step_transport("session-cli.service.wait"), "PRODUCT_CLI")
        self.assertEqual(
            expected_step_transport(SESSION_CLI_GROUP_DELETE_OPERATION), "PRODUCT_HTTP")
        self.assertEqual(
            expected_step_transport(SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION), "PRODUCT_HTTP")
        self.assertEqual(
            expected_step_transport(SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION), "PRODUCT_HTTP")
        self.assertEqual(
            expected_step_transport(SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION),
            "PRODUCT_HTTP")
        self.assertEqual(
            expected_step_transport(SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION),
            "PRODUCT_HTTP")

    def test_case_permissions_and_fresh_state_are_enforced(self):
        state_dir = self.root / "guard"
        profile_path = self.root / "guard-profile.json"
        profile_path.write_text(json.dumps(PROFILE_BUILDERS["service-lifecycle"]()),
                                encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "session_service case must be"):
            dual_session_service_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="file", state_dir=state_dir,
                run_id=RUN_ID, allow_write=True, confirm_run_id=RUN_ID)
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_session_service_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="service-lifecycle",
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id="other")
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_session_service_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key="service-lifecycle",
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_session_service_case_runner(self):
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(PROFILE_BUILDERS["service-roundtrip"]()),
                                encoding="utf-8")
        arguments = [
            "dual-session-service-run",
            "--profile", str(profile_path),
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--case", "service-roundtrip",
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_session_service_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            case_key="service-roundtrip",
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")

    def test_dual_session_service_run_is_a_registered_case_command(self):
        self.assertIn("dual-session-service-run", CASE_COMMANDS)


VALIDATORS = {
    "service-lifecycle": validate_session_cli_lifecycle_profile,
    "service-member-chat": validate_session_cli_lifecycle_profile,
    "service-complete": validate_session_cli_complete_invoke_profile,
    "service-roundtrip": validate_session_cli_complete_invoke_profile,
}


class SessionServiceBrowserOriginTests(unittest.TestCase):
    """Preprod dual Session Service profiles pin the workstation browser context."""

    BROWSER_ORIGIN = "https://teamclaw-pre.alipay.com"

    def with_browser_origin(self, value):
        def mutate(profile):
            profile["browser_origin"] = value
        return mutate

    def validated(self, case_key, *, preprod, value):
        builders = {
            "service-lifecycle": lifecycle_profile,
            "service-member-chat": lifecycle_profile,
            "service-complete": complete_invoke_profile,
            "service-roundtrip": complete_invoke_profile,
        }
        profile = builders[case_key](preprod=preprod,
                                     mutate=self.with_browser_origin(value))
        return VALIDATORS[case_key](profile)

    def test_preprod_dual_profiles_admit_and_preserve_browser_origin(self):
        for case_key in PROFILE_BUILDERS:
            with self.subTest(case_key=case_key):
                profile = self.validated(case_key, preprod=True,
                                         value=self.BROWSER_ORIGIN)
                self.assertEqual(profile["browser_origin"], self.BROWSER_ORIGIN)

    def test_local_dual_profiles_reject_browser_origin(self):
        for case_key in PROFILE_BUILDERS:
            with self.subTest(case_key=case_key), self.assertRaises(ConfigError):
                self.validated(case_key, preprod=False, value=self.BROWSER_ORIGIN)

    def test_browser_origin_format_is_enforced_on_preprod(self):
        for value in ("http://teamclaw-pre.alipay.com",
                      " https://teamclaw-pre.alipay.com",
                      "https://teamclaw-pre.alipay.com/workbench",
                      "https://name:secret@teamclaw-pre.alipay.com",
                      "https://teamclaw-pre.alipay.com\n"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.validated("service-lifecycle", preprod=True, value=value)

    def test_browser_origin_changes_the_profile_digest(self):
        plain = validate_session_cli_lifecycle_profile(lifecycle_profile(preprod=True))
        pinned = self.validated("service-lifecycle", preprod=True,
                                value=self.BROWSER_ORIGIN)
        self.assertNotEqual(fingerprint(plain), fingerprint(pinned))

    def test_cleanup_write_client_carries_browser_context(self):
        profile = lifecycle_profile(
            preprod=True, mutate=self.with_browser_origin(self.BROWSER_ORIGIN))
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "synthetic-cookie"}):
            adapter = DualSessionServiceLifecycleAdapter(profile, FakeRunner)
        self.assertEqual(adapter.cleanup_client.headers["Origin"], self.BROWSER_ORIGIN)
        self.assertEqual(adapter.cleanup_client.headers["Referer"],
                         self.BROWSER_ORIGIN + "/")
        self.assertEqual(adapter.cleanup_client.headers["Cookie"], "synthetic-cookie")

    def test_trio_cleanup_write_client_carries_browser_context(self):
        profile = complete_invoke_profile(
            preprod=True, mutate=self.with_browser_origin(self.BROWSER_ORIGIN))
        environment = {
            "BCS_E2E_HUMAN_COOKIE": "synthetic-cookie",
            "BCS_E2E_DRIVER_BOT_TOKEN": "synthetic-driver",
            "BCS_E2E_MEMBER_BOT_TOKEN": "synthetic-member",
        }
        with patch.dict(os.environ, environment):
            adapter = DualSessionServiceCompleteAdapter(profile, FakeRunner)
        self.assertEqual(adapter.cleanup_client.headers["Origin"], self.BROWSER_ORIGIN)
        self.assertEqual(adapter.cleanup_client.headers["Referer"],
                         self.BROWSER_ORIGIN + "/")


if __name__ == "__main__":
    unittest.main()
