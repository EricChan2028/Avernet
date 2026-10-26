"""Command and receipt tests for the Event Webhook subscription lifecycle."""

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

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.local_webhook_subscription_contract import (
    CASE_ID,
    COMMUNITY_ONLY_CLOSURES,
    webhook_subscription_adaptability,
    webhook_subscription_case_plan,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from bcs_e2e.webhook_subscription_case_run import (
    REPORT_CONTRACT,
    dual_webhook_subscription_case_run,
)

RUN_ID = "webhook-subscription-run-1"
RECEIVER_ORIGIN = "https://receiver.example.com"


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="preprod-webhook", expected_bcs_env="pre",
        tier="preprod", lifecycle="remote-attach", deployment="managed",
        provider="internal")
    value["entrypoint"].update(
        kind="gateway", origin="https://teamclawgw.example.com",
        api_face="public-v1", manifest_face="internal-v1")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["observer"].update(kind="alisddbquery", server="observ",
                             database="agentclawdb",
                             environment_binding_confirmed=True)
    value["auth"] = {
        "principal_signing_key_env": "BCS_E2E_PRINCIPAL_SIGNING_KEY_VALUE",
    }
    value["policy"].update(
        fixture_pool="preprod-webhook-pool",
        exclusive_use_confirmed=True,
        remote_effect_review={
            "isolated_test_runtime": True,
            "no_operational_channels": True,
            "implicit_bot_execution_allowed": True,
        })
    value["edge_case_bindings"] = {
        "webhook.subscription.list": {
            "method": "GET", "path": "/openapi/v1/collaboration/event-subscriptions?scope_type=group&scope_id={group_id}&limit=20",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.subscription.read": {
            "method": "GET", "path": "/openapi/v1/collaboration/event-subscriptions/{subscription_id}",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.subscription.test": {
            "method": "POST", "path": "/openapi/v1/collaboration/event-subscriptions/{subscription_id}:test",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.subscription.disable": {
            "method": "PATCH", "path": "/openapi/v1/collaboration/event-subscriptions/{subscription_id}",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.subscription.enable": {
            "method": "PATCH", "path": "/openapi/v1/collaboration/event-subscriptions/{subscription_id}",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.subscription.deliveries.list": {
            "method": "GET", "path": "/openapi/v1/collaboration/event-subscriptions/{subscription_id}/deliveries?status=succeeded&limit=20",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.delivery.read": {
            "method": "GET", "path": "/openapi/v1/collaboration/event-deliveries/{delivery_id}",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
        "webhook.subscription.delete": {
            "method": "DELETE", "path": "/openapi/v1/collaboration/event-subscriptions/{subscription_id}?revision=3",
            "api_face": "public-v1", "auth": "gateway_principal",
            "response": "public-envelope", "traceparent": "disabled"},
    }
    return value


def expected_execution(*, status: str = "PASS") -> CaseExecution:
    plan = webhook_subscription_case_plan()
    requirement_ids = list(plan.requirement_ids)
    steps = []
    for index, requirement_id in enumerate(requirement_ids):
        if status == "PASS":
            steps.append(
                StepOutcome(
                    requirement_id=requirement_id,
                    method=plan.adapter_methods[index],
                    status="PASS",
                    data={"requirement_id": requirement_id},
                    evidence={"operation": plan.adapters[requirement_id]},
                    error_type=None,
                ))
        else:
            steps.append(
                StepOutcome(
                    requirement_id=requirement_id,
                    method=plan.adapter_methods[index],
                    status="NOT_RUN",
                    data=None,
                    evidence={},
                    error_type="RemoteWebhookSubscriptionAdapterError",
                ))
    completed = len(steps) if status == "PASS" else 0
    error_type = "RemoteWebhookSubscriptionAdapterError" if status != "PASS" else None
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id=plan.parent_id,
        status=status,
        steps=tuple(steps),
        completed_count=completed,
        total_count=len(steps),
        error_type=error_type,
        ready_to_execute=status == "PASS",
    )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile, receiver_origin):
        self.profile = profile
        self.receiver_origin = receiver_origin


class WebhookSubscriptionCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *, status: str = "PASS"):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution(status=status)
        with patch(
                "bcs_e2e.webhook_subscription_case_run.RemoteWebhookSubscriptionAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.webhook_subscription_case_run.execute_webhook_subscription_case",
                lambda adapter: expected):
            report = dual_webhook_subscription_case_run(
                profile_path=self.profile_path,
                receiver_origin=RECEIVER_ORIGIN,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected

    def test_local_run_writes_sanitized_receipts_and_case_scope(self):
        report, profile, expected = self.invoke()
        plan = webhook_subscription_case_plan()
        adaptive = webhook_subscription_adaptability()
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["parent_id"], plan.parent_id)
        self.assertEqual(report["credential_kind"], "gateway_principal")
        self.assertEqual(report["receiver_origin"], RECEIVER_ORIGIN)
        self.assertEqual(report["resource_disposition"], "FINALIZED_WEBHOOK_SUBSCRIPTION_GROUP")
        self.assertEqual(report["case_scope"]["requirement_ids"],
                         list(plan.requirement_ids))
        self.assertEqual(report["case_scope"]["selected_obligations"],
                         adaptive["selected_obligations"])
        self.assertEqual(report["case_scope"]["community_only_closures"],
                         list(COMMUNITY_ONLY_CLOSURES))
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / "webhook-subscription-profile.json"), profile)
        binding = read(self.state_dir / "webhook-subscription-binding.json")
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["credential_kind"], "gateway_principal")
        self.assertEqual(binding["receiver_origin"], RECEIVER_ORIGIN)
        self.assertEqual(read(self.state_dir / "webhook-subscription-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_PRINCIPAL_SIGNING_KEY_VALUE", serialized)
        self.assertNotIn("principal-key-private-value", serialized)

    def test_blocked_run_never_claims_finalization(self):
        report, profile, expected = self.invoke(status="BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(report["execution"]["total_count"], len(expected.steps))
        self.assertEqual(report["resource_disposition"],
                         "UNKNOWN_UNFINALIZED_WEBHOOK_SUBSCRIPTION_GROUP")
        self.assertEqual(report["case_scope"]["community_only_closures"],
                         list(COMMUNITY_ONLY_CLOSURES))
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / "webhook-subscription-run.json"), report)

    def test_profile_must_bind_the_exact_lifecycle_operations(self):
        profile = base_profile()
        profile["edge_case_bindings"].pop("webhook.subscription.delete")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(
                ConfigError, "webhook-subscription profile must bind exactly"):
            dual_webhook_subscription_case_run(
                profile_path=self.profile_path,
                receiver_origin=RECEIVER_ORIGIN,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_must_carry_the_principals_signing_key_reference(self):
        profile = base_profile()
        profile["auth"].pop("principal_signing_key_env")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(
                ConfigError, "principal signing key reference"):
            dual_webhook_subscription_case_run(
                profile_path=self.profile_path,
                receiver_origin=RECEIVER_ORIGIN,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_webhook_subscription_case_run(
                profile_path=self.profile_path,
                receiver_origin=RECEIVER_ORIGIN,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_webhook_subscription_case_run(
                profile_path=self.profile_path,
                receiver_origin=RECEIVER_ORIGIN,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_webhook_subscription_lifecycle(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-webhook-subscription-run",
            "--profile", str(self.profile_path),
            "--receiver-origin", RECEIVER_ORIGIN,
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_webhook_subscription_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            receiver_origin=RECEIVER_ORIGIN,
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
