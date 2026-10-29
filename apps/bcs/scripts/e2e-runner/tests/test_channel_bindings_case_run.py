"""Command and receipt tests for the Channel-binding lifecycle."""

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
from bcs_e2e.channel_bindings_case_run import (
    BINDING_KEYS,
    CREDENTIAL_KIND,
    RESOURCE_DISPOSITION,
    dual_channel_bindings_case_run,
)
from bcs_e2e.cli import main
from bcs_e2e.local_channel_public_contract import channel_bindings_case_plan
from bcs_e2e.profile import ConfigError
from bcs_e2e.remote_network_adapters import RemoteChannelBindingsAdapter
from bcs_e2e.route_bindings import channel_bindings_by_target_path
from bcs_e2e.run_state import read

RUN_ID = "channel-bindings-run-1"


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="channel-bindings-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "channel-bindings-local-pool"
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    value["edge_case_bindings"] = {
        "channel_bindings.create_denied": {
            "method": "POST", "path": "/openapi/v1/collaboration/channels/bindings",
            "api_face": "public-v1", "auth": "human_cookie",
            "response": "public-envelope", "traceparent": "disabled",
            "target_actor_id": "driver-bot",
        },
        "channel_bindings.list": {
            "method": "GET", "path": "/openapi/v1/collaboration/channels/bindings",
            "api_face": "public-v1", "auth": "human_cookie",
            "response": "public-envelope", "traceparent": "disabled",
        },
        "channel_bindings.by_target": {
            "method": "GET", "path": channel_bindings_by_target_path(value),
            "api_face": "public-v1", "auth": "human_cookie",
            "response": "public-envelope", "traceparent": "disabled",
            "target_actor_id": "driver-bot",
        },
    }
    return value


def profile_value() -> dict:
    value = base_profile()
    if value["entrypoint"].get("kind") == "singlebox":
        value["entrypoint"].update(kind="singlebox")
    return value


def expected_execution() -> CaseExecution:
    plan = channel_bindings_case_plan()
    steps = tuple(
        StepOutcome(
            requirement_id=row.requirement_id,
            method="verify_uninstalled_channel_binding_is_rejected",
            status="PASS",
            data={"requirement_id": row.requirement_id},
            evidence={},
            error_type=None,
        )
        for row in plan.requirements
    )
    execution = CaseExecution(
        case_id=plan.case_id,
        parent_id=plan.parent_id,
        status="PASS",
        steps=steps,
        completed_count=len(steps),
        total_count=len(steps),
        error_type=None,
        ready_to_execute=True,
    )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class ChannelBindingsCaseRunTests(unittest.TestCase):
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
                "bcs_e2e.channel_bindings_case_run.RemoteChannelBindingsAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.channel_bindings_case_run.execute_channel_bindings_case",
                lambda adapter: expected):
            report = dual_channel_bindings_case_run(
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
        self.assertEqual(report["parent_id"], expected.parent_id)
        self.assertEqual(report["resource_disposition"], RESOURCE_DISPOSITION)
        self.assertEqual(report["credential_kind"], CREDENTIAL_KIND)
        self.assertEqual(report["case_scope"]["requirement_ids"],
                         [row.requirement_id for row in expected.steps])
        self.assertEqual(report["case_scope"]["blocked_requirement_ids"],
                         ["CHANNEL.pause-missing", "CHANNEL.delete-missing"])
        self.assertEqual(
            report["case_scope"]["requirement_ids"],
            [step.requirement_id for step in expected.steps])
        self.assertEqual(read(state_dir / "channel-bindings-profile.json"), profile)
        self.assertEqual(
            read(state_dir / "channel-bindings-binding.json")["case_id"],
            expected.case_id)
        self.assertEqual(
            read(state_dir / "channel-bindings-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("channel-binding-cookie", serialized)

    def test_profile_must_bind_the_exact_case_operations(self):
        profile = profile_value()
        profile["edge_case_bindings"].pop("channel_bindings.by_target")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "channel-bindings profile must bind exactly"):
            dual_channel_bindings_case_run(
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
            dual_channel_bindings_case_run(
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
            dual_channel_bindings_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_channel_bindings_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_channel_bindings_lifecycle(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-channel-bindings-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_channel_bindings_case_run",
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
