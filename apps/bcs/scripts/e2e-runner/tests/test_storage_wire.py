"""Real loopback HTTP transport boundaries; synthetic servers, not product coverage."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.file_transport import FileTransportError, RemoteFileClient
import test_file_transport as fixtures


class Endpoint:
    def __init__(self):
        self.requests = []
        self.responses = []
        endpoint = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle_request(self):
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(min(length, 4096)) if length else b''
                endpoint.requests.append({'method': self.command, 'path': self.path,
                    'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
                status, headers, payload = (endpoint.responses.pop(0) if endpoint.responses
                                            else (500, {}, b'unexpected fixture request'))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_PUT = handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError('owned loopback fixture did not stop')


class StorageWireTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'TEST_COOKIE': 'test-cookie', 'TEST_AUTH': 'test-auth'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.product = Endpoint()
        self.addCleanup(self.product.close)
        self.storage = Endpoint()
        self.addCleanup(self.storage.close)
        self.profile = fixtures.FileTransportTests().profile()
        self.profile['entrypoint'].update(kind='gateway', origin=self.product.origin)
        self.profile['browser_origin'] = self.product.origin
        self.profile['auth'] = {'header_env': {'Cookie': 'TEST_COOKIE', 'Authorization': 'TEST_AUTH'}}
        self.profile['file_transfer'] = {'allowed_storage_origins': [self.storage.origin]}
        self.signed = self.storage.origin + '/private-signed-path?signature=test-signature'

    def client(self):
        # Real urllib opener and NoRedirect handler: only server responses are synthetic.
        return RemoteFileClient(self.profile)

    def assert_no_product_headers(self, request):
        for header in ('cookie', 'authorization', 'origin', 'referer', 'x-request-id',
                       'traceparent', 'x-avernet-principal'):
            self.assertNotIn(header, request['headers'])

    def assert_redacted(self, value):
        encoded = json.dumps(value)
        for secret in ('test-cookie', 'test-auth', 'test-signature', 'test-share', 'private-signed-path'):
            self.assertNotIn(secret, encoded)

    def test_download_only_initial_product_request_carries_human_credentials(self):
        self.product.responses = [(302, {'Location': self.signed}, b'')]
        self.storage.responses = [(200, {}, b'payload')]
        body, record = self.client().download('s1', 'f1')
        self.assertEqual(body, b'payload')
        self.assertEqual(len(self.product.requests), 1)
        self.assertEqual(len(self.storage.requests), 1)
        headers = self.product.requests[0]['headers']
        self.assertEqual(headers['cookie'], 'test-cookie')
        self.assertEqual(headers['authorization'], 'test-auth')
        self.assertEqual(headers['origin'], self.product.origin)
        self.assert_no_product_headers(self.storage.requests[0])
        self.assertEqual(self.storage.requests[0]['path'], '/private-signed-path?signature=test-signature')
        self.assertEqual([hop['http_status'] for hop in record['hops']], [302, 200])
        self.assert_redacted(record)

    def test_unapproved_redirect_is_not_followed_by_urllib(self):
        self.profile['file_transfer']['allowed_storage_origins'] = []
        self.product.responses = [(302, {'Location': self.signed}, b'')]
        with self.assertRaises(FileTransportError) as caught:
            self.client().download('s1', 'f1')
        self.assertEqual(len(self.product.requests), 1)
        self.assertEqual(self.storage.requests, [])
        self.assert_redacted(caught.exception.request_records)
        self.assert_redacted(str(caught.exception))

    def test_shared_download_never_sends_human_credentials_on_either_origin(self):
        self.product.responses = [(302, {'Location': self.signed}, b'')]
        self.storage.responses = [(200, {}, b'payload')]
        body, record = self.client().shared_download('test-share')
        self.assertEqual(body, b'payload')
        self.assertEqual(len(self.product.requests), 1)
        self.assertEqual(len(self.storage.requests), 1)
        initial = self.product.requests[0]
        self.assertTrue(initial['path'].endswith('?token=test-share'))
        self.assertNotIn('cookie', initial['headers'])
        self.assertNotIn('authorization', initial['headers'])
        self.assert_no_product_headers(self.storage.requests[0])
        self.assert_redacted(record)

    def test_signed_upload_uses_actual_put_without_product_credentials(self):
        self.storage.responses = [(204, {}, b'')]
        body, record = self.client().upload_to_signed_url(self.signed, b'payload')
        self.assertEqual(body, b'')
        self.assertEqual(self.product.requests, [])
        self.assertEqual(len(self.storage.requests), 1)
        request = self.storage.requests[0]
        self.assertEqual(request['method'], 'PUT')
        self.assertEqual(request['body'], b'payload')
        self.assert_no_product_headers(request)
        self.assert_redacted(record)

    def test_redirect_loop_stops_within_shared_four_hop_budget(self):
        self.product.responses = [(302, {'Location': self.signed}, b'')]
        self.storage.responses = [(302, {'Location': self.signed}, b'')] * 4
        with self.assertRaises(FileTransportError) as caught:
            self.client().download('s1', 'f1')
        self.assertEqual(len(self.product.requests), 1)
        self.assertEqual(len(self.storage.requests), 3)
        for request in self.storage.requests:
            self.assert_no_product_headers(request)
        self.assertEqual(len(caught.exception.request_records), 4)
        self.assert_redacted(caught.exception.request_records)

    def test_product_auth_denial_does_not_follow_location_or_retry(self):
        self.product.responses = [(401, {'Location': self.signed}, b'test-cookie')]
        with self.assertRaises(FileTransportError) as caught:
            self.client().download('s1', 'f1')
        self.assertEqual(len(self.product.requests), 1)
        self.assertEqual(self.storage.requests, [])
        self.assertEqual(caught.exception.request_records[0]['http_status'], 401)
        self.assert_redacted(caught.exception.request_records)
        self.assert_redacted(str(caught.exception))


if __name__ == '__main__':
    unittest.main()
