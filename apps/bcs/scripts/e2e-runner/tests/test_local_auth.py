"""Owned-loopback signing capability cannot be obtained from a remote profile."""
import base64
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import AdapterCapabilityError, adapter_for
from bcs_e2e.file_transport import RemoteFileClient
from bcs_e2e.local_adapter import resolve_owned_profile
from bcs_e2e.local_auth import OwnedLocalAuth, LocalAuthError
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_fixtures import LocalFixtureError
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.run_state import write
from bcs_e2e.transport import ReadClient
from bcs_e2e.write_transport import WriteClient, WriteTransportError
from test_write_transport import Reply


class LocalAuthTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Mock(spec=OwnedBcsProcess)
        inputs = prepare_local_bcs_inputs(Path(tmp.name) / 'runtime', run_id='auth-test', port=45000)
        self.runtime.inputs, self.runtime.root = inputs, inputs.root
        self.runtime.run_id, self.runtime.state, self.runtime.ready = 'auth-test', 'RUNNING', True
        self.runtime.child = Mock()
        self.runtime.child.poll.return_value = None
        self.runtime._owns_listener.return_value = True
        self.roles = {role: {'bot_id': 'bot-' + role, 'owner_id': inputs.human_id, 'dedicated': True}
                      for role in ('driver', 'member')}
        write(inputs.root / 'local-fixtures.json', {'schema_version': 1, 'roles': self.roles})
        check = patch('bcs_e2e.local_fixtures.OwnedLocalFixtures.check', return_value={'roles': self.roles})
        check.start()
        self.addCleanup(check.stop)
        self.profile = resolve_owned_profile(self.runtime)
        self.auth = OwnedLocalAuth(self.runtime, self.profile)

    def test_short_lived_principal_uses_only_runtime_key_and_is_refreshed_per_request(self):
        with patch.dict(os.environ, {'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE': 'ambient-wrong-key'}):
            with patch('bcs_e2e.local_auth.time.time', return_value=100):
                first = self.auth.headers(self.profile)['X-Avernet-Principal']
            with patch('bcs_e2e.local_auth.time.time', return_value=170):
                second = self.auth.headers(self.profile)['X-Avernet-Principal']
        self.assertNotEqual(first, second)
        header, claims, signature = first.split('.')
        decoded = json.loads(base64.urlsafe_b64decode(claims + '=' * (-len(claims) % 4)))
        self.assertEqual((decoded['iat'], decoded['exp']), (100, 160))
        self.assertEqual(decoded['principals'][0]['subject']['id'], self.runtime.inputs.human_id)
        expected = hmac.new(self.runtime.inputs.environment['AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'].encode(),
                            (header + '.' + claims).encode(), hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4)), expected)
        self.assertEqual(self.profile['auth'], {'kind': 'owned-local'})
        self.assertNotIn(first, json.dumps(self.profile))

    def test_remote_mock_wrong_origin_and_wrong_human_cannot_sign(self):
        for mutation in ('remote', 'mock', 'origin', 'human'):
            value = copy.deepcopy(self.profile)
            if mutation == 'remote': value['environment']['tier'] = 'preprod'
            elif mutation == 'mock': value['entrypoint']['kind'] = 'mock'
            elif mutation == 'origin': value['entrypoint']['origin'] = 'http://127.0.0.1:45001'
            else:
                value['human']['user_id'] = 'someoneelse'
                for role in value['actors'].values(): role['owner_id'] = 'someoneelse'
            with self.subTest(mutation=mutation), self.assertRaises((LocalAuthError, ConfigError)):
                OwnedLocalAuth(self.runtime, value)

    def test_clients_and_default_adapter_refuse_profile_without_live_capability(self):
        for client in (ReadClient, WriteClient, RemoteFileClient):
            with self.subTest(client=client), self.assertRaises(LocalAuthError): client(self.profile)
        with self.assertRaises(AdapterCapabilityError): adapter_for(self.profile)

    def test_bound_profile_and_fixture_changes_and_stopped_runtime_block_before_send(self):
        client = WriteClient(self.profile, local_auth=self.auth)
        client.opener = Mock()
        self.profile['policy']['timeout_seconds'] = 1
        with self.assertRaises(LocalAuthError): client.call('group.read', group_id='g1')
        self.profile['policy']['timeout_seconds'] = 30
        self.runtime.ready = False
        with self.assertRaises(LocalFixtureError): client.call('group.read', group_id='g1')
        self.runtime.ready = True
        write(self.runtime.root / 'local-fixtures.json', {'schema_version': 1, 'roles': {}})
        with self.assertRaises(LocalAuthError): client.call('group.read', group_id='g1')
        client.opener.open.assert_not_called()

    def test_explicit_local_identity_route_and_proxy_disabled(self):
        with patch('bcs_e2e.transport.build_opener') as factory:
            factory.return_value.open.return_value = Reply({'user_id': self.runtime.inputs.human_id})
            client = ReadClient(self.profile, local_auth=self.auth)
            identity = client.read('identity.read')
            self.assertEqual(identity.data['user_id'], self.runtime.inputs.human_id)
            request = factory.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.endswith('/auth/user'))
            self.assertIn('X-avernet-principal', request.headers)
            self.assertEqual(factory.call_args.args[0].proxies, {})
            self.assertNotIn('X-avernet-principal', json.dumps(identity.evidence))

    def test_denied_request_never_falls_back_or_leaks_principal(self):
        for code in (401, 403, 302):
            client = WriteClient(self.profile, local_auth=self.auth)
            client.opener = Mock()
            client.opener.open.return_value = Reply(b'private-body', code)
            with self.assertRaises(WriteTransportError) as error:
                client.call('group.create', body={}, expected=(200, 201))
            self.assertNotIn('private-body', str(error.exception))
            self.assertEqual(client.opener.open.call_count, 1)

    def test_sqlite_profile_cannot_claim_remote_physical_binding(self):
        for change in ({'database': 'other'}, {'server': 'other'}):
            value = copy.deepcopy(self.profile)
            value['observer'].update(change)
            with self.assertRaises(ConfigError): validate(value)
