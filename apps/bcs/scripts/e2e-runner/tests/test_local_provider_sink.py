"""Unit tests for the bounded owned-local Provider HTTP rejection sink."""
from __future__ import annotations

import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import sys
import time
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_provider_sink import LocalProviderSinkError, OwnedLocalProviderSink
from bcs_e2e.runtime import RuntimeContractError


OWNER_RUN_ID = "provider-sink-owner-1"
PROVIDER_RUN_ID = "admin-run-actual-1"
PROVIDER_ID = "provider-test-1"
BOT_ID = "bot-uuid-1"
TOKEN = "local-bcs-to-provider-token"
REQUEST_ID = "provider-http-request-1"


def body(**changes):
    value = {
        "type": "req", "id": PROVIDER_RUN_ID, "method": "chat.send",
        "session_id": "admin-session-1", "bcn_group_id": "admin-session-1",
        "to_bot": {"provider_id": PROVIDER_ID, "provider_bot_ref": OWNER_RUN_ID},
        "from": {"kind": "bot", "name": "Provider Bot", "actor_id": BOT_ID},
        "message": {"role": "user", "content": [
            {"type": "text", "text": "review this release"}], "timestamp": 1},
        "timeout_ms": 7_200_000,
        "extensions": {"caller_wait_mode": "detached"},
    }
    value.update(changes)
    return value


def encoded(value=None):
    return json.dumps(value or body(), separators=(",", ":")).encode()


