from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.ws_transport import SessionWebSocketClient


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketTransportTests(unittest.TestCase):
    def test_upgrade_and_close_redact_connection_token(self):
        self._roundtrip()

    def test_real_loopback_upgrade_carries_bounded_browser_context(self):
        self._roundtrip({'Cookie': 'session=synthetic-cookie', 'Origin': 'https://workbench.example.test',
                         'Referer': 'https://workbench.example.test/'})

    def test_prejournaled_request_ids_are_exact_on_upgrade_and_control_frame(self):
        self._roundtrip(request_ids=("journal-upgrade-001", "journal-connect-001"))

    def _roundtrip(self, request_headers=None, request_ids=None):
        observed = []
        observed_control = []
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        closed = threading.Event()

        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += connection.recv(4096)
                    lines = request.decode("iso-8859-1").split("\r\n")
                    observed.extend(lines)
                    key = next(line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("sec-websocket-key:"))
                    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
                    connection.sendall((
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode())
                    connection.settimeout(2)
                    header = connection.recv(2)
                    length = header[1] & 0x7f
                    if length == 126:
                        length = int.from_bytes(connection.recv(2), "big")
                    elif length == 127:
                        length = int.from_bytes(connection.recv(8), "big")
                    mask = connection.recv(4)
                    payload = connection.recv(length)
                    payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                    request_frame = json.loads(payload.decode())
                    observed_control.append(request_frame["id"])
                    response = json.dumps({
                        "type": "res", "id": request_frame["id"], "ok": True,
                        "payload": {"group_id": "group-1", "view_actor_id": "", "message_view_scope": "full"},
                    }, separators=(",", ":")).encode()
                    if len(response) < 126:
                        response_header = bytes([0x81, len(response)])
                    else:
                        response_header = bytes([0x81, 126]) + len(response).to_bytes(2, "big")
                    connection.sendall(response_header + response)
                    connection.recv(128)
                    closed.set()
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        token = "sensitive-session-token"
        client = SessionWebSocketClient(f"http://127.0.0.1:{port}", timeout_seconds=2, request_headers=request_headers)
        options = ({"upgrade_request_id": request_ids[0], "connect_request_id": request_ids[1]}
                   if request_ids else {})
        connection, record = client.connect(token, "group-1", "session-1", **options)
        self.assertEqual(record["http_status"], 101)
        self.assertNotIn(token, repr(record))
        self.assertIn("[REDACTED]", record["path"])
        connection.close()
        self.assertTrue(closed.wait(2))
        thread.join(2)
        if request_ids:
            self.assertIn("X-Request-ID: " + request_ids[0], observed)
            self.assertEqual(observed_control, [request_ids[1]])
            self.assertEqual(record["submitted_request_id"], request_ids[0])
            self.assertEqual(record["connect_request_id"], request_ids[1])
        for key, value in (request_headers or {}).items():
            self.assertIn(f'{key}: {value}', observed)
            self.assertNotIn(value, json.dumps(record))
        if not request_headers:
            self.assertFalse(any(line.startswith('Cookie:') for line in observed))


if __name__ == "__main__":
    unittest.main()

class WebSocketProtocolSafetyTests(unittest.TestCase):
    def test_upgrade_reader_preserves_coalesced_first_frame(self):
        from bcs_e2e.ws_transport import WebSocketConnection, _read_headers
        class Sock:
            def __init__(self):
                self.chunks = [b"HTTP/1.1 101 Switching Protocols\r\n\r\n\x81\x02ok"]
            def recv(self, _size):
                return self.chunks.pop(0)
        status, headers, rest = _read_headers(Sock())
        self.assertEqual(status, "HTTP/1.1 101 Switching Protocols")
        self.assertEqual(rest, b"\x81\x02ok")
        connection = WebSocketConnection(Sock(), initial_buffer=rest)
        self.assertEqual(connection.receive_frame(), (0x1, b"ok"))

    def test_server_mask_reserved_fragment_control_and_opcode_frames_are_rejected(self):
        from bcs_e2e.ws_transport import WebSocketConnection, WebSocketTransportError
        class Sock:
            def __init__(self, payload): self.payload = payload
            def recv(self, size):
                if not self.payload: return b""
                value, self.payload = self.payload[:size], self.payload[size:]
                return value
        for payload in (b"\x81\x80\x00\x00\x00\x00", b"\xc1\x00", b"\x01\x00", b"\x83\x00", b"\x88\x7e\x00\x7e"):
            with self.subTest(payload=payload), self.assertRaises(WebSocketTransportError):
                WebSocketConnection(Sock(payload), max_frame_bytes=1024).receive_frame()

    def test_connect_protocol_error_closes_raw_socket(self):
        from bcs_e2e.ws_transport import SessionWebSocketClient, WebSocketTransportError
        class Sock:
            def __init__(self): self.closed = False; self.sent = []
            def settimeout(self, _): pass
            def sendall(self, value): self.sent.append(value)
            def recv(self, _): return b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: wrong\r\n\r\n"
            def close(self): self.closed = True
        sock = Sock()
        with patch("bcs_e2e.ws_transport.socket.create_connection", return_value=sock), self.assertRaises(WebSocketTransportError):
            SessionWebSocketClient("http://example.test", timeout_seconds=1).connect("token", "g", "s")
        self.assertTrue(sock.closed)


class PrejournaledWebSocketIdTests(unittest.TestCase):
    def test_invalid_ids_fail_before_socket_or_frame_dispatch(self):
        from bcs_e2e.ws_transport import WebSocketConnection, WebSocketTransportError
        from unittest.mock import Mock
        for bad in ('', 'a'*129, 'a\r\nHeader: injected', 'bad id', 1, '中文'):
            for key in ('upgrade_request_id', 'connect_request_id'):
                with self.subTest(key=key, bad=bad), patch('bcs_e2e.ws_transport.socket.create_connection') as open_socket:
                    with self.assertRaises(WebSocketTransportError):
                        SessionWebSocketClient('http://127.0.0.1:21000').connect('token', 'g', 's', **{key: bad})
                    open_socket.assert_not_called()
            sock = Mock()
            with self.assertRaises(WebSocketTransportError):
                WebSocketConnection(sock).request_connect('g', 's', request_id=bad)
            sock.sendall.assert_not_called()
