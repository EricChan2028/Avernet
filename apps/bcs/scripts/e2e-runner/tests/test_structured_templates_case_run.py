"""Command and receipt tests for the Structured template catalog lifecycle."""

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
from bcs_e2e.local_structured_public_contract import (
    CASE_ID,
    PUBLIC_DETAIL_PATH,
    PUBLIC_LIST_PATH,
    structured_templates_adaptability,
    structured_templates_case_plan,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from bcs_e2e.structured_templates_case_run import (
    REPORT_CONTRACT,
    dual_structured_templates_case_run,
)

RUN_ID = "structured-templates-command-1"


def base_profile(*, gateway: bool = False) -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="structured-templates-preprod" if gateway else "structured-templates-local",
        expected_bcs_env="pre" if gateway else "local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["observer"].update(
        kind="alisddbquery" if gateway else "local-sqlite",
        server="observ" if gateway else "owned-local",
        database="agentclawdb" if gateway else "owned-local",
        environment_binding_confirmed=True)
    value["policy"]["fixture_pool"] = (
        "structured-templates-preprod-pool" if gateway
        else "structured-templates-local-pool")
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    value["edge_case_bindings"] = {
        "structured_templates.list": {
            "method": "GET",
            "path": PUBLIC_LIST_PATH,
            "api_face": "internal-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
        },
        "structured_templates.detail": {
            "method": "GET",
            "path": PUBLIC_DETAIL_PATH,
            "api_face": "internal-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
        },
    }
    if gateway:
        value["environment"].update(
            tier="preprod", lifecycle="remote-attach", deployment="managed",
            provider="internal")
        value["entrypoint"].update(
            kind="gateway", origin="https://teamclawgw.example.com")
    return value


def expected_execution(*, status: str = "PASS") -> CaseExecution:
    plan = structured_templates_case_plan()
    requirement_ids = [row.requirement_id for row in plan.requirements]
    if status == "PASS":
        steps = tuple(
            StepOutcome(
                requirement_id=requirement_id,
                method=plan.adapter_method,
                status="PASS",
                data={
                    "selected_template_id": "single-bot-guided-answer",
                    "detail_template_id": "single-bot-guided-answer",
                    "detail_lang": "zh-CN",
                    "list_item_count": 2,
                    "detail_yaml_chars": 35,
                },
                evidence={"request": {"operation": operation}},
                error_type=None,
            )
            for requirement_id, operation in zip(
                requirement_ids, ["structured.templates.list", "structured.template.detail"])
        )
        execution = CaseExecution(
            case_id=CASE_ID,
            parent_id=plan.parent_id,
            status="PASS",
            steps=steps,
            completed_count=2,
            total_count=2,
            error_type=None,
            ready_to_execute=True,
        )
    else:
        steps = tuple(
            StepOutcome(
                requirement_id=requirement_id,
                method=plan.adapter_method,
                status="NOT_RUN",
                data=None,
                evidence={},
                error_type="RemoteStructuredTemplatesAdapterError",
            )
            for requirement_id in requirement_ids
        )
        execution = CaseExecution(
            case_id=CASE_ID,
            parent_id=plan.parent_id,
            status="BLOCKED",
            steps=steps,
            completed_count=0,
            total_count=2,
            error_type="RemoteStructuredTemplatesAdapterError",
            ready_to_execute=False,
        )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class StructuredTemplatesCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *, gateway: bool = False, status: str = "PASS"):
        profile = base_profile(gateway=gateway)
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution(status=status)
        with patch(
                "bcs_e2e.structured_templates_case_run.RemoteStructuredTemplatesAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.structured_templates_case_run.execute_structured_templates_case",
                lambda adapter: expected):
            report = dual_structured_templates_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected

    def test_local_run_writes_sanitized_receipts_and_case_scope(self):
        report, profile, expected = self.invoke()
        plan = structured_templates_case_plan()
        adaptive = structured_templates_adaptability()
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["parent_id"], expected.parent_id)
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["resource_disposition"],
                         "READ_ONLY_NO_BUSINESS_RESOURCE")
        self.assertEqual(report["case_scope"]["requirement_ids"],
                         [row.requirement_id for row in plan.requirements])
        self.assertEqual(report["case_scope"]["selected_obligations"],
                         adaptive["selected_obligations"])
        self.assertEqual(report["case_scope"]["public_paths"],
                         adaptive["public_paths"])
        self.assertEqual(read(self.state_dir / "structured-templates-profile.json"), profile)
        binding = read(self.state_dir / "structured-templates-binding.json")
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["credential_kind"], "human_cookie")
        self.assertEqual(read(self.state_dir / "structured-templates-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("human-cookie-private-value", serialized)

    def test_preprod_gateway_profile_uses_the_same_receipt_contract(self):
        report, _, _ = self.invoke(gateway=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", json.dumps(report, sort_keys=True))

    def test_blocked_run_is_reported_without_partial_credit(self):
        report, _, expected = self.invoke(status="BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(report["execution"]["total_count"], 2)
        self.assertEqual(report["execution"]["error_type"], expected.error_type)
        self.assertEqual(report["resource_disposition"],
                         "READ_ONLY_NO_BUSINESS_RESOURCE")
        self.assertEqual(read(self.state_dir / "structured-templates-run.json"), report)

    def test_profile_must_bind_the_exact_template_operations(self):
        profile = base_profile()
        profile["edge_case_bindings"].pop("structured_templates.detail")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(
                ConfigError, "structured-template profile must bind exactly"):
            dual_structured_templates_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_must_be_cookie_bound_only(self):
        for mutation in (
            lambda profile: profile["auth"]["header_env"].pop("Cookie"),
            lambda profile: profile["auth"]["header_env"].update(
                Authorization="BCS_E2E_DRIVER_BOT_TOKEN"),
        ):
            with self.subTest(mutation=mutation.__name__):
                profile = base_profile()
                mutation(profile)
                self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
                with self.assertRaisesRegex(
                        ConfigError, "auth|Cookie-bound|Bot Authorization"):
                    dual_structured_templates_case_run(
                        profile_path=self.profile_path,
                        state_dir=self.state_dir,
                        run_id=RUN_ID,
                        allow_write=True,
                        confirm_run_id=RUN_ID,
                    )

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_structured_templates_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_structured_templates_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_structured_templates_lifecycle(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-structured-templates-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_structured_templates_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
