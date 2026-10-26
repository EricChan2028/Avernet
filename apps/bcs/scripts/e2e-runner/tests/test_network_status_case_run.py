"""Command and receipt tests for the actor-status lifecycle."""

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
from bcs_e2e.local_network_actor_status_contract import (
    CASE_ID,
    network_actor_status_case_plan,
)
from bcs_e2e.network_status_case_run import (
    CREDENTIAL_KIND,
    RESOURCE_DISPOSITION,
    dual_network_actor_status_case_run,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.remote_network_adapters import RemoteNetworkActorStatusAdapter
from bcs_e2e.run_state import read

RUN_ID = "network-actor-status-run-1"


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="network-status-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "network-status-local-pool"
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    value["edge_case_bindings"] = {
        "network_actor_status.read": {
            "method": "GET",
            "path": "/openapi/v1/collaboration/bots/driver-bot",
            "api_face": "public-v1", "auth": "human_cookie",
            "response": "public-envelope", "traceparent": "disabled",
            "target_actor_id": "driver-bot",
        },
        "network_actor_status.write": {
            "method": "PATCH",
            "path": "/openapi/v1/collaboration/bots/driver-bot",
            "api_face": "public-v1", "auth": "human_cookie",
            "response": "public-envelope", "traceparent": "disabled",
            "target_actor_id": "driver-bot",
        },
    }
    return value


def profile_value() -> dict:
    return base_profile()


def expected_execution() -> CaseExecution:
    plan = network_actor_status_case_plan()
    step = StepOutcome(
        requirement_id=plan.requirement.requirement_id,
        method=plan.requirement.adapter_method,
        status="PASS",
        data={
            "target_actor_id": "driver-bot",
            "before_status": "online",
            "hidden_status": "hidden",
            "online_status": "online",
        },
        evidence={},
        error_type=None,
    )
    execution = CaseExecution(
        case_id=plan.case_id,
        parent_id=plan.parent_id,
        status="PASS",
        steps=(step,),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class NetworkActorStatusCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution()
        with patch(
                "bcs_e2e.network_status_case_run.RemoteNetworkActorStatusAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.network_status_case_run.execute_network_actor_status_case",
                lambda adapter: expected):
            report = dual_network_actor_status_case_run(
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
        self.assertEqual(report["case_id"], expected.case_id)
        self.assertEqual(report["resource_disposition"], RESOURCE_DISPOSITION)
        self.assertEqual(report["credential_kind"], CREDENTIAL_KIND)
        self.assertEqual(report["case_scope"]["requirement_id"], "NETWORK.actor-status")
        self.assertEqual(report["case_scope"]["selected_obligations"],
                         ["before-online", "hidden", "online", "restored"])
        self.assertEqual(read(state_dir / "network-actor-status-profile.json"), profile)
        self.assertEqual(
            read(state_dir / "network-actor-status-binding.json")["case_id"],
            expected.case_id)
        self.assertEqual(
            read(state_dir / "network-actor-status-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("human-cookie-private-value", serialized)

    def test_profile_must_bind_the_exact_case_operations(self):
        profile = profile_value()
        profile["edge_case_bindings"].pop("network_actor_status.write")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "actor-status profile must bind exactly"):
            dual_network_actor_status_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_must_not_carry_a_bot_authorization_reference(self):
        profile = profile_value()
        profile["auth"]["header_env"]["Authorization"] = "BCS_E2E_DRIVER_BOT_TOKEN"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Cookie-bound"):
            dual_network_actor_status_case_run(
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
            dual_network_actor_status_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_network_actor_status_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_network_actor_status_lifecycle(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-network-status-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_network_actor_status_case_run",
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
