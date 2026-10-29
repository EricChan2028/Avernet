from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_session_contract import PARENT
from bcs_e2e.local_session_public_case import (
    ADAPTER_METHOD,
    CASE_ID,
    EXECUTION_CASE_ID,
    LOCAL_EXECUTION_CASE_ID,
    REQUIREMENT_ID,
    project_session_public_result,
    session_public_adaptability,
    session_public_case_admission,
    session_public_case_plan,
    validate_session_public_adapter,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, load, validate
from bcs_e2e.run_state import RunStateError


PROFILES = ROOT / "profiles"


class _SharedAdapter:
    def run_shared_session_public_case(self):
        pass
    def preflight(self): pass
    def group_create(self, body): pass
    def group_read(self, group_id): pass
    def session_create(self, group_id, body): pass
    def session_read(self, session_id): pass
    def session_update(self, session_id, body): pass
    def session_messages(self, session_id): pass
    def session_token(self, session_id): pass
    def session_ws_connect(self, group_id, session_id, token): pass
    def session_ws_close(self, session_id): pass
    def session_delete(self, session_id): pass
    def session_verify_deleted(self, session_id): pass
    def group_delete(self, group_id): pass
    def group_verify_deleted(self, group_id): pass
    def session_quiesce(self, session_id): pass


class PublicSessionCaseTests(unittest.TestCase):
    def test_plan_binds_the_exact_session_helper_and_shared_executor(self):
        plan = session_public_case_plan()
        self.assertEqual(plan["case_id"], CASE_ID)
        self.assertEqual(plan["legacy_id"], PARENT)
        self.assertEqual(plan["requirement_id"], "SESSION.public-jwt-ws")
        self.assertEqual(plan["source_function"], "_story_connect_with_group_session_jwt")
        self.assertEqual(plan["execution_case_id"], EXECUTION_CASE_ID)
        self.assertEqual(plan["local_execution_case_id"], LOCAL_EXECUTION_CASE_ID)
        self.assertEqual(plan["local_executor_status"], "LOCAL_FULL_EXECUTOR")
        self.assertEqual(plan["remote_validation"], "NOT_RUN")
        self.assertFalse(plan["grants_release_credit"])
        self.assertIn("singlebox-public-v1", plan["environments"])
        self.assertIn("preprod-public-v1", plan["environments"])

    def test_singlebox_admission_has_routes_and_local_scope_without_a_request(self):
        profile = load(PROFILES / "local.singlebox.example.json")
        result = session_public_case_admission(profile)
        self.assertEqual(result["case_id"], CASE_ID)
        self.assertEqual(result["status"], "SINGLEBOX_OWNED_LOCAL_READY")
        self.assertEqual(result["route_status"], "PUBLIC_V1_BOUND")
        self.assertEqual(result["effect_admission"]["status"], "OWNED_LOCAL_SCOPE")
        self.assertFalse(result["ready_to_execute"])
        self.assertFalse(result["grants_release_credit"])

    def test_gateway_admission_fails_closed_on_reviewed_effects(self):
        profile = json.loads((PROFILES / "preprod.example.json").read_text())
        profile["human"]["user_id"] = "39464"
        profile["actors"]["driver"].update(bot_id="20261010_gekng5hk", owner_id="39464")
        profile["actors"]["member"].update(bot_id="20261010_zy82brbu", owner_id="39464")
        profile = validate(profile)
        result = session_public_case_admission(profile)
        self.assertEqual(result["status"], "GATEWAY_EFFECT_BLOCKED")
        self.assertEqual(result["effect_admission"]["status"], "BLOCKED")
        self.assertEqual(result["remote_validation"], "NOT_RUN")
        self.assertFalse(result["ready_to_execute"])

    def test_unsupported_entrypoints_or_partial_routes_are_rejected_offline(self):
        profile = load(PROFILES / "local.singlebox.example.json")
        malformed = json.loads(json.dumps(profile))
        malformed["entrypoint"]["kind"] = "gateway"
        malformed["environment"]["tier"] = "local"
        with self.assertRaises(ConfigError):
            session_public_case_admission(malformed)

        missing_route = json.loads(json.dumps(profile))
        missing_route["route_bindings"].pop("session.token")
        with self.assertRaises(ConfigError):
            session_public_case_admission(missing_route)

        wrong_face = json.loads(json.dumps(profile))
        wrong_face["entrypoint"]["api_face"] = "legacy"
        with self.assertRaises(ConfigError):
            session_public_case_admission(wrong_face)

    def test_shared_adapter_contract_and_projection_do_not_extend_credit(self):
        validate_session_public_adapter(_SharedAdapter())
        missing = _SharedAdapter()
        missing.session_ws_connect = None
        with self.assertRaises(RunStateError):
            validate_session_public_adapter(missing)

        projection = project_session_public_result({
            "case_id": EXECUTION_CASE_ID, "status": "PASS",
            "functional": "PASS", "cleanup": "PASS",
        })
        self.assertEqual(projection["case_id"], CASE_ID)
        self.assertEqual(projection["requirement_id"], REQUIREMENT_ID)
        self.assertEqual(projection["execution_case_id"], EXECUTION_CASE_ID)
        self.assertEqual(projection["full_story_status"], "NOT_MIGRATED")
        self.assertFalse(projection["grants_release_credit"])

        with self.assertRaises(RunStateError):
            project_session_public_result({"case_id": "PRE-01", "status": "PASS"})

    def test_plan_inventory_includes_the_new_dual_slice_without_release_credit(self):
        rows = migration_plan()["public_v1_case_slices"]
        row = next(item for item in rows if item["case_id"] == CASE_ID)
        self.assertEqual(row["requirement_id"], REQUIREMENT_ID)
        self.assertEqual(row["adapter_method"], ADAPTER_METHOD)
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])
        ness = session_public_adaptability()
        self.assertIn("PRE-02", ness["blocker"])
        self.assertIn("effect admission", ness["blocker"])


if __name__ == "__main__":
    unittest.main()
