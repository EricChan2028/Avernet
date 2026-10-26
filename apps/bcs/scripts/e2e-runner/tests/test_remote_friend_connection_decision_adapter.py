"""Cross-environment friend accept/reject contract tests; no real request is made."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_friend_connection_decision_contract import (
    CASE_ID,
    execute_friend_connection_decision_case,
    friend_connection_decision_adaptability,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_friend_connection_decision_adapter import RemoteFriendConnectionDecisionAdapter
from bcs_e2e.route_bindings import (
    friend_connection_member_requests_path,
    friend_connection_driver_requests_path,
    friend_connection_driver_connections_path,
    FRIEND_CONNECTION_DECISION_CREATE_PATH,
    FRIEND_CONNECTION_REJECT_PATH,
    FRIEND_CONNECTION_ACCEPT_PATH,
)
from bcs_e2e.run_state import RunStateError

COOKIE = "human-cookie-private-value"


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


def envelope(data, code=20000):
    return json.dumps({"code": code, "message": "OK", "request_id": "server-request",
                       "data": data}).encode()


def create_result(*request_ids):
    return {"request_ids": list(request_ids), "edge_ids": [],
            "status": "pending", "auto_accepted": False}


def request_view(request_id, sender, receiver, status="pending", decided_by=None):
    value = {"request_id": request_id,
             "from_actor": {"type": "bot", "id": sender},
             "to_actor": {"type": "bot", "id": receiver},
             "status": status,
             "created_by": {"type": "bot", "id": sender}}
    if decided_by is not None:
        value["decided_by"] = {"type": "bot", "id": decided_by}
    return value


def request_page(*items):
    return {"items": list(items), "total": len(items), "page": 1, "page_size": 20}


def connection_page(*actor_ids):
    return {"items": [{"actor": {"type": "bot", "id": value}, "is_online": True}
                      for value in actor_ids],
            "total": len(actor_ids), "page": 1, "page_size": 20}


def base_profile(*, gateway=False, bounded=True):
    value = json.loads((ROOT / "profiles/local.singlebox.example.json").read_text())
    value["environment"]["id"] = "preprod-friend-decision" if gateway else "local-friend-decision"
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["auth"] = {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE"}}
    value["relationships"]["friendship"] = (
        "bounded-owned-bot-decisions" if bounded else "unchanged")
    driver = "driver-bot"
    member = "member-bot"
    common = {"api_face": "public-v1", "auth": "human_cookie",
              "response": "public-envelope", "traceparent": "disabled"}
    value["edge_case_bindings"] = {
        "friend_connection_decision.reject.create": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_DECISION_CREATE_PATH,
            "from_actor_id": driver, "to_actor_id": member,
        },
        "friend_connection_decision.accept.create": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_DECISION_CREATE_PATH,
            "from_actor_id": member, "to_actor_id": driver,
        },
        "friend_connection_decision.member_requests.read": {
            **common, "method": "GET", "path": friend_connection_member_requests_path(value),
            "actor_id": member,
        },
        "friend_connection_decision.reject": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_REJECT_PATH,
            "from_actor_id": driver, "to_actor_id": member,
        },
        "friend_connection_decision.driver_requests.read": {
            **common, "method": "GET", "path": friend_connection_driver_requests_path(value),
            "actor_id": driver,
        },
        "friend_connection_decision.accept": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_ACCEPT_PATH,
            "from_actor_id": member, "to_actor_id": driver,
        },
        "friend_connection_decision.driver_connections.read": {
            **common, "method": "GET", "path": friend_connection_driver_connections_path(value),
            "actor_id": driver,
        },
    }
    if not bounded:
        value["edge_case_bindings"] = {}
    if gateway:
        value["environment"].update(tier="preprod", expected_bcs_env="pre",
                                  lifecycle="remote-attach", deployment="managed",
                                  provider="internal")
        value["entrypoint"].update(kind="gateway", origin="https://teamclawgw.example.com")
        value["observer"].update(kind="alisddbquery", server="observ",
                                  database="agentclawdb", environment_binding_confirmed=True)
    return validate(value)


def standard_replies():
    reads = (
        envelope(request_page(request_view("reject-request", "driver-bot", "member-bot"))),
        envelope(connection_page()),
        envelope(request_page(request_view("accept-request", "member-bot", "driver-bot"))),
        envelope(connection_page("member-bot")),
    )
    writes = (
        Reply(envelope(create_result("reject-request", "reject-reverse"), 20100), 201),
        envelope(request_view("reject-request", "driver-bot", "member-bot", "rejected", "member-bot")),
        Reply(envelope(create_result("accept-request", "accept-reverse"), 20100), 201),
        envelope(request_view("accept-request", "member-bot", "driver-bot", "approved", "driver-bot")),
    )
    return reads, writes


class FriendConnectionDecisionTests(unittest.TestCase):
    def run_case(self, reads=None, writes=None, profile_value=None, initialize=True):
        if reads is None or writes is None:
            standard_reads, standard_writes = standard_replies()
            reads = standard_reads if reads is None else reads
            writes = standard_writes if writes is None else writes
        requests = []
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
             patch("bcs_e2e.transport.build_opener") as read_factory, \
             patch("bcs_e2e.write_transport.build_opener") as write_factory:
            read_iterator = (Reply(body) if isinstance(body, bytes) else body for body in reads)
            write_iterator = (Reply(body) if isinstance(body, bytes) else body
                              for body in writes)
            def read_open(request, timeout=None):
                requests.append(request)
                return next(read_iterator)
            def write_open(request, timeout=None):
                requests.append(request)
                return next(write_iterator)
            read_factory.return_value.open.side_effect = read_open
            write_factory.return_value.open.side_effect = write_open
            adapter = RemoteFriendConnectionDecisionAdapter(
                profile_value if profile_value is not None else base_profile())
            if initialize:
                return execute_friend_connection_decision_case(adapter), requests
            return adapter, requests

    def test_case_guard_accepts_the_cross_environment_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/local_friend_connection_decision_contract.py",
                 ROOT / "bcs_e2e/remote_friend_connection_decision_adapter.py")
        result = subprocess.run([sys.executable, str(guard), *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_declared_decisions_require_explicit_bounded_admission(self):
        cases = {
            "unchanged-with-binding": bounded_without_admission,
            "missing-binding": missing_binding,
            "cookie-only": extra_authorization,
            "non-exclusive": non_exclusive,
            "owned-local-profile": owned_local,
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_bounded_profile_rejects_route_and_actor_drift(self):
        cases = {
            "create-direction": lambda v: v["edge_case_bindings"]["friend_connection_decision.reject.create"].update(
                from_actor_id="member-bot", to_actor_id="member-bot"),
            "reject-direction": lambda v: v["edge_case_bindings"]["friend_connection_decision.reject"].update(
                from_actor_id="member-bot"),
            "accept-direction": lambda v: v["edge_case_bindings"]["friend_connection_decision.accept"].update(
                to_actor_id="member-bot"),
            "wrong-receiver-page": lambda v: v["edge_case_bindings"]["friend_connection_decision.member_requests.read"].update(
                path="/openapi/v1/collaboration/friend-connections/requests?direction=received"),
            "wrong-auth": lambda v: v["edge_case_bindings"]["friend_connection_decision.reject.create"].update(
                auth="authenticated_bot"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_case_plan_and_inventory_claim_no_credit(self):
        adaptability = friend_connection_decision_adaptability()
        self.assertEqual(adaptability["case_id"], CASE_ID)
        self.assertEqual(adaptability["requirement_ids"],
                         ["FRIEND.protected-reject", "FRIEND.protected-accept"])
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        row = next(item for item in migration_plan()["public_v1_case_slices"]
                    if item["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-public-slice", "preprod-public-slice"])
        self.assertFalse(row["grants_release_credit"])

    def test_local_profile_runs_reject_then_accept_in_order(self):
        execution, requests = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual([request.method for request in requests],
                         ["POST", "GET", "POST", "GET", "POST", "GET", "POST", "GET"])
        self.assertEqual(requests[0].full_url.endswith(FRIEND_CONNECTION_DECISION_CREATE_PATH), True)
        self.assertIn("reject-request", requests[2].full_url)
        self.assertIn("accept-request", requests[6].full_url)
        for index in (0, 2, 4, 6):
            headers = {name.lower(): value for name, value in requests[index].header_items()}
            self.assertNotIn("authorization", headers)
            self.assertEqual(headers["cookie"], COOKIE)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["status"], "rejected")
        self.assertFalse(snapshot["steps"][0]["data"]["friendship_visible"])
        self.assertEqual(snapshot["steps"][1]["data"]["status"], "approved")
        self.assertTrue(snapshot["steps"][1]["data"]["friendship_visible"])
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(COOKIE, serialized)

    def test_gateway_profile_uses_the_same_public_contract(self):
        execution, requests = self.run_case(profile_value=base_profile(gateway=True))
        self.assertEqual(execution.status, "PASS")
        self.assertTrue(all(request.full_url.startswith("https://teamclawgw.example.com/")
                           for request in requests))

    def test_wrong_response_blocks_without_partial_credit(self):
        reads, writes = standard_replies()
        no_exact_request = envelope(request_page())
        wrong_status = envelope(request_view("reject-request", "driver-bot", "member-bot",
                                                "approved", "member-bot"))
        cases = {
            "no-exact-request": ((no_exact_request, reads[1], *reads[2:]), reads),
            "wrong-reject-status": ((reads[0], reads[1], *reads[2:]),
                                   (writes[0], wrong_status, *writes[2:])),
            "friend-visible-after-reject": ((reads[0], envelope(connection_page("member-bot")),
                                             *reads[2:]), (writes[0], writes[1], *writes[2:])), 
        }
        for name, (mutated_reads, mutated_writes) in cases.items():
            with self.subTest(name):
                execution, _ = self.run_case(reads=mutated_reads, writes=mutated_writes)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[1].status, "NOT_RUN")

    def test_accept_requires_a_completed_reject_step(self):
        adapter, _ = self.run_case(initialize=False)
        with self.assertRaisesRegex(RunStateError, "must follow rejection"):
            adapter.accept_friend_connection_request()

    def test_profile_admission_fails_before_network_for_initial_pilot(self):
        value = base_profile()
        value["relationships"]["friendship"] = "unchanged"
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
             patch("bcs_e2e.transport.build_opener") as read_factory, \
             patch("bcs_e2e.write_transport.build_opener") as write_factory:
            with self.assertRaises(RunStateError):
                RemoteFriendConnectionDecisionAdapter(value)
            read_factory.return_value.open.assert_not_called()
            write_factory.return_value.open.assert_not_called()


def bounded_without_admission(value):
    value["relationships"]["friendship"] = "unchanged"
    # Recreate the decision bindings without validation-provided state.
    bounded = base_profile()["edge_case_bindings"]
    value["edge_case_bindings"] = bounded


def missing_binding(value):
    value["edge_case_bindings"].pop("friend_connection_decision.reject.create")


def extra_authorization(value):
    value["auth"]["header_env"]["Authorization"] = "BCS_E2E_DRIVER_BOT_TOKEN"


def non_exclusive(value):
    value["policy"]["exclusive_use_confirmed"] = False


def owned_local(value):
    value["auth"] = {"kind": "owned-local"}


if __name__ == "__main__":
    unittest.main()
