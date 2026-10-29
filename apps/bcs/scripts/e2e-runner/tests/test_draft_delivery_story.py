"""Run the Draft delivery black-box child against a deterministic HTTP fence."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.checks import (verify_all_obligations,
                                           scan_redaction)
from bcs_e2e.draft_delivery.story import EXPECTED_CHECKS, Story

FIXTURE = ROOT.parents[1] / 'tests' / 'fixtures' / 'collaboration-definition-draft' / 'indentation-regression.json'
BOT_TOKEN = 'owner-secret-only-in-memory'
DRAFT_ID = '750c52c6-60e2-4249-8ee5-1fb8cb9402ab'


class DraftFence:
    def __init__(self, source: str):
        self.source = source
        self.updated = source + '\n# second revision\n'
        self.head = 0
        self.revoked: set[int] = set()
        self.deleted = False
        self.created_delete_attempts = 0

    @staticmethod
    def content(text: str, revision: int) -> dict:
        return {'draft_id': DRAFT_ID, 'revision': revision, 'definition_yaml': text,
                'title': 'Draft delivery regression',
                'content_hash': hashlib.sha256(text.encode()).hexdigest(),
                'size_bytes': len(text.encode()),
                'created_by': 'author', 'created_at': '2026-10-30T00:00:00Z'}

    def read(self, revision: int):
        if self.deleted or revision in self.revoked or revision > self.head:
            return None
        return self.content(self.source if revision == 1 else self.updated, revision)


class Handler(BaseHTTPRequestHandler):
    fence: DraftFence

    def log_message(self, *_args):
        pass

    def _send(self, status, value=None):
        raw = b'' if value is None else json.dumps(value).encode()
        self.send_response(status)
        if self.path.startswith('/collaboration/drafts/'):
            self.send_header('Cache-Control', 'no-store')
        if raw:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _authorized(self):
        return self.headers.get('Authorization') == 'Bearer ' + BOT_TOKEN

    def _body(self):
        length = int(self.headers.get('Content-Length', '0'))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = urlsplit(self.path).path
        fence = self.fence
        if path == '/register/token':
            return self._send(200, {'token': 'registration-secret-only-in-memory'})
        if path.startswith('/collaboration/drafts/'):
            parts = path.split('/')
            if len(parts) == 4:
                if not self._authorized():
                    return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
                return self._send(200, {'draft_id': DRAFT_ID, 'owner_bot_id': 'author',
                                        'head_revision': fence.head,
                                        'created_at': '2026-10-30T00:00:00Z',
                                        'deleted': fence.deleted})
            revision = int(parts[5])
            if len(parts) == 7 and parts[6] == 'reference':
                if not self._authorized():
                    return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
                value = fence.read(revision)
                if value is None:
                    return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
                return self._send(200, {'schemaVersion': 2, 'draftId': parts[3],
                                        'revision': revision, 'readToken': 'A' * 43})
            value = fence.read(revision)
            if value is None:
                return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
            return self._send(200, value)
        return self._send(404, {'error': 'NOT_FOUND'})

    def do_POST(self):
        path = urlsplit(self.path).path
        fence = self.fence
        if path == '/me/ensure-human':
            return self._send(200, {})
        if path == '/register':
            return self._send(200, {'bot_uuid': 'author-bot', 'bot_token': BOT_TOKEN})
        if path == '/bots/onboard':
            return self._send(200, {})
        if path.startswith('/collaboration/drafts/'):
            parts = path.split('/')
            if parts[-1] == 'validate':
                revision = int(parts[-2])
                value = fence.read(revision)
                if value is None:
                    return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
                return self._send(200, {'draft_id': DRAFT_ID, 'revision': revision,
                                        'content_hash': value['content_hash'],
                                        'validation': {'valid': True}})
            if parts[-1] == 'revisions':
                if not self._authorized():
                    return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
                body = self._body()
                if body.get('expected_revision') != fence.head:
                    return self._send(409, {'error': 'DRAFT_CONFLICT'})
                fence.head = 2
                return self._send(200, fence.content(fence.updated, 2))
        return self._send(404, {'error': 'NOT_FOUND'})

    def do_PUT(self):
        path = urlsplit(self.path).path
        fence = self.fence
        if not path.startswith('/collaboration/drafts/'):
            return self._send(404, {'error': 'NOT_FOUND'})
        if not self._authorized():
            return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
        body = self._body()
        if fence.deleted:
            return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
        if fence.head == 0:
            fence.head = 1
            return self._send(201, fence.content(fence.source, 1))
        if body.get('definition_yaml') == fence.source:
            return self._send(200, fence.content(fence.source, 1))
        return self._send(409, {'error': 'DRAFT_CONFLICT'})

    def do_DELETE(self):
        path = urlsplit(self.path).path
        fence = self.fence
        if not path.startswith('/collaboration/drafts/'):
            return self._send(404, {'error': 'NOT_FOUND'})
        if not self._authorized():
            fence.created_delete_attempts += 1
            return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
        parts = path.split('/')
        if len(parts) == 7 and parts[6] == 'share':
            fence.revoked.add(int(parts[5]))
            return self._send(204)
        if len(parts) == 4:
            fence.deleted = True
            return self._send(204)
        return self._send(404, {'error': 'NOT_FOUND'})


class DraftDeliveryStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = json.loads(FIXTURE.read_text(encoding='utf-8'))['revision']['definition_yaml']
        cls.fence = DraftFence(source)
        Handler.fence = cls.fence
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = 'http://127.0.0.1:' + str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_child_runs_exact_assertion_count_and_emits_redacted_evidence(self):
        story = Story(self.base_url, 'e2ehuman', FIXTURE)
        evidence = story.run()
        self.assertEqual(story.checks, EXPECTED_CHECKS)
        self.assertEqual(EXPECTED_CHECKS, 57)
        payload = json.dumps(evidence, sort_keys=True)
        for canary in ('registration-secret-only-in-memory', BOT_TOKEN, 'readToken', 'Bearer '):
            self.assertNotIn(canary, payload)
        self.assertTrue(scan_redaction('evidence', payload.encode())['clean'])
        evidence['redaction'] = {
            'token_material_captured': False,
            'streams': [{'name': name, 'clean': True}
                        for name in ('story.stdout', 'story.stderr', 'owned.console')],
            'artifacts': [{'name': name, 'clean': True}
                          for name in ('child.receipt', 'flow.report', 'canonical.report')],
        }
        verify_all_obligations(evidence)
        self.assertEqual(len(evidence['create']['attempts']), 2)
        self.assertEqual(evidence['availability']['deleted']['status'], 404)


if __name__ == '__main__':
    unittest.main()
