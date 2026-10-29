"""Public-v1 channel-binding slice and quiet-state contracts."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_channel_public_contract import (
    CASE_ID,
    channel_bindings_adaptability,
    channel_bindings_case_plan,
    execute_channel_bindings_case,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_network_adapters import RemoteChannelBindingsAdapter
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ReadClient

COOKIE = 'human-cookie-private-value'
DRIVER = 'driver-bot'
CREATE_PATH = '/openapi/v1/collaboration/channels/bindings'
LIST_PATH = CREATE_PATH
BY_TARGET_PATH = ('/openapi/v1/collaboration/channels/bindings/by-target'
                 '?target_type=bot&target_id=driver-bot'
                 '&channel_type=e2e-uninstalled-channel')


def base_profile(*, gateway=False):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment']['id'] = 'preprod-channel' if gateway else 'local-singlebox-channel'
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id=DRIVER, owner_id='owner-1')
    value['actors']['member'].update(bot_id='member-bot', owner_id='owner-1')
    value['auth']['header_env'] = {
        'Authorization': 'BCS_E2E_DRIVER_BOT_TOKEN',
        'Cookie': 'BCS_E2E_HUMAN_COOKIE',
    }
    value['edge_case_bindings'] = {
        'channel_bindings.create_denied': {
            'method': 'POST', 'path': CREATE_PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled', 'target_actor_id': DRIVER,
        },
        'channel_bindings.list': {
            'method': 'GET', 'path': LIST_PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
        },
        'channel_bindings.by_target': {
            'method': 'GET', 'path': BY_TARGET_PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled', 'target_actor_id': DRIVER,
        },
    }
    if gateway:
        value['environment'].update(tier='preprod', expected_bcs_env='pre',
                                  lifecycle='remote-attach', deployment='managed',
                                  provider='internal')
        value['entrypoint'].update(kind='gateway', origin='https://teamclawgw.example.com')
        value['observer'].update(kind='alisddbquery', server='observ',
                                  database='agentclawdb', environment_binding_confirmed=True)
    return validate(value)


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


def error_envelope(error_code='invalid_channel_params', code=40000):
    return json.dumps({
        'code': code, 'message': "channel provider 'e2e-uninstalled-channel' is not available",
        'request_id': 'server-request',
        'data': {'error_code': error_code},
    }).encode()


def items_envelope(items):
    return json.dumps({'code': 20000, 'message': 'OK', 'request_id': 'server-request',
                       'data': {'items': items}}).encode()


def unrelated_item():
    return {
        'id': 'unrelated-binding', 'channel_type': 'dingtalk',
        'account_ref': 'unrelated-account', 'target': {'bot': {'bot_id': 'member-bot'}},
        'outbound_visibility': 'full_transcript', 'env': 'local', 'status': 'active',
        'created_by': 'owner-1', 'config': {},
    }


def ghost_item(target=DRIVER):
    return {
        'id': 'ghost-binding', 'channel_type': 'e2e-uninstalled-channel',
        'account_ref': 'ghost-account', 'target': {'bot': {'bot_id': target}},
        'outbound_visibility': 'full_transcript', 'env': 'local',
        'status': 'active', 'created_by': None, 'config': {},
    }


class ChannelBindingTests(unittest.TestCase):
    def run_case(self, responses=None, profile_value=None):
        if responses is None:
            responses = (
                Reply(error_envelope(), code=400),
                Reply(items_envelope([unrelated_item()])),
                Reply(items_envelope([])),
                Reply(items_envelope([unrelated_item()])),
            )
        requests = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': 'driver-token',
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            iterator = iter(responses)
            def open(request, timeout=None):
                requests.append(request)
                try:
                    return next(iterator)
                except StopIteration:
                    raise AssertionError('unexpected channel request count')
            factory.return_value.open.side_effect = open
            adapter = RemoteChannelBindingsAdapter(
                profile_value if profile_value is not None else base_profile())
            return execute_channel_bindings_case(adapter), requests

    def test_case_guard_accepts_the_channel_slice(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/local_channel_public_contract.py',
                 ROOT / 'bcs_e2e/remote_network_adapters.py')
        result = subprocess.run([sys.executable, str(guard), *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_binding_rejects_route_target_and_identity_drift(self):
        cases = {
            'create-method': lambda v: v['edge_case_bindings']['channel_bindings.create_denied'].update(method='PUT'),
            'create-path': lambda v: v['edge_case_bindings']['channel_bindings.create_denied'].update(path=CREATE_PATH + '/x'),
            'create-target': lambda v: v['edge_case_bindings']['channel_bindings.create_denied'].update(target_actor_id='member-bot'),
            'by-target-path': lambda v: v['edge_case_bindings']['channel_bindings.by_target'].update(path=BY_TARGET_PATH + '&x=1'),
            'by-target-target': lambda v: v['edge_case_bindings']['channel_bindings.by_target'].update(target_actor_id='member-bot'),
            'list-method': lambda v: v['edge_case_bindings']['channel_bindings.list'].update(method='POST'),
            'face': lambda v: v['edge_case_bindings']['channel_bindings.list'].update(api_face='internal-v1'),
            'auth': lambda v: v['edge_case_bindings']['channel_bindings.list'].update(auth='authenticated_bot'),
            'extra': lambda v: v['edge_case_bindings']['channel_bindings.list'].update(other={}),
            'cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
            'empty': lambda v: v.update(edge_case_bindings={}),
        }
        for name, mutate in cases.items():
            with self.subTest(mutation=name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_plan_and_inventory_claim_no_remote_or_release_credit(self):
        plan = channel_bindings_case_plan()
        adaptability = channel_bindings_adaptability()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_validates_external_channel_setup')
        self.assertEqual([row.requirement_id for row in plan.requirements],
                         ['CHANNEL.bind-denied', 'CHANNEL.list-before',
                          'CHANNEL.target-empty', 'CHANNEL.list-after'])
        self.assertEqual(plan.blocked_requirements,
                         ('CHANNEL.pause-missing', 'CHANNEL.delete-missing'))
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        row = next(item for item in migration_plan()['public_v1_case_slices']
                   if item['case_id'] == CASE_ID)
        self.assertEqual(row['blocked_requirement_ids'],
                         ['CHANNEL.pause-missing', 'CHANNEL.delete-missing'])
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])

    def test_channel_create_is_rejected_and_no_ghost_binding_appears(self):
        execution, requests = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 4)
        self.assertEqual([request.method for request in requests], ['POST', 'GET', 'GET', 'GET'])
        self.assertEqual([request.full_url.split('http://127.0.0.1:21000', 1)[1]
                          for request in requests],
                         [CREATE_PATH, LIST_PATH, BY_TARGET_PATH, LIST_PATH])
        body = json.loads(requests[0].data)
        self.assertEqual(body['channel_type'], 'e2e-uninstalled-channel')
        self.assertEqual(body['target'], {'bot': {'bot_id': DRIVER}})
        self.assertEqual(body['outbound_visibility'], 'full_transcript')
        self.assertEqual(body['config'], {'mode': 'story'})
        self.assertRegex(body['account_ref'], r'^e2e-uninstalled-channel-e2e-channel-[0-9a-f]{32}$')
        for request in requests:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn('authorization', headers)
            self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual([step['status'] for step in snapshot['steps']], ['PASS'] * 4)
        self.assertEqual(snapshot['steps'][0]['data'], {
            'target_actor_id': DRIVER, 'error_code': 'invalid_channel_params'})
        self.assertEqual(snapshot['steps'][2]['data'], {'item_count': 0})
        self.assertEqual(snapshot['steps'][3]['data'],
                         {'item_count': 1, 'ghost_item_count': 0})
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn('human-cookie-private-value', serialized)

    def test_gateway_profile_executes_the_same_station_contract(self):
        execution, requests = self.run_case(profile_value=base_profile(gateway=True))
        self.assertEqual(execution.status, 'PASS')
        self.assertTrue(all(request.full_url.startswith('https://teamclawgw.example.com')
                           for request in requests))

    def test_gateway_profile_accepts_the_station_percent_encoded_bot_id(self):
        profile = base_profile(gateway=True)
        driver = 'driver:bot'
        encoded_path = (
            '/openapi/v1/collaboration/channels/bindings/by-target'
            '?target_type=bot&target_id=driver%3Abot'
            '&channel_type=e2e-uninstalled-channel'
        )
        profile['actors']['driver']['bot_id'] = driver
        profile['edge_case_bindings']['channel_bindings.create_denied'][
            'target_actor_id'] = driver
        profile['edge_case_bindings']['channel_bindings.by_target'][
            'target_actor_id'] = driver
        profile['edge_case_bindings']['channel_bindings.by_target'][
            'path'] = encoded_path
        profile = validate(profile)

        execution, requests = self.run_case(profile_value=profile)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual([request.full_url.split('https://teamclawgw.example.com', 1)[1]
                          for request in requests],
                         [CREATE_PATH, LIST_PATH, encoded_path, LIST_PATH])
        for step in execution.steps:
            self.assertEqual(step.status, 'PASS')

    def test_wrong_rejection_target_or_ghost_visible_blocks_without_partial_credit(self):
        cases = {
            'invalid-request-code': (
                Reply(error_envelope('invalid_request'), code=400),
                Reply(items_envelope([])), Reply(items_envelope([])), Reply(items_envelope([]))),
            'unexpected-create-201': (
                Reply(items_envelope([]), code=201),
                Reply(items_envelope([])), Reply(items_envelope([])), Reply(items_envelope([]))),
            'target-not-empty': (
                Reply(error_envelope(), code=400),
                Reply(items_envelope([])), Reply(items_envelope([ghost_item()])),
                Reply(items_envelope([ghost_item()]))),
            'ghost-after': (
                Reply(error_envelope(), code=400),
                Reply(items_envelope([])), Reply(items_envelope([])),
                Reply(items_envelope([ghost_item()]))),
            'create-http-403': (
                Reply(error_envelope(), code=403),
                Reply(items_envelope([])), Reply(items_envelope([])), Reply(items_envelope([]))),
        }
        for name, responses in cases.items():
            with self.subTest(case=name):
                execution, _ = self.run_case(responses=responses)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertTrue(all(step.status == 'NOT_RUN' for step in execution.steps))

    def test_invalid_profile_and_transport_targets_fail_before_network(self):
        for kind in ('mock', 'bcs-direct'):
            value = base_profile()
            value['entrypoint']['kind'] = kind
            with self.subTest(kind=kind), patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises((ConfigError, RunStateError)):
                    RemoteChannelBindingsAdapter(value)
                factory.return_value.open.assert_not_called()

        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': 'driver-token',
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(base_profile())
            for operation in ('channel.bindings.create', 'channel.bindings.global'):
                with self.subTest(operation=operation), self.assertRaises(Exception):
                    client.read(operation)
            factory.return_value.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
