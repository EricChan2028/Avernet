"""Cross-environment WORKSPACE.cli-add-member product-CLI tests; no live request."""

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
from bcs_e2e.workspace_cli_member_contract import (
    RemoteWorkspaceCliMemberAdapter,
    CASE_ID,
    execute_workspace_cli_member_case,
    workspace_cli_member_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_workspace_cli_member_profile,
)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace-member-01"
SESSION = GROUP + ":01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-cli-member-profile-v1",
        "environment": {"tier": "local" if not gateway else "preprod",
                        "id": "workspace-member-local" if not gateway else "workspace-member-preprod",
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
        "relationships": {"group": "bounded-owned-bots-retained-cli-create-add-member",
                          "initial_session": "retained", "cleanup": "none",
                          "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "workspace-cli-member-pool", "exclusive_use_confirmed": True,
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


def add_member_output(*, group=GROUP, bot=MEMBER, prefix=True):
    text = ("✓ Member added to group:\n" if prefix else "")
    return (text + f"  Group: {group}\n  Bot: {bot}\n"
            f"  Role: participant\n").encode()


def read_output(*, group_id=GROUP, driver=DRIVER, status="active",
                kind="chat", visibility="private", participants=None):
    participants = participants or [
        {"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}
    ]
    return json.dumps({"id": group_id, "driver_bot": driver, "status": status,
                        "group_kind": kind, "visibility": visibility,
                        "participants": participants}).encode()


class WorkspaceCliMemberTests(unittest.TestCase):
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

    def run_case(self, *, gateway=False, added=None, read=None):
        profile = validate_workspace_cli_member_profile(profile_value(gateway=gateway))
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
            elif len(commands) == 2:
                raw = added if added is not None else add_member_output()
            else:
                raw = read if read is not None else read_output()
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            execution = execute_workspace_cli_member_case(
                RemoteWorkspaceCliMemberAdapter(profile, runner))
        return execution, runner, commands, contexts

    def test_case_guard_accepts_the_member_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/workspace_cli_member_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = workspace_cli_member_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], ["WORKSPACE.cli-add-member"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_local_sequence_adds_member_and_reads_it_back(self):
        execution, runner, commands, contexts = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(runner.executed,
                         ["member.group.create", "member.group.add-member",
                          "member.group.read"])
        self.assertEqual(commands[0][-7:],
                         ["create-group", "--driver", DRIVER,
                          "--participants", f"{DRIVER},{MEMBER}",
                          "--topic", "bcs-e2e-workspace-cli-member"])
        self.assertEqual(commands[1][-5:],
                         ["add-member", "--group", GROUP, "--bot-uuid", MEMBER])
        self.assertEqual(commands[2][-3:], ["get-group", "--id", GROUP])
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertIsNone(cookie)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["group_id"], GROUP)
        self.assertEqual(snapshot["steps"][0]["data"]["member_bot"], MEMBER)
        self.assertTrue(snapshot["steps"][0]["data"]["member_added_and_read_back"])
        self.assertEqual(snapshot["steps"][0]["data"]["readback_participants"],
                         [DRIVER, MEMBER])
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 3)
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_gateway_cookie_is_not_evidence(self):
        execution, _, _, contexts = self.run_case(gateway=True)
        self.assertEqual(execution.status, "PASS")
        for context, cookie in contexts:
            self.assertEqual(context["token"], "")
            self.assertEqual(cookie, COOKIE)
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))

    def test_bad_addition_outputs_block_without_partial_credit(self):
        cases = {
            "wrong-group": add_member_output(group="other"),
            "wrong-bot": add_member_output(bot="other"),
            "missing-prefix": add_member_output(prefix=False),
            "not-utf8": b"\xff\xfe Member added to group:\n",
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                execution, _, _, _ = self.run_case(added=raw)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)

    def test_bad_readback_outputs_block_without_partial_credit(self):
        cases = {
            "wrong-id": read_output(group_id="other"),
            "wrong-driver": read_output(driver="other"),
            "wrong-status": read_output(status="completed"),
            "missing-kind": read_output(kind=""),
            "missing-visibility": read_output(visibility=""),
            "member-absent": read_output(participants=[{"bot_uuid": DRIVER}]),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                execution, _, _, _ = self.run_case(read=raw)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)

    def test_group_creation_is_one_shot(self):
        profile = validate_workspace_cli_member_profile(profile_value())
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest, state_dir=self.state)

        def fake_capture(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, create_output(), b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            runner.execute("member.group.create")
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
                runner.execute("member.group.create")

    def test_member_addition_requires_the_created_group(self):
        profile = validate_workspace_cli_member_profile(profile_value())
        stub = type("StubRunner", (), {"execute": staticmethod(
            lambda *args, **kwargs: None)})()
        adapter = RemoteWorkspaceCliMemberAdapter(profile, stub)
        with self.assertRaises(RunStateError):
            adapter.add_workspace_member()
        with self.assertRaises(RunStateError):
            adapter.read_workspace_group_after_addition()

    def test_profile_rejects_unadmitted_member_effects(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "borrowed-bot": lambda value: value["actors"]["member"].update(dedicated=False),
            "private-bot": lambda value: value["actors"]["driver"].update(visibility="protected"),
            "session-family": lambda value: value["cli"].update(command_family="session"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_cli_member_profile(profile_value(mutate=mutate))


if __name__ == "__main__":
    unittest.main()
