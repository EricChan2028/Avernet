from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.profile import validate
from bcs_e2e.transport import tls_context
from bcs_e2e.write_transport import WriteClient, WriteTransportError


class TLSContextTests(unittest.TestCase):
    def test_custom_ca_fallback_never_disables_certificate_or_hostname_verification(self):
        context = tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class Reply:
    def __init__(self, body, code=200, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.code = code
        self.headers = headers or {}
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, _limit=-1): return self.body


def profile():
    root = Path(__file__).resolve().parents[1]
    p = json.loads((root / "profiles/preprod.example.json").read_text())
    p["environment"].update(id="test-pre", expected_bcs_env="pre")
    p["entrypoint"].update(origin="https://gateway.example.test", api_face="public-v1")
    p["human"]["user_id"] = "owner-1"
    p["actors"]["driver"].update(bot_id="bot-a", owner_id="owner-1")
    p["actors"]["member"].update(bot_id="bot-b", owner_id="owner-1")
    p["auth"]["header_env"] = {"Cookie": "BCS_E2E_COOKIE"}
    p["observer"].update(server="observer", database="db", environment_binding_confirmed=True)
    p["policy"]["exclusive_use_confirmed"] = True
    return validate(p)


class WriteTransportTests(unittest.TestCase):
    def test_public_envelope_requires_request_id_message_and_data(self):
        invalid = [
            {"code": 20000, "data": {}, "message": "OK"},
            {"code": 20000, "request_id": "r", "data": {}},
            {"code": 20000, "request_id": "r", "message": "OK"},
            {"code": "20000", "request_id": "r", "message": "OK", "data": {}},
        ]
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            for body in invalid:
                factory.return_value.open.return_value = Reply(body)
                with self.subTest(body=body), self.assertRaises(WriteTransportError):
                    WriteClient(profile()).call("group.read", group_id="g-1")

    def test_response_request_id_is_recorded_and_header_echo_is_separate(self):
        body = {"code": 20000, "message": "OK", "request_id": "server-request", "data": {"group_id": "g-1"}}
        reply = Reply(body, headers={"x-request-id": "client-request-not-echoed"})
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = reply
            value, record = WriteClient(profile()).call("group.read", group_id="g-1")
        self.assertEqual(value["group_id"], "g-1")
        self.assertEqual(record["response_request_id"], "server-request")
        self.assertEqual(record['response_header_request_id'], 'client-request-not-echoed')
        self.assertEqual(record['response_envelope_request_id'], 'server-request')
        self.assertEqual(record['response_request_id_source'], 'ENVELOPE')
        self.assertTrue(record['response_request_id_conflict'])
        self.assertFalse(record["request_id_echoed"])
        self.assertNotIn("private-cookie", json.dumps(record))

    def test_401_403_redirect_and_204_are_not_normalized_to_success(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            for code in (204, 301, 302, 401, 403):
                factory.return_value.open.return_value = Reply(b"private-body", code)
                with self.subTest(code=code), self.assertRaises(WriteTransportError):
                    WriteClient(profile()).call("group.delete", group_id="g-1")

    def test_created_group_and_session_require_matching_http_envelope_pair(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            for operation in ("group.create", "session.create"):
                for http, code, accepted in ((201, 20100, True), (200, 20000, True),
                                            (201, 20000, False), (200, 20100, False),
                                            (201, "20100", False), (201, 40000, False)):
                    body = {"code": code, "message": "Created", "request_id": "r", "data": {"group_id": "g-1"}}
                    factory.return_value.open.return_value = Reply(body, http)
                    client = WriteClient(profile())
                    with self.subTest(operation=operation, http=http, code=code):
                        if accepted:
                            data, record = client.call(operation, group_id="g-1", body={}, expected=(200, 201))
                            self.assertEqual(data["group_id"], "g-1")
                            self.assertEqual(record["http_status"], http)
                        else:
                            with self.assertRaises(WriteTransportError):
                                client.call(operation, group_id="g-1", body={}, expected=(200, 201))

    def test_session_messages_projects_only_a_valid_public_array(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            for messages in ([], [{"id": "m1"}], {}, {"items": []}, ["invalid"], None):
                factory.return_value.open.return_value = Reply({"code": 20000, "message": "OK",
                                                                "request_id": "r", "data": messages})
                with self.subTest(messages=messages):
                    if isinstance(messages, list) and all(isinstance(m, dict) for m in messages):
                        data, record = WriteClient(profile()).call("session.messages", session_id="s1")
                        self.assertEqual(data, {"items": messages})
                        self.assertEqual(record["response_projection"], "public-message-array-to-items")
                    else:
                        with self.assertRaises(WriteTransportError):
                            WriteClient(profile()).call("session.messages", session_id="s1")

    def test_legacy_session_cli_messages_projects_only_a_valid_array(self):
        legacy = profile()
        legacy["entrypoint"]["api_face"] = "legacy"
        legacy["route_bindings"]["session_cli.session.messages"] = {
            "method": "GET", "path": "/sessions/{session_id}/messages",
            "traceparent": "disabled",
        }
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = WriteClient(legacy)
            self.assertIn("session_cli.session.messages", client.profile["route_bindings"])
            for messages in ([], [{"id": "m1"}], {}, ["invalid"], None):
                factory.return_value.open.return_value = Reply(messages)
                with self.subTest(messages=messages):
                    if isinstance(messages, list) and all(isinstance(m, dict) for m in messages):
                        data, record = client.call(
                            "session_cli.session.messages", session_id="s1")
                        self.assertEqual(data, {"items": messages})
                        self.assertEqual(record["response_projection"],
                                         "legacy-session-message-array-to-items")
                    else:
                        with self.assertRaises(WriteTransportError):
                            client.call(
                                "session_cli.session.messages", session_id="s1")

    def test_cli_message_deliveries_pass_a_valid_array_through_verbatim(self):
        legacy = profile()
        legacy["entrypoint"]["api_face"] = "legacy"
        legacy["route_bindings"] = dict(legacy["route_bindings"])
        for operation, method, path in (
                ("session_cli.message.deliveries.read", "GET",
                 ("/openapi/v1/collaboration/messages/not-a-message"
                  "/deliveries?session_id={session_id}")),
                ("session_cli.message.deliveries.query", "POST",
                 ("/openapi/v1/collaboration/sessions/{session_id}"
                  "/message-deliveries/query"))):
            legacy["route_bindings"][operation] = {
                "method": method, "path": path, "traceparent": "disabled"}
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = WriteClient(legacy)
            for operation in ("session_cli.message.deliveries.read",
                              "session_cli.message.deliveries.query"):
                for deliveries in ([], [{"delivery_id": "d1"}]):
                    factory.return_value.open.return_value = Reply(deliveries)
                    with self.subTest(operation=operation,
                                      deliveries=deliveries):
                        data, record = client.call(operation, session_id="s1")
                        self.assertEqual(data, deliveries)
                        self.assertEqual(
                            record["response_projection"],
                            "cli-delivery-array-passthrough")
                factory.return_value.open.return_value = Reply(
                    {"session_id": "s1"})
                data, record = client.call(operation, session_id="s1")
                self.assertEqual(data, {"session_id": "s1"})
                self.assertNotIn("response_projection", record)
                for invalid in (["invalid"], "array", 12):
                    factory.return_value.open.return_value = Reply(invalid)
                    with self.subTest(operation=operation, invalid=invalid), \
                            self.assertRaises(WriteTransportError):
                        client.call(operation, session_id="s1")

    def test_public_delivery_arrays_pass_through_after_the_envelope(self):
        value = profile()
        value["route_bindings"] = dict(value["route_bindings"])
        value["route_bindings"]["session_cli.message.deliveries.read"] = {
            "method": "GET",
            "path": ("/openapi/v1/collaboration/messages/not-a-message"
                     "/deliveries?session_id={session_id}"),
            "traceparent": "disabled"}
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = WriteClient(value)
            factory.return_value.open.return_value = Reply(
                {"code": 20000, "message": "OK", "request_id": "r",
                 "data": [{"delivery_id": "d1"}]})
            data, record = client.call(
                "session_cli.message.deliveries.read", session_id="s1")
            self.assertEqual(data, [{"delivery_id": "d1"}])
            self.assertEqual(record["response_projection"],
                             "cli-delivery-array-passthrough")
            factory.return_value.open.return_value = Reply(
                {"code": 20000, "message": "OK", "request_id": "r",
                 "data": {"session_id": "s1"}})
            data, record = client.call(
                "session_cli.message.deliveries.read", session_id="s1")
            self.assertEqual(data, {"session_id": "s1"})
            self.assertNotIn("response_projection", record)

    def test_delete_operations_use_contract_200_not_204(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            body = {"code": 20000, "message": "OK", "request_id": "r", "data": {"deleted": True}}
            factory.return_value.open.return_value = Reply(body, 200)
            WriteClient(profile()).call("group.delete", group_id="g-1")
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.method, "DELETE")


class RouteResponseBudgetTests(unittest.TestCase):
    """Reviewed per-route budgets for heavy bounded legacy lists."""

    def legacy_list_profile(self, budget="unset"):
        binding = {
            "method": "GET", "path": "/groups?limit=50&group_kind=all",
            "api_face": "legacy", "auth": "case_owned_driver",
            "response": "legacy-json", "traceparent": "disabled"}
        if budget != "unset":
            binding["max_response_bytes"] = budget
        return {
            "schema_version": 1,
            "environment": {"tier": "preprod", "id": "route-budget-test"},
            "entrypoint": {"kind": "gateway",
                           "origin": "https://bcs-pre.example.test",
                           "api_face": "legacy", "manifest_face": "legacy"},
            "auth": {"kind": "human-cookie",
                     "header_env": {"Cookie": "BCS_E2E_COOKIE"}},
            "policy": {"timeout_seconds": 10, "max_response_bytes": 65536},
            "route_bindings": {"workspace_group.list": binding},
        }

    def heavy_list_body(self):
        return {
            "group_kind": "all", "limit": 50, "offset": 0, "total": 1,
            "items": [{"id": "g-1", "driver_bot": "d-1",
                       "label": "dual-group-lifecycle-e2e",
                       "context": "x" * (128 * 1024)}],
        }

    def test_default_policy_budget_rejects_the_heavy_bounded_list(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(self.heavy_list_body())
            with self.assertRaises(WriteTransportError):
                WriteClient(self.legacy_list_profile()).call(
                    "workspace_group.list")

    def test_binding_budget_override_admits_and_records_the_heavy_list(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(self.heavy_list_body())
            data, record = WriteClient(
                self.legacy_list_profile(1024 * 1024)).call(
                    "workspace_group.list")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["id"], "g-1")
        self.assertEqual(record["response_budget_bytes"], 1024 * 1024)
        self.assertNotIn("private-cookie", json.dumps(record))

    def test_policy_budget_is_recorded_without_an_override(self):
        body = {"group_kind": "all", "items": [], "limit": 50,
                "offset": 0, "total": 0}
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            factory.return_value.open.return_value = Reply(body)
            _, record = WriteClient(self.legacy_list_profile()).call(
                "workspace_group.list")
        self.assertEqual(record["response_budget_bytes"], 65536)

    def test_invalid_binding_budgets_fail_closed_before_any_request(self):
        for budget in (0, -1, 16 * 1024 * 1024 + 1, "1048576", 4096.0, True):
            with self.subTest(budget=budget), \
                    patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), \
                    patch("bcs_e2e.write_transport.build_opener") as factory:
                with self.assertRaises(WriteTransportError):
                    WriteClient(self.legacy_list_profile(budget)).call(
                        "workspace_group.list")
                self.assertEqual(factory.return_value.open.call_count, 0)


class AuthorizationSchemeTests(unittest.TestCase):
    """Opt-in Bearer shaping for the station ACE duo/trio writes."""

    def _duo_profile(self, *, scheme=True):
        value = profile()
        value["auth"] = {
            "kind": "human-cookie-with-owned-bot-token",
            "header_env": {"Authorization": "BCS_E2E_DRIVER_BOT_TOKEN",
                           "Cookie": "BCS_E2E_COOKIE"},
        }
        if scheme:
            value["auth"]["authorization_scheme"] = "bearer"
        return value

    def test_bearer_scheme_prefixes_the_owned_bot_token_once(self):
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN": "owned-token",
                                     "BCS_E2E_COOKIE": "human-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = WriteClient(self._duo_profile())
        self.assertEqual(client.headers["Authorization"], "Bearer owned-token")
        self.assertEqual(client.headers["Cookie"], "human-cookie")

    def test_missing_scheme_keeps_the_raw_authorization_value(self):
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN": "owned-token",
                                     "BCS_E2E_COOKIE": "human-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = WriteClient(self._duo_profile(scheme=False))
        self.assertEqual(client.headers["Authorization"], "owned-token")
        self.assertEqual(client.headers["Cookie"], "human-cookie")

    def test_existing_bearer_prefix_is_not_duplicated(self):
        with patch.dict(os.environ, {"BCS_E2E_DRIVER_BOT_TOKEN":
                                     "Bearer owned-token",
                                     "BCS_E2E_COOKIE": "human-cookie"}), \
                patch("bcs_e2e.write_transport.build_opener") as factory:
            client = WriteClient(self._duo_profile())
        self.assertEqual(client.headers["Authorization"], "Bearer owned-token")


if __name__ == "__main__":
    unittest.main()
