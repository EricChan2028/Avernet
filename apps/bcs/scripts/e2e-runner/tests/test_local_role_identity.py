"""Managed credentials never survive partial initialization or invalid identity."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_fixtures import LocalFixtureError, OwnedLocalFixtures
from bcs_e2e.local_role_identity import LocalRoleIdentityError, OwnedLocalRoleIdentity
import test_local_fixture_sets as fixtures


class RoleIdentityTests(unittest.TestCase):
    response = fixtures.LocalFixtureSetTests.response

    def setUp(self):
        fixtures.LocalFixtureSetTests.setUp(self)
        self.bound = OwnedLocalFixtures(self.runtime, fixture_set='workspace')

    def provision(self, identity):
        with patch.object(self.bound, '_request', side_effect=self.response):
            return self.bound.apply(allow_write=True, role_identity=identity)

    @staticmethod
    def identity_response(operation, bot_id, token):
        return {'success': True, 'data': {'bot_uuid': bot_id, 'visibility': 'protected'}
                if operation == 'visibility' else []}

    def test_only_selected_roles_are_captured_then_cleared_without_artifact_tokens(self):
        with OwnedLocalRoleIdentity(self.bound, roles=('CEO', 'PM')) as identity:
            applied = self.provision(identity)
            self.assertEqual(set(identity._entries), {'CEO', 'PM'})
            with patch.object(identity, '_request', side_effect=self.identity_response) as request:
                result = identity.verify('CEO')
            self.assertEqual(result['bot_id'], applied['roles']['CEO']['bot_id'])
            self.assertFalse(result['atomic_identity_attestation'])
            self.assertEqual([call.args[0] for call in request.call_args_list], ['visibility', 'friends', 'visibility'])
            self.assertTrue(all(call.args[1:] == ('bot-CEO', 'test-token-CEO') for call in request.call_args_list))
            self.assertNotIn('test-token-', json.dumps(result) + self.bound.path.read_text() + repr(identity))
        self.assertEqual(len(identity._entries), 0)
        self.assertEqual(identity.summary()['credential_memory'], 'CLEARED')
        with self.assertRaises(LocalRoleIdentityError): identity.verify('PM')
        with self.assertRaises(LocalRoleIdentityError): identity.__enter__()

    def test_missing_permission_or_late_provisioning_failure_clears_all_captured_tokens(self):
        for failure in ('permission', 'lost-last-response', 'cancel', 'missing-token', 'publication'):
            with self.subTest(failure=failure):
                identity = OwnedLocalRoleIdentity(self.bound, roles=('CEO', 'PM'))
                with identity:
                    from bcs_e2e.run_state import write
                    def respond(operation, **kwargs):
                        if operation == 'register' and '-QA-' in kwargs['name']:
                            if failure == 'lost-last-response': raise LocalFixtureError('lost response')
                            if failure == 'cancel': raise KeyboardInterrupt()
                        result = self.response(operation, **kwargs)
                        if failure == 'missing-token' and operation == 'register' and '-PM-' in kwargs['name']:
                            result.pop('bot_token')
                        return result
                    def publish(path, value):
                        if failure == 'publication' and value.get('status') == 'APPLIED':
                            raise OSError('publication failure')
                        return write(path, value)
                    with patch.object(self.bound, '_request', side_effect=respond), \
                         patch('bcs_e2e.local_fixtures.write', side_effect=publish), \
                         self.assertRaises((LocalFixtureError, LocalRoleIdentityError, OSError, KeyboardInterrupt)):
                        self.bound.apply(allow_write=failure != 'permission', role_identity=identity)
                    self.assertEqual(len(identity._entries), 0)
                    self.assertEqual(identity.summary()['state'], 'CLOSED')
                if self.bound.path.exists(): self.bound.path.unlink()  # Test-only isolated receipt.

    def test_cross_handler_replay_or_missing_context_refused_before_http(self):
        identity = OwnedLocalRoleIdentity(self.bound, roles=('CEO',))
        with patch.object(self.bound, '_request') as request:
            with self.assertRaises(LocalRoleIdentityError): self.bound.apply(allow_write=True, role_identity=identity)
            request.assert_not_called()
        self.assertFalse(self.bound.path.exists())
        with OwnedLocalRoleIdentity(self.bound, roles=('CEO',)) as identity:
            other = OwnedLocalFixtures(self.runtime, fixture_set='workspace')
            with patch.object(other, '_request') as request:
                with self.assertRaises(LocalRoleIdentityError): other.apply(allow_write=True, role_identity=identity)
                request.assert_not_called()
        self.assertFalse(self.bound.path.exists())

    def test_capture_and_probe_replay_are_refused(self):
        with OwnedLocalRoleIdentity(self.bound, roles=('CEO',)) as identity:
            self.provision(identity)
            with self.assertRaises(LocalRoleIdentityError): identity._capture(self.bound, 'CEO', 'bot-CEO', 'replacement')
            with patch.object(identity, '_request', side_effect=self.identity_response) as request:
                identity.verify('CEO')
                with self.assertRaises(LocalRoleIdentityError): identity.verify('CEO')
                self.assertEqual(request.call_count, 3)
            self.assertEqual(len(identity._entries), 0)

    def test_foreign_role_swapped_id_receipt_or_runtime_drift_closes_before_request(self):
        for failure in ('role', 'swapped-id', 'receipt', 'stopped', 'run-id'):
            with self.subTest(failure=failure), OwnedLocalRoleIdentity(self.bound, roles=('CEO', 'PM')) as identity:
                self.provision(identity)
                if failure == 'swapped-id': identity._entries['CEO'] = identity._entries['PM']
                if failure == 'receipt':
                    state = json.loads(self.bound.path.read_text()); state['status'] = 'BLOCKED'
                    self.bound.path.write_text(json.dumps(state))
                if failure == 'stopped': self.runtime.child.poll.return_value = 0
                if failure == 'run-id': self.runtime.run_id = 'other-run'
                with patch.object(identity, '_request') as request:
                    with self.assertRaises((LocalRoleIdentityError, LocalFixtureError)):
                        identity.verify('foreign' if failure == 'role' else 'CEO')
                    request.assert_not_called()
                self.assertEqual(len(identity._entries), 0)
            self.runtime.child.poll.return_value = None; self.runtime.run_id = 'fixtures'
            self.bound.path.unlink()

    def test_request_failure_cancellation_or_post_read_drift_never_verifies(self):
        for failure in ('first-denied', 'second-denied', 'last-denied', 'cancel', 'drift'):
            with self.subTest(failure=failure), OwnedLocalRoleIdentity(self.bound, roles=('CEO',)) as identity:
                self.provision(identity)
                calls = []
                def request(operation, bot_id, token):
                    calls.append(operation)
                    if (failure == 'first-denied' or failure == 'second-denied' and len(calls) == 2
                            or failure == 'last-denied' and len(calls) == 3):
                        raise LocalRoleIdentityError('rejected')
                    if failure == 'cancel': raise KeyboardInterrupt()
                    if failure == 'drift':
                        state = json.loads(self.bound.path.read_text()); state['status'] = 'BLOCKED'
                        self.bound.path.write_text(json.dumps(state))
                    return self.identity_response(operation, bot_id, token)
                with patch.object(identity, '_request', side_effect=request), \
                     self.assertRaises((LocalRoleIdentityError, KeyboardInterrupt)):
                    identity.verify('CEO')
                self.assertEqual(identity.summary()['verified_roles'], [])
                self.assertEqual(len(identity._entries), 0)
                self.assertLessEqual(len(calls), 3)
            self.bound.path.unlink()

    def test_wire_is_get_bearer_only_and_errors_do_not_echo_secrets(self):
        identity = OwnedLocalRoleIdentity(self.bound, roles=('CEO',))
        response = Mock()
        response.status = 200
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"success":true,"data":[]}'
        identity.opener = Mock(); identity.opener.open.return_value = response
        identity._request('friends', 'bot-CEO', 'test-token-CEO')
        request = identity.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(dict(request.header_items()), {'Accept': 'application/json', 'Authorization': 'Bearer test-token-CEO'})
        self.assertEqual(request.full_url, self.inputs.origin + '/bots/bot-CEO/friends')
        for code in (401, 403, 302):
            identity.opener.open.side_effect = HTTPError(request.full_url, code, 'test-token-CEO', {}, None)
            with self.assertRaises(LocalRoleIdentityError) as caught: identity._request('friends', 'bot-CEO', 'test-token-CEO')
            self.assertNotIn('test-token-CEO', str(caught.exception))
        with self.assertRaises(LocalRoleIdentityError): identity._request('write', 'bot-CEO', 'test-token-CEO')

    def test_selection_and_cli_permission_boundaries(self):
        for roles in ((), ('missing',), ('CEO', 'CEO'), ['CEO']):
            with self.assertRaises(LocalRoleIdentityError): OwnedLocalRoleIdentity(self.bound, roles=roles)
        with self.assertRaises(LocalRoleIdentityError): OwnedLocalRoleIdentity(self.handler, roles=('driver',))
        for action, selected in (('smoke', 'workspace'), ('fixture-smoke', 'pilot')):
            with contextlib.redirect_stdout(io.StringIO()), patch('bcs_e2e.cli.local_runtime_smoke') as smoke:
                rc = main(['runtime', action, '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                           '--state-dir', str(self.inputs.root / 'unused'), '--fixture-set', selected,
                           '--verify-bot-identities', '--allow-local-processes', '--allow-fixture-write'])
            self.assertEqual(rc, 2); smoke.assert_not_called()
