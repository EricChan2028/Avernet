"""Receipt and loader tests for the Edge friend-mutation case command."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests"
for path in (ROOT, TEST_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_remote_edge_friend_mutation_adapter import base_profiles
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.cli import main as cli_main
from bcs_e2e.edge_friend_mutation_case import CASE_IDS
from bcs_e2e.edge_friend_mutation_case_run import (
    CASE_KEYS,
    CREDENTIAL_KIND,
    DOCUMENTED_GAPS,
    PRODUCT_DEFECTS,
    dual_edge_friend_mutation_case_run,
    load_edge_friend_mutation_case_profile,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import RunStateError, read

import json
RUN_ID = "edge-friend-run-1"


class PassingAdapter:
    def __init__(self, *args):
        pass

    def run_revoke(self):
        data = {"station_revoked_edges": [1, 2], "fresh_request_ids": ["a", "b"]}
        return AdapterCall(
            data=data,
            evidence={
                "transport": "public-v1-http", "operation": "friend.mutation.revoke",
                "fixture_effect": "REGISTERED_BOT_RETAINED;EDGES_REVOKED;FRESH_MUTUAL_PENDING_RETAINED",
                "remote_validation": "NOT_RUN", "grants_release_credit": False,
                "projection": data, "requests": [{"http_status": 200}],
            },
        )


class PreprodGapAdapter:
    def __init__(self, *args):
        pass

    def run_ensure_bot(self):
        error = RunStateError("PREPROD_SERVICE_SCOPE_GAP: missing service credential")
        error.gap = {"gap": "PREPROD_SERVICE_SCOPE_GAP", "singlebox_closure": True}
        raise error


class HardFailureAdapter:
    def __init__(self, *args):
        pass

    def run_revoke(self):
        raise RunStateError("station legacy revoke did not revoke exactly both edges")


class BacklogGapAdapter:
    def __init__(self, *args):
        pass

    def _gap(self):
        error = RunStateError("PREPROD_SERVICE_SCOPE_GAP: unavailable material")
        error.gap = {"gap": "PREPROD_SERVICE_SCOPE_GAP", "singlebox_closure": True}
        raise error

    def run_tc_friend_request_create_accept(self):
        self._gap()

    def run_tc_friend_auth_sync_readdress(self):
        self._gap()

    def run_dept_scope_mixed_owner_patch(self):
        self._gap()

    def run_dept_scope_human_no_check(self):
        self._gap()

    def run_dept_scope_human_user_scope(self):
        self._gap()

    def run_dept_scope_bot_agent_scope(self):
        self._gap()


class EdgeFriendMutationCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.gateway_path = self.root / "gateway.json"
        self.station_path = self.root / "station.json"
        self.state_dir = self.root / "state"
        gateway, station = base_profiles()
        self.gateway_path.write_text(json.dumps(gateway), encoding="utf-8")
        self.station_path.write_text(json.dumps(station), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, case_key: str = "friend-revoke", adapter=PassingAdapter()):
        with patch(
                "bcs_e2e.edge_friend_mutation_case_run.RemoteEdgeFriendMutationAdapter",
                adapter.__class__):
            report = dual_edge_friend_mutation_case_run(
                profile_path=self.gateway_path,
                station_profile_path=self.station_path,
                case_key=case_key,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report

    def test_case_keys_derive_nine_reviewed_case_ids(self):
        self.assertEqual(len(CASE_KEYS), 9)
        self.assertEqual(len(set(CASE_KEYS)), 9)
        self.assertEqual(
            CASE_KEYS, tuple(
                case_id.removeprefix("LOCAL-EDGE-").removesuffix("-01").lower()
                for case_id in CASE_IDS
            )
        )

    def test_successful_run_writes_three_complete_receipts(self):
        report = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["credential_kind"], CREDENTIAL_KIND)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["product_defects"], PRODUCT_DEFECTS)
        self.assertEqual(report["documented_gaps"], DOCUMENTED_GAPS)
        self.assertEqual(report["environment"]["gateway"]["entrypoint_kind"], "gateway")
        self.assertEqual(report["environment"]["station"]["entrypoint_kind"], "bcs-direct")

        profile = read(self.state_dir / "edge-friend-mutation-profile.json")
        self.assertEqual(profile["schema_version"], 1)
        self.assertIn("gateway", profile)
        self.assertIn("station", profile)
        binding = read(self.state_dir / "edge-friend-mutation-binding.json")
        self.assertEqual(binding["contract"], "dual-edge-friend-mutation-case-run-v1")
        self.assertEqual(binding["case_id"], "LOCAL-EDGE-FRIEND-REVOKE-01")
        self.assertIn("gateway_digest", binding["profiles"])
        self.assertIn("station_digest", binding["profiles"])
        self.assertEqual(binding["case_scope"]["requirement_ids"],
                         ["EDGE_FRIEND_MUTATIONS.revoke"])
        self.assertIn("friend_connection_mutation.create",
                      binding["case_scope"]["public_operations"])
        self.assertEqual(read(self.state_dir / "edge-friend-mutation-run.json"), report)

    def test_service_scope_gap_is_recorded_as_blocked_without_credit(self):
        report = self.invoke("ensure-bot", PreprodGapAdapter())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["error_type"], "RunStateError")
        self.assertEqual(report["case"]["steps"][0]["status"], "NOT_RUN")
        # A pure documented-gap block never mutates; the receipt keeps the planned
        # gap disposition rather than the conservative unknown-mutations label.
        self.assertEqual(report["resource_disposition"], "PREPROD_SERVICE_SCOPE_GAP")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["case_scope"]["public_operations"], [])
        self.assertEqual(report["documented_gaps"]["PREPROD_SERVICE_SCOPE_GAP"],
                         "ensure-bot, TC friend-auth sync, and department-scope extensions need a user-credential/mock-Provider batch unavailable in preprod.")

    def test_backlog_case_receipts_expose_their_exact_parsed_density(self):
        cases = (
            ("tc-friend-auth-sync", "run_tc_friend_request_create_accept", 2,
             "EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept"),
            ("dept-scope-friend-ext", "run_dept_scope_mixed_owner_patch", 4,
             "EDGE_FRIEND_MUTATIONS.dept-scope-mixed-owner-patch"),
        )
        for index, (case_key, adapter_method, expected, first_requirement) in enumerate(cases):
            with self.subTest(case_key=case_key), patch(
                    f"bcs_e2e.edge_friend_mutation_case_run.RemoteEdgeFriendMutationAdapter",
                    BacklogGapAdapter):
                state_dir = self.root / f"backlog-{index}"
                report = dual_edge_friend_mutation_case_run(
                    profile_path=self.gateway_path,
                    station_profile_path=self.station_path,
                    case_key=case_key,
                    state_dir=state_dir,
                    run_id=RUN_ID,
                    allow_write=True,
                    confirm_run_id=RUN_ID,
                )
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["case"]["total_count"], expected)
            self.assertEqual(report["execution"]["completed_count"], 0)
            self.assertEqual(report["case"]["steps"][0]["requirement_id"], first_requirement)
            self.assertEqual(report["resource_disposition"], "PREPROD_SERVICE_SCOPE_GAP")

    def test_non_gap_failure_keeps_unknown_mutations_disposition(self):
        report = self.invoke("friend-revoke", HardFailureAdapter())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["execution"]["error_type"], "RunStateError")
        self.assertEqual(report["resource_disposition"], "UNKNOWN_OWNED_BOT_MUTATIONS")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["case"]["steps"][0]["error_gap"], None)

    def test_gateway_loader_rejects_non_mutation_relationship_and_missing_browser_origin(self):
        profile = json.loads(self.gateway_path.read_text(), object_pairs_hook=dict)
        profile["relationships"]["friendship"] = "bounded-owned-bot-decisions"
        self.gateway_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, r"relationships.friendship: exactly this mode's bounded bindings are required"):
            load_edge_friend_mutation_case_profile(self.gateway_path)
        profile["relationships"]["friendship"] = "bounded-owned-bot-mutations"
        profile.pop("browser_origin")
        self.gateway_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "browser_origin"):
            load_edge_friend_mutation_case_profile(self.gateway_path)

    def test_station_loader_rejects_dual_identity_or_actor_drift(self):
        station = json.loads(self.station_path.read_text(), object_pairs_hook=dict)
        station["auth"]["header_env"].pop("Authorization")
        self.station_path.write_text(json.dumps(station), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "dual-identity route requires Cookie and Authorization references"):
            self.invoke("friend-revoke", PassingAdapter())
        self.state_dir.parent.mkdir(exist_ok=True)
        station["auth"]["header_env"]["Authorization"] = "BCS_E2E_STATION_BOT_TOKEN"
        station["actors"]["member"]["bot_id"] = "different-member"
        self.station_path.write_text(json.dumps(station), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "identical actors"):
            self.invoke("friend-revoke", PassingAdapter())

    def test_existing_state_directory_is_rejected_without_writes(self):
        self.state_dir.mkdir(mode=0o700)
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            self.invoke()
        self.assertTrue(self.state_dir.is_dir())
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_cli_dispatches_case_arguments_without_extra_translation(self):
        dispatched = {}

        def fake_case_run(**kwargs):
            dispatched.update(kwargs)
            return {"status": "PASS"}

        with patch("bcs_e2e.cli.dual_edge_friend_mutation_case_run", fake_case_run):
            rc = cli_main([
                "dual-edge-friend-mutation-run",
                "--profile", str(self.gateway_path),
                "--station-profile", str(self.station_path),
                "--case", "friend-revoke",
                "--state-dir", str(self.state_dir),
                "--run-id", RUN_ID,
                "--confirm-run-id", RUN_ID,
                "--allow-write",
            ])

        self.assertEqual(rc, 0)
        self.assertEqual(dispatched, {
            "profile_path": self.gateway_path,
            "station_profile_path": self.station_path,
            "case_key": "friend-revoke",
            "state_dir": self.state_dir,
            "run_id": RUN_ID,
            "allow_write": True,
            "confirm_run_id": RUN_ID,
        })

    def test_exact_permissions_are_required(self):
        with self.assertRaisesRegex(ConfigError, "explicit write permission"):
            dual_edge_friend_mutation_case_run(
                profile_path=self.gateway_path,
                station_profile_path=self.station_path,
                case_key="friend-revoke",
                state_dir=self.state_dir,
                run_id=RUN_ID, allow_write=False, confirm_run_id=RUN_ID,
            )
        self.assertFalse(self.state_dir.exists())


if __name__ == "__main__":
    unittest.main()
