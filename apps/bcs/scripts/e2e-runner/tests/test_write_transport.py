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

    def test_delete_operations_use_contract_200_not_204(self):
        with patch.dict(os.environ, {"BCS_E2E_COOKIE": "private-cookie"}), patch("bcs_e2e.write_transport.build_opener") as factory:
            body = {"code": 20000, "message": "OK", "request_id": "r", "data": {"deleted": True}}
            factory.return_value.open.return_value = Reply(body, 200)
            WriteClient(profile()).call("group.delete", group_id="g-1")
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.method, "DELETE")


if __name__ == "__main__":
    unittest.main()
