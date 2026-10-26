"""Command and receipt tests for the dual Edge read lifecycle."""

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
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.edge_read_case import (
    ADMISSION_PUBLIC_CASE_ID,
    FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
    FRIEND_REQUESTS_PUBLIC_CASE_ID,
)
from bcs_e2e.edge_search_contract import PARENT as BOT_SEARCH_PARENT
from bcs_e2e.edge_search_case import EDGE_BOT_SEARCH_CANDIDATES_CASE_ID
from bcs_e2e.edge_read_case_run import (
    CASE_KEYS,
    REPORT_CONTRACT,
    dual_edge_read_case_run,
)
from bcs_e2e.edge_read_contract import (
    ADMISSION_PARENT,
    FRIEND_CONNECTIONS_PARENT,
    FRIEND_REQUESTS_PARENT,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.route_bindings import (
    edge_admission_actor_kind_path,
    edge_admission_human_actor_path,
    edge_bot_search_empty_path,
    edge_bot_search_name_path,
)
from bcs_e2e.run_state import read

RUN_ID = "edge-read-command-1"

PARENTS = {
    "admission": ADMISSION_PARENT,
    "friend-requests": FRIEND_REQUESTS_PARENT,
    "friend-connections": FRIEND_CONNECTIONS_PARENT,
    "bot-search-candidates": BOT_SEARCH_PARENT,
}
CASE_IDS = {
    "admission": ADMISSION_PUBLIC_CASE_ID,
    "friend-requests": FRIEND_REQUESTS_PUBLIC_CASE_ID,
    "friend-connections": FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
    "bot-search-candidates": EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
}
REQUIREMENTS = {
    "admission": (
        ("EDGE-ADMISSION.human-actor", "read_admission_human_actor"),
        ("EDGE-ADMISSION.actor-kind", "read_admission_actor_kind"),
    ),
    "friend-requests": (
        ("EDGE-FRIEND-REQUESTS.received", "read_friend_requests_received"),
        ("EDGE-FRIEND-REQUESTS.sent", "read_friend_requests_sent"),
        ("EDGE-FRIEND-REQUESTS.all", "read_friend_requests_all"),
    ),
    "friend-connections": (
        ("EDGE-FRIEND-CONNECTIONS.list", "read_friend_connections_list"),
        ("EDGE-FRIEND-CONNECTIONS.list-repeat", "read_friend_connections_list_repeat"),
    ),
    "bot-search-candidates": (
        ("EDGE-BOT-SEARCH.name-fuzzy", "read_bot_search_name_fuzzy"),
        ("EDGE-BOT-SEARCH.empty-query", "read_bot_search_empty_query"),
    ),
}


def base_profile(case_key: str) -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(id="edge-command-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "owned-edge-command-pool"
    if case_key == "admission":
        value["auth"]["header_env"] = {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN"}
        value["edge_case_bindings"] = {
            "edge_admission_human_actor.read": {
                "method": "GET", "path": edge_admission_human_actor_path(value),
                "api_face": "internal-v1", "auth": "unauthenticated",
                "response": "admission-json", "traceparent": "disabled",
            },
            "edge_admission_actor_kind.read": {
                "method": "",
                "path": "",
                "api_face": "internal-v1", "auth": "unauthenticated",
                "response": "admission-json", "traceparent": "disabled",
            },
        }
        value["edge_case_bindings"]["edge_admission_actor_kind.read"]["path"] = (
            edge_admission_actor_kind_path(value))
        value["edge_case_bindings"]["edge_admission_actor_kind.read"]["method"] = "GET"
    elif case_key == "bot-search-candidates":
        value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
        value["edge_case_bindings"] = {
            "edge_bot_search_name.read": {
                "method": "GET", "path": edge_bot_search_name_path(value),
                "api_face": "internal-v1", "auth": "human_cookie",
                "response": "public-envelope", "traceparent": "disabled",
                "caller_actor_id": "driver-bot",
            },
            "edge_bot_search_empty.read": {
                "method": "GET", "path": edge_bot_search_empty_path(value),
                "api_face": "internal-v1", "auth": "human_cookie",
                "response": "public-envelope", "traceparent": "disabled",
                "caller_actor_id": "driver-bot",
            },
        }
    elif case_key == "friend-requests":
        value["auth"]["header_env"] = {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN"}
        value["edge_case_bindings"] = {
            "friend_requests.read": {
                "method": "GET",
                "path": "/openapi/v1/collaboration/friend-connections/requests",
                "api_face": "public-v1", "auth": "authenticated_bot",
                "response": "public-envelope", "traceparent": "disabled",
                "caller_actor_id": "driver-bot",
            },
        }
    else:
        value["auth"]["header_env"] = {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN"}
        value["edge_case_bindings"] = {
            "friend_connections.read": {
                "method": "GET",
                "path": "/openapi/v1/collaboration/friend-connections",
                "api_face": "public-v1", "auth": "authenticated_bot",
                "response": "public-envelope", "traceparent": "disabled",
                "caller_actor_id": "driver-bot",
            },
        }
    return value


def profile_value(case_key: str, *, preprod: bool = False) -> dict:
    value = base_profile(case_key)
    if preprod:
        value["environment"].update(
            id="edge-command-preprod", expected_bcs_env="pre",
            lifecycle="remote-attach", deployment="managed", provider="internal",
            tier="preprod")
        value["entrypoint"].update(kind="gateway", origin="https://gateway.example.com")
        value["observer"].update(kind="alisddbquery", server="observ",
                                 database="agentclawdb",
                                 environment_binding_confirmed=True)
        value["policy"]["fixture_pool"] = "owned-edge-command-preprod-pool"
        if case_key == "friend-connections":
            value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    return value


def execution(case_key: str, *, preprod: bool = False) -> CaseExecution:
    credential_kind = (
        "unauthenticated" if case_key == "admission"
        else "human_cookie" if preprod or case_key == "bot-search-candidates"
        else "owned_local_bot_token")
    steps = tuple(
        StepOutcome(
            requirement_id=requirement_id,
            method=method,
            status="PASS",
            data={"case_key": case_key},
            evidence={"credential_kind": credential_kind},
            error_type=None,
        )
        for requirement_id, method in REQUIREMENTS[case_key]
    )
    result = CaseExecution(
        case_id=CASE_IDS[case_key],
        parent_id=PARENTS[case_key],
        status="PASS",
        steps=steps,
        completed_count=len(steps),
        total_count=len(steps),
        error_type=None,
        ready_to_execute=True,
    )
    result.validate()
    return result


class FakeAdapter:
    def __init__(self, profile, case_id=None):
        self.profile = profile
        self.case_id = case_id


class EdgeReadCaseRunTests(unittest.TestCase):
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
        expected = execution(case_key, preprod=preprod)
        self.invocation += 1
        self.state_dir = self.state_root / f"run-{self.invocation}"

        with patch(
                "bcs_e2e.edge_read_case_run.RemoteEdgeAdmissionAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.edge_read_case_run.RemoteEdgeReadAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.edge_read_case_run.RemoteEdgeBotSearchCandidatesAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.edge_read_case_run.execute_edge_read_case",
                lambda case_id, adapter: expected), \
             patch(
                "bcs_e2e.edge_read_case_run.execute_edge_bot_search_candidates_case",
                lambda adapter: expected):
            report = dual_edge_read_case_run(
                profile_path=self.profile_path,
                case_key=case_key,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected, self.state_dir

    def test_command_registry_covers_the_reviewed_read_slices(self):
        self.assertEqual(CASE_KEYS,
                         ("admission", "friend-requests", "friend-connections",
                          "bot-search-candidates"))
        self.assertEqual(set(CASE_IDS), set(CASE_KEYS))

    def test_local_reads_write_bound_sanitized_receipts(self):
        for case_key in CASE_KEYS:
            with self.subTest(case_key=case_key):
                report, profile, expected, state_dir = self.invoke(case_key)
                self.assertEqual(report["contract"], REPORT_CONTRACT)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], expected.case_id)
                self.assertEqual(report["parent_id"], PARENTS[case_key])
                self.assertEqual(report["remote_validation"], "NOT_RUN")
                self.assertFalse(report["grants_release_credit"])
                self.assertEqual(report["resource_disposition"],
                                 "READ_ONLY_NO_BUSINESS_RESOURCE")
                if case_key == "bot-search-candidates":
                    self.assertEqual(report["credential_kind"], "human_cookie")
                self.assertEqual(read(state_dir / f"edge-{case_key}-profile.json"), profile)
                self.assertEqual(
                    read(state_dir / f"edge-{case_key}-binding.json")["case_id"],
                    expected.case_id)
                self.assertEqual(
                    read(state_dir / f"edge-{case_key}-run.json"), report)
                serialized = json.dumps(report, sort_keys=True)
                self.assertNotIn("BCS_E2E_DRIVER_BOT_TOKEN", serialized)
                self.assertNotIn("private-cookie", serialized)

    def test_preprod_friend_read_reports_cookie_kind_without_cookie_value(self):
        report, _, _, _ = self.invoke("friend-requests", preprod=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], "owned_local_bot_token")
        self.assertEqual(report["environment"]["tier"], "preprod")
        self.assertEqual(report["environment"]["entrypoint_kind"], "gateway")
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", json.dumps(report, sort_keys=True))

    def test_bot_search_candidates_use_cookie_kind_in_both_environments(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                report, _, expected, _ = self.invoke(
                    "bot-search-candidates", preprod=preprod)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["case_id"], expected.case_id)
                self.assertEqual(report["credential_kind"], "human_cookie")
                self.assertEqual(report["execution"]["total_count"], 2)
                self.assertEqual(report["environment"]["tier"],
                                 "preprod" if preprod else "local")
                serialized = json.dumps(report, sort_keys=True)
                self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
                self.assertNotIn("private-cookie", serialized)

    def test_admission_remains_unauthenticated_in_both_environments(self):
        for preprod in (False, True):
            with self.subTest(preprod=preprod):
                report, _, _, _ = self.invoke("admission", preprod=preprod)
                self.assertEqual(report["credential_kind"], "unauthenticated")

    def test_profile_case_bindings_must_be_exact(self):
        profile = profile_value("friend-requests")
        profile["edge_case_bindings"]["friend_connections.read"] = {
            "method": "GET",
            "path": "/openapi/v1/collaboration/friend-connections",
            "api_face": "public-v1", "auth": "authenticated_bot",
            "response": "public-envelope", "traceparent": "disabled",
            "caller_actor_id": "driver-bot",
        }
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "reviewed case operations"):
            dual_edge_read_case_run(
                profile_path=self.profile_path,
                case_key="friend-requests",
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_permissions_and_fresh_state_are_enforced(self):
        profile = profile_value("admission")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_edge_read_case_run(
                profile_path=self.profile_path,
                case_key="admission",
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id="other",
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_edge_read_case_run(
                profile_path=self.profile_path,
                case_key="admission",
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_edge_read_runner(self):
        profile = profile_value("friend-requests", preprod=True)
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-edge-read-run",
            "--profile", str(self.profile_path),
            "--case", "friend-requests",
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_edge_read_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            case_key="friend-requests",
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
