"""Cross-environment FRIEND.cli-accept product-CLI contract tests; no live request."""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.friend_cli_profile import ConfigError, validate_friend_cli_profile
from bcs_e2e.local_friend_cli_accept_contract import (
    CASE_ID,
    execute_friend_cli_accept_case,
    friend_cli_accept_adaptability,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.remote_friend_cli_accept_adapter import RemoteFriendCliAcceptAdapter
from bcs_e2e.run_state import RunStateError


DRIVER = "driver-bot"
MEMBER = "member-bot"
REQUEST_ID = "request-1"


def base_profile(*, gateway=False):
    local = not gateway
    value = {
        "schema_version": 1,
        "contract": "dual-friend-cli-profile-v1",
        "environment": {
            "tier": "local" if local else "preprod",
            "id": "friend-cli-local" if local else "friend-cli-preprod",
            "expected_bcs_env": "local" if local else "pre",
        },
        "entrypoint": {
            "kind": "singlebox" if local else "gateway",
            "origin": "http://127.0.0.1:21000" if local else "https://gateway.example.com",
            "api_face": "legacy",
            "manifest_face": "legacy",
        },
        "human": {"user_id": "owner-1"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                       "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                       "dedicated": True, "visibility": "protected"},
        },
        "auth": (
            {"kind": "owned-local-bot-tokens",
             "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
             "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN"}
            if local else
            {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
        ),
        "relationships": {
            "friendship": "bounded-owned-bot-cli-acceptance",
            "baseline": "no-pending-driver-member",
            "visibility": "member-protected",
            "retention": "request-history-and-friendship",
        },
        "policy": {"fixture_pool": "friend-cli-pool", "exclusive_use_confirmed": True,
                   "timeout_seconds": 10, "max_response_bytes": 65536,
                   "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "friend",
                "api_face": "legacy"},
    }
    return validate_friend_cli_profile(value)


def cli_record(operation, role, arguments, kind="human_cookie"):
    return {
        "transport": "PRODUCT_CLI", "operation": operation, "caller_role": role,
        "command_arguments": list(arguments), "http_method_declared": "POST",
        "route_declared": "/friends/request", "credential_kind": kind,
        "exit_status": 0, "elapsed_ms": 1.2, "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


class ScriptedRunner:
    def __init__(self, declared=None, outputs=None):
        self.declared = declared or {}
        self.outputs = outputs or {}
        self.calls: list[tuple[str, str | None]] = []

    def execute(self, operation, *, request_id=None):
        self.calls.append((operation, request_id))
        if operation not in self.outputs:
            raise AssertionError("unexpected operation " + operation)
        return {"data": self.outputs[operation],
                "evidence": self.declared.get(operation, cli_record(
                    operation, self._role(operation),
                    self._arguments(operation)))}

    @staticmethod
    def _role(operation):
        return "member" if operation in {"friend.requests.before",
                                          "friend.requests.select", "friend.accept"} else "driver"

    @staticmethod
    def _arguments(operation):
        return {
            "friend.requests.before": ("friend", "requests", "--direction", "received", "--status", "pending"),
            "friend.requests.select": ("friend", "requests", "--direction", "received", "--status", "pending"),
            "friend.request": ("friend", "request", "--bot-uuid", MEMBER),
            "friend.accept": ("friend", "accept", "--request-id", REQUEST_ID),
            "friend.list": ("friend", "list"),
        }[operation]


def standard_outputs():
    pending = [{"id": REQUEST_ID, "from_bot": DRIVER, "to_bot": MEMBER, "status": "pending"}]
    accepted = dict(pending[0]); accepted["status"] = "accepted"
    return {
        "friend.requests.before": [],
        "friend.request": {"success": True, "data": dict(pending[0])},
        "friend.requests.select": pending,
        "friend.accept": {"success": True},
        "friend.list": [{"bot_uuid": MEMBER}],
    }


class FriendCliAcceptTests(unittest.TestCase):
    def run_case(self, *, gateway=False, runner=None):
        profile = base_profile(gateway=gateway)
        runner = runner if runner is not None else ScriptedRunner(outputs=standard_outputs())
        return execute_friend_cli_accept_case(
            RemoteFriendCliAcceptAdapter(profile, runner)), runner

    def test_case_guard_accepts_the_product_cli_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/local_friend_cli_accept_contract.py",
                 ROOT / "bcs_e2e/remote_friend_cli_accept_adapter.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_case_plan_and_inventory_claim_no_credit(self):
        adaptability = friend_cli_accept_adaptability()
        self.assertEqual(adaptability["case_id"], CASE_ID)
        self.assertEqual(adaptability["requirement_ids"], ["FRIEND.cli-accept"])
        self.assertFalse(adaptability["grants_release_credit"])
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        plan = migration_plan()["cross_environment_product_cli_case_slices"]
        rows = [row for row in plan if row["case_id"] == CASE_ID]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["requirement_ids"], ["FRIEND.cli-accept"])
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])

    def test_local_and_gateway_use_one_product_cli_sequence(self):
        for gateway in (False, True):
            with self.subTest(gateway=gateway):
                execution, runner = self.run_case(gateway=gateway)
                self.assertEqual(execution.status, "PASS")
                self.assertEqual(execution.completed_count, 1)
                self.assertEqual([row[0] for row in runner.calls], [
                    "friend.requests.before", "friend.request", "friend.requests.select",
                    "friend.accept", "friend.list"])
                snapshot = execution.snapshot()
                self.assertEqual(snapshot["steps"][0]["data"]["request_id"], REQUEST_ID)
                self.assertTrue(snapshot["steps"][0]["data"]["final_list_contains_member"])
                self.assertEqual(len(snapshot["steps"][0]["evidence"]["requests"]), 5)

    def test_wrong_business_result_blocks_without_partial_credit(self):
        outputs = standard_outputs()
        cases = {
            "pending-prefix": lambda o: o.update({"friend.requests.before": [
                {"id": "old", "from_bot": DRIVER, "to_bot": MEMBER, "status": "pending"}]}),
            "auto-accepted": lambda o: o.update({"friend.request": {
                "success": True, "data": {
                    "id": REQUEST_ID, "from_bot": DRIVER, "to_bot": MEMBER,
                    "status": "accepted"}}}),
            "wrong-pair": lambda o: o.update({"friend.request": {
                "success": True, "data": {
                    "id": REQUEST_ID, "from_bot": MEMBER, "to_bot": DRIVER,
                    "status": "pending"}}}),
            "missing-exact-request": lambda o: o.update({"friend.requests.select": []}),
            "duplicate-exact-request": lambda o: o.update({"friend.requests.select": [
                {"id": REQUEST_ID, "from_bot": DRIVER, "to_bot": MEMBER, "status": "pending"},
                {"id": REQUEST_ID, "from_bot": DRIVER, "to_bot": MEMBER, "status": "pending"}]}),
            "accept-failed": lambda o: o.update({"friend.accept": {"success": False}}),
            "member-missing": lambda o: o.update({"friend.list": []}),
            "member-duplicated": lambda o: o.update({"friend.list": [
                {"bot_uuid": MEMBER}, {"bot_uuid": MEMBER}]}),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                outputs_case = standard_outputs()
                mutate(outputs_case)
                execution, _ = self.run_case(
                    runner=ScriptedRunner(outputs=outputs_case))
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, "NOT_RUN")

    def test_wrong_cli_evidence_blocks_even_if_business_data_is_valid(self):
        declared = {operation: cli_record(
            operation, "driver", ("unexpected",), "human_cookie")
            for operation in standard_outputs()}
        execution, _ = self.run_case(
            runner=ScriptedRunner(outputs=standard_outputs(), declared=declared))
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)

    def test_adapter_requires_request_before_selection_and_acceptance(self):
        adapter = RemoteFriendCliAcceptAdapter(base_profile(),
                                                 ScriptedRunner(outputs=standard_outputs()))
        with self.assertRaisesRegex(RunStateError, "must follow friendship request"):
            adapter.select_exact_member_request()
        with self.assertRaisesRegex(RunStateError, "must follow request selection"):
            adapter.accept_member_friend_request()
        with self.assertRaisesRegex(RunStateError, "must follow acceptance"):
            adapter.read_final_driver_friend_list()

    def test_profile_rejects_incorrect_admission_or_identity(self):
        cases = {
            "public-member": lambda value: value["actors"]["member"].update(visibility="public"),
            "non-exclusive": lambda value: value["policy"].update(exclusive_use_confirmed=False),
            "local-cookie": lambda value: value.update(auth={
                "kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}),
            "public-face": lambda value: value["entrypoint"].update(api_face="public-v1"),
            "cross-owner": lambda value: value["actors"]["member"].update(owner_id="owner-2"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate_friend_cli_profile(value)


if __name__ == "__main__":
    unittest.main()
