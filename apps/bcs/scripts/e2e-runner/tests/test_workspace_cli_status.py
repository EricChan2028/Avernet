"""Cross-environment WORKSPACE.cli-status tests; no live request."""

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
from bcs_e2e.remote_workspace_cli_status_adapter import RemoteWorkspaceCliStatusAdapter
from bcs_e2e.run_state import RunStateError
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_workspace_cli_status_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError
from bcs_e2e.workspace_cli_status_contract import (
    CASE_ID,
    execute_workspace_cli_status_case,
    workspace_cli_status_adaptability,
)

DRIVER = "driver-bot"
MEMBER = "member-bot"
COMPLETION_GROUP = "bcs_grp_status-completion"
TERMINATION_GROUP = "bcs_grp_status-termination"
COMPLETION_SESSION = COMPLETION_GROUP + ":01020304"
TERMINATION_SESSION = TERMINATION_GROUP + ":05060708"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False, mutate=None):
    local = not gateway
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-cli-status-profile-v1",
        "environment": {"tier": "local" if local else "preprod",
                         "id": "workspace-status-local" if local else "workspace-status-preprod",
                         "expected_bcs_env": "local" if local else "pre"},
        "entrypoint": {"kind": "singlebox" if local else "gateway",
                       "origin": "http://127.0.0.1:21000" if local else "https://gateway.example.com",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                        "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                        "dedicated": True, "visibility": "public"},
        },
        "auth": ({"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
                 if local else {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}),
        "relationships": {"group": "bounded-owned-bots-retained-cli-status-termination",
                           "initial_session": "retained", "cleanup": "none",
                           "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "workspace-cli-status-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 65536,
                    "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "group", "api_face": "legacy"},
    }
    if mutate is not None:
        mutate(value)
    return value


def group_read(group_id: str, status: str):
    return {"id": group_id, "driver_bot": DRIVER, "status": status,
             "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}


def create_output(group_id: str, session_id: str):
    return (f"Group created:\n  ID: {group_id}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n  Session: {session_id}\n").encode()


class StatusScriptedRunner:
    def __init__(self, *, status_result=None, termination_result=None,
                 final_status="completed", final_termination="completed"):
        self.status_result = status_result
        self.termination_result = termination_result
        self.final_status = final_status
        self.final_termination = final_termination
        self.calls: list[tuple[str, str | None]] = []

    def execute(self, operation, *, group_id=None):
        self.calls.append((operation, group_id))
        if operation == "status.group.create":
            data = {"group_id": COMPLETION_GROUP, "driver_bot": DRIVER,
                    "participants": [DRIVER, MEMBER], "session_id": COMPLETION_SESSION}
        elif operation == "termination.group.create":
            data = {"group_id": TERMINATION_GROUP, "driver_bot": DRIVER,
                    "participants": [DRIVER, MEMBER], "session_id": TERMINATION_SESSION}
        elif operation == "status.group.read.active":
            data = group_read(group_id, "active")
        elif operation == "status.group.read.completed":
            data = group_read(group_id, self.final_status)
        elif operation == "status.group.status":
            data = self.status_result or {"updated": True, "group_id": group_id,
                                           "status": "completed", "changed_by": DRIVER}
        elif operation == "termination.group.read.active":
            data = group_read(group_id, "active")
        elif operation == "termination.group.terminate":
            data = self.termination_result or {"terminated": True, "group_id": group_id,
                                                 "status": "completed", "terminated_by": DRIVER}
        elif operation == "termination.group.read.completed":
            data = group_read(group_id, self.final_termination)
        else:
            raise AssertionError("unexpected operation " + operation)
        return {"data": data, "evidence": status_evidence(operation)}


def status_evidence(operation, *, good=True):
    value = {"transport": "PRODUCT_CLI", "operation": operation, "caller_role": "driver",
              "command_arguments": ["expected"],
              "http_method_declared": "POST", "route_declared": "/groups",
              "credential_kind": "owned_local_bot_token", "exit_status": 0,
              "elapsed_ms": 1.2,
              "fixture_effect": "RETAINED_DYNAMIC_GROUPS_AND_INITIAL_SESSIONS",
              "remote_validation": "NOT_RUN", "grants_release_credit": False}
    if not good:
        value["fixture_effect"] = "CHANGED"
    return value


class WorkspaceCliStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                     "BCS_E2E_HUMAN_COOKIE": COOKIE}

    def run_status_sequence(self, *, gateway=False, outputs=None):
        outputs = {} if outputs is None else outputs
        profile = validate_workspace_cli_status_profile(profile_value(gateway=gateway))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                     binary_sha256=self.digest, state_dir=self.state)
        commands = []
        contexts = []
        expected_calls = [
            ("status.group.create", None), ("status.group.read.active", COMPLETION_GROUP),
            ("status.group.status", COMPLETION_GROUP),
            ("status.group.read.completed", COMPLETION_GROUP),
            ("termination.group.create", None), ("termination.group.read.active", TERMINATION_GROUP),
            ("termination.group.terminate", TERMINATION_GROUP),
            ("termination.group.read.completed", TERMINATION_GROUP),
        ]

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            environment = kwargs["environment"]
            context_path = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            contexts.append((json.loads(context_path.read_text()),
                              environment.get("BCS_COOKIE")))
            operation, group_id = expected_calls[len(commands) - 1]
            raw = outputs.get(operation)
            if raw is None:
                if operation.endswith(".create"):
                    group_id, session_id = ((COMPLETION_GROUP, COMPLETION_SESSION)
                                             if operation == "status.group.create"
                                             else (TERMINATION_GROUP, TERMINATION_SESSION))
                    raw = create_output(group_id, session_id)
                elif operation == "status.group.status":
                    raw = json.dumps({"updated": True, "group_id": group_id,
                                       "status": "completed", "changed_by": DRIVER}).encode()
                elif operation == "termination.group.terminate":
                    raw = json.dumps({"terminated": True, "group_id": group_id,
                                       "status": "completed", "terminated_by": DRIVER}).encode()
                else:
                    status = ("active" if operation.endswith(".active")
                              else "completed")
                    raw = json.dumps(group_read(group_id, status)).encode()
            return subprocess.CompletedProcess(cmd, 0, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture) as capture:
            adapter = RemoteWorkspaceCliStatusAdapter(profile, runner)
            execution = execute_workspace_cli_status_case(adapter)
            self.assertEqual(execution.status, "BLOCKED" if outputs else "PASS")
            capture.assert_called()
        return execution, runner, commands, contexts

    def test_case_guard_accepts_the_status_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/remote_workspace_cli_status_adapter.py",
                  ROOT / "bcs_e2e/workspace_cli_status_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = workspace_cli_status_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], ["WORKSPACE.cli-status"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_local_product_cli_status_sequence(self):
        execution, runner, commands, contexts = self.run_status_sequence()
        self.assertEqual(runner.executed, [
            "status.group.create", "status.group.read.active", "status.group.status",
            "status.group.read.completed", "termination.group.create",
            "termination.group.read.active", "termination.group.terminate",
            "termination.group.read.completed"])
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["completion_group_id"], COMPLETION_GROUP)
        self.assertEqual(snapshot["steps"][0]["data"]["termination_group_id"], TERMINATION_GROUP)
        self.assertTrue(snapshot["steps"][0]["data"]["distinct_completion_and_termination_groups"])
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 8)
        for context, cookie in contexts:
            self.assertEqual(context["bot_uuid"], DRIVER)
            self.assertEqual(context["token"], TOKEN)
            self.assertIsNone(cookie)
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))

    def test_gateway_cookie_is_not_evidence(self):
        execution, runner, _, contexts = self.run_status_sequence(gateway=True)
        for context, cookie in contexts:
            self.assertEqual(context["token"], "")
            self.assertEqual(cookie, COOKIE)
        self.assertNotIn(COOKIE, json.dumps(execution.snapshot(), sort_keys=True))

    def test_status_slices_stay_one_shot(self):
        profile = validate_workspace_cli_status_profile(profile_value())
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                     binary_sha256=self.digest, state_dir=self.state)

        def fake_capture(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0,
                                                create_output(COMPLETION_GROUP, COMPLETION_SESSION),
                                                b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            runner.execute("status.group.create")
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
                runner.execute("status.group.create")

    def test_bad_observed_state_blocks_without_credit(self):
        cases = {
            "wrong-status": lambda outputs: outputs.update({
                "status.group.status": json.dumps({
                    "updated": True, "group_id": COMPLETION_GROUP,
                    "status": "closed", "changed_by": DRIVER}).encode()}),
            "wrong-termination": lambda outputs: outputs.update({
                "termination.group.terminate": json.dumps({
                    "terminated": True, "group_id": TERMINATION_GROUP,
                    "status": "active", "terminated_by": DRIVER}).encode()}),
            "bad-final-read": lambda outputs: outputs.update({
                "status.group.read.completed": json.dumps(
                    group_read(COMPLETION_GROUP, "active")).encode()}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                outputs = {}
                mutate(outputs)
                execution, _, _, _ = self.run_status_sequence(outputs=outputs)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)

    def test_adapter_requires_setup_before_reads(self):
        profile = validate_workspace_cli_status_profile(profile_value())
        adapter = RemoteWorkspaceCliStatusAdapter(profile, StatusScriptedRunner())
        with self.assertRaisesRegex(RunStateError, "requires its completion Group first"):
            adapter.read_completion_group_active()
        adapter.create_retained_completion_group()
        with self.assertRaisesRegex(RunStateError, "requires its termination Group first"):
            adapter.read_termination_group_active()

    def test_profile_rejects_unadmitted_status_effects(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "create-get-effect": lambda value: value["relationships"].update(
                group="bounded-owned-bots-retained-cli-create-get"),
            "private-bot": lambda value: value["actors"]["member"].update(visibility="protected"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_workspace_cli_status_profile(profile_value(mutate=mutate))

    def test_missing_credential_blocks_before_process(self):
        for gateway in (False, True):
            with self.subTest(gateway=gateway):
                profile = validate_workspace_cli_status_profile(profile_value(gateway=gateway))
                runner = WorkspaceCliRunner(profile, binary=self.binary,
                                            binary_sha256=self.digest, state_dir=self.state)
                with patch.dict(os.environ, {}, clear=True), \
                     patch("bcs_e2e.workspace_cli_runner.capture") as capture:
                    with self.assertRaisesRegex(
                            WorkspaceCliRunnerError,
                            "(?:owned Bot credential|Human Cookie) is absent"):
                        runner.execute("status.group.create")
                capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
