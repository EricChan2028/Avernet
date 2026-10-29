"""Cross-environment WORKSPACE.list product-CLI tests; no live request."""

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
from bcs_e2e.workspace_cli_list_contract import (
    RemoteWorkspaceCliListAdapter,
    execute_workspace_cli_list_case,
    workspace_cli_list_adaptability,
    CASE_ID,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_workspace_cli_list_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace-list-01"
SESSION = GROUP + ":01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-cli-list-profile-v1",
        "environment": {"tier": "local" if not gateway else "preprod",
                         "id": "workspace-list-local" if not gateway else "workspace-list-preprod",
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
                 if not gateway else {"kind": "human-cookie-with-owned-driver-bot-token",
                                      "cookie_env": "BCS_E2E_HUMAN_COOKIE",
                                      "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}),
        "relationships": {"group": "bounded-owned-bots-retained-cli-create-list",
                           "initial_session": "retained", "cleanup": "none",
                           "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "workspace-cli-list-pool", "exclusive_use_confirmed": True,
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


def _group_row(group_id, coordinator, participants):
    return {"group_id": group_id, "label": "bcs-e2e-workspace-cli-list",
            "coordinator_bot": coordinator,
            "participants": [{"bot_uuid": bot} for bot in participants],
            "created_at": 1, "updated_at": 1, "group_kind": "chat",
            "group_strategy": "cooperative", "visibility": "private",
            "human_mention_notify_mode": "driver_bot_only"}


def list_output(include_target=True):
    items = []
    if include_target:
        items.append(_group_row(GROUP, DRIVER, [DRIVER, MEMBER]))
    items.append(_group_row("bcs_grp_unrelated", "unrelated", ["unrelated"]))
    return json.dumps({"items": items, "offset": 0, "returned": len(items),
                        "total": 10, "has_more": False}).encode()


class WorkspaceCliListTests(unittest.TestCase):
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

    def run_case(self, *, gateway=False, include_target=True):
        profile = validate_workspace_cli_list_profile(profile_value(gateway=gateway))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                     binary_sha256=self.digest, state_dir=self.state)
        commands = []
        contexts = []

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            environment = kwargs["environment"]
            context = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            contexts.append((json.loads(context.read_text()),
                              environment.get("BCS_COOKIE")))
            raw = create_output() if len(commands) == 1 else list_output(include_target)
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            execution = execute_workspace_cli_list_case(
                RemoteWorkspaceCliListAdapter(profile, runner))
        return execution, runner, commands, contexts

    def test_error_gap_carries_the_contract_message(self):
        execution, _, _, _ = self.run_case(include_target=False)
        self.assertEqual(execution.status, "BLOCKED")
        step = execution.snapshot()["steps"][0]
        self.assertEqual(step["error_type"], "RunStateError")
        self.assertTrue(step["error_gap"])

    def test_case_guard_accepts_the_list_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/workspace_cli_list_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = workspace_cli_list_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], ["WORKSPACE.list"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_local_sequence_finds_the_exact_group(self):
        execution, runner, commands, contexts = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(runner.executed, ["list.group.create", "list.group.list"])
        self.assertEqual(commands[1][-3:], ["list-groups", "--batch-size", "50"])
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertIsNone(cookie)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["group_id"], GROUP)
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 2)
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_preprod_human_cookie_is_not_admitted_for_list(self):
        def mutate(value):
            value["auth"] = {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
        with self.assertRaises(ConfigError):
            validate_workspace_cli_list_profile(profile_value(gateway=True, mutate=mutate))

    def test_gateway_owned_session_cookie_and_bot_token_are_not_evidence(self):
        execution, _, _, contexts = self.run_case(gateway=True)
        self.assertEqual(execution.status, "PASS")
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertEqual(cookie, COOKIE)
        self.assertNotIn(TOKEN, json.dumps(execution.snapshot(), sort_keys=True))
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))

    def test_preprod_owned_bot_tokens_only_is_not_admitted_for_list(self):
        def mutate(value):
            value["auth"] = {"kind": "owned-bot-tokens",
                             "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
        with self.assertRaises(ConfigError):
            validate_workspace_cli_list_profile(profile_value(gateway=True, mutate=mutate))

    def test_nonzero_total_without_exact_group_blocks(self):
        execution, _, _, _ = self.run_case(include_target=False)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)

    def test_list_operation_is_one_shot(self):
        profile = validate_workspace_cli_list_profile(profile_value())
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                     binary_sha256=self.digest, state_dir=self.state)

        def fake_capture(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, create_output(), b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            runner.execute("list.group.create")
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
                runner.execute("list.group.create")

    def test_profile_rejects_unadmitted_list_effects(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "borrowed-bot": lambda value: value["actors"]["member"].update(dedicated=False),
            "private-bot": lambda value: value["actors"]["driver"].update(visibility="protected"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_cli_list_profile(profile_value(mutate=mutate))


if __name__ == "__main__":
    unittest.main()
