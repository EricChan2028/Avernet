"""Contract tests for remote friend-mutation route and transport bindings."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.route_bindings import (
    _EDGE_CASES,
    _FRIEND_MUTATION_STATION_OPS,
    ROUTES,
    validate_edge_case_bindings,
)
from bcs_e2e.transport import ObservationError, ReadClient
from bcs_e2e.write_transport import WriteClient, WriteTransportError

COOKIE = "human-cookie-private-value"
BOT_TOKEN = "owned-bot-private-token"
REGISTER_TOKEN = "register-token-private-value"
BOT_UUID = "runtime-bot-uuid"
DRIVER = "driver-bot"
MEMBER = "member-bot"
HUMAN = "owner-1"
ORIGIN = "https://bcn-direct.example.test"

_RESPONSES = {
    "friend_connection_mutation.register_token.read": "plain-json",
    "friend_connection_mutation.register_bot.create": "station-register-json",
    "friend_connection_mutation.legacy_revoke": "station-revoke-json",
    "friend_connection_mutation.station_admission.read": "admission-json",
    "friend_connection_mutation.station_discover_collaborate.read": "plain-json",
    "friend_connection_mutation.station_discover_role_no_org.read": "plain-json",
    "friend_connection_mutation.station_discover_org_no_requester.read": "plain-json",
}
_PAIR_OPS = {
    "friend_connection_mutation.create",
    "friend_connection_mutation.accept",
    "friend_connection_mutation.reject",
    "friend_connection_mutation.cancel",
}
_ACTOR_OPS = {
    "friend_connection_mutation.requests_sent.read",
    "friend_connection_mutation.requests_received.read",
    "friend_connection_mutation.requests_all.read",
    "friend_connection_mutation.connections.read",
}
_MUTATION_OPS = {
    "friend_connection_mutation.create",
    "friend_connection_mutation.accept",
    "friend_connection_mutation.reject",
    "friend_connection_mutation.cancel",
    "friend_connection_mutation.requests_sent.read",
    "friend_connection_mutation.requests_received.read",
    "friend_connection_mutation.requests_all.read",
    "friend_connection_mutation.connections.read",
    "friend_connection_mutation.bot_detail.read",
    "friend_connection_mutation.visibility.write",
    "friend_connection_mutation.revoke",
    "friend_connection_mutation.discover_denied.read",
    "friend_connection_mutation.register_token.read",
    "friend_connection_mutation.register_bot.create",
    "friend_connection_mutation.legacy_revoke",
    "friend_connection_mutation.station_admission.read",
    "friend_connection_mutation.station_discover_collaborate.read",
    "friend_connection_mutation.station_discover_role_no_org.read",
    "friend_connection_mutation.station_discover_org_no_requester.read",
}
_TARGET_OPS = {
    "friend_connection_mutation.bot_detail.read",
    "friend_connection_mutation.visibility.write",
    "friend_connection_mutation.revoke",
    "friend_connection_mutation.discover_denied.read",
    "friend_connection_mutation.legacy_revoke",
    "friend_connection_mutation.station_discover_collaborate.read",
}


def bindings_for(profile: dict, operations: set[str]) -> dict:
    result = {}
    for operation in operations:
        method, path, auth = _EDGE_CASES[operation]
        binding = {
            "method": method, "path": path,
            "api_face": "station-legacy" if operation in _FRIEND_MUTATION_STATION_OPS else "public-v1",
            "auth": auth,
            "response": _RESPONSES.get(operation, "public-envelope"),
            "traceparent": "disabled",
        }
        if operation in _PAIR_OPS:
            binding.update(from_actor_id=DRIVER, to_actor_id=MEMBER)
        elif operation in _ACTOR_OPS:
            binding.update(actor_id=DRIVER)
        elif operation in _TARGET_OPS:
            binding.update(target_actor_id=MEMBER)
        elif operation == "friend_connection_mutation.station_admission.read":
            binding.update(target_actor_id=MEMBER, actor_id=HUMAN)
        result[operation] = binding
    return result


def gateway_profile() -> dict:
    raw = json.loads((ROOT / "profiles/preprod.example.json").read_text())
    raw["environment"]["id"] = "friend-mutation-gateway"
    raw["environment"]["expected_bcs_env"] = "pre"
    raw["entrypoint"].update(kind="gateway", origin="https://gateway.example.test")
    raw["human"]["user_id"] = HUMAN
    raw["actors"]["driver"].update(bot_id=DRIVER, owner_id=HUMAN, dedicated=True)
    raw["actors"]["member"].update(bot_id=MEMBER, owner_id=HUMAN, dedicated=True)
    raw["auth"] = {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE"}}
    raw["observer"].update(server="observer", database="agentclawdb",
                            environment_binding_confirmed=True)
    raw["policy"].update(exclusive_use_confirmed=True)
    raw["relationships"]["friendship"] = "bounded-owned-bot-mutations"
    raw["edge_case_bindings"] = bindings_for(
        raw, _MUTATION_OPS - _FRIEND_MUTATION_STATION_OPS)
    return validate(raw)


def station_profile() -> dict:
    raw = {
        "schema_version": 1,
        "environment": {"tier": "preprod", "id": "friend-mutation-station",
                          "expected_bcs_env": "pre", "lifecycle": "remote-attach",
                          "deployment": "managed", "provider": "internal"},
        "entrypoint": {"kind": "bcs-direct", "origin": ORIGIN,
                         "api_face": "public-v1", "manifest_face": "legacy"},
        "human": {"user_id": HUMAN},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": HUMAN, "dedicated": True},
            "member": {"bot_id": MEMBER, "owner_id": HUMAN, "dedicated": True},
        },
        "auth": {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE",
                                  "Authorization": "BCS_E2E_OWNED_BOT_TOKEN"}},
        "browser_origin": ORIGIN,
        "relationships": {"friendship": "bounded-owned-bot-mutations"},
        "observer": {"kind": "alisddbquery", "server": "observer",
                       "database": "agentclawdb", "environment_binding_confirmed": True},
        "policy": {"fixture_pool": "friend-mutation-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 1048576,
                    "failure_retention_hours": 24},
        "route_bindings": {
            operation: {
                "method": method, "path": path, "api_face": "public-v1",
                "auth": auth, "response": "public-envelope", "traceparent": "disabled",
            }
            for operation, (method, path, auth) in ROUTES.items()
        },
        "edge_case_bindings": bindings_for(
            {"actors": {"driver": {"bot_id": DRIVER},
                         "member": {"bot_id": MEMBER},
                         "human": {"user_id": HUMAN}}},
            set(_FRIEND_MUTATION_STATION_OPS)),
    }
    profile = validate(raw)
    validate_edge_case_bindings(profile["edge_case_bindings"], profile)
    return profile


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.code = code
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def envelope(data: dict, *, code: int = 20000, http: int = 200,
             message: str = "OK") -> Reply:
    return Reply({"code": code, "message": message, "request_id": "server-request",
                  "data": data}, code=http)


class FriendMutationBindingTests(unittest.TestCase):
    def test_all_nineteen_bindings_pass_the_exact_face_contracts(self):
        gateway = gateway_profile()
        station = station_profile()
        self.assertEqual(set(gateway["edge_case_bindings"]),
                         _MUTATION_OPS - _FRIEND_MUTATION_STATION_OPS)
        self.assertEqual(set(station["edge_case_bindings"]), _FRIEND_MUTATION_STATION_OPS)
        self.assertEqual(len(gateway["edge_case_bindings"]) + len(station["edge_case_bindings"]), 19)
        self.assertEqual(gateway["edge_case_bindings"]["friend_connection_mutation.create"]["auth"],
                         "human_cookie")
        self.assertEqual(station["edge_case_bindings"][
            "friend_connection_mutation.register_bot.create"]["auth"], "register_token")
        self.assertEqual(station["edge_case_bindings"][
            "friend_connection_mutation.legacy_revoke"]["auth"], "human_cookie_owned_bot")
        self.assertEqual(station["edge_case_bindings"][
            "friend_connection_mutation.station_admission.read"]["auth"], "human_cookie")
        self.assertEqual(station["entrypoint"]["kind"], "bcs-direct")
        self.assertEqual(station["entrypoint"]["api_face"], "public-v1")
        self.assertEqual(station["entrypoint"]["manifest_face"], "legacy")
        self.assertEqual(set(station["route_bindings"]), set(ROUTES))

    def test_binding_drift_and_wrong_entrypoint_are_rejected(self):
        raw = gateway_profile()
        raw["edge_case_bindings"]["friend_connection_mutation.register_token.read"] = {
            "method": "GET", "path": "/register/token", "api_face": "station-legacy",
            "auth": "human_cookie", "response": "plain-json", "traceparent": "disabled",
        }
        with self.assertRaises(ConfigError):
            validate(raw)

        station = station_profile()
        station["edge_case_bindings"]["friend_connection_mutation.legacy_revoke"]["path"] += "/drift"
        with self.assertRaises(ConfigError):
            validate_edge_case_bindings(station["edge_case_bindings"], station)

        gateway_wrong_face = gateway_profile()
        gateway_wrong_face["edge_case_bindings"]["friend_connection_mutation.create"]["api_face"] = "station-legacy"
        with self.assertRaises(ConfigError):
            validate(gateway_wrong_face)

    def test_dual_identity_and_station_register_need_the_reviewed_context(self):
        station = station_profile()
        del station["auth"]["header_env"]["Authorization"]
        with self.assertRaisesRegex(ConfigError, "Cookie and Authorization"):
            validate_edge_case_bindings(station["edge_case_bindings"], station)

        station = station_profile()
        del station["auth"]["header_env"]["Cookie"]
        with self.assertRaisesRegex(ConfigError, "Cookie reference"):
            validate_edge_case_bindings(station["edge_case_bindings"], station)


class FriendMutationWriteTransportTests(unittest.TestCase):
    def client(self, profile_value=None):
        return WriteClient(profile_value if profile_value is not None else gateway_profile())

    def test_create_and_decision_discipline(self):
        data = {"request_ids": [1], "edge_ids": [], "status": "pending",
                 "auto_accepted": False}
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope(data, code=20100, http=201)
            client = self.client()
            result, record = client.create_edge_friend_request(DRIVER, MEMBER)
            request = factory.return_value.open.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url,
                         "https://gateway.example.test/openapi/v1/collaboration/friend-connections/requests")
        self.assertEqual(json.loads(request.data), {"from_actor": {"type": "bot", "id": DRIVER},
                                                     "to_actor": {"type": "bot", "id": MEMBER}})
        self.assertEqual(headers["cookie"], COOKIE)
        self.assertNotIn("authorization", headers)
        self.assertEqual(record["http_status"], 201)
        self.assertEqual(record["operation"], "friend_connection_mutation.create")
        self.assertNotIn(COOKIE, json.dumps(record, sort_keys=True))

        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope({"request_id": 1, "status": "approved"})
            client = self.client()
            client.decide_edge_friend_request("accept", 1, DRIVER, MEMBER)
            request = factory.return_value.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/requests/1/accept"))
        self.assertEqual(request.method, "POST")

    def test_negative_create_projection_is_admitted_only_explicitly(self):
        private = {"error_code": "internal_error"}
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = self.client()
            def open(request, timeout=None):
                return envelope(private, code=50000, http=500,
                                 message="Internal Server Error")
            factory.return_value.open.side_effect = open
            with self.assertRaisesRegex(WriteTransportError, "requires explicit error admission"):
                client.create_edge_friend_request(DRIVER, MEMBER,
                                                   expected=(500,), admit_error=False)
            projected, record = client.create_edge_friend_request(
                DRIVER, MEMBER, expected=(500,), admit_error=True)
        self.assertEqual(projected, {"code": 50000,
                                      "message": "Internal Server Error",
                                      "data": private, "error": "internal_error"})
        self.assertEqual(record["http_status"], 500)
        self.assertNotIn(COOKIE, json.dumps(record, sort_keys=True))

    def test_revoke_and_profile_patch_use_the_reviewed_query_and_patch_routes(self):
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope({"deleted": False})
            self.client().revoke_edge_friend_connection(MEMBER)
            revoke_request = factory.return_value.open.call_args.args[0]
        self.assertEqual(revoke_request.method, "DELETE")
        self.assertIn("target_actor_type=bot&target_actor_id=member-bot",
                      revoke_request.full_url)

        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope({"visibility": "private"})
            self.client().patch_bot_friend_profile(MEMBER, visibility="private")
            patch_request = factory.return_value.open.call_args.args[0]
        self.assertEqual(patch_request.method, "PATCH")
        self.assertEqual(json.loads(patch_request.data), {"visibility": "private"})
        with self.assertRaises(WriteTransportError):
            with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                    patch("bcs_e2e.write_transport.build_opener"):
                self.client().patch_bot_friend_profile(MEMBER)

    def test_station_register_redacts_the_token_and_sends_cookie_only(self):
        body = {"bot_name": "runtime-friend", "bot_uuid": BOT_UUID,
                 "bot_token": "registered-bot-token-private-value"}
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE,
                                      "BCS_E2E_OWNED_BOT_TOKEN": BOT_TOKEN}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(body)
            data, record = WriteClient(station_profile()).station_register_bot(
                REGISTER_TOKEN, "runtime-friend")
            request = factory.return_value.open.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(request.method, "POST")
        self.assertIn("token=" + REGISTER_TOKEN, request.full_url)
        self.assertEqual(headers["cookie"], COOKIE)
        self.assertNotIn("authorization", headers)
        self.assertEqual(data["bot_uuid"], BOT_UUID)
        self.assertEqual(record["method"], "POST")
        self.assertIn("token=[REDACTED-register-token]", record["path"])
        self.assertEqual(record["register_token_sha256"],
                         hashlib.sha256(REGISTER_TOKEN.encode()).hexdigest())
        self.assertNotIn(REGISTER_TOKEN, json.dumps(record, sort_keys=True))
        self.assertNotIn("registered-bot-token-private-value", json.dumps(record, sort_keys=True))

    def test_station_legacy_revoke_sends_cookie_and_bot_authorization(self):
        body = {"success": True, "error": None, "message": None,
                 "data": {"revoked_edges": [1, 2]}}
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE,
                                      "BCS_E2E_OWNED_BOT_TOKEN": BOT_TOKEN}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(body)
            data, record = WriteClient(station_profile()).station_legacy_revoke(MEMBER)
            request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(request.full_url,
                         ORIGIN + "/collaboration/friend-connections/member-bot")
        self.assertEqual(request.headers.get("Cookie"), COOKIE)
        self.assertEqual(request.headers.get("Authorization"), BOT_TOKEN)
        self.assertEqual(data["data"]["revoked_edges"], [1, 2])
        self.assertEqual(record["credentials_sent"], True)
        self.assertEqual(record["authorization_sent"], True)
        self.assertNotIn(COOKIE, json.dumps(record, sort_keys=True))
        self.assertNotIn(BOT_TOKEN, json.dumps(record, sort_keys=True))


class FriendMutationReadTransportTests(unittest.TestCase):
    def test_gateway_reads_replace_exact_runtime_identifiers_and_use_cookie_only(self):
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope({"items": [], "page": 1,
                                                                 "page_size": 20, "total": 0})
            observation = ReadClient(gateway_profile()).read(
                "friend.mutation.requests_sent.read", actor_id=DRIVER)
            request = factory.return_value.open.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertTrue(request.full_url.endswith(
            "/friend-connections/requests?actor_type=bot&actor_id=driver-bot"
            "&direction=sent&status=pending&page=1&page_size=20"))
        self.assertEqual(request.method, "GET")
        self.assertEqual(headers["cookie"], COOKIE)
        self.assertNotIn("authorization", headers)
        self.assertEqual(observation.evidence["identity_kind"], "human_cookie")
        self.assertEqual(observation.evidence["bound_actor_id"], DRIVER)

        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope({"bot_id": MEMBER})
            ReadClient(gateway_profile()).read(
                "friend.mutation.bot_detail.read", bot_id=MEMBER)
            detail_request = factory.return_value.open.call_args.args[0]
        self.assertTrue(detail_request.full_url.endswith("/collaboration/bots/member-bot"))

    def test_fail_open_returns_only_admitted_negative_diagnostics(self):
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope(
                {"error_code": "unauthenticated"}, code=401000, http=401,
                message="Unauthenticated")
            observation = ReadClient(gateway_profile()).read(
                "friend.mutation.discover_denied.read", target_actor_id=MEMBER,
                fail_open=True)
        self.assertEqual(observation.data, {"code": 401000,
                                            "message": "Unauthenticated",
                                            "data": {"error_code": "unauthenticated"},
                                            "error": "unauthenticated"})
        self.assertEqual(observation.evidence["http_status"], 401)
        self.assertNotIn(COOKIE, json.dumps(observation.evidence, sort_keys=True))

        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = envelope({"items": []}, http=401)
            with self.assertRaises(ObservationError):
                ReadClient(gateway_profile()).read(
                    "friend.mutation.discover_denied.read", target_actor_id=MEMBER)

    def test_station_register_token_and_admission_use_the_reviewed_faces(self):
        token_body = {"token": REGISTER_TOKEN, "expires_at": 123,
                       "note": "station token"}
        admission = {"allowed": True, "grants": [], "reason_code": "ok",
                      "public_default": False}
        environment = {"BCS_E2E_HUMAN_COOKIE": COOKIE,
                        "BCS_E2E_OWNED_BOT_TOKEN": BOT_TOKEN}
        with patch.dict(os.environ, environment), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(token_body)
            client = ReadClient(station_profile())
            token_observation = client.read("station.register_token.read")
            token_request = factory.return_value.open.call_args.args[0]
        self.assertEqual(token_request.full_url, ORIGIN + "/register/token")
        self.assertEqual(token_request.headers.get("Cookie"), COOKIE)
        self.assertNotIn("Authorization", token_request.headers)
        self.assertEqual(token_observation.data, token_body)
        self.assertNotIn(REGISTER_TOKEN, json.dumps(token_observation.evidence,
                                                        sort_keys=True))

        with patch.dict(os.environ, environment), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.side_effect = [Reply(admission), Reply(admission)]
            client = ReadClient(station_profile())
            target_observation = client.read(
                "station.admission.read", target_actor_id=MEMBER, actor_id=HUMAN)
            bot_observation = client.read(
                "station.admission.read", bot_id=MEMBER, actor_id=HUMAN)
            requests = [call_args.args[0] for call_args
                        in factory.return_value.open.call_args_list]
        expected_url = ORIGIN + "/bots/member-bot/admission?actor=owner-1"
        self.assertEqual([request.full_url for request in requests], [expected_url, expected_url])
        for request in requests:
            # ACE gates bcs-pre admission behind the human browser context; the
            # owned-Bot Authorization is stripped for this human_cookie slice.
            self.assertEqual(request.headers.get("Cookie"), COOKIE)
            self.assertNotIn("Authorization", request.headers)
        projected = dict(admission, reason="ok", source=None)
        self.assertEqual(target_observation.data, projected)
        self.assertEqual(bot_observation.data, projected)
        self.assertEqual(target_observation.evidence["credentials_sent"], True)
        self.assertEqual(target_observation.evidence["authorization_sent"], False)
        self.assertEqual(target_observation.evidence["identity_kind"], "human_cookie")

    def test_station_admission_projection_covers_the_reviewed_matrix(self):
        cases = (
            ({"allowed": False, "grants": [], "reason_code": "no_edge", "public_default": False},
             "no_edge", None),
            ({"allowed": False, "grants": [], "reason_code": "bot_not_found", "public_default": False},
             "bot_not_found", None),
            ({"allowed": True, "grants": [{"kind": "permission_profile", "ref_id": 4, "revision": 1,
                                           "digest": "d", "source": "public_default"}],
              "reason_code": "public_default", "public_default": True},
             "public_default", "public_default"),
            ({"allowed": True, "grants": [{"kind": "permission_profile", "ref_id": 4, "revision": 1,
                                           "digest": "d", "source": "edge_grant"}],
              "reason_code": "ok", "public_default": False},
             "ok", "edge_grant"),
        )
        for body, reason, source in cases:
            with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE,
                                         "BCS_E2E_OWNED_BOT_TOKEN": BOT_TOKEN}), \
                    patch("bcs_e2e.transport.build_opener") as factory, \
                    self.subTest(reason=reason):
                factory.return_value.open.return_value = Reply(body)
                observation = ReadClient(station_profile()).read(
                    "station.admission.read", target_actor_id=MEMBER, actor_id=HUMAN)
            self.assertEqual(observation.data["allowed"], body["allowed"])
            self.assertEqual(observation.data["reason"], reason)
            self.assertEqual(observation.data["source"], source)

    def test_station_plain_json_negative_discovers_return_the_bounded_object(self):
        body = {"code": "invalid_operation", "error": "role_requires_organization_code",
                 "params": {"message": "role_requires_organization_code"}, "status": 400}
        with patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": COOKIE,
                                      "BCS_E2E_OWNED_BOT_TOKEN": BOT_TOKEN}), \
                patch("bcs_e2e.transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(body, code=400)
            observation = ReadClient(station_profile()).read(
                "station.discover.role_no_org.read", fail_open=True)
        self.assertEqual(observation.data, body)
        self.assertEqual(observation.evidence["http_status"], 400)
        self.assertEqual(observation.evidence["identity_kind"], "human_cookie")


if __name__ == "__main__":
    unittest.main()
