"""Bounded WebSocket failure evidence, with no remote deployment calls."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.adapters import LocalMockAdapter, RemotePilotAdapter
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_case
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint
from bcs_e2e.ws_transport import SessionWebSocketClient, WebSocketTransportError

ROOT = Path(__file__).resolve().parents[1]


class ReplySocket:
    def __init__(self, status=101, interrupt=False):
        self.status, self.interrupt = status, interrupt
        self.sent, self.closed, self.buffer = [], False, b''

    def settimeout(self, value): pass
    def close(self): self.closed = True

    def sendall(self, value):
        self.sent.append(value)
        if value.startswith(b'GET '):
            headers = dict(line.split(': ', 1) for line in value.decode().split('\r\n')[1:] if ': ' in line)
            accept = base64.b64encode(hashlib.sha1(
                (headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.buffer = (f'HTTP/1.1 {self.status} private-reason\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n'
                           f'X-Request-ID: {headers["X-Request-ID"]}\r\n'
                           'Location: https://private-location.test/?token=private-token\r\n\r\n').encode()
        else:
            length, offset = value[1] & 127, 2
            if length == 126:
                length, offset = int.from_bytes(value[2:4], 'big'), 4
            mask = value[offset:offset + 4]
            request = json.loads(bytes(byte ^ mask[i % 4] for i, byte in enumerate(value[offset + 4:])))
            self.frame_id = request['id']
            payload = json.dumps({'type': 'res', 'id': request['id'], 'ok': False,
                                  'error': {'code': 'private-server-payload'}}).encode()
            self.buffer = (bytes([0x81, 126]) + len(payload).to_bytes(2, 'big') + payload)

    def recv(self, count):
        if self.interrupt:
            raise KeyboardInterrupt()
        chunk, self.buffer = self.buffer[:count], self.buffer[count:]
        return chunk


class WebSocketEvidenceTests(unittest.TestCase):
    def test_denied_redirect_and_connect_rejection_preserve_actual_attempt_only(self):
        for status in (401, 403, 302, 101):
            with self.subTest(status=status):
                sock = ReplySocket(status)
                with patch('bcs_e2e.ws_transport.socket.create_connection', return_value=sock) as connect:
                    with self.assertRaises(WebSocketTransportError) as caught:
                        SessionWebSocketClient('http://example.test').connect('private-token', 'g1', 's1')
                self.assertTrue(sock.closed)
                self.assertEqual(connect.call_count, 1)
                error = caught.exception
                self.assertEqual(len(error.request_records), 1)
                record = error.request_records[0]
                self.assertEqual(record['http_status'], status)
                self.assertEqual(record['phase'], 'connect' if status == 101 else 'upgrade')
                self.assertFalse(record['connected'])
                self.assertTrue(record['request_id_echoed'])
                self.assertIn(record['submitted_request_id'].encode(), sock.sent[0])
                self.assertEqual(len(sock.sent), 2 if status == 101 else 1)
                self.assertEqual(record['connect_request_id'], sock.frame_id if status == 101 else None)
                self.assertGreaterEqual(record['elapsed_ms'], 0)
                self.assertNotIn('private', str(error) + json.dumps(record))

    def test_connection_failure_has_no_fictional_submitted_request(self):
        with patch('bcs_e2e.ws_transport.socket.create_connection', side_effect=OSError('private-network')):
            with self.assertRaises(WebSocketTransportError) as caught:
                SessionWebSocketClient('http://example.test').connect('private-token', 'g1', 's1')
        record = caught.exception.request_records[0]
        self.assertEqual(record['phase'], 'connection')
        self.assertIsNone(record['http_status'])
        self.assertIsNone(record['submitted_request_id'])
        self.assertIsNone(record['connect_request_id'])
        self.assertNotIn('private', str(caught.exception) + json.dumps(record))

    def test_invalid_input_precedes_network_and_has_no_attempt(self):
        with patch('bcs_e2e.ws_transport.socket.create_connection') as connect:
            with self.assertRaises(WebSocketTransportError) as caught:
                SessionWebSocketClient('http://example.test').connect('', 'g1', 's1')
        connect.assert_not_called()
        self.assertEqual(caught.exception.request_records, [])

    def test_interrupt_closes_the_held_socket_without_reconnect(self):
        sock = ReplySocket(interrupt=True)
        with patch('bcs_e2e.ws_transport.socket.create_connection', return_value=sock) as connect:
            with self.assertRaises(KeyboardInterrupt):
                SessionWebSocketClient('http://example.test').connect('private-token', 'g1', 's1')
        self.assertTrue(sock.closed)
        self.assertEqual(connect.call_count, 1)

    def test_pre02_keeps_failure_and_only_cleans_while_authorized(self):
        for status in (403, 101):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                    profile = load(ROOT / 'profiles/local.mock.json')
                    state = root / 'state'
                    prepare_run(profile, state, 'ws-failure')
                    adapter = LocalMockAdapter(profile)
                    remote = RemotePilotAdapter.__new__(RemotePilotAdapter)
                    remote.websocket = SessionWebSocketClient('http://example.test')
                    remote.ws_connections = {}
                    sock = ReplySocket(status)
                    with patch.object(adapter, 'session_ws_connect', side_effect=remote.session_ws_connect), \
                         patch('bcs_e2e.ws_transport.socket.create_connection', return_value=sock):
                        report = run_case(profile, state, 'ws-failure', 'PRE-02',
                                          adapter_factory=lambda *args, **kwargs: adapter)
                    self.assertEqual(report['functional'], 'FAIL')
                    self.assertEqual(report['cleanup'], 'BLOCKED' if status == 403 else 'PASS')
                    self.assertEqual(report['quiescence'], 'UNKNOWN' if status == 403 else 'CONFIRMED')
                    self.assertEqual(remote.ws_connections, {})
                    failed = [record for record in report['evidence'] if record.get('outcome') == 'ERROR']
                    self.assertEqual(len(failed), 1)
                    self.assertEqual(failed[0]['http_status'], status)
                    self.assertEqual(failed[0]['correlation_status'], 'RECORDED')
                    self.assertEqual(failed[0]['connect_request_id'], sock.frame_id if status == 101 else None)
                    with Ledger(state / 'run.sqlite', 'ws-failure', fingerprint(profile), read_only=True) as ledger:
                        correlations = [row for row in ledger.correlations() if row['operation'] == 'session.ws.connect']
                        self.assertEqual(len(correlations), 1)
                        self.assertEqual(correlations[0]['request_id'], failed[0]['submitted_request_id'])
                        self.assertEqual(bool(ledger.cleanup_plan()['resources']), status == 403)
                        if status == 403:
                            self.assertEqual(report['operation_authority'], 'INVALIDATED')
                            self.assertFalse(any(e.get('method') == 'DELETE' for e in report['evidence']))
                    self.assertTrue(sock.closed)
                    self.assertNotIn('private', json.dumps(report))
