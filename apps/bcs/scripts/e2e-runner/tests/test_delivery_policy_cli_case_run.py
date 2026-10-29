"""Command and report tests for the dual Delivery Policy product-HTTP case."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
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
from bcs_e2e import delivery_policy_cli_case_run as delivery_policy_case_runner
from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.delivery_policy_cli_case_run import (
    DUAL_DELIVERY_POLICY_CASE_ID,
    DELIVERY_POLICY_PARENT,
    dual_delivery_policy_case_run,
    validate_delivery_policy_profile,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read

RUN_ID = "delivery-policy-run-1"
CASE_KEY = "delivery-policy"
HUMAN = "39464"
DRIVER_BOT = "20261026_xq102n2b"
MEMBER_BOT = "20261010_gekng5hk"
PREPROD_GATEWAY = "https://bcs-pre.alipay.com"
PREPROD_BROWSER_ORIGIN = "https://teamclaw-pre.alipay.com"
LOCAL_ORIGIN = "http://127.0.0.1:21001"
ENVIRONMENT = {
    "BCS_E2E_DRIVER_BOT_TOKEN": "synthetic-driver-bot-token-secret",
    "BCS_E2E_MEMBER_BOT_TOKEN": "synthetic-member-bot-token-secret",
    "BCS_E2E_HUMAN_COOKIE": "synthetic-human-cookie-secret",
}


def delivery_policy_profile(*, preprod=False):
    tier = "preprod" if preprod else "local"
    value = {
        "schema_version": 1,
        "contract": "dual-delivery-policy-profile-v1",
        "environment": {
            "tier": tier,
            "id": "delivery-policy-preprod" if preprod else "delivery-policy-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": {
            "kind": "gateway" if preprod else "singlebox",
            "origin": PREPROD_GATEWAY if preprod else LOCAL_ORIGIN,
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "human": {"user_id": HUMAN},
        "actors": {
            "driver": {"bot_id": DRIVER_BOT, "owner_id": HUMAN, "dedicated": True,
                       "visibility": "public"},
            "member": {"bot_id": MEMBER_BOT, "owner_id": HUMAN, "dedicated": True,
                       "visibility": "public"},
        },
        "auth": ({"kind": "human-cookie-with-owned-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                  "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN",
                  "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
                 if preprod else
                 {"kind": "owned-local-bot-tokens",
                  "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}),
        "policy": {
            "fixture_pool": ("dual-delivery-policy-admin-http" if preprod
                             else "owned-local-delivery-policy-admin-http"),
            "exclusive_use_confirmed": True, "timeout_seconds": 10,
            "max_response_bytes": 65536, "failure_retention_hours": 24,
        },
        "cli": {"transport": "product-http", "command_family": "delivery-policy",
                "api_face": "legacy"},
        "route_bindings": {
            row["operation"]: {
                "method": row["method"], "path": row["path"], "api_face": "legacy",
                "auth": row["binding_auth"], "response": "legacy-json",
                "traceparent": "disabled"}
            for row in delivery_policy_case_runner.STEP_ROWS},
    }
    if preprod:
        value["browser_origin"] = PREPROD_BROWSER_ORIGIN
    return value


def local_profile() -> dict:
    return validate_delivery_policy_profile(delivery_policy_profile())


def preprod_profile() -> dict:
    return validate_delivery_policy_profile(delivery_policy_profile(preprod=True))


def fake_execution() -> CaseExecution:
    step = StepOutcome(
        requirement_id="DELIVERY.restored",
        method="read_restored_policy",
        status="PASS",
        data={"policy_restored": True},
        evidence={"credential_kind": "owned_local_bot_token"},
        error_type=None,
    )
    execution = CaseExecution(
        case_id=DUAL_DELIVERY_POLICY_CASE_ID,
        parent_id=DELIVERY_POLICY_PARENT,
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    execution.validate()
    return execution


class FakeWriteClient:
    def __init__(self, profile):
        self.profile = profile

    def call(self, operation, **kwargs):
        raise AssertionError("fake WriteClient must not execute")


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class DeliveryPolicyCliCaseRunTests(unittest.TestCase):
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
        case_patch = {"adapter": FakeAdapter, "execute": lambda adapter: expected}
        with patch.object(delivery_policy_case_runner, "WriteClient", FakeWriteClient), \
             patch.dict(delivery_policy_case_runner._CASES[CASE_KEY], case_patch), \
             patch.dict(os.environ, ENVIRONMENT):
            report = dual_delivery_policy_case_run(
                profile_path=profile_path,
                cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256,
                case_key=CASE_KEY,
                state_dir=state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, state_dir

    def receipt_paths(self, state_dir: Path):
        prefix = f"delivery-policy-{CASE_KEY}"
        return (state_dir / f"{prefix}-profile.json",
                state_dir / f"{prefix}-binding.json",
                state_dir / f"{prefix}-run.json")

    def test_report_is_registered_in_the_case_inventory(self):
        rows = {row["case_id"] for row in migration_plan()[
            "cross_environment_product_cli_case_slices"]}
        self.assertIn(DUAL_DELIVERY_POLICY_CASE_ID, rows)

    def test_local_run_writes_bound_sanitized_receipts(self):
        report, profile, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], DUAL_DELIVERY_POLICY_CASE_ID)
        self.assertEqual(report["resource_disposition"],
                         "NO_BOT_CREATED_NO_GROUP_DELETED_POLICY_RESTORED")
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        profile_file, binding_file, run_file = self.receipt_paths(state_dir)
        self.assertEqual(read(profile_file), profile)
        self.assertEqual(read(binding_file)["case_id"], DUAL_DELIVERY_POLICY_CASE_ID)
        self.assertEqual(read(binding_file)["run_id"], RUN_ID)
        self.assertEqual(read(run_file), report)
        raw = (json.dumps(report, sort_keys=True)
               + profile_file.read_text() + binding_file.read_text() + run_file.read_text())
        for secret in ENVIRONMENT.values():
            self.assertNotIn(secret, raw)

    def test_preprod_report_records_cookie_kind_without_secret(self):
        report, _, state_dir = self.invoke(preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"],
                         "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        raw = json.dumps(report, sort_keys=True)
        self.assertNotIn("synthetic-human-cookie-secret", raw)
        self.assertNotIn("synthetic-driver-bot-token-secret", raw)
        profile_file, binding_file, run_file = self.receipt_paths(state_dir)
        receipts = profile_file.read_text() + binding_file.read_text() + run_file.read_text()
        for secret in ENVIRONMENT.values():
            self.assertNotIn(secret, receipts)
        self.assertIn("BCS_E2E_HUMAN_COOKIE", receipts)

    def test_permissions_and_fresh_state_are_enforced(self):
        state_dir = self.root / "state"
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_delivery_policy_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key=CASE_KEY,
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id="other")
        self.assertFalse(state_dir.exists())
        state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_delivery_policy_case_run(
                profile_path=profile_path, cli_binary=self.cli_binary,
                cli_binary_sha256=self.cli_sha256, case_key=CASE_KEY,
                state_dir=state_dir, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID)

    def test_cli_dispatch_uses_the_dual_delivery_policy_runner(self):
        state_dir = self.root / "dispatch"
        profile_path = self.root / "dispatch-profile.json"
        profile_path.write_text(json.dumps(local_profile()), encoding="utf-8")
        arguments = [
            "dual-delivery-policy-run",
            "--profile", str(profile_path),
            "--case", CASE_KEY,
            "--cli-binary", str(self.cli_binary),
            "--cli-binary-sha256", self.cli_sha256,
            "--state-dir", str(state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch("bcs_e2e.cli.dual_delivery_policy_case_run",
                   return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=profile_path,
            cli_binary=self.cli_binary,
            cli_binary_sha256=self.cli_sha256,
            case_key=CASE_KEY,
            state_dir=state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")

    def test_preprod_driver_probes_present_the_declared_trio(self):
        bot_profile = delivery_policy_case_runner.RemoteLocalDeliveryPolicyAdapter._bot_profile(
            preprod_profile())
        auth = bot_profile["auth"]
        self.assertEqual(auth["kind"], "authenticated-product")
        self.assertEqual(auth["authorization_scheme"], "bearer")
        self.assertEqual(set(auth["header_env"]), {"Cookie", "Authorization"})
        self.assertEqual(auth["header_env"]["Cookie"], "BCS_E2E_HUMAN_COOKIE")
        self.assertEqual(auth["header_env"]["Authorization"], "BCS_E2E_DRIVER_BOT_TOKEN")
        human_auth = delivery_policy_case_runner.RemoteLocalDeliveryPolicyAdapter._human_profile(
            preprod_profile())["auth"]
        self.assertEqual(human_auth["header_env"], {"Cookie": "BCS_E2E_HUMAN_COOKIE"})

    def test_local_driver_probes_keep_the_singlebox_bot_token_header(self):
        bot_profile = delivery_policy_case_runner.RemoteLocalDeliveryPolicyAdapter._bot_profile(
            local_profile())
        auth = bot_profile["auth"]
        self.assertEqual(auth["header_env"], {"X-BCS-Bot-Token": "BCS_E2E_DRIVER_BOT_TOKEN"})
        self.assertNotIn("authorization_scheme", auth)

    def test_step_evidence_labels_the_declared_credential_kind(self):
        with patch.dict(os.environ, ENVIRONMENT):
            remote = delivery_policy_case_runner.RemoteLocalDeliveryPolicyAdapter(
                preprod_profile())
            self.assertEqual(remote._credential_kind(),
                             "human_cookie_with_owned_driver_bot_token")
            local = delivery_policy_case_runner.RemoteLocalDeliveryPolicyAdapter(
                local_profile())
            self.assertEqual(local._credential_kind(), "owned_local_bot_token")

if __name__ == "__main__":
    unittest.main()
