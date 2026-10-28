"""Public-v1 network platform-asset read contract."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import RemoteNetworkPlatformAssetAdapter
from bcs_e2e.local_network_contract import (
    NETWORK_PLATFORM_ASSET_CASE_ID,
    NETWORK_PLATFORM_ASSET_IDENTITY_PATH,
    NETWORK_PLATFORM_ASSET_MANIFEST_PATH,
    execute_network_platform_asset_case,
    network_platform_asset_adaptability,
    network_platform_asset_case_plan,
)
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.identity_binding import HUMAN_VIEW
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ObservationError, ReadClient

TOKEN = 'driver-bot-private-token'
COOKIE = 'human-cookie-private-value'
PLATFORM_ASSET_PATH = '/api/v1/collaboration/assets/bcsPanel/test-panel.umd.js'
PLATFORM_ASSET_URL = 'https://asset.example.com/bcsPanel/index.umd.js'
ASSET_BYTES = b'(function(){})()' + b'/*' + b'x' * 2048 + b'*/'
ASSET_SHA256 = hashlib.sha256(ASSET_BYTES).hexdigest()


def platform_profile(*, absolute=False):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment']['id'] = 'local-singlebox-test'
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id='driver-bot', owner_id='owner-1')
    value['actors']['member'].update(bot_id='member-bot', owner_id='owner-1')
    value['observer'].update(server='local-sqlite', database='owned-local')
    value['auth']['header_env'].update(Authorization='BCS_E2E_DRIVER_BOT_TOKEN',
                                      Cookie='BCS_E2E_HUMAN_COOKIE')
    value['edge_case_bindings'] = {'network_platform_asset.read': {
        'method': 'GET',
        'path': PLATFORM_ASSET_URL if absolute else PLATFORM_ASSET_PATH,
        'api_face': 'internal-v1', 'auth': 'unauthenticated',
        'response': 'javascript-asset', 'traceparent': 'disabled',
        'asset_name': 'bcsPanel', 'asset_sha256': ASSET_SHA256,
    }}
    if absolute:
        value['entrypoint'].update(kind='gateway', origin='https://teamclawgw.example.com')
        value['identity_binding'] = dict(HUMAN_VIEW)
        value['environment'].update(tier='preprod', expected_bcs_env='pre', id='pre-profile',
                                  lifecycle='remote-attach', deployment='managed',
                                  provider='internal')
        value['observer'].update(kind='alisddbquery', server='observ',
                                  database='agentclawdb', environment_binding_confirmed=True)
    return validate(value)


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


class PlatformAssetTests(unittest.TestCase):
    def test_the_platform_binding_accepts_only_the_bound_asset_contract(self):
        value = platform_profile()
        binding = value['edge_case_bindings']['network_platform_asset.read']
        self.assertEqual(binding['method'], 'GET')
        self.assertEqual(binding['path'], PLATFORM_ASSET_PATH)
        self.assertEqual(binding['api_face'], 'internal-v1')
        self.assertEqual(binding['auth'], 'unauthenticated')
        self.assertEqual(binding['response'], 'javascript-asset')
        self.assertEqual(binding['asset_name'], 'bcsPanel')
        self.assertEqual(binding['asset_sha256'], ASSET_SHA256)
        self.assertEqual(value['entrypoint']['api_face'], 'public-v1')
        self.assertEqual(value['entrypoint']['manifest_face'], 'internal-v1')

    def test_the_platform_binding_rejects_route_and_binding_drift(self):
        mutations = {
            'method': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(method='POST'),
            'face': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(api_face='public-v1'),
            'auth': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(auth='human_cookie'),
            'response': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(response='public-envelope'),
            'manifest-face': lambda v: v['entrypoint'].update(manifest_face='legacy'),
            'asset-name': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(asset_name='otherPanel'),
            'asset-sha': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(asset_sha256='invalid'),
            'traversal': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path='/api/v1/collaboration/assets/bcsPanel/../index.umd.js'),
            'http-url': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path='http://asset.example.com/panel/index.umd.js'),
            'user-info': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path='https://user:pass@asset.example.com/panel/index.umd.js'),
            'query': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path=PLATFORM_ASSET_URL + '?version=1'),
            'fragment': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path=PLATFORM_ASSET_URL + '#bundle'),
            'non-js': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path='https://asset.example.com/panel/index.umd.txt'),
            'relative-non-js': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path=PLATFORM_ASSET_PATH[:-3] + '.txt'),
            'relative-query': lambda v: v['edge_case_bindings']['network_platform_asset.read'].update(
                path=PLATFORM_ASSET_PATH + '?version=1'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
            'missing-cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
        }
        for name, mutate in mutations.items():
            value = platform_profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)

    def test_the_platform_slice_plan_reuses_the_reviewed_requirement_without_credit(self):
        plan = network_platform_asset_case_plan()
        adaptability = network_platform_asset_adaptability()
        self.assertEqual(plan.case_id, NETWORK_PLATFORM_ASSET_CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.platform')
        self.assertEqual(plan.requirement.source_function, '_story_platform_entrypoints')
        self.assertEqual(plan.selected_obligations, ('manifest', 'identity', 'asset'))
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        from bcs_e2e.migration_inventory import migration_plan
        inventory_row = next(row for row in migration_plan()['public_v1_case_slices']
                             if row['case_id'] == NETWORK_PLATFORM_ASSET_CASE_ID)
        self.assertEqual(inventory_row['requirement_id'], 'NETWORK.platform')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def envelope(self, data):
        return json.dumps({'code': 20000, 'message': 'OK',
                           'request_id': 'server-request', 'data': data}).encode()

    def asset_reply(self, body=ASSET_BYTES, code=200,
                    content_type='application/javascript; charset=utf-8'):
        return Reply(body, code=code, headers={'Content-Type': content_type})

    def platform_responses(self, *, env='local', asset_url=PLATFORM_ASSET_PATH,
                          schema_version=1, bundle_name='bcsPanel',
                          user_id='owner-1', provider='test-provider',
                          asset_body=ASSET_BYTES, asset_code=200,
                          content_type='application/javascript; charset=utf-8'):
        manifest = self.envelope({
            'schema_version': schema_version, 'env': env,
            'bundles': [{'name': bundle_name, 'url': asset_url}],
        })
        identity = self.envelope({'user_id': user_id, 'provider': provider})
        return [Reply(manifest), Reply(identity),
                self.asset_reply(asset_body, code=asset_code, content_type=content_type)]

    def run_platform_case(self, responses, profile_value=None):
        requests = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            iterator = iter(responses)
            def open(request, timeout=None):
                requests.append(request)
                return next(iterator)
            factory.return_value.open.side_effect = open
            adapter = RemoteNetworkPlatformAssetAdapter(
                profile_value if profile_value is not None else platform_profile())
            return execute_network_platform_asset_case(adapter), requests

    def test_the_local_case_reads_manifest_identity_and_bound_asset(self):
        execution, requests = self.run_platform_case(self.platform_responses())
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 3)
        self.assertEqual([request.method for request in requests], ['GET', 'GET', 'GET'])
        self.assertEqual(requests[0].full_url,
                         'http://127.0.0.1:21000' + NETWORK_PLATFORM_ASSET_MANIFEST_PATH)
        self.assertEqual(requests[1].full_url,
                         'http://127.0.0.1:21000' + NETWORK_PLATFORM_ASSET_IDENTITY_PATH)
        self.assertEqual(requests[2].full_url,
                         'http://127.0.0.1:21000' + PLATFORM_ASSET_PATH)
        for request in requests[:2]:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn('authorization', headers)
            self.assertIn(COOKIE, headers.values())
        asset_headers = {name.lower(): value for name, value in requests[2].header_items()}
        self.assertNotIn('authorization', asset_headers)
        self.assertNotIn('cookie', asset_headers)
        self.assertEqual(asset_headers['accept'], 'application/javascript')

        snapshot = execution.snapshot()
        self.assertEqual(snapshot['case_id'], NETWORK_PLATFORM_ASSET_CASE_ID)
        self.assertEqual(snapshot['steps'][0]['data'], {
            'schema_version': 1, 'env': 'local', 'panel_bundle_name': 'bcsPanel',
            'panel_asset_url': PLATFORM_ASSET_PATH,
            'panel_asset_size': len(ASSET_BYTES),
            'panel_asset_sha256': ASSET_SHA256, 'user_id': 'owner-1',
        })
        requests_evidence = snapshot['steps'][0]['evidence']['requests']
        self.assertEqual([row['operation'] for row in requests_evidence],
                         ['environment.read', 'identity.read', 'network.platform_asset.read'])
        self.assertEqual([row['credentials_sent'] for row in requests_evidence], [True, True, False])
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn(ASSET_BYTES.decode(), serialized)

    def test_the_gateway_case_uses_the_station_human_view_and_bound_asset(self):
        manifest = self.envelope({
            'schema_version': 1, 'env': 'pre',
            'bundles': [{'name': 'bcsPanel', 'url': PLATFORM_ASSET_URL}],
        })
        actor = self.envelope({
            'bot_id': 'human_owner-1', 'kind': 'human',
            'created_by': 'owner-1', 'env': 'pre',
        })
        candidate_page = self.envelope({'items': [], 'total': 0,
                                         'offset': 0, 'limit': 1})
        execution, requests = self.run_platform_case(
            [Reply(manifest), Reply(actor), Reply(candidate_page),
             self.asset_reply()],
            platform_profile(absolute=True))
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 4)
        self.assertEqual([request.method for request in requests], ['GET'] * 4)
        expected = [
            'https://teamclawgw.example.com' + NETWORK_PLATFORM_ASSET_MANIFEST_PATH,
            'https://teamclawgw.example.com/openapi/v1/collaboration/bots/human_owner-1',
            'https://teamclawgw.example.com/openapi/v1/collaboration/bots/human_owner-1/candidates?offset=0&limit=1',
            PLATFORM_ASSET_URL,
        ]
        self.assertEqual([request.full_url for request in requests], expected)
        for request in requests[:3]:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn('authorization', headers)
            self.assertIn(COOKIE, headers.values())
        asset_headers = {name.lower(): value for name, value in requests[3].header_items()}
        self.assertNotIn('authorization', asset_headers)
        self.assertNotIn('cookie', asset_headers)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data']['env'], 'pre')
        self.assertEqual(snapshot['steps'][0]['data']['panel_asset_url'],
                         PLATFORM_ASSET_URL)
        self.assertEqual(snapshot['steps'][0]['data']['user_id'], 'owner-1')
        self.assertEqual([row['operation'] for row in
                         snapshot['steps'][0]['evidence']['requests']],
                         ['environment.read', 'identity.actor.read',
                          'identity.read', 'network.platform_asset.read'])
        self.assertEqual([row['credentials_sent'] for row in
                         snapshot['steps'][0]['evidence']['requests'][:3]],
                         [True, True, True])

    def test_invalid_platform_results_block_without_partial_credit(self):
        cases = {
            'wrong-env': self.platform_responses(env='staging'),
            'wrong-schema': self.platform_responses(schema_version=2),
            'wrong-bundle': self.platform_responses(bundle_name='otherPanel'),
            'wrong-url': self.platform_responses(asset_url=PLATFORM_ASSET_PATH + '/changed.js'),
            'wrong-identity': self.platform_responses(user_id='different-owner'),
            'wrong-sha': self.platform_responses(asset_body=b'console.log(1);' + b'a' * 2000),
            'wrong-media-type': self.platform_responses(content_type='text/plain'),
            'too-small': self.platform_responses(asset_body=b'console.log(1);' + b'a' * 1000),
            'http-failure': self.platform_responses(asset_code=404),
        }
        for name, responses in cases.items():
            with self.subTest(case=name):
                execution, _ = self.run_platform_case(responses)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')
                self.assertNotEqual(execution.steps[0].error_type, None)

        oversize_profile = platform_profile()
        oversize_profile['policy']['max_response_bytes'] = 1025
        execution, _ = self.run_platform_case(
            self.platform_responses(asset_body=ASSET_BYTES[:1026]), oversize_profile)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_platform_profiles_fail_before_network(self):
        wrong_face = platform_profile()
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        wrong_manifest = platform_profile()
        wrong_manifest['entrypoint']['manifest_face'] = 'legacy'
        missing_binding = platform_profile()
        missing_binding['edge_case_bindings'].pop('network_platform_asset.read')
        wrong_identity = platform_profile(absolute=True)
        wrong_identity['environment']['tier'] = 'local'
        for value in (wrong_face, wrong_manifest, missing_binding, wrong_identity):
            with self.subTest(value=value['entrypoint'].get('api_face')), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteNetworkPlatformAssetAdapter(value)
                factory.return_value.open.assert_not_called()

    def test_transport_rejects_unbound_or_malformed_asset_targets_before_network(self):
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(platform_profile())
            targets = ('', 'http://asset.example.com/panel/index.umd.js',
                       PLATFORM_ASSET_URL + '?version=1',
                       PLATFORM_ASSET_URL + '#bundle',
                       '/openapi/v1/auth/user',
                       '/api/v1/collaboration/assets/bcsPanel/index.umd.js')
            for target in targets:
                with self.subTest(target=target), self.assertRaises(ObservationError):
                    client.read_platform_asset(target)
            factory.return_value.open.assert_not_called()
            self.assertEqual(client.evidence, [])
