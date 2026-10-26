"""Public-v1 actor-status slice and bounded mutation contracts."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_network_actor_status_contract import (
    CASE_ID,
    execute_network_actor_status_case,
    network_actor_status_adaptability,
    network_actor_status_case_plan,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_network_adapters import RemoteNetworkActorStatusAdapter
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ReadClient
from bcs_e2e.write_transport import WriteClient

TOKEN = 'driver-bot-private-token'
COOKIE = 'human-cookie-private-value'
PATH = '/openapi/v1/collaboration/bots/driver-bot'


def base_profile(*, gateway=False):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment']['id'] = 'preprod-status' if gateway else 'local-singlebox-status'
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id='driver-bot', owner_id='owner-1')
    value['actors']['member'].update(bot_id='member-bot', owner_id='owner-1')
    value['auth']['header_env'] = {
        'Authorization': 'BCS_E2E_DRIVER_BOT_TOKEN',
        'Cookie': 'BCS_E2E_HUMAN_COOKIE',
    }
    value['relationships']['friendship'] = 'unchanged'
    value['edge_case_bindings'] = {}
    for operation, method in (
        ('network_actor_status.read', 'GET'),
        ('network_actor_status.write', 'PATCH'),
    ):
        value['edge_case_bindings'][operation] = {
            'method': method,
            'path': PATH,
            'api_face': 'public-v1',
            'auth': 'human_cookie',
            'response': 'public-envelope',
            'traceparent': 'disabled',
            'target_actor_id': 'driver-bot',
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


def envelope(status, bot_id='driver-bot'):
    return json.dumps({'code': 20000, 'message': 'OK', 'request_id': 'server-request',
                       'data': {'bot_id': bot_id, 'kind': 'bot', 'status': status,
                                'env': 'local'}}).encode()


class ActorStatusTests(unittest.TestCase):
    def run_case(self, reads=None, writes=None, profile_value=None):
        if reads is None:
            reads = (envelope('online'), envelope('hidden'), envelope('online'))
        if writes is None:
            writes = (envelope('hidden'), envelope('online'))
        requests = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as read_factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            read_iterator = iter(Reply(body) for body in reads)
            write_iterator = iter(Reply(body) for body in writes)
            def read_open(request, timeout=None):
                requests.append(request)
                try:
                    return next(read_iterator)
                except StopIteration:
                    raise AssertionError('unexpected read count')
            def write_open(request, timeout=None):
                requests.append(request)
                try:
                    return next(write_iterator)
                except StopIteration:
                    raise AssertionError('unexpected write count')
            read_factory.return_value.open.side_effect = read_open
            write_factory.return_value.open.side_effect = write_open
            adapter = RemoteNetworkActorStatusAdapter(
                profile_value if profile_value is not None else base_profile())
            return execute_network_actor_status_case(adapter), requests

    def test_case_guard_accepts_the_reviewed_mutation_slice(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/local_network_actor_status_contract.py',
                 ROOT / 'bcs_e2e/remote_network_adapters.py')
        result = subprocess.run([sys.executable, str(guard), *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_binding_rejects_route_target_and_identity_drift(self):
        cases = {}
        for operation in ('network_actor_status.read', 'network_actor_status.write'):
            cases[operation + '-method'] = lambda v, op=operation: v['edge_case_bindings'][op].update(
                method='PUT' if op.endswith('.write') else 'POST')
            cases[operation + '-path'] = lambda v, op=operation: v['edge_case_bindings'][op].update(
                path=PATH + '/x')
            cases[operation + '-target'] = lambda v, op=operation: v['edge_case_bindings'][op].update(
                target_actor_id='member-bot')
            cases[operation + '-extra'] = lambda v, op=operation: v['edge_case_bindings'][op].update(
                other={})
        cases['read-face'] = lambda v: v['edge_case_bindings']['network_actor_status.read'].update(
            api_face='internal-v1')
        cases['write-auth'] = lambda v: v['edge_case_bindings']['network_actor_status.write'].update(
            auth='authenticated_bot')
        cases['missing-cookie'] = lambda v: v['auth']['header_env'].pop('Cookie')
        cases['mock'] = lambda v: v['entrypoint'].update(kind='mock', origin='mock://local')
        for name, mutate in cases.items():
            with self.subTest(mutation=name):
                value = base_profile()
                mutate(value)
                with self.assertRaises(ConfigError):
                    validate(value)

    def test_plan_and_inventory_claim_no_release_credit(self):
        plan = network_actor_status_case_plan()
        adaptability = network_actor_status_adaptability()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.actor-status')
        self.assertEqual(plan.requirement.source_function, 'test_actor_put_status')
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        row = next(item for item in migration_plan()['public_v1_case_slices']
                    if item['case_id'] == CASE_ID)
        self.assertEqual(row['requirement_id'], 'NETWORK.actor-status')
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])

    def test_local_case_round_trips_hidden_and_restores_online(self):
        execution, requests = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual([request.method for request in requests],
                         ['GET', 'PATCH', 'GET', 'PATCH', 'GET'])
        self.assertTrue(all(request.full_url.endswith(PATH) for request in requests))
        self.assertEqual(requests[1].data, b'{"status":"hidden"}')
        self.assertEqual(requests[3].data, b'{"status":"online"}')
        for request in requests:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn('authorization', headers)
            self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data'], {
            'target_actor_id': 'driver-bot',
            'before_status': 'online',
            'hidden_status': 'hidden',
            'online_status': 'online',
        })
        evidence = snapshot['steps'][0]['evidence']
        self.assertEqual([row['operation'] for row in evidence['requests']],
                         ['network.actor_status.read', 'network.actor_status.write',
                          'network.actor_status.read', 'network.actor_status.write',
                          'network.actor_status.read'])
        self.assertEqual([row['requested_status'] for row in evidence['requests'][1::2]],
                         ['hidden', 'online'])
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_gateway_profile_uses_the_same_station_contract(self):
        execution, requests = self.run_case(
            reads=(envelope('online'), envelope('hidden'), envelope('online')),
            writes=(envelope('hidden'), envelope('online')),
            profile_value=base_profile(gateway=True))
        self.assertEqual(execution.status, 'PASS')
        self.assertTrue(all(request.full_url.startswith('https://teamclawgw.example.com' + PATH)
                           for request in requests))

    def test_wrong_state_blocks_without_partial_credit(self):
        cases = {
            'starts-hidden': ((envelope('hidden'),), ()),
            'hidden-readback-online': ((envelope('online'), envelope('online')),
                                       (envelope('hidden'),)),
            'wrong-write-ack': ((envelope('online'), envelope('hidden', 'member-bot')),
                               (envelope('online'),)),
            'final-read-hidden': ((envelope('online'), envelope('hidden'), envelope('hidden')),
                                  (envelope('hidden'), envelope('online'))),
            'http-failure': ((envelope('online'),), (Reply(envelope('hidden'), code=403),)),
        }
        for name, (reads, writes) in cases.items():
            with self.subTest(case=name):
                execution, _ = self.run_case(reads=reads, writes=writes)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_profile_and_transport_targets_fail_before_network(self):
        for kind in ('mock', 'bcs-direct'):
            value = base_profile()
            value['entrypoint']['kind'] = kind
            with self.subTest(kind=kind), patch('bcs_e2e.transport.build_opener') as read_factory, \
                 patch('bcs_e2e.write_transport.build_opener') as write_factory:
                with self.assertRaises((ConfigError, RunStateError)):
                    RemoteNetworkActorStatusAdapter(value)
                read_factory.return_value.open.assert_not_called()
                write_factory.return_value.open.assert_not_called()

        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as read_factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            client = ReadClient(base_profile())
            with self.assertRaises(Exception):
                client.read('network.bot_query.read')
            writer = WriteClient(base_profile())
            for target, status in (('member-bot', 'hidden'), ('driver-bot', 'offline')):
                with self.subTest(target=target, status=status), self.assertRaises(Exception):
                    writer.patch_bot_status(target, status)
            read_factory.return_value.open.assert_not_called()
            write_factory.return_value.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
