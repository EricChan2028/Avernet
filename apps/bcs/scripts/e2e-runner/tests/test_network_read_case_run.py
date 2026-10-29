"""Command and receipt tests for the dual network read lifecycle."""

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
from bcs_e2e.local_network_contract import (
    NETWORK_ACTOR_LIST_CASE_ID,
    NETWORK_ACTOR_SEARCH_CASE_ID,
    NETWORK_BOTS_MINE_CASE_ID,
    NETWORK_BOTS_PAGED_CASE_ID,
    NETWORK_BOTS_QUERY_CASE_ID,
    NETWORK_PLATFORM_ASSET_CASE_ID,
    PARENT,
    network_actor_list_case_plan,
    network_actor_search_case_plan,
    network_bots_mine_case_plan,
    network_bots_paged_case_plan,
    network_bots_query_case_plan,
    network_platform_asset_case_plan,
)
from bcs_e2e.network_read_case_run import (
    CASE_KEYS,
    REPORT_CONTRACT,
    dual_network_read_case_run,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.route_bindings import (
    NETWORK_BOTS_MINE_READ_PATH,
    NETWORK_BOTS_QUERY_READ_PATH,
    actor_list_read_path,
    actor_search_read_path,
    bots_paged_read_path,
)
from bcs_e2e.run_state import read

RUN_ID = "network-read-command-1"
DRIVER = "driver-bot"
MEMBER = "member-bot"

CASE_IDS = {
    "bots-query": NETWORK_BOTS_QUERY_CASE_ID,
    "bots-mine": NETWORK_BOTS_MINE_CASE_ID,
    "actor-list": NETWORK_ACTOR_LIST_CASE_ID,
    "actor-search": NETWORK_ACTOR_SEARCH_CASE_ID,
    "bots-paged": NETWORK_BOTS_PAGED_CASE_ID,
    "platform-asset": NETWORK_PLATFORM_ASSET_CASE_ID,
}

REQUIREMENTS = {
    "bots-query": ("NETWORK.bots-query", "read_directory_bots"),
    "bots-mine": ("NETWORK.bots-my", "read_owned_directory_bots"),
    "actor-list": ("NETWORK.actor-list", "read_actor_directory"),
    "actor-search": ("NETWORK.actor-search", "read_actor_search"),
    "bots-paged": ("NETWORK.bots-paged", "read_bots_paged"),
    "platform-asset": ("NETWORK.platform", "read_platform_manifest_identity_asset"),
}

SELECTED_OBLIGATIONS = {
    key: [requirement[0]] for key, requirement in REQUIREMENTS.items()
}
SELECTED_OBLIGATIONS["platform-asset"] = ["manifest", "identity", "asset"]

PROJECTIONS = {
    "bots-query": {
        "item_count": 2,
        "returned_ids": [DRIVER, MEMBER],
    },
    "bots-mine": {
        "item_count": 2,
        "total": 2,
        "offset": 0,
        "limit": 20,
    },
    "actor-list": {
        "item_count": 2,
        "total": 2,
        "offset": 0,
        "limit": 20,
    },
    "actor-search": {
        "item_count": 2,
        "search_mode": "name_fallback",
    },
    "bots-paged": {
        "item_count": 2,
        "total": 2,
        "offset": 0,
        "limit": 5,
    },
    "platform-asset": {
        "schema_version": 1,
        "env": "local",
        "panel_bundle_name": "bcsPanel",
        "panel_asset_url": "/api/v1/collaboration/assets/bcsPanel/test-panel.umd.js",
        "panel_asset_size": 2048,
        "panel_asset_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "user_id": "owner-1",
    },
}

BINDINGS = {
    "bots-query": (
        "network_bot_query.read",
        {
            "method": "POST",
            "path": NETWORK_BOTS_QUERY_READ_PATH,
            "api_face": "public-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
            "target_actor_ids": [DRIVER, MEMBER],
        },
    ),
    "bots-mine": (
        "network_bot_mine.read",
        {
            "method": "GET",
            "path": NETWORK_BOTS_MINE_READ_PATH,
            "api_face": "public-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
        },
    ),
    "actor-list": (
        "network_actor_list.read",
        {
            "method": "GET",
            "path": "",
            "api_face": "public-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
        },
    ),
    "actor-search": (
        "network_actor_search.read",
        {
            "method": "GET",
            "path": "",
            "api_face": "internal-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
            "caller_actor_id": DRIVER,
        },
    ),
    "bots-paged": (
        "network_bots_paged.read",
        {
            "method": "GET",
            "path": "",
            "api_face": "public-v1",
            "auth": "human_cookie",
            "response": "public-envelope",
            "traceparent": "disabled",
        },
    ),
    "platform-asset": (
        "network_platform_asset.read",
        {
            "method": "GET",
            "path": "/api/v1/collaboration/assets/bcsPanel/test-panel.umd.js",
            "api_face": "internal-v1",
            "auth": "unauthenticated",
            "response": "javascript-asset",
            "traceparent": "disabled",
            "asset_name": "bcsPanel",
            "asset_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        },
    ),
}


def base_profile(case_key: str) -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="network-read-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id=DRIVER, owner_id="owner-1")
    value["actors"]["member"].update(bot_id=MEMBER, owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "network-read-local-pool"
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    binding_name, binding = BINDINGS[case_key]
    if case_key == "actor-list":
        binding["path"] = actor_list_read_path(value)
    elif case_key == "actor-search":
        binding["path"] = actor_search_read_path(value)
    elif case_key == "bots-paged":
        binding["path"] = bots_paged_read_path(value)
    value["edge_case_bindings"] = {binding_name: binding}
    return value


def profile_value(case_key: str, *, preprod: bool = False) -> dict:
    value = base_profile(case_key)
    if preprod:
        value["environment"].update(
            id="network-read-preprod", expected_bcs_env="pre",
            lifecycle="remote-attach", deployment="managed", provider="internal",
            tier="preprod")
        value["entrypoint"].update(kind="gateway", origin="https://gateway.example.com")
        value["observer"].update(kind="alisddbquery", server="observ",
                                 database="agentclawdb",
                                 environment_binding_confirmed=True)
        value["policy"]["fixture_pool"] = "network-read-preprod-pool"
    return value


def expected_execution(case_key: str) -> CaseExecution:
    requirement_id, method = REQUIREMENTS[case_key]
    step = StepOutcome(
        requirement_id=requirement_id,
        method=method,
        status="PASS",
        data=PROJECTIONS[case_key],
        evidence={"case_key": case_key},
        error_type=None,
    )
    execution = CaseExecution(
        case_id=CASE_IDS[case_key],
        parent_id=PARENT,
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
    def __init__(self, profile, case_key):
        self.profile = profile
        self.case_key = case_key


class NetworkReadCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_root = self.root / "run-state"
        self.state_root.mkdir()
        self.invocation = 0
        self.state_dir = self.state_root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, case_key: str, *, preprod: bool = False):
        profile = profile_value(case_key, preprod=preprod)
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution(case_key)
        self.invocation += 1
        self.state_dir = self.state_root / f"run-{self.invocation}"
        fake_adapter = FakeAdapter(profile, case_key)
        with patch(
                "bcs_e2e.network_read_case_run._adapter",
                lambda value, key: fake_adapter), \
             patch(
                "bcs_e2e.network_read_case_run._execute",
                lambda adapter, key: expected):
            report = dual_network_read_case_run(
                profile_path=self.profile_path,
                case_key=case_key,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected, fake_adapter, self.state_dir

    def test_case_registry_covers_the_reviewed_network_read_slices(self):
        self.assertEqual(
            CASE_KEYS,
            ("bots-query", "bots-mine", "actor-list", "actor-search",
             "bots-paged", "platform-asset"))
        self.assertEqual(set(CASE_IDS), set(CASE_KEYS))
        for case_key in CASE_KEYS:
            plan = (
                network_bots_query_case_plan if case_key == "bots-query" else
                network_bots_mine_case_plan if case_key == "bots-mine" else
                network_actor_list_case_plan if case_key == "actor-list" else
                network_actor_search_case_plan if case_key == "actor-search" else
                network_bots_paged_case_plan if case_key != "platform-asset"
                else network_platform_asset_case_plan
            )()
            self.assertEqual(plan.case_id, CASE_IDS[case_key])
            self.assertEqual(plan.parent_id, PARENT)
            self.assertEqual(plan.requirement.requirement_id,
                             REQUIREMENTS[case_key][0])

    def test_local_reads_write_sanitized_receipts_and_case_scope(self):
        for case_key in CASE_KEYS:
            with self.subTest(case_key=case_key):
                report, profile, expected, adapter, state_dir = self.invoke(case_key)
                self.assertEqual(report["contract"], REPORT_CONTRACT)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], expected.case_id)
                self.assertEqual(report["parent_id"], PARENT)
                self.assertEqual(report["credential_kind"], "human_cookie")
                self.assertEqual(report["remote_validation"], "NOT_RUN")
                self.assertFalse(report["grants_release_credit"])
                self.assertEqual(report["resource_disposition"],
                                 "READ_ONLY_NO_BUSINESS_RESOURCE")
                self.assertEqual(report["case_scope"]["requirement_id"],
                                 REQUIREMENTS[case_key][0])
                self.assertEqual(report["case_scope"]["selected_obligations"],
                                 SELECTED_OBLIGATIONS[case_key])
                self.assertEqual(adapter.profile, profile)
                self.assertEqual(read(state_dir / f"network-{case_key}-profile.json"), profile)
                binding = read(state_dir / f"network-{case_key}-binding.json")
                self.assertEqual(binding["case_id"], expected.case_id)
                self.assertEqual(binding["credential_kind"], "human_cookie")
                self.assertEqual(read(state_dir / f"network-{case_key}-run.json"), report)
                serialized = json.dumps(report, sort_keys=True)
                self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
                self.assertNotIn("private-cookie", serialized)

    def test_blocked_execution_is_reported_without_partial_credit(self):
        profile = profile_value("bots-mine")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        blocked_step = StepOutcome(
            requirement_id=REQUIREMENTS["bots-mine"][0],
            method=REQUIREMENTS["bots-mine"][1],
            status="NOT_RUN", data=None, evidence={},
            error_type="RemoteNetworkBotsMineAdapterError",
        )
        expected = CaseExecution(
            case_id=CASE_IDS["bots-mine"], parent_id=PARENT,
            status="BLOCKED", steps=(blocked_step,),
            completed_count=0, total_count=1,
            error_type="RemoteNetworkBotsMineAdapterError",
            ready_to_execute=False,
        )
        self.invocation += 1
        self.state_dir = self.state_root / f"run-{self.invocation}"
        fake_adapter = FakeAdapter(profile, "bots-mine")
        with patch(
                "bcs_e2e.network_read_case_run._adapter",
                lambda value, key: fake_adapter),              patch(
                "bcs_e2e.network_read_case_run._execute",
                lambda adapter, key: expected):
            report = dual_network_read_case_run(
                profile_path=self.profile_path,
                case_key="bots-mine",
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(report["resource_disposition"],
                         "READ_ONLY_NO_BUSINESS_RESOURCE")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(read(self.state_dir / "network-bots-mine-run.json"), report)

    def test_preprod_actor_search_keeps_cookie_kind_without_cookie_value(self):
        report, profile, expected, adapter, state_dir = self.invoke(
            "actor-search", preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "human_cookie")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertEqual(report["case_id"], expected.case_id)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("private-cookie", serialized)

    def test_profile_must_bind_the_exact_case_operation(self):
        for case_key in CASE_KEYS:
            with self.subTest(case_key=case_key):
                excess = json.loads(json.dumps(profile_value(case_key)))
                other_key = next(key for key in CASE_KEYS if key != case_key)
                excess["edge_case_bindings"].update({
                    BINDINGS[other_key][0]: dict(BINDINGS[other_key][1]),
                })
                self.profile_path.write_text(json.dumps(excess), encoding="utf-8")
                with self.assertRaisesRegex(
                        ConfigError,
                        "profile must bind exactly its reviewed read operation"):
                    dual_network_read_case_run(
                        profile_path=self.profile_path,
                        case_key=case_key,
                        state_dir=self.state_dir,
                        run_id=RUN_ID,
                        allow_write=True,
                        confirm_run_id=RUN_ID,
                    )

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = profile_value("bots-query")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_network_read_case_run(
                profile_path=self.profile_path,
                case_key="bots-query",
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_network_read_case_run(
                profile_path=self.profile_path,
                case_key="bots-query",
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_network_read_lifecycle(self):
        profile = profile_value("bots-query")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-network-read-run",
            "--profile", str(self.profile_path),
            "--case", "bots-query",
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_network_read_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            case_key="bots-query",
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
