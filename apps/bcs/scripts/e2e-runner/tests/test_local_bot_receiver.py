"""Unit tests for the bounded owned-local Bot WebSocket receiver."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_bot_receiver import LocalBotReceiverError, OwnedLocalBotReceiver
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.ws_transport import WebSocketTransportError


_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_TOKEN = "secret-existing-bot-token"


def server_frame(value: dict) -> bytes:
    payload = json.dumps(value, separators=(",", ":")).encode()
    if len(payload) < 126:
        return bytes((0x81, len(payload))) + payload
    return b"\x81\x7e" + len(payload).to_bytes(2, "big") + payload


def decode_client_frame(value: bytes) -> dict:
    second = value[1]
    length = second & 0x7f
    offset = 2
    if length == 126:
        length = int.from_bytes(value[offset:offset + 2], "big")
        offset += 2
    elif length == 127:
        length = int.from_bytes(value[offset:offset + 8], "big")
        offset += 8
    mask = value[offset:offset + 4]
    offset += 4
    payload = bytes(byte ^ mask[index % 4]
                    for index, byte in enumerate(value[offset:offset + length]))
    return json.loads(payload)


class FakeSocket:
    def __init__(self, *, connect_payload=None, frames=(), upgrade="valid"):
        self.connect_payload = connect_payload or {
            "bot_uuid": "bot-PM", "is_new": False, "protocol_version": 2,
            "token": "server-secret", "env": {"PRIVATE": "server-env-secret"},
        }
        self.frames = bytearray().join(server_frame(frame) for frame in frames)
        self.upgrade = upgrade
        self.sent = []
        self.closed = False
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.sent.append(value)
        if value.startswith(b"GET "):
            return
        frame = decode_client_frame(value)
        if frame.get("method") == "bot.connect":
            response = {"type": "res", "id": frame["id"], "ok": True,
                        "payload": self.connect_payload}
            self.frames[:0] = server_frame(response)

    def recv(self, size):
        if not self.frames:
            raise socket.timeout()
        value = bytes(self.frames[:size])
        del self.frames[:size]
        return value

    def shutdown(self, _direction):
        pass

    def close(self):
        self.closed = True


def identities():
    value = object.__new__(OwnedLocalRoleIdentity)
    value.fixture_set = "workspace"
    value.roles = ("CEO", "PM", "ENG", "QA")
    value._verified = ["CEO", "PM", "ENG"]
    value._entries = {role: ("bot-" + role, _TOKEN) for role in value.roles}
    value.runtime = Mock()
    value.runtime.inputs.origin = "http://127.0.0.1:43123"
    value.fixtures = Mock()
    return value


def delivery(*, group="group-1", method="chat.send", text="literal payload"):
    return {
        "type": "req", "id": "delivery-1", "method": method,
        "params": {
            "session_key": "group:group-1",
            "bcs_group_id": group,
            "message": {"role": "user", "content": [
                {"type": "text", "text": "[BCS Group Context]\n\n[消息内容]\n" + text}
            ], "timestamp": 1},
            "channel": {"thread_id": "group-1"},
        },
    }


class LocalBotReceiverTests(unittest.TestCase):
    def open(self, sock, *, connect_headers=True):
        def headers(reader):
            raw = reader.sock
            request = raw.sent[0].decode("ascii")
            key = next(line.split(":", 1)[1].strip() for line in request.split("\r\n")
                       if line.lower().startswith("sec-websocket-key:"))
            accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
            result = {"upgrade": "websocket", "connection": "keep-alive, Upgrade",
                      "sec-websocket-accept": accept}
            if not connect_headers:
                result.pop("upgrade")
            return "HTTP/1.1 101 Switching Protocols", result, b""
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"), \
             patch("bcs_e2e.local_bot_receiver.socket.create_connection", return_value=sock), \
             patch("bcs_e2e.local_bot_receiver._read_headers", side_effect=headers):
            receiver = OwnedLocalBotReceiver(identities(), "PM")
            receiver.__enter__()
        return receiver

    def test_receives_matching_delivery_without_ack_or_secret_receipt(self):
        sock = FakeSocket(frames=[delivery()])
        receiver = self.open(sock)
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"):
            receipt = receiver.receive_delivery(group_id="group-1", text="literal payload",
                                                method="chat.send")
        self.assertEqual(receipt["status"], "TRANSPORT_RECEIVED_ONLY")
        self.assertEqual(receipt["target_role"], "PM")
        self.assertEqual(receipt["target_bot_id"], "bot-PM")
        self.assertFalse(receipt["success_ack_sent"])
        self.assertNotIn(_TOKEN, repr(receipt))
        self.assertNotIn("server-secret", repr(receipt))
        methods = [decode_client_frame(item).get("method") for item in sock.sent
                   if not item.startswith(b"GET ") and item[:1] != b"\x88"]
        self.assertEqual(methods, ["bot.connect"])
        receiver.close()
        self.assertTrue(sock.closed)
        self.assertEqual(receiver.summary()["state"], "CLOSED")

    def test_handshake_mismatch_and_new_identity_close_without_secrets(self):
        for payload, connect_headers in [
            ({"bot_uuid": "bot-PM", "is_new": False, "protocol_version": 2}, False),
            ({"bot_uuid": "new-bot", "is_new": True, "protocol_version": 2,
              "token": _TOKEN}, True),
        ]:
            sock = FakeSocket(connect_payload=payload)
            with self.subTest(payload=payload, headers=connect_headers), \
                 self.assertRaises(LocalBotReceiverError) as raised:
                self.open(sock, connect_headers=connect_headers)
            self.assertTrue(sock.closed)
            self.assertNotIn(_TOKEN, str(raised.exception))
            self.assertNotIn("new-bot", str(raised.exception))

    def test_foreign_group_is_rejected_and_closed_not_silently_consumed(self):
        sock = FakeSocket(frames=[delivery(group="foreign"), delivery()])
        receiver = self.open(sock)
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"), \
             self.assertRaisesRegex(LocalBotReceiverError, "foreign Group"):
            receiver.receive_delivery(group_id="group-1", text="literal payload",
                                      method="chat.send")
        self.assertTrue(sock.closed)
        self.assertEqual(receiver.summary()["state"], "CLOSED")

    def test_known_initial_session_wire_id_requires_explicit_session_id(self):
        first = self.open(FakeSocket(frames=[delivery(group="initial-session-1")]))
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"), self.assertRaises(LocalBotReceiverError):
            first.receive_delivery(group_id="group-1", text="literal payload", method="chat.send")
        second = self.open(FakeSocket(frames=[delivery(group="initial-session-1")]))
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"):
            receipt = second.receive_delivery(group_id="group-1", session_id="initial-session-1",
                                              text="literal payload", method="chat.send")
        self.assertEqual(receipt["wire_group_id"], "initial-session-1")
        second.close()

    def test_unrelated_frame_budget_is_bounded_and_closes(self):
        unrelated = [{"type": "event", "event": "noise", "payload": {}} for _ in range(17)]
        sock = FakeSocket(frames=unrelated)
        receiver = self.open(sock)
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"), \
             self.assertRaisesRegex(LocalBotReceiverError, "too many unrelated"):
            receiver.receive_delivery(group_id="group-1", text="literal payload",
                                      method="chat.send")
        self.assertTrue(sock.closed)

    def test_frame_deadline_closes_receiver(self):
        sock = FakeSocket()
        receiver = self.open(sock)
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"), \
             self.assertRaises(WebSocketTransportError):
            receiver.receive_delivery(group_id="group-1", text="literal payload",
                                      method="chat.send", timeout_seconds=0.001)
        self.assertTrue(sock.closed)

    def test_cancel_closes_receiver_and_clears_credential_reference(self):
        sock = FakeSocket()
        receiver = self.open(sock)
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"), \
             patch.object(receiver._connection, "receive_frame", side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            receiver.receive_delivery(group_id="group-1", text="literal payload",
                                      method="chat.inject")
        self.assertTrue(sock.closed)
        self.assertEqual(receiver._token, "")
        self.assertNotIn(_TOKEN, repr(receiver.summary()))

    def test_foreign_session_metadata_cannot_enter_receipt(self):
        for field, value in [('bcs_session_id', 'foreign-session'), ('session_key', 'bad\nkey')]:
            frame = delivery()
            frame['params'][field] = value
            sock = FakeSocket(frames=[frame])
            receiver = self.open(sock)
            with patch.object(OwnedLocalRoleIdentity, '_require_bound'), self.assertRaises(LocalBotReceiverError):
                receiver.receive_delivery(group_id='group-1', text='literal payload', method='chat.send')
            self.assertTrue(sock.closed)

    def test_buffered_control_flood_is_bounded(self):
        receiver = self.open(FakeSocket())
        with patch.object(OwnedLocalRoleIdentity, '_require_bound'), \
             patch.object(receiver._connection, 'receive_frame', return_value=(0xA, b'')), \
             self.assertRaisesRegex(LocalBotReceiverError, 'frame budget'):
            receiver.receive_delivery(group_id='group-1', text='literal payload', method='chat.send')
        self.assertEqual(receiver.summary()['state'], 'CLOSED')

    def test_failed_socket_close_cannot_claim_closed_or_clear_handle(self):
        receiver = self.open(FakeSocket())
        connection = receiver._connection
        with patch.object(connection, 'close', side_effect=OSError('close failed')), \
             patch.object(connection.sock, 'close', side_effect=OSError('socket close failed')), \
             self.assertRaisesRegex(LocalBotReceiverError, 'not confirmed'):
            receiver.close()
        self.assertEqual(receiver.summary()['state'], 'CLOSE_UNCONFIRMED')
        self.assertIs(receiver._connection, connection)
        self.assertEqual(receiver._token, '')
        receiver.close()
        self.assertEqual(receiver.summary()['state'], 'CLOSED')

    def test_constructor_preserves_cancellation(self):
        with patch.object(OwnedLocalRoleIdentity, '_require_bound', side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            OwnedLocalBotReceiver(identities(), 'PM')

    def test_constructor_rejects_unverified_role_and_non_loopback_origin(self):
        value = identities()
        with self.assertRaises(LocalBotReceiverError):
            OwnedLocalBotReceiver(value, "QA")
        value.runtime.inputs.origin = "http://example.test:43123"
        with patch.object(OwnedLocalRoleIdentity, "_require_bound"):
            with self.assertRaises(LocalBotReceiverError):
                OwnedLocalBotReceiver(value, "PM")


@unittest.skipUnless(os.environ.get('BCS_E2E_TEST_BINARY') and
                     os.environ.get('BCS_E2E_TEST_BINARY_SHA256') and shutil.which('lsof'),
                     'approved real owned BCS binary required')
class LocalBotReceiverLiveTests(unittest.TestCase):
    def test_existing_bot_connections_close_without_engine_execution(self):
        from bcs_e2e.owned_bcs import OwnedBcsProcess
        from bcs_e2e.local_fixtures import OwnedLocalFixtures
        from bcs_e2e.run_state import read
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'runtime'
            with OwnedBcsProcess(binary=Path(os.environ['BCS_E2E_TEST_BINARY']),
                                 binary_sha256=os.environ['BCS_E2E_TEST_BINARY_SHA256'],
                                 root=root, run_id='bot-receiver-live', store_messages=True) as runtime:
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as roles:
                    fixtures.apply(allow_write=True, role_identity=roles)
                    for role in roles.roles:
                        roles.verify(role)
                    for role in ('CEO', 'PM', 'ENG'):
                        with OwnedLocalBotReceiver(roles, role) as receiver:
                            self.assertEqual(receiver.summary()['state'], 'OPEN')
                            self.assertFalse(receiver.summary()['success_ack_sent'])
                            self.assertEqual(receiver.bot_id, roles._entries[role][0])
                        self.assertEqual(receiver.summary()['state'], 'CLOSED')
                        self.assertEqual(receiver._token, '')
                    self.assertTrue(json.loads(runtime.inputs.config_path.read_text())['store_messages'])
            self.assertEqual(read(root/'owned-bcs.json')['status'], 'STOPPED')
            self.assertEqual(roles.summary()['credential_memory'], 'CLEARED')


if __name__ == "__main__":
    unittest.main()
