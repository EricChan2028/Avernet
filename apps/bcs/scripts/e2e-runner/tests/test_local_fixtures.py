from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_fixtures import OwnedLocalFixtures, LocalFixtureError
from bcs_e2e.owned_bcs import OwnedBcsProcess


class LocalFixtureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.inputs = prepare_local_bcs_inputs(Path(tmp.name) / 'runtime', run_id='fixtures', port=45000)
        self.runtime = Mock(spec=OwnedBcsProcess)
        self.runtime.inputs = self.inputs
        self.runtime.root = self.inputs.root
        self.runtime.run_id = 'fixtures'
        self.runtime.state = 'RUNNING'
        self.runtime.ready = True
        self.runtime.child = Mock()
        self.runtime.child.poll.return_value = None
        self.runtime.allow_ambient_local_identity = True
        self.runtime._owns_listener.return_value = True
        self.handler = OwnedLocalFixtures(self.runtime)
        self.operations = []

    def response(self, operation, **kwargs):
        self.operations.append(operation)
        human = self.inputs.human_id
        if operation == 'identity': return {'user_id': human}
        if operation == 'ensure-human':
            return {'actor_uuid': 'human_' + human, 'failed_bots': [], 'errors': []}
        if operation == 'registration-token': return {'token': 'private-registration-token'}
        if operation == 'register':
            role = 'driver' if 'driver' in kwargs['name'] else 'member'
            return {'bot_uuid': 'bot-' + role, 'bot_token': 'private-bot-token'}
        if operation == 'read-bot':
            role = kwargs['bot_id'].removeprefix('bot-')
            return {'bot_uuid': kwargs['bot_id'], 'created_by': human, 'actor_kind': 'bot',
                    'env': 'local', 'status': 'online',
                    'capabilities': {'name': f'e2e-{role}-{human}'}}
        raise AssertionError(operation)

    def test_create_once_then_read_only_check_without_credentials_in_artifacts(self):
        with patch.object(self.handler, '_request', side_effect=self.response):
            applied = self.handler.apply(allow_write=True)
            self.assertEqual(applied['status'], 'APPLIED')
            self.operations.clear()
            verified = self.handler.check()
            self.assertEqual(self.operations, ['identity', 'read-bot', 'read-bot'])
            self.assertEqual(verified['status'], 'VERIFIED')
            with self.assertRaises(LocalFixtureError):
                self.handler.apply(allow_write=True)
        combined = self.handler.path.read_text() + json.dumps(verified)
        self.assertNotIn('private-registration-token', combined)
        self.assertNotIn('private-bot-token', combined)
        self.assertEqual(self.handler.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.inputs.human_id.isascii() and self.inputs.human_id.isalnum())

    def test_optional_driver_handoff_does_not_persist_any_bot_token(self):
        sink = Mock()
        with patch.object(self.handler, '_request', side_effect=self.response):
            result = self.handler.apply(allow_write=True, driver_token_sink=sink)
        sink.assert_called_once_with('bot-driver', 'private-bot-token')
        self.assertEqual(result['status'], 'APPLIED')
        self.assertNotIn('private-bot-token', self.handler.path.read_text()+json.dumps(result))

    def test_handoff_waits_for_driver_readback_and_missing_token_blocks(self):
        sink = Mock()
        def missing(operation, **kwargs):
            value = self.response(operation, **kwargs)
            if operation == 'register':
                value.pop('bot_token')
            return value
        with patch.object(self.handler, '_request', side_effect=missing), self.assertRaises(LocalFixtureError):
            self.handler.apply(allow_write=True, driver_token_sink=sink)
        sink.assert_not_called()
        self.assertEqual(json.loads(self.handler.path.read_text())['status'], 'BLOCKED')
        self.assertEqual(self.operations[-1], 'read-bot')

    def test_default_human_pilot_does_not_require_a_returned_bot_token(self):
        def without_token(operation, **kwargs):
            value = self.response(operation, **kwargs)
            value.pop('bot_token', None)
            return value
        with patch.object(self.handler, '_request', side_effect=without_token):
            self.assertEqual(self.handler.apply(allow_write=True)['status'], 'APPLIED')

    def test_permission_and_live_ownership_are_required_before_http(self):
        with patch.object(self.handler, '_request') as request:
            with self.assertRaises(LocalFixtureError): self.handler.apply()
            self.runtime.ready = False
            with self.assertRaises(LocalFixtureError): self.handler.apply(allow_write=True)
            request.assert_not_called()
        self.assertFalse(self.handler.path.exists())

    def test_lost_create_response_remains_unknown_and_is_never_retried(self):
        def lost(operation, **kwargs):
            if operation == 'register': raise LocalFixtureError('synthetic lost response')
            return self.response(operation, **kwargs)
        with patch.object(self.handler, '_request', side_effect=lost) as request:
            with self.assertRaises(LocalFixtureError): self.handler.apply(allow_write=True)
            calls = request.call_count
            with self.assertRaises(LocalFixtureError): self.handler.apply(allow_write=True)
            self.assertEqual(request.call_count, calls)
        state = json.loads(self.handler.path.read_text())
        self.assertEqual(state['status'], 'BLOCKED')
        self.assertEqual(state['pending_operation'], 'register-driver')
        self.assertEqual(state['roles'], {})

    def test_onboard_owner_mismatch_retains_exact_created_id_and_blocks(self):
        def bad_owner(operation, **kwargs):
            result = self.response(operation, **kwargs)
            if operation == 'read-bot': result['created_by'] = 'other-owner'
            return result
        with patch.object(self.handler, '_request', side_effect=bad_owner):
            with self.assertRaises(LocalFixtureError): self.handler.apply(allow_write=True)
        state = json.loads(self.handler.path.read_text())
        self.assertEqual(state['status'], 'BLOCKED')
        self.assertEqual(state['roles']['driver']['bot_id'], 'bot-driver')
        self.assertNotIn('member', state['roles'])

    def test_drifted_marker_blocks_check_without_repair(self):
        with patch.object(self.handler, '_request', side_effect=self.response):
            self.handler.apply(allow_write=True)
        state = json.loads(self.handler.path.read_text())
        state['roles']['member']['bot_id'] = state['roles']['driver']['bot_id']
        self.handler.path.write_text(json.dumps(state))
        with patch.object(self.handler, '_request') as request:
            with self.assertRaises(LocalFixtureError): self.handler.check()
            request.assert_not_called()

    def test_no_ambient_runtime_uses_an_explicit_fixture_provisioning_identity(self):
        self.runtime.allow_ambient_local_identity = False
        request = None
        opener = Mock()
        def capture(value, timeout=5):
            nonlocal request
            request = value
            raise HTTPError(value.full_url, 401, 'private-value', {}, None)
        opener.open.side_effect = capture
        self.handler.opener = opener
        with self.assertRaises(LocalFixtureError):
            self.handler._request('identity')
        self.assertEqual(request.headers.get('X-mock-user-id'), self.inputs.human_id)
        self.assertEqual(request.headers.get('X-mock-nick-name'), 'E2E local test human')

    def test_network_error_cannot_leak_registration_url(self):
        self.handler.opener = Mock()
        self.handler.opener.open.side_effect = HTTPError('http://127.0.0.1/?token=private-value', 401,
                                                         'private-value', {}, None)
        with self.assertRaises(LocalFixtureError) as caught:
            self.handler._request('register', token='private-value', name='e2e-bot')
        self.assertNotIn('private-value', str(caught.exception))
