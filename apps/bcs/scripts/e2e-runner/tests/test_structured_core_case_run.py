"""Command and receipt tests for the dual structured-core lifecycle."""

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
from bcs_e2e.local_structured_core_contract import (
    CASE_ID,
    FIXTURE_EFFECT,
    PUBLIC_OPERATIONS,
    structured_core_case_plan,
)
from bcs_e2e.structured_core_case_run import (
    BLOCKED_RESOURCE_DISPOSITION,
    CREDENTIAL_KIND,
    dual_structured_core_case_run,
    SUCCESS_RESOURCE_DISPOSITION,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.route_bindings import _EDGE_CASES
from bcs_e2e.run_state import read

RUN_ID = "structured-core-run-1"


def operation_keys():
    return tuple(dict.fromkeys(
        ('structured.group.create', 'structured.group.read',
         'structured.definition.read', 'structured.definition.patch',
         'structured.definition.upgrade.missing', 'structured.run.start',
         'structured.run.read', 'structured.run.graph.read',
         'structured.run.node.read', 'structured.run.pending.human.nodes',
         'structured.run.human.response.denied', 'structured.run.cancel',
         'structured.run.rerun.aborted.denied', 'structured.group.delete')))


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="structured-core-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "structured-core-local-pool"
    value["auth"]["header_env"] = {
        "Authorization": "BCS_E2E_DRIVER_BOT_TOKEN",
        "Cookie": "BCS_E2E_HUMAN_COOKIE",
    }
    bindings = {}
    for operation in operation_keys():
        method, path, auth = _EDGE_CASES[operation]
        binding = {
            "method": method,
            "path": path,
            "api_face": "internal-v1",
            "auth": auth,
            "response": "public-envelope",
            "traceparent": "disabled",
        }
        if auth == "authenticated_bot":
            binding["caller_actor_id"] = "driver-bot"
        bindings[operation] = binding
    value["edge_case_bindings"] = bindings
    return value


def profile_value() -> dict:
    return base_profile()


def expected_execution(*, status: str = "PASS") -> CaseExecution:
    plan = structured_core_case_plan()
    steps = []
    for requirement in plan.requirements:
        steps.append(StepOutcome(
            requirement_id=requirement.requirement_id,
            method=requirement.adapter_method,
            status="PASS" if status == "PASS" else "NOT_RUN",
            data={"id": "core"} if status == "PASS" else None,
            evidence={} if status == "PASS" else {},
            error_type=None,
        ))
    if status == "PASS":
        execution = CaseExecution(
            case_id=plan.case_id,
            parent_id=plan.parent_id,
            status="PASS",
            steps=tuple(steps),
            completed_count=len(steps),
            total_count=len(steps),
            error_type=None,
            ready_to_execute=True,
        )
    else:
        execution = CaseExecution(
            case_id=plan.case_id,
            parent_id=plan.parent_id,
            status="BLOCKED",
            steps=tuple(steps),
            completed_count=0,
            total_count=len(steps),
            error_type="RemoteStructuredCoreAdapterError",
            ready_to_execute=False,
        )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class StructuredCoreCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, status: str = "PASS"):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution(status=status)
        with patch(
                "bcs_e2e.structured_core_case_run.RemoteStructuredCoreAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.structured_core_case_run.execute_structured_core_case",
                lambda adapter: expected):
            report = dual_structured_core_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected, self.state_dir

    def test_local_run_writes_sanitized_receipts_and_case_scope(self):
        report, profile, expected, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["resource_disposition"], SUCCESS_RESOURCE_DISPOSITION)
        self.assertEqual(
            report["resource_disposition"],
            FIXTURE_EFFECT)
        self.assertEqual(report["credential_kind"], CREDENTIAL_KIND)
        self.assertEqual(len(report["case_scope"]["requirement_ids"]), 15)
        self.assertEqual(len(report["case_scope"]["selected_obligations"]), 15)
        self.assertEqual(len(report["case_scope"]["public_operations"]), 14)
        self.assertEqual(report["case"]["completed_count"], 15)
        self.assertEqual(read(state_dir / "structured-core-profile.json"), profile)
        binding = read(state_dir / "structured-core-binding.json")
        self.assertEqual(binding["case_id"], expected.case_id)
        self.assertEqual(binding["planned_resource_disposition"], FIXTURE_EFFECT)
        self.assertEqual(read(state_dir / "structured-core-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("human-cookie-private-value", serialized)
        self.assertNotIn("driver-bot-private-token", serialized)

    def test_blocked_run_never_claims_finalization(self):
        report, profile, expected, state_dir = self.invoke(status="BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["resource_disposition"], BLOCKED_RESOURCE_DISPOSITION)
        self.assertEqual(report["case"]["completed_count"], 0)
        self.assertEqual(read(state_dir / "structured-core-run.json"), report)

    def test_profile_must_bind_the_exact_case_operations(self):
        profile = profile_value()
        profile["edge_case_bindings"].pop("structured.group.delete")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "structured-core profile must bind exactly"):
            dual_structured_core_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_must_bind_the_bot_authorization_reference(self):
        profile = profile_value()
        del profile["auth"]["header_env"]["Authorization"]
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "requires an Authorization reference"):
            dual_structured_core_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_structured_core_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_structured_core_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_structured_core_lifecycle(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-structured-core-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_structured_core_case_run",
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
