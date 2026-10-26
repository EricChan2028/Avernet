"""Cross-environment WORKSPACE.detail product-CLI tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.workspace_cli_detail_contract import (
    RemoteWorkspaceCliDetailAdapter,
    CASE_ID,
    execute_workspace_cli_detail_case,
    workspace_cli_detail_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_workspace_cli_detail_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace-detail-01"
SESSION = GROUP + ":01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-cli-detail-profile-v1",
        "environment": {"tier": "local" if not gateway else "preprod",
                        "id": "workspace-detail-local" if not gateway else "workspace-detail-preprod",
                        "expected_bcs_env": "local" if not gateway else "pre"},
        "entrypoint": {"kind": "singlebox" if not gateway else "gateway",
                       "origin": "http://127.0.0.1:21000" if not gateway else "https://gateway.example.com",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {"driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"},
                   "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"}},
        "auth": ({"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
                 if not gateway else {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}),
        "relationships": {"group": "bounded-owned-bots-retained-cli-create-detail",
                          "initial_session": "retained", "cleanup": "none",
                          "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "workspace-cli-detail-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 65536,
                    "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "group", "api_face": "legacy"},
    }
    if mutate is not None:
        mutate(value)
    return value


def create_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n  Session: {SESSION}\n").encode()


def detail_output(*, group_id=GROUP, driver=DRIVER, status="active",
                   kind="chat", visibility="private", participants=None):
    participants = participants or [
        {"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}
    ]
    return json.dumps({"id": group_id, "driver_bot": driver, "status": status,
                        "group_kind": kind, "visibility": visibility,
                        "participants": participants}).encode()


class WorkspaceCliDetailTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}

    def run_case(self, *, gateway=False, detail=None):
        profile = validate_workspace_cli_detail_profile(profile_value(gateway=gateway))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)
        commands = []
        contexts = []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            environment = kwargs["environment"]
            context_path = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            contexts.append((json.loads(context_path.read_text()),
                              environment.get("BCS_COOKIE")))
            if len(commands) == 1:
                raw = create_output()
            else:
                raw = detail if detail is not None else detail_output()
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            execution = execute_workspace_cli_detail_case(
                RemoteWorkspaceCliDetailAdapter(profile, runner))
        return execution, runner, commands, contexts

    def test_case_guard_accepts_the_detail_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/workspace_cli_detail_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = workspace_cli_detail_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], ["WORKSPACE.detail"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_local_sequence_preserves_exact_detail(self):
        execution, runner, commands, contexts = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(runner.executed, ["detail.group.create", "detail.group.get"])
        self.assertEqual(commands[1][-3:], ["get-group", "--id", GROUP])
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertIsNone(cookie)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["group_id"], GROUP)
        self.assertEqual(snapshot["steps"][0]["data"]["group_kind"], "chat")
        self.assertEqual(snapshot["steps"][0]["data"]["visibility"], "private")
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 2)
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_gateway_cookie_is_not_evidence(self):
        execution, _, _, contexts = self.run_case(gateway=True)
        self.assertEqual(execution.status, "PASS")
        for context, cookie in contexts:
            self.assertEqual(context["token"], "")
            self.assertEqual(cookie, COOKIE)
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))

    def test_missing_detail_fields_block_without_partial_credit(self):
        cases = {
            "wrong-id": detail_output(group_id="other"),
            "wrong-driver": detail_output(driver="other"),
            "wrong-status": detail_output(status="completed"),
            "missing-kind": detail_output(kind=""),
            "missing-visibility": detail_output(visibility=""),
            "missing-participant": detail_output(participants=[{"bot_uuid": DRIVER}]),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                execution, _, _, _ = self.run_case(detail=raw)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)

    def test_detail_operation_is_one_shot(self):
        profile = validate_workspace_cli_detail_profile(profile_value())
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)

        def fake_capture(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, create_output(), b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            runner.execute("detail.group.create")
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
                runner.execute("detail.group.create")

    def test_profile_rejects_unadmitted_detail_effects(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "borrowed-bot": lambda value: value["actors"]["member"].update(dedicated=False),
            "private-bot": lambda value: value["actors"]["driver"].update(visibility="protected"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_cli_detail_profile(profile_value(mutate=mutate))


if __name__ == "__main__":
    unittest.main()
