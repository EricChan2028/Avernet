"""Cross-environment public friendship auto-accept contract tests; no live request."""

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
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.local_friend_public_accept_contract import (
    CASE_ID,
    execute_friend_public_accept_case,
    friend_public_accept_adaptability,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_friend_public_accept_adapter import RemoteFriendPublicAcceptAdapter
from bcs_e2e.route_bindings import (
    FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH,
    friend_connection_public_accept_driver_connections_path,
)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.local_friend_connection_decision_contract import CASE_ID as FRIEND_DECISION_CASE_ID

COOKIE = "human-cookie-private-value"


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


def envelope(data, code=20000):
    return json.dumps({"code": code, "message": "OK", "request_id": "server-request",
                       "data": data}).encode()


def public_accept_result(*, request_count=2, edge_count=2,
                         status="approved", auto_accepted=True):
    return {
        "request_ids": [f"public-request-{index}" for index in range(request_count)],
        "edge_ids": [1000 + index for index in range(edge_count)],
        "status": status,
        "auto_accepted": auto_accepted,
    }


def connection_page(*actor_ids):
    return {"items": [{"actor": {"type": "bot", "id": value}, "is_online": True}
                       for value in actor_ids],
            "total": len(actor_ids), "page": 1, "page_size": 100}


def base_profile(*, gateway=False, admitted=True):
    value = json.loads((ROOT / "profiles/local.singlebox.example.json").read_text())
    value["environment"]["id"] = "preprod-friend-public" if gateway else "local-friend-public"
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["auth"] = {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE"}}
    driver, member = "driver-bot", "member-bot"
    common = {"api_face": "public-v1", "auth": "human_cookie",
              "response": "public-envelope", "traceparent": "disabled"}
    value["edge_case_bindings"] = {
        "friend_connection_public_accept.create": {
            **common, "method": "POST",
            "path": FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH,
            "from_actor_id": driver, "to_actor_id": member,
        },
        "friend_connection_public_accept.driver_connections.read": {
            **common, "method": "GET",
            "path": friend_connection_public_accept_driver_connections_path(value),
            "actor_id": driver,
        },
    }
    value["relationships"]["friendship"] = (
        "bounded-owned-bot-public-acceptance" if admitted else "unchanged")
    if not admitted:
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
    reads = (envelope(connection_page()),
             envelope(connection_page("member-bot")),
             envelope(connection_page("member-bot")))
    writes = (Reply(envelope(public_accept_result(), 20100), 201),)
    return reads, writes


class OutOfPairAcceptAdapter(RemoteFriendPublicAcceptAdapter):
    """Present an out-of-pair projection from the accept step."""

    def auto_accept_public_friend_connection(self) -> AdapterCall:
        result = super().auto_accept_public_friend_connection()
        data = dict(result.data)
        data["from_actor_id"] = "intruder-bot"
        return AdapterCall(data=data, evidence=dict(result.evidence))


class OutOfPairListAdapter(RemoteFriendPublicAcceptAdapter):
    """Present an out-of-pair projection from the list step."""

    def read_established_friend_list(self) -> AdapterCall:
        result = super().read_established_friend_list()
        data = dict(result.data)
        data["friend_actor_id"] = "intruder-bot"
        return AdapterCall(data=data, evidence=dict(result.evidence))


class FriendPublicAcceptTests(unittest.TestCase):
    def run_case(self, reads=None, writes=None, profile_value=None,
                 initialize=True, adapter_factory=None):
        if reads is None or writes is None:
            default_reads, default_writes = standard_replies()
            reads = default_reads if reads is None else reads
            writes = default_writes if writes is None else writes
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
            profile = profile_value if profile_value is not None else base_profile()
            adapter = (RemoteFriendPublicAcceptAdapter(profile) if adapter_factory
                       is None else adapter_factory(profile))
            if initialize:
                return execute_friend_public_accept_case(adapter), requests
            return adapter, requests

    def test_case_guard_accepts_the_cross_environment_files(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/local_friend_public_accept_contract.py",
                 ROOT / "bcs_e2e/remote_friend_public_accept_adapter.py")
        result = subprocess.run([sys.executable, str(guard), *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_bounded_public_acceptance_requires_exact_admission(self):
        cases = {
            "unchanged": lambda v: v.update(
                **{"relationships": {"friendship": "unchanged"},
                   "edge_case_bindings": base_profile()["edge_case_bindings"]}),
            "missing-read": lambda v: v["edge_case_bindings"].pop(
                "friend_connection_public_accept.driver_connections.read"),
            "wrong-mode": lambda v: v["relationships"].update(
                friendship="bounded-owned-bot-decisions"),
            "mixed-mode": lambda v: v["edge_case_bindings"].update(
                base_profile()["edge_case_bindings"],
                **{"friend_connection_decision.reject.create": dict(
                    base_profile()["edge_case_bindings"]["friend_connection_public_accept.create"],
                    from_actor_id="driver-bot", to_actor_id="member-bot")}),
            "extra-authorization": lambda v: v["auth"]["header_env"].update(
                Authorization="BCS_E2E_DRIVER_BOT_TOKEN"),
            "non-exclusive": lambda v: v["policy"].update(exclusive_use_confirmed=False),
            "owned-local": lambda v: v.update(auth={"kind": "owned-local"}),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_bounded_profile_rejects_route_and_actor_drift(self):
        cases = {
            "reverse-pair": lambda v: v["edge_case_bindings"][
                "friend_connection_public_accept.create"].update(
                    from_actor_id="member-bot"),
            "wrong-reader": lambda v: v["edge_case_bindings"][
                "friend_connection_public_accept.driver_connections.read"].update(
                    actor_id="member-bot"),
            "wrong-route": lambda v: v["edge_case_bindings"][
                "friend_connection_public_accept.driver_connections.read"].update(
                    path="/openapi/v1/collaboration/friend-connections?actor_type=bot&actor_id=member-bot"),
            "wrong-auth": lambda v: v["edge_case_bindings"][
                "friend_connection_public_accept.create"].update(auth="authenticated_bot"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_case_plan_and_inventory_claim_no_credit(self):
        adaptability = friend_public_accept_adaptability()
        self.assertEqual(adaptability["case_id"], CASE_ID)
        self.assertEqual(adaptability["requirement_ids"],
                         ["FRIEND.public-accept", "FRIEND.list"])
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        rows = migration_plan()["public_v1_case_slices"]
        row = next(item for item in rows if item["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-public-slice", "preprod-public-slice"])
        self.assertFalse(row["grants_release_credit"])
        self.assertEqual(rows[-2]["case_id"], FRIEND_DECISION_CASE_ID)
        self.assertEqual(rows[-1]["case_id"], CASE_ID)

    def test_local_profile_runs_read_create_read_in_order(self):
        execution, requests = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.total_count, 2)
        self.assertEqual([request.method for request in requests],
                         ["GET", "POST", "GET", "GET"])
        self.assertTrue(requests[0].full_url.endswith(
            friend_connection_public_accept_driver_connections_path(base_profile())))
        self.assertTrue(requests[1].full_url.endswith(FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH))
        for index in (0, 1, 2):
            headers = {name.lower(): value for name, value in requests[index].header_items()}
            self.assertNotIn("authorization", headers)
            self.assertEqual(headers["cookie"], COOKIE)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["status"], "approved")
        self.assertEqual(snapshot["steps"][0]["data"]["request_count"], 2)
        self.assertEqual(snapshot["steps"][0]["data"]["edge_count"], 2)
        self.assertEqual(snapshot["steps"][1]["data"]["list_contains_friend"], True)
        self.assertEqual(snapshot["steps"][1]["data"]["friend_count"], 1)
        self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))

    def test_gateway_profile_uses_the_same_public_contract(self):
        execution, requests = self.run_case(profile_value=base_profile(gateway=True))
        self.assertEqual(execution.status, "PASS")
        self.assertTrue(all(request.full_url.startswith("https://teamclawgw.example.com/")
                           for request in requests))

    def test_wrong_response_blocks_without_partial_credit(self):
        reads, writes = standard_replies()
        cases = {
            "already-friends": ((envelope(connection_page("member-bot")), reads[1]), writes),
            "public-no-edge": (reads, (Reply(envelope(
                public_accept_result(status="public_no_edge"), 20100), 201),)),
            "manual-pending": (reads, (Reply(envelope(
                public_accept_result(status="pending", auto_accepted=False), 20100), 201),)),
            "one-request": (reads, (Reply(envelope(
                public_accept_result(request_count=1, edge_count=1), 20100), 201),)),
            "still-invisible": ((reads[0], envelope(connection_page()),
                                  envelope(connection_page())), writes),
        }
        for name, (mutated_reads, mutated_writes) in cases.items():
            with self.subTest(name):
                execution, _ = self.run_case(reads=mutated_reads,
                                              writes=mutated_writes)
                self.assertEqual(execution.status, "BLOCKED")
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, "NOT_RUN")

    def test_case_is_one_shot_and_list_requires_establishment(self):
        adapter, _ = self.run_case(initialize=False)
        with self.assertRaisesRegex(RunStateError, "must follow established"):
            adapter.read_established_friend_list()
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
             patch("bcs_e2e.transport.build_opener") as read_factory, \
             patch("bcs_e2e.write_transport.build_opener") as write_factory:
            read_iterator = iter((Reply(envelope(connection_page())),
                                  Reply(envelope(connection_page("member-bot")))))
            write_iterator = iter((Reply(envelope(public_accept_result(), 20100), 201),))
            read_factory.return_value.open.side_effect = lambda request, timeout=None: next(read_iterator)
            write_factory.return_value.open.side_effect = lambda request, timeout=None: next(write_iterator)
            adapter.auto_accept_public_friend_connection()
            with self.assertRaisesRegex(RunStateError, "one-shot"):
                adapter.auto_accept_public_friend_connection()

    def test_contract_rejects_out_of_pair_accept_projection(self):
        execution, _ = self.run_case(adapter_factory=OutOfPairAcceptAdapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, "NOT_RUN")
        self.assertEqual(execution.steps[0].error_type, "RunStateError")

    def test_contract_rejects_out_of_pair_list_projection(self):
        execution, _ = self.run_case(adapter_factory=OutOfPairListAdapter)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(execution.steps[0].status, "PASS")
        self.assertEqual(execution.steps[1].status, "NOT_RUN")
        self.assertEqual(execution.steps[1].error_type, "RunStateError")

    def test_exact_identity_is_profile_driven_not_hardcoded(self):
        value = base_profile()
        value["actors"]["driver"].update(bot_id="ceo-bot-7", owner_id="owner-1")
        value["actors"]["member"].update(bot_id="eng-bot-9", owner_id="owner-1")
        common = {"api_face": "public-v1", "auth": "human_cookie",
                  "response": "public-envelope", "traceparent": "disabled"}
        value["edge_case_bindings"]["friend_connection_public_accept.create"] = {
            **common, "method": "POST",
            "path": FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH,
            "from_actor_id": "ceo-bot-7", "to_actor_id": "eng-bot-9"}
        value["edge_case_bindings"][
            "friend_connection_public_accept.driver_connections.read"] = {
            **common, "method": "GET",
            "path": friend_connection_public_accept_driver_connections_path(value),
            "actor_id": "ceo-bot-7"}
        reads = (envelope(connection_page()),
                 envelope(connection_page("eng-bot-9")),
                 envelope(connection_page("eng-bot-9")))
        writes = (Reply(envelope(public_accept_result(), 20100), 201),)
        execution, _ = self.run_case(reads=reads, writes=writes,
                                      profile_value=value)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.steps[0].data["from_actor_id"], "ceo-bot-7")
        self.assertEqual(execution.steps[0].data["to_actor_id"], "eng-bot-9")
        self.assertEqual(execution.steps[1].data["driver_actor_id"], "ceo-bot-7")
        self.assertEqual(execution.steps[1].data["friend_actor_id"], "eng-bot-9")

    def test_bad_list_readback_blocks_only_the_list_obligation(self):
        reads = (envelope(connection_page()),
                 envelope(connection_page("member-bot")),
                 envelope(connection_page()))
        writes = (Reply(envelope(public_accept_result(), 20100), 201),)
        execution, _ = self.run_case(reads=reads, writes=writes)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(execution.steps[0].status, "PASS")
        self.assertEqual(execution.steps[1].status, "NOT_RUN")

    def test_profile_admission_fails_before_network_for_initial_pilot(self):
        value = base_profile()
        value["relationships"]["friendship"] = "unchanged"
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
             patch("bcs_e2e.transport.build_opener") as read_factory, \
             patch("bcs_e2e.write_transport.build_opener") as write_factory:
            with self.assertRaises(RunStateError):
                RemoteFriendPublicAcceptAdapter(value)
            read_factory.return_value.open.assert_not_called()
            write_factory.return_value.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
