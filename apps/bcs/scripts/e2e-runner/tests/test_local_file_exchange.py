"""Finite local-file CLI exchange tests using real loopback HTTP sockets."""
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_file_exchange import LocalFileExchange, LocalFileExchangeError


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.reply_mode = 'normal'
        outer = self

        class Product(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def _body(self):
                return self.rfile.read(int(self.headers.get('Content-Length', '0')))

            def _send(self, status, body=b'', headers=()):
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_POST(self):
                body = self._body()
                outer.received.append((self.command, self.path, dict(self.headers), body))
                origin = 'http://127.0.0.1:' + str(outer.server.server_port)
                if self.path == '/sessions/s%3A1/files':
                    target = origin + '/sessions/s%3A1/files/file-1/content'
                    if outer.reply_mode == 'foreign':
                        target = 'http://127.0.0.1:9/sessions/s%3A1/files/file-1/content'
                    if outer.reply_mode == 'path-drift':
                        target = origin + '/sessions/s%3A1/files/file-2/content'
                    value = {'mode': 'single', 'method': 'PUT', 'method': 'PUT', 'expires_at': 1,
                             'upload_url': target, 'file_id': 'file-1'}
                    if outer.reply_mode == 'multipart':
                        value = {'mode': 'multipart', 'file_id': 'file-1', 'parts': []}
                    self._send(201, json.dumps(value).encode())
                elif self.path == '/sessions/s%3A1/files/file-1/complete':
                    if outer.reply_mode == 'complete-failure':
                        self._send(500, b'{"error":"failed"}')
                    else:
                        self._send(200, b'{"file_id":"file-1","status":"Ready"}')
                elif self.path == '/sessions/s%3A1/files/file-1/share':
                    self._send(201, b'{"share_url":"secret-url","share_token":"secret","expires_at":1}')
                else:
                    self._send(404, b'{"error":"missing"}')

            def do_PUT(self):
                body = self._body()
                outer.received.append((self.command, self.path, dict(self.headers), body))
                self._send(202, b'{"file_id":"file-1","status":"Pending"}')

            def do_GET(self):
                outer.received.append((self.command, self.path, dict(self.headers), b''))
                if self.path.endswith('/content'):
                    self._send(200, b'hello')
                elif self.path.endswith('/capabilities'):
                    self._send(200, b'{"supports_stream_put":true}')
                else:
                    self._send(200, b'{"items":[],"total":0}')

            def do_DELETE(self):
                outer.received.append((self.command, self.path, dict(self.headers), self._body()))
                self._send(204)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Product)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.case = SimpleNamespace(
            identities=SimpleNamespace(origin='http://127.0.0.1:' + str(self.server.server_port)),
            invalidated=False, saves=0, _active=lambda: None)
        self.case._save = lambda: setattr(self.case, 'saves', self.case.saves + 1)
        self.event = {'submitted_request_id': 'file-exchange'}

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, exchange, method, path, body=b'', headers=None):
        values = {'Authorization': 'Bearer bot-token'}
        if body:
            values['Content-Type'] = 'application/json'
        values.update(headers or {})
        connection = HTTPConnection('127.0.0.1', exchange.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body if body else None, headers=values)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def raw(self, exchange, request):
        sock = socket.create_connection(('127.0.0.1', exchange.server.server_port), timeout=3)
        try:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            result = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    return bytes(result)
                result.extend(chunk)
        finally:
            sock.close()

    def upload(self, exchange, payload=b'hello'):
        prepare = json.dumps({'file_name': 'note.txt', 'size': len(payload),
                              'mime_type': 'text/plain'}, separators=(',', ':')).encode()
        status, body = self.request(exchange, 'POST', '/sessions/s%3A1/files', prepare)
        if status != 201:
            return [(status, body)]
        target = json.loads(body)['upload_url']
        path = target.split(exchange.origin, 1)[1]
        chunked = (b'PUT ' + path.encode() + b' HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                   b'Authorization: Bearer bot-token\r\nContent-Type: text/plain\r\n'
                   b'Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n'
                   + format(len(payload), 'x').encode() + b'\r\n' + payload + b'\r\n0\r\n\r\n')
        put_reply = self.raw(exchange, chunked)
        complete = self.request(exchange, 'POST', '/sessions/s%3A1/files/file-1/complete', b'{}')
        return [(status, body), (int(put_reply.split(b' ', 2)[1]), put_reply), complete]

    def test_real_forwarded_upload_is_ordered_and_chunked_is_normalized(self):
        prepared = []
        with LocalFileExchange(
                self.case, self.event, token='bot-token', operation='upload',
                session_id='s:1', payload=b'hello', filename='note.txt',
                on_prepared=prepared.append) as exchange:
            replies = self.upload(exchange)
        self.assertEqual([item[0] for item in replies], [201, 202, 200])
        self.assertEqual(prepared, ['file-1'])
        self.assertEqual([(m, p, b) for m, p, _h, b in self.received], [
            ('POST', '/sessions/s%3A1/files', b'{"file_name":"note.txt","size":5,"mime_type":"text/plain"}'),
            ('PUT', '/sessions/s%3A1/files/file-1/content', b'hello'),
            ('POST', '/sessions/s%3A1/files/file-1/complete', b'{}'),
        ])
        self.assertEqual(self.received[1][2]['Content-Length'], '5')
        self.assertNotIn('Transfer-Encoding', self.received[1][2])
        self.assertEqual(exchange.response, {'file_id': 'file-1', 'status': 'Ready'})
        self.assertEqual(exchange.attempts, 3)
        self.assertEqual(exchange.forwarded, 3)
        self.assertIsNone(exchange.failure)
        self.assertEqual([row['phase'] for row in self.event['wire']],
                         ['prepare', 'upload-content', 'complete'])
        self.assertTrue(self.event['wire'][0]['transport_only_upload_url_rewrite'])
        self.assertEqual(self.event['wire'][1]['transport_only_original_framing'], 'chunked')
        persisted = json.dumps(self.event)
        self.assertNotIn('bot-token', persisted)
        self.assertNotIn('secret-url', persisted)
        self.assertNotIn('hello', persisted)

    def test_malformed_chunk_framing_never_reaches_product(self):
        with LocalFileExchange(self.case, self.event, token='bot-token', operation='upload',
                               session_id='s:1', payload=b'hello', filename='note.txt') as exchange:
            prepare = b'{"file_name":"note.txt","size":5,"mime_type":"text/plain"}'
            self.assertEqual(self.request(exchange, 'POST', '/sessions/s%3A1/files', prepare)[0], 201)
            request = (b'PUT /sessions/s%3A1/files/file-1/content HTTP/1.1\r\nHost: x\r\n'
                       b'Authorization: Bearer bot-token\r\nContent-Type: text/plain\r\n'
                       b'Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n'
                       b'5;ext=x\r\nhello\r\n0\r\n\r\n')
            self.assertIn(b' 502 ', self.raw(exchange, request))
        self.assertEqual(len(self.received), 1)
        self.assertEqual(exchange.forwarded, 1)
        self.assertIsNotNone(exchange.failure)

    def test_foreign_path_drift_and_multipart_targets_are_rejected_before_cli_put(self):
        for mode in ('foreign', 'path-drift', 'multipart'):
            with self.subTest(mode=mode):
                self.reply_mode = mode
                event = {'submitted_request_id': 'target-' + mode}
                with LocalFileExchange(self.case, event, token='bot-token', operation='upload',
                                       session_id='s:1', payload=b'hello', filename='note.txt') as exchange:
                    prepare = b'{"file_name":"note.txt","size":5,"mime_type":"text/plain"}'
                    status, _body = self.request(exchange, 'POST', '/sessions/s%3A1/files', prepare)
                    self.assertEqual(status, 502)
                self.assertEqual(exchange.forwarded, 1)
                self.assertIsNotNone(exchange.failure)
                self.reply_mode = 'normal'
        self.assertEqual(len(self.received), 3)

    def test_complete_failure_rejects_cli_fallback_delete_without_forwarding(self):
        self.reply_mode = 'complete-failure'
        with LocalFileExchange(self.case, self.event, token='bot-token', operation='upload',
                               session_id='s:1', payload=b'hello', filename='note.txt') as exchange:
            replies = self.upload(exchange)
            self.assertEqual(replies[-1][0], 500)
            status, _ = self.request(exchange, 'DELETE', '/sessions/s%3A1/files/file-1')
            self.assertEqual(status, 502)
        self.assertEqual([row[0] for row in self.received], ['POST', 'PUT', 'POST'])
        self.assertEqual(exchange.forwarded, 3)
        self.assertEqual(exchange.attempts, 4)
        self.assertIsNotNone(exchange.failure)

    def test_callback_failure_prevents_prepare_reply_put_and_complete(self):
        def fail(_file_id):
            raise RuntimeError('ledger unavailable')
        with LocalFileExchange(self.case, self.event, token='bot-token', operation='upload',
                               session_id='s:1', payload=b'hello', filename='note.txt',
                               on_prepared=fail) as exchange:
            prepare = b'{"file_name":"note.txt","size":5,"mime_type":"text/plain"}'
            self.assertEqual(self.request(exchange, 'POST', '/sessions/s%3A1/files', prepare)[0], 502)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(exchange.forwarded, 1)
        self.assertIsInstance(exchange.failure, RuntimeError)

    def test_single_operations_forward_exact_routes_and_keep_sensitive_share_only_in_memory(self):
        cases = [
            ('list', '', 'GET', '/sessions/s%3A1/files', b'', 200),
            ('capabilities', '', 'GET', '/sessions/s%3A1/files/capabilities', b'', 200),
            ('download', 'file-1', 'GET', '/sessions/s%3A1/files/file-1/content', b'', 200),
            ('share', 'file-1', 'POST', '/sessions/s%3A1/files/file-1/share', b'{}', 201),
            ('delete', 'file-1', 'DELETE', '/sessions/s%3A1/files/file-1', b'', 204),
        ]
        for operation, file_id, method, path, body, expected in cases:
            with self.subTest(operation=operation):
                event = {'submitted_request_id': 'single-' + operation}
                with LocalFileExchange(self.case, event, token='bot-token', operation=operation,
                                       session_id='s:1', file_id=file_id) as exchange:
                    status, payload = self.request(exchange, method, path, body)
                self.assertEqual(status, expected)
                self.assertEqual(exchange.forwarded, 1)
                if operation == 'download':
                    self.assertEqual(exchange.response, b'hello')
                if operation == 'share':
                    self.assertEqual(exchange.response['share_token'], 'secret')
                    self.assertNotIn('secret', json.dumps(event))
                    self.assertNotIn('secret-url', json.dumps(event))

    def test_duplicate_unexpected_delete_and_privileged_headers_never_forward(self):
        with LocalFileExchange(self.case, self.event, token='bot-token', operation='list',
                               session_id='s:1') as exchange:
            self.assertEqual(self.request(exchange, 'GET', '/sessions/s%3A1/files')[0], 200)
            self.assertEqual(self.request(exchange, 'GET', '/sessions/s%3A1/files')[0], 502)
        self.assertEqual(exchange.forwarded, 1)
        event = {'submitted_request_id': 'bad-delete'}
        with LocalFileExchange(self.case, event, token='bot-token', operation='delete',
                               session_id='s:1', file_id='file-1') as exchange:
            status, _ = self.request(exchange, 'DELETE', '/sessions/s%3A1/files/file-1',
                                     headers={'Cookie': 'session=forbidden'})
            self.assertEqual(status, 502)
        self.assertEqual(exchange.forwarded, 0)

    def test_actual_correlation_is_saved_before_upstream_dispatch(self):
        persisted = []
        def save():
            persisted.append(json.loads(json.dumps(self.event)))
        self.case._save = save
        with LocalFileExchange(self.case, self.event, token='bot-token', operation='list', session_id='s:1') as exchange:
            status, _ = self.request(exchange, 'GET', '/sessions/s%3A1/files')
        self.assertEqual(status, 200)
        first = next(v['wire'][0] for v in persisted if v.get('wire'))
        self.assertEqual(first['dispatch_status'], 'INTENDED')
        self.assertEqual(first['submitted_request_id'], self.received[0][2]['X-Request-ID'])

    def test_stop_and_absolute_timeout_close_owned_handles(self):
        exchange = LocalFileExchange(self.case, self.event, token='bot-token', operation='list',
                                     session_id='s:1')
        exchange.__enter__()
        left, right = socket.socketpair()
        exchange._incoming = left
        exchange.deadline = time.monotonic() - 1
        with self.assertRaises(LocalFileExchangeError):
            exchange._require_running()
        exchange.__exit__(KeyboardInterrupt, None, None)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        self.assertEqual(right.recv(1), b'')
        self.assertFalse(exchange.thread.is_alive())
        self.assertTrue(self.case.invalidated)


if __name__ == '__main__':
    unittest.main()
