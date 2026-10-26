"""Cross-environment WORKSPACE.cli-create-get contract tests; no live request."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError
from bcs_e2e.workspace_cli_create_get_contract import (
    RemoteWorkspaceCliCreateGetAdapter,
    CASE_ID,
    execute_workspace_cli_create_get_case,
    workspace_cli_create_get_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_workspace_cli_profile,
)
from test_workspace_cli_runner import DRIVER, MEMBER, GROUP, SESSION, profile_value


def cli_evidence(operation, *, good=True):
    value = {"transport": "PRODUCT_CLI", "operation": operation, "caller_role": "driver",
             "command_arguments": ["expected"], "http_method_declared": "POST",
             "route_declared": "/groups", "credential_kind": "owned_local_bot_token",
             "exit_status": 0, "elapsed_ms": 1.2,
             "fixture_effect": "RETAINED_DYNAMIC_GROUP_AND_INITIAL_SESSION",
             "remote_validation": "NOT_RUN", "grants_release_credit": False}
    if not good:
        value["fixture_effect"] = "CHANGED"
    return value


class ScriptedRunner:
    def __init__(self, *, create=None, fetched=None, create_evidence=None, get_evidence=None):
        self.create = create
        self.fetched = fetched
        self.create_evidence = create_evidence
        self.get_evidence = get_evidence
        self.calls: list[tuple[str, str | None]] = []

    def execute(self, operation, *, group_id=None):
        self.calls.append((operation, group_id))
        if operation == "group.create":
            return {"data": self.create, "evidence": self.create_evidence or cli_evidence(operation)}
        if operation == "group.get":
            return {"data": self.fetched, "evidence": self.get_evidence or cli_evidence(operation)}
        raise AssertionError("unexpected operation " + operation)


class WorkspaceCliAdapterTests(unittest.TestCase):
    def run_case(self, *, runner=None):
        profile = validate_workspace_cli_profile(profile_value())
        if runner is None:
            create = {"group_id": GROUP, "driver_bot": DRIVER,
                      "participants": [DRIVER, MEMBER], "session_id": SESSION}
            fetched = {"id": GROUP, "driver_bot": DRIVER, "status": "active",
                        "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}
            runner = ScriptedRunner(create=create, fetched=fetched)
        return execute_workspace_cli_create_get_case(
            RemoteWorkspaceCliCreateGetAdapter(profile, runner)), runner

    def test_case_guard_accepts_the_shared_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                 ROOT / "bcs_e2e/workspace_cli_runner.py",
                 ROOT / "bcs_e2e/workspace_cli_create_get_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = workspace_cli_create_get_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], ["WORKSPACE.cli-create-get"])
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_success_uses_exact_create_and_read_sequence(self):
        execution, runner = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(runner.calls, [("group.create", None), ("group.get", GROUP)])
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["group_id"], GROUP)
        self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 2)

    def test_wrong_business_result_blocks_without_credit(self):
        cases = {
            "wrong-driver": {"create": {"group_id": GROUP, "driver_bot": "wrong",
                                         "participants": [DRIVER, MEMBER], "session_id": SESSION}},
            "wrong-members": {"create": {"group_id": GROUP, "driver_bot": DRIVER,
                                          "participants": [DRIVER, "other"], "session_id": SESSION}},
            "missing-session": {"create": {"group_id": GROUP, "driver_bot": DRIVER,
                                             "participants": [DRIVER, MEMBER], "session_id": ""}},
            "read-wrong-id": {"fetched": {"id": "other", "driver_bot": DRIVER, "status": "active",
                                           "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}},
            "read-missing-member": {"fetched": {"id": GROUP, "driver_bot": DRIVER, "status": "active",
                                                 "participants": [{"bot_uuid": DRIVER}]}},
            "read-wrong-status": {"fetched": {"id": GROUP, "driver_bot": DRIVER, "status": "completed",
                                                "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}},
        }
        outputs = {"group_id": GROUP, "driver_bot": DRIVER,
                    "participants": [DRIVER, MEMBER], "session_id": SESSION}
        fetched = {"id": GROUP, "driver_bot": DRIVER, "status": "active",
                    "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]}
        for name, changes in cases.items():
            with self.subTest(name):
                create = {**outputs, **changes.get("create", {})}
                updated_fetch = {**fetched, **changes.get("fetched", {})}
                execution, _ = self.run_case(runner=ScriptedRunner(
                    create=create, fetched=updated_fetch))
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)

    def test_wrong_evidence_blocks(self):
        execution, _ = self.run_case(runner=ScriptedRunner(
            create={"group_id": GROUP, "driver_bot": DRIVER,
                     "participants": [DRIVER, MEMBER], "session_id": SESSION},
            fetched={"id": GROUP, "driver_bot": DRIVER, "status": "active",
                      "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}]},
            create_evidence=cli_evidence("group.create", good=False)))
        self.assertEqual(execution.status, "BLOCKED")

    def test_adapter_requires_create_before_read(self):
        profile = validate_workspace_cli_profile(profile_value())
        adapter = RemoteWorkspaceCliCreateGetAdapter(
            profile, ScriptedRunner(create={"group_id": GROUP, "driver_bot": DRIVER,
                                             "participants": [DRIVER, MEMBER], "session_id": SESSION}))
        with self.assertRaisesRegex(RunStateError, "must follow creation"):
            adapter.read_workspace_group()

    def test_profile_rejects_unadmitted_effects(self):
        cases = {
            "no-initial-session": lambda value: value["relationships"].update(initial_session="deleted"),
            "cleanup": lambda value: value["relationships"].update(cleanup="on-failure"),
            "private-bot": lambda value: value["actors"]["driver"].update(visibility="protected"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                value = profile_value()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_workspace_cli_profile(value)


if __name__ == "__main__":
    unittest.main()
