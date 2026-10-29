"""Real-loopback tests for the owned local Event Webhook sink."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_event_sink import LocalEventSinkError, OwnedLocalEventSink
from bcs_e2e.runtime import RuntimeContractError


RUN_ID = "event-sink-run-1"
GROUP_ID = "group-1"
SUBSCRIPTION_ID = "sub-1"


def envelope(event_type, *, event_id=None, sequence=1, group_id=GROUP_ID,
             session_id="session-1", subscription_id=SUBSCRIPTION_ID):
    subject = {"type": "group", "id": group_id}
    scope = {"group_id": group_id}
    stream_key = "group:" + group_id
    data = {
        "group_kind": "normal", "strategy": "chat", "name": "Event E2E",
        "status": "active", "version": 1,
    }
    if event_type == "session.created":
        subject = {"type": "session", "id": session_id}
        scope["session_id"] = session_id
        stream_key = "session:" + session_id
        data = {"session_kind": "chat", "status": "active", "initial": sequence == 1}
    elif event_type == "message.created":
        subject = {"type": "message", "id": "message-1"}
        scope["session_id"] = session_id
        stream_key = "session:" + session_id
        data = {
            "logical_message_id": "message-1", "message_type": "chat",
            "sender": {"type": "bot", "id": "bot-1"},
            "content": {"included": False, "content_type": "text/plain",
                        "size_bytes": 3, "truncated": False},
            "attachments": [],
        }
    elif event_type == "event_subscription.test":
        subject = {"type": "event_subscription", "id": subscription_id}
        stream_key = "event-subscription-test:" + subscription_id
        data = {"test": True}
        sequence = 1
    return {
        "spec_version": "1.0", "event_id": event_id or "evt-" + event_type,
        "event_type": event_type, "schema_version": "1.0", "source": "bcs",
        "occurred_at": "2026-10-15T00:00:00Z",
        "recorded_at": "2026-10-15T00:00:00Z",
        "subject": subject, "scope": scope,
        "stream": {"key": stream_key, "sequence": sequence}, "data": data,
    }


class LocalEventSinkTests(unittest.TestCase):
    def start(self):
        sink = OwnedLocalEventSink(RUN_ID).start()
        self.addCleanup(sink.close)
        sink.arm_create_intent(RUN_ID)
        return sink

    def post(self, sink, value, *, path=None, headers=None):
        target = urlsplit(sink.url)
        raw = json.dumps(value, separators=(",", ":")).encode()
        request_headers = {"Content-Type": "application/json; charset=utf-8"}
        request_headers.update(headers or {})
        connection = HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            connection.request("POST", path or target.path, body=raw, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def wait_stopped(self, sink):
        deadline = time.monotonic() + 2
        while sink._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_exact_subject_wait_does_not_reuse_initial_session_or_bootstrap_message(self):
        sink=self.start();sink.bind_group(GROUP_ID,SUBSCRIPTION_ID)
        self.assertEqual(self.post(sink,envelope('session.created',event_id='initial',session_id='initial-session'))[0],204)
        with self.assertRaises(LocalEventSinkError):
            sink.wait_for('session.created',GROUP_ID,time.monotonic()+.01,subject_id='explicit-session')
        self.assertEqual(self.post(sink,envelope('session.created',event_id='explicit',session_id='explicit-session'))[0],204)
        receipt=sink.wait_for('session.created',GROUP_ID,time.monotonic()+1,subject_id='explicit-session')
        self.assertEqual(receipt['subject_id'],'explicit-session')
        self.assertFalse(sink.snapshot()['failed'])

    def test_connection_budget_counts_empty_connections_before_http_parsing(self):
        sink=self.start();target=urlsplit(sink.url)
        with patch('bcs_e2e.local_event_sink._MAX_CONNECTIONS',2):
            for count in range(1,4):
                connection=socket.create_connection((target.hostname,target.port),timeout=1)
                connection.close()
                deadline=time.monotonic()+1
                while sink._connections_seen<count and time.monotonic()<deadline:time.sleep(.005)
                self.assertEqual(sink._connections_seen,count)
            self.wait_stopped(sink)
        self.assertTrue(sink.snapshot()['failed'])
        self.assertEqual(sink.snapshot()['failure'],'CONNECTION_QUOTA')

    def test_buffers_creation_events_until_exact_binding_then_waits(self):
        sink = self.start()
        private_url = sink.url
        self.assertEqual(self.post(sink, envelope("group.created"))[0], 204)
        self.assertEqual(self.post(sink, envelope("session.created"))[0], 204)
        before = sink.snapshot()
        self.assertEqual((before["received"], before["buffered"], before["validated"]), (2, 2, 0))

        sink.bind_group(GROUP_ID, SUBSCRIPTION_ID)
        group = sink.wait_for("group.created", GROUP_ID, time.monotonic() + 1)
        session = sink.wait_for("session.created", GROUP_ID, time.monotonic() + 1)
        self.assertEqual(group, {
            "group_id": GROUP_ID, "event_type": "group.created",
            "stream_key": "group:" + GROUP_ID, "sequence": 1,
            "event_id": "evt-group.created", "authorization": None, "subject_id": GROUP_ID,
        })
        self.assertEqual(session["stream_key"], "session:session-1")
        summary = sink.summary()
        self.assertEqual((summary["validated"], summary["buffered"]), (2, 0))
        self.assertNotIn(private_url, repr(summary))
        self.assertNotIn("Event E2E", repr(summary))

    def test_test_event_uses_subscription_identity_and_separate_stream(self):
        sink = self.start()
        sink.bind_group(GROUP_ID, SUBSCRIPTION_ID)
        self.assertEqual(self.post(sink, envelope("session.created", sequence=8))[0], 204)
        self.assertEqual(self.post(sink, envelope("event_subscription.test"))[0], 204)
        self.assertEqual(self.post(sink, envelope(
            "message.created", event_id="evt-message", sequence=9))[0], 204)
        test = sink.wait_for("event_subscription.test", GROUP_ID, time.monotonic() + 1)
        self.assertEqual(test["stream_key"], "event-subscription-test:" + SUBSCRIPTION_ID)
        self.assertEqual(test["sequence"], 1)
        self.assertFalse(sink.snapshot()["failed"])

    def test_wrong_buffered_group_fails_binding_without_validated_receipt(self):
        sink = self.start()
        self.assertEqual(self.post(sink, envelope("group.created", group_id="other-group"))[0], 204)
        with self.assertRaises(LocalEventSinkError):
            sink.bind_group(GROUP_ID, SUBSCRIPTION_ID)
        self.wait_stopped(sink)
        summary = sink.snapshot()
        self.assertTrue(summary["failed"])
        self.assertEqual(summary["validated"], 0)
        self.assertEqual(summary["failure"], "BINDING_MISMATCH")

    def test_duplicate_event_or_nonincreasing_stream_sequence_latches_failure(self):
        cases = [
            (envelope("event_subscription.test",event_id="test-one"), envelope("event_subscription.test",event_id="test-two")),
            (envelope("group.created"), envelope("group.created")),
            (envelope("session.created", event_id="evt-a", sequence=3),
             envelope("message.created", event_id="evt-b", sequence=3)),
        ]
        for first, second in cases:
            with self.subTest(second=second["event_id"]):
                sink = self.start()
                sink.bind_group(GROUP_ID, SUBSCRIPTION_ID)
                self.assertEqual(self.post(sink, first)[0], 204)
                self.assertEqual(self.post(sink, second)[0], 400)
                self.wait_stopped(sink)
                self.assertTrue(sink.snapshot()["failed"])

    def test_authorization_duplicate_header_transfer_encoding_and_wrong_path_reject(self):
        operations = [
            lambda sink: self.post(sink, envelope("group.created"), headers={"Authorization": "Bearer secret"}),
            lambda sink: self.post(sink, envelope("group.created"), headers={"Transfer-Encoding": "chunked"}),
            lambda sink: self.post(sink, envelope("group.created"), path="/control/reset"),
            self.duplicate_header_request,
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                sink = self.start()
                status, _ = operation(sink)
                self.assertIn(status, (400, 405))
                self.wait_stopped(sink)
                self.assertTrue(sink.snapshot()["failed"])
                self.assertEqual(sink.snapshot()["validated"], 0)

    def test_lifecycle_wait_bound_and_body_deadline_are_bounded(self):
        self.assertIsInstance(LocalEventSinkError("x"), RuntimeContractError)
        sink = OwnedLocalEventSink(RUN_ID)
        with self.assertRaises(LocalEventSinkError):
            _ = sink.url
        sink.start()
        self.addCleanup(sink.close)
        with self.assertRaises(LocalEventSinkError):
            sink.validate_live("other-run")
        sink.arm_create_intent(RUN_ID)
        with self.assertRaises(LocalEventSinkError):
            sink.wait_for("group.created", GROUP_ID, time.monotonic() + 1)
        sink.bind_group(GROUP_ID, SUBSCRIPTION_ID)
        for invalid in (float("nan"),float("inf"),time.monotonic()+5.1):
            with self.assertRaises(LocalEventSinkError):
                sink.wait_for("group.created", GROUP_ID, invalid)

        client = self.partial_client(sink)
        client.settimeout(3)
        response = client.recv(4096)
        client.close()
        self.assertIn(b" 400 ", response)
        self.wait_stopped(sink)
        self.assertTrue(sink.snapshot()["failed"])
        sink.close()
        self.assertEqual(sink.snapshot()["state"], "CLOSED")
        with self.assertRaises(LocalEventSinkError):
            _ = sink.url

    def duplicate_header_request(self, sink):
        target = urlsplit(sink.url)
        raw = json.dumps(envelope("group.created"), separators=(",", ":")).encode()
        client = socket.create_connection((target.hostname, target.port), timeout=2)
        client.sendall((
            f"POST {target.path} HTTP/1.1\r\nHost: {target.hostname}:{target.port}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "X-Duplicate: one\r\nX-Duplicate: two\r\n"
            f"Content-Length: {len(raw)}\r\n\r\n"
        ).encode() + raw)
        client.settimeout(3)
        response = client.recv(4096)
        client.close()
        status = int(response.split(b" ", 2)[1])
        return status, response

    def partial_client(self, sink):
        target = urlsplit(sink.url)
        client = socket.create_connection((target.hostname, target.port), timeout=2)
        client.sendall((
            f"POST {target.path} HTTP/1.1\r\nHost: {target.hostname}:{target.port}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "Content-Length: 100\r\n\r\n{}"
        ).encode())
        return client


if __name__ == "__main__":
    unittest.main()