class LocalProviderSinkTests(unittest.TestCase):
    def start(self):
        sink = OwnedLocalProviderSink(OWNER_RUN_ID).start()
        self.addCleanup(sink.close)
        sink.arm(PROVIDER_ID, BOT_ID, TOKEN)
        return sink

    def request(self, sink, *, method="POST", path=None, payload=None,
                token=TOKEN, headers=None):
        target = urlsplit(sink.url)
        raw = encoded() if payload is None else payload
        request_headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "text/event-stream, application/json",
            "X-BCN-Protocol-Version": "2.0", "X-BCN-Transport": "sse",
            "X-BCN-Message-Id": REQUEST_ID, "X-BCN-Timestamp": "1790000000000",
        }
        request_headers.update(headers or {})
        connection = HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            connection.request(method, path or target.path, body=raw, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def wait_stopped(self, sink):
        deadline = time.monotonic() + 1
        while sink._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_exact_auth_body_and_negative_record_are_sanitized(self):
        raw = encoded()
        with OwnedLocalProviderSink(OWNER_RUN_ID) as sink:
            self.assertIsInstance(LocalProviderSinkError("x"), RuntimeContractError)
            sink.arm(PROVIDER_ID, BOT_ID, TOKEN)
            private_url = sink.url
            status, payload = self.request(sink, payload=raw)
            sink.validate_live(OWNER_RUN_ID)
            live = sink.summary()
        self.assertEqual((status, payload), (503, b"REJECTED_NO_ENGINE\n"))
        self.assertEqual(live["state"], "LIVE")
        self.assertEqual((live["received"], live["rejected"]), (1, 1))
        self.assertFalse(live["failed"])
        self.assertEqual(live["engine"], "NOT_STARTED")
        self.assertEqual(len(live["records"]), 1)
        record = live["records"][0]
        self.assertEqual(record["status"], "REJECTED_NO_ENGINE")
        self.assertEqual(record["owner_run_id"], OWNER_RUN_ID)
        self.assertEqual(record["provider_run_id"], PROVIDER_RUN_ID)
        self.assertEqual(record["provider_id"], PROVIDER_ID)
        self.assertEqual(record["bot_id"], BOT_ID)
        self.assertEqual(record["request_id"], REQUEST_ID)
        self.assertEqual(record["body_sha256"], hashlib.sha256(raw).hexdigest())
        text = repr(live)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("review this release", text)
        self.assertNotIn(private_url, text)
        self.assertEqual(sink.summary()["state"], "CLOSED")
        self.assertIsNone(sink._bearer_token)
        self.assertFalse(sink._thread.is_alive())
        with self.assertRaises(LocalProviderSinkError):
            _ = sink.url

    def test_use_before_start_after_close_and_mismatched_run_is_refused(self):
        sink = OwnedLocalProviderSink(OWNER_RUN_ID)
        with self.assertRaises(LocalProviderSinkError):
            _ = sink.url
        with self.assertRaises(LocalProviderSinkError):
            sink.validate_live(OWNER_RUN_ID)
        sink.start()
        self.assertRegex(sink.url, r"^http://127\.0\.0\.1:\d+/provider/[0-9a-f]{32}$")
        with self.assertRaises(LocalProviderSinkError):
            sink.validate_live("another-run")
        sink.close()
        with self.assertRaises(LocalProviderSinkError):
            sink.arm(PROVIDER_ID, BOT_ID, TOKEN)
        with self.assertRaises(LocalProviderSinkError):
            sink.start()

    def test_wrong_path_principal_target_and_method_latch_failure(self):
        cases = [
            ("path", lambda sink: self.request(sink, path="/provider/wrong")),
            ("auth", lambda sink: self.request(sink, token="wrong-token")),
            ("provider", lambda sink: self.request(sink, payload=encoded(body(
                to_bot={"provider_id": "wrong", "provider_bot_ref": OWNER_RUN_ID})))),
            ("bot", lambda sink: self.request(sink, payload=encoded(body(
                **{"from": {"kind": "bot", "name": "Provider Bot", "actor_id": "wrong"}})))),
            ("provider-ref", lambda sink: self.request(sink, payload=encoded(body(
                to_bot={"provider_id": PROVIDER_ID, "provider_bot_ref": "wrong"})))),
            ("method", lambda sink: self.request(sink, method="PUT")),
            ("unknown-method", lambda sink: self.request(sink, method="TRACE")),
        ]
        for label, operation in cases:
            with self.subTest(label=label):
                sink = self.start()
                status, _ = operation(sink)
                self.assertIn(status, (400, 405))
                self.wait_stopped(sink)
                summary = sink.summary()
                self.assertTrue(summary["failed"])
                self.assertEqual((summary["received"], summary["rejected"]), (0, 0))
                self.assertEqual(summary["records"], [])
                self.assertFalse(sink._thread.is_alive())

    def test_duplicate_invalidates_the_single_received_proof_and_stops(self):
        sink = self.start()
        self.assertEqual(self.request(sink)[0], 503)
        self.assertEqual(self.request(sink)[0], 400)
        self.wait_stopped(sink)
        summary = sink.summary()
        self.assertEqual((summary["received"], summary["rejected"]), (1, 1))
        self.assertTrue(summary["failed"])
        self.assertEqual(summary["failure"], "UNEXPECTED_REQUEST")
        self.assertEqual(len(summary["records"]), 1)
        self.assertFalse(sink._thread.is_alive())

    def test_oversize_body_latches_failure_without_receiving(self):
        sink = self.start()
        target = urlsplit(sink.url)
        connection = HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            connection.putrequest("POST", target.path)
            for name, value in {
                "Authorization": "Bearer " + TOKEN,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "text/event-stream, application/json",
                "X-BCN-Protocol-Version": "2.0", "X-BCN-Transport": "sse",
                "X-BCN-Message-Id": REQUEST_ID, "X-BCN-Timestamp": "1790000000000",
                "Content-Length": str(64 * 1024 + 1),
            }.items():
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()
        self.assertTrue(sink.summary()["failed"])
        self.assertEqual(sink.summary()["received"], 0)

    def test_partial_body_times_out_and_close_leaves_no_thread_or_socket(self):
        sink = self.start()
        client = self.partial_client(sink)
        client.settimeout(3)
        response = client.recv(4096)
        self.assertIn(b" 400 ", response)
        client.close()
        sink.close()
        self.assertTrue(sink.summary()["failed"])
        self.assertEqual(sink.summary()["state"], "CLOSED")
        self.assertFalse(sink._thread.is_alive())
        self.assertEqual(sink._connections, set())
        self.assertIsNone(sink._bearer_token)

    def test_close_interrupts_pending_body_read_and_clears_credentials(self):
        sink = self.start()
        client = self.partial_client(sink)
        deadline = time.monotonic() + 1
        while not sink._connections and time.monotonic() < deadline:
            time.sleep(0.01)
        sink.close()
        client.close()
        self.assertFalse(sink._thread.is_alive())
        self.assertEqual(sink.summary()["state"], "CLOSED")
        self.assertIsNone(sink._bearer_token)
        self.assertEqual(sink._connections, set())

    def partial_client(self, sink):
        target = urlsplit(sink.url)
        client = socket.create_connection((target.hostname, target.port), timeout=2)
        client.sendall((
            f"POST {target.path} HTTP/1.1\r\nHost: {target.hostname}:{target.port}\r\n"
            f"Authorization: Bearer {TOKEN}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "Accept: text/event-stream, application/json\r\n"
            "X-BCN-Protocol-Version: 2.0\r\nX-BCN-Transport: sse\r\n"
            f"X-BCN-Message-Id: {REQUEST_ID}\r\nX-BCN-Timestamp: 1790000000000\r\n"
            "Content-Length: 100\r\n\r\n{}"
        ).encode())
        return client


if __name__ == "__main__":
    unittest.main()
