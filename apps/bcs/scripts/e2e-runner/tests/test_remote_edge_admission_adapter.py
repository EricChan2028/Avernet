"""Public native-face Edge admission slices are contract-tested, not credited."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.edge_read_case import (
    ADMISSION_PUBLIC_CASE_ID,
    admission_public_adaptability,
    execute_edge_read_case,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_edge_admission_adapter import RemoteEdgeAdmissionAdapter
from bcs_e2e.route_bindings import (
    edge_admission_actor_kind_path,
    edge_admission_human_actor_path,
)
from bcs_e2e.run_state import RunStateError

TOKEN = "driver-bot-private-token"
COOKIE = "human-cookie-private-value"
HUMAN_ACTOR_PATH = "/bots/driver-bot/admission?actor=human_owner-1"
ACTOR_KIND_PATH = "/bots/driver-bot/admission?actor=owner-1&actor_kind=human"


def profile(*, gateway=False):
    value = json.loads((ROOT / "profiles/local.singlebox.example.json").read_text())
    value["environment"]["id"] = "preprod-profile" if gateway else "local-singlebox-test"
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    if gateway:
        value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
        value["entrypoint"].update(kind="gateway", origin="https://teamclawgw.example.com")
        value["environment"].update(tier="preprod", expected_bcs_env="pre",
                                   lifecycle="remote-attach", deployment="managed",
                                   provider="internal")
        value["observer"].update(kind="alisddbquery", server="observ",
                                 database="agentclawdb",
                                 environment_binding_confirmed=True)
    else:
        value["auth"]["header_env"].update(Authorization="BCS_E2E_DRIVER_BOT_TOKEN",
                                           Cookie="BCS_E2E_HUMAN_COOKIE")
    admission_auth = "human_cookie" if gateway else "unauthenticated"
    value["edge_case_bindings"] = {
        "edge_admission_human_actor.read": {
            "method": "GET", "path": edge_admission_human_actor_path(value),
            "api_face": "internal-v1", "auth": admission_auth,
            "response": "admission-json", "traceparent": "disabled",
        },
        "edge_admission_actor_kind.read": {
            "method": "GET", "path": edge_admission_actor_kind_path(value),
            "api_face": "internal-v1", "auth": admission_auth,
            "response": "admission-json", "traceparent": "disabled",
        },
    }
    return validate(value)


class RemoteEdgeAdmissionContractTests(unittest.TestCase):
    def test_bindings_use_exact_dedicated_actor_routes_without_credentials(self):
        value = profile()
        self.assertEqual(value["edge_case_bindings"]["edge_admission_human_actor.read"]["path"],
                         HUMAN_ACTOR_PATH)
        self.assertEqual(value["edge_case_bindings"]["edge_admission_actor_kind.read"]["path"],
                         ACTOR_KIND_PATH)
        for binding in value["edge_case_bindings"].values():
            self.assertEqual(binding["method"], "GET")
            self.assertEqual(binding["api_face"], "internal-v1")
            self.assertEqual(binding["auth"], "unauthenticated")
            self.assertEqual(binding["response"], "admission-json")

    def test_binding_drift_fails_before_network(self):
        mutations = {
            "method": lambda v: v["edge_case_bindings"]["edge_admission_human_actor.read"].update(method="POST"),
            "path": lambda v: v["edge_case_bindings"]["edge_admission_human_actor.read"].update(path=HUMAN_ACTOR_PATH + "&x=1"),
            "actor": lambda v: v["edge_case_bindings"]["edge_admission_human_actor.read"].update(
                path="/bots/driver-bot/admission?actor=member-bot"),
            "face": lambda v: v["edge_case_bindings"]["edge_admission_human_actor.read"].update(api_face="public-v1"),
            "auth": lambda v: v["edge_case_bindings"]["edge_admission_human_actor.read"].update(auth="human_cookie"),
            "response": lambda v: v["edge_case_bindings"]["edge_admission_actor_kind.read"].update(response="public-envelope"),
            "manifest": lambda v: v["entrypoint"].update(manifest_face="legacy"),
            "entrypoint": lambda v: v["entrypoint"].update(kind="mock", origin="mock://local"),
            "unsupported": lambda v: v["edge_case_bindings"].update(other={}),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                value = profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_a_partial_admission_binding_fails_in_the_adapter_before_network(self):
        value = profile()
        value["edge_case_bindings"].pop("edge_admission_actor_kind.read")
        with patch.dict("os.environ", {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                                      "BCS_E2E_HUMAN_COOKIE": COOKIE}),              patch("bcs_e2e.transport.build_opener") as factory:
            with self.assertRaises(RunStateError):
                RemoteEdgeAdmissionAdapter(value)
            factory.return_value.open.assert_not_called()

    def test_admission_plan_and_inventory_stay_uncredited(self):
        adaptability = admission_public_adaptability()
        self.assertEqual(adaptability["case_id"], ADMISSION_PUBLIC_CASE_ID)
        self.assertEqual(adaptability["legacy_id"], "test_ep_admission")
        self.assertEqual(adaptability["requirement_ids"],
                         ["EDGE-ADMISSION.human-actor", "EDGE-ADMISSION.actor-kind"])
        self.assertEqual(adaptability["local_executor_status"], "LOCAL_FULL_EXECUTOR")
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        rows = migration_plan()["public_v1_case_slices"]
        row = next(item for item in rows if item["case_id"] == ADMISSION_PUBLIC_CASE_ID)
        self.assertEqual(row["remote_validation"], "NOT_RUN")
        self.assertFalse(row["grants_release_credit"])


class RemoteEdgeAdmissionExecutionTests(unittest.TestCase):
    def run_case(self, responses, profile_value=None):
        records = []
        environment = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                        "BCS_E2E_HUMAN_COOKIE": COOKIE}
        with patch.dict("os.environ", environment), \
             patch("bcs_e2e.transport.build_opener") as factory:
            iterator = iter(responses)
            def open(request, timeout=None):
                records.append(request)
                return next(iterator)
            factory.return_value.open.side_effect = open
            adapter = RemoteEdgeAdmissionAdapter(
                profile_value if profile_value is not None else profile())
            return execute_edge_read_case(ADMISSION_PUBLIC_CASE_ID, adapter), records

    def test_both_native_reads_send_no_identity_or_credentials(self):
        body = {"allowed": True, "grants": [], "reason_code": "ok",
                "public_default": False}
        execution, requests = self.run_case([Reply(body), Reply(body)])
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual([request.method for request in requests], ["GET", "GET"])
        self.assertEqual([request.full_url.split("http://127.0.0.1:21000", 1)[1]
                          for request in requests],
                         [HUMAN_ACTOR_PATH, ACTOR_KIND_PATH])
        for request in requests:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn("authorization", headers)
            self.assertNotIn("cookie", headers)
            self.assertEqual(headers["accept"], "application/json")
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["case_id"], ADMISSION_PUBLIC_CASE_ID)
        self.assertEqual([row["data"]["allowed"] for row in snapshot["steps"]],
                         [True, True])
        self.assertEqual([row["evidence"]["credentials_sent"] for row in snapshot["steps"]],
                         [False, False])
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_gateway_admission_reads_use_the_same_routes_with_cookie_not_token(self):
        value = profile(gateway=True)
        self.assertEqual(value["edge_case_bindings"]["edge_admission_human_actor.read"]["path"],
                         HUMAN_ACTOR_PATH)
        self.assertEqual(value["edge_case_bindings"]["edge_admission_actor_kind.read"]["path"],
                         ACTOR_KIND_PATH)
        for binding in value["edge_case_bindings"].values():
            self.assertEqual(binding["auth"], "human_cookie")
        body = {"allowed": True, "grants": [], "reason_code": "ok",
                "public_default": False}
        execution, requests = self.run_case([Reply(body), Reply(body)], profile(gateway=True))
        self.assertEqual(execution.status, "PASS")
        for request in requests:
            self.assertEqual(request.headers.get("Cookie"), COOKIE)
            self.assertNotIn("Authorization", request.headers)
        snapshot = execution.snapshot()
        self.assertEqual([row["evidence"]["credentials_sent"] for row in snapshot["steps"]],
                         [True, True])
        self.assertEqual([row["evidence"]["identity"] for row in snapshot["steps"]],
                         ["HUMAN_COOKIE_DRIVER_VIEW", "HUMAN_COOKIE_DRIVER_VIEW"])
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_invalid_admission_results_get_zero_partial_credit(self):
        bodies = (
            {"allowed": "true", "grants": [], "reason_code": "ok", "public_default": False},
            {"allowed": True, "grants": {}, "reason_code": "ok", "public_default": False},
            {"allowed": True, "grants": [], "reason_code": "ok"},
            {"allowed": True, "grants": [], "reason_code": "ok", "public_default": "false"},
            {"allowed": True, "grants": [], "reason_code": "ok", "public_default": False, "extra": 1},
            {"allowed": True, "grants": [{} for _ in range(101)], "reason_code": "ok",
             "public_default": False},
        )
        for body in bodies:
            with self.subTest(body=body):
                execution, requests = self.run_case([Reply(body)])
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(len(requests), 1)
                self.assertEqual(execution.steps[0].status, "NOT_RUN")

    def test_route_drift_or_helper_repetition_blocks_before_the_second_read(self):
        with patch.dict("os.environ", {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                                      "BCS_E2E_HUMAN_COOKIE": COOKIE}), \
             patch("bcs_e2e.transport.build_opener") as factory:
            adapter = RemoteEdgeAdmissionAdapter(profile())
            records = []
            def open(request, timeout=None):
                records.append(request)
                return Reply({"allowed": True, "grants": [], "reason_code": "ok",
                              "public_default": False})
            factory.return_value.open.side_effect = open
            self.assertEqual(adapter.human_actor_binding["path"], HUMAN_ACTOR_PATH)
            adapter.human_actor_binding = dict(adapter.human_actor_binding,
                                               path=HUMAN_ACTOR_PATH + "/drift")
            with self.assertRaises(RunStateError):
                adapter.read_admission_human_actor()
            self.assertEqual(len(records), 1)

        with patch.dict("os.environ", {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN,
                                      "BCS_E2E_HUMAN_COOKIE": COOKIE}), \
             patch("bcs_e2e.transport.build_opener") as factory:
            adapter = RemoteEdgeAdmissionAdapter(profile())
            adapter._attempted.add("read_admission_human_actor")
            with self.assertRaises(RunStateError):
                adapter.read_admission_human_actor()
            factory.return_value.open.assert_not_called()


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.code = code
        self.headers = headers or {}


if __name__ == "__main__":
    unittest.main()
