"""Public-v1 friend-request Case adapter and profile binding contracts."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[3]
sys.path.insert(0, str(ROOT))
from bcs_e2e.edge_read_case import (
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_CONNECTIONS_CASE_ID,
    FRIEND_REQUESTS_PUBLIC_CASE_ID,
    FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
    friend_requests_public_adaptability,
    friend_connections_public_adaptability,
    edge_read_public_aliases,
    execute_edge_read_case,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.adapters import RemoteEdgeReadAdapter
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ObservationError, ReadClient

GUARD = REPO_ROOT / 'scripts/ci/e2e_runner_case_guard.py'
TOKEN = 'driver-bot-private-token'
COOKIE = 'human-cookie-private-value'
PATH = '/openapi/v1/collaboration/friend-connections/requests'
CONNECTIONS_PATH = '/openapi/v1/collaboration/friend-connections'


def profile():
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment']['id'] = 'local-singlebox-test'
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id='bot-driver', owner_id='owner-1')
    value['actors']['member'].update(bot_id='bot-member', owner_id='owner-1')
    value['observer'].update(server='local-sqlite', database='owned-local')
    value['auth']['header_env'].update(Authorization='BCS_E2E_DRIVER_BOT_TOKEN',
                                      Cookie='BCS_E2E_HUMAN_COOKIE')
    enable_friend_requests_binding(value)
    return validate(value)


def connections_profile():
    value = profile()
    value['edge_case_bindings'] = {'friend_connections.read': {
        'method': 'GET', 'path': CONNECTIONS_PATH, 'api_face': 'public-v1',
        'auth': 'authenticated_bot', 'response': 'public-envelope',
        'traceparent': 'disabled', 'caller_actor_id': 'bot-driver',
    }}
    return validate(value)


def preprod_profile():
    value = json.loads((ROOT / 'profiles/preprod.example.json').read_text())
    value['environment'].update(id='preprod-test', expected_bcs_env='pre')
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id='bot-driver', owner_id='owner-1')
    value['actors']['member'].update(bot_id='bot-member', owner_id='owner-1')
    value['observer'].update(server='alisddbquery', database='agentclawdb',
                            environment_binding_confirmed=True)
    value['policy']['exclusive_use_confirmed'] = True
    enable_friend_requests_binding(value)
    return validate(value)


def preprod_connections_profile():
    value = preprod_profile()
    value['edge_case_bindings'] = {'friend_connections.read': {
        'method': 'GET', 'path': CONNECTIONS_PATH, 'api_face': 'public-v1',
        'auth': 'human_cookie', 'response': 'public-envelope',
        'traceparent': 'disabled', 'caller_actor_id': 'bot-driver',
    }}
    return validate(value)


def enable_friend_requests_binding(value, driver='bot-driver'):
    value['auth']['header_env']['Authorization'] = 'BCS_E2E_DRIVER_BOT_TOKEN'
    value['edge_case_bindings'] = {'friend_requests.read': {
        'method': 'GET',
        'path': PATH,
        'api_face': 'public-v1',
        'auth': 'authenticated_bot',
        'response': 'public-envelope',
        'traceparent': 'disabled',
        'caller_actor_id': driver,
    }}
    return value


def envelope(data):
    return {'code': 20000, 'message': 'OK', 'request_id': 'server-request', 'data': data}


def connection_data(item_count=0):
    return {'items': [{'actor': {'actor_type': 'bot', 'id': 'friend-' + str(index)}}
                       for index in range(item_count)],
            'page': 1, 'page_size': 100, 'total': item_count}


def friend_data(item_count=0):
    return {'items': [{'id': 'request-' + str(index)} for index in range(item_count)],
            'page': 0, 'page_size': 20, 'total': item_count}


class Reply(io.BytesIO):
    def __init__(self, body, code=200):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.code = code
        self.headers = {}


def install_reply(factory, value):
    factory.return_value.open.return_value = Reply(value)
    return factory


class RemoteEdgeReadProfileTests(unittest.TestCase):
    def test_profiles_opt_into_the_exact_public_binding_and_bot_authority(self):
        for name, function in (('local.singlebox.example.json', profile),
                                ('preprod.example.json', preprod_profile)):
            raw = json.loads((ROOT / 'profiles' / name).read_text())
            self.assertNotIn('edge_case_bindings', raw)
            with self.subTest(name=name), patch('bcs_e2e.transport.build_opener') as factory:
                result = function()
                binding = result['edge_case_bindings']['friend_requests.read']
                self.assertEqual(binding['method'], 'GET')
                self.assertEqual(binding['path'], PATH)
                self.assertEqual(binding['api_face'], 'public-v1')
                self.assertEqual(binding['auth'], 'authenticated_bot')
                self.assertEqual(binding['response'], 'public-envelope')
                self.assertEqual(binding['caller_actor_id'], result['actors']['driver']['bot_id'])
                self.assertIn('Authorization', result['auth']['header_env'])
                factory.return_value.open.assert_not_called()

    def test_binding_rejects_invented_paths_fields_callers_and_cookie_fallback(self):
        mutations = {
            'extra case': lambda p: p['edge_case_bindings'].update(other={}),
            'empty cases': lambda p: p.update(edge_case_bindings={}),
            'method': lambda p: p['edge_case_bindings']['friend_requests.read'].update(method='POST'),
            'path': lambda p: p['edge_case_bindings']['friend_requests.read'].update(path=PATH + '/x'),
            'api_face': lambda p: p['edge_case_bindings']['friend_requests.read'].update(api_face='internal-v1'),
            'auth': lambda p: p['edge_case_bindings']['friend_requests.read'].update(auth='human_cookie'),
            'response': lambda p: p['edge_case_bindings']['friend_requests.read'].update(response='raw'),
            'traceparent': lambda p: p['edge_case_bindings']['friend_requests.read'].update(traceparent='always'),
            'caller': lambda p: p['edge_case_bindings']['friend_requests.read'].update(caller_actor_id='bot-member'),
            'cookie only': lambda p: p['auth']['header_env'].pop('Authorization'),
            'mock': lambda p: p['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)


class RemoteEdgeReadAdapterTests(unittest.TestCase):
    def run_case(self, replies, profile_value=None, case_id=FRIEND_REQUESTS_CASE_ID):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                assert request.method == 'GET'
                records.append(request)
                return Reply(replies[len(records) - 1])
            factory.return_value.open.side_effect = open
            adapter = RemoteEdgeReadAdapter(profile_value if profile_value is not None else profile(), case_id)
            execution = execute_edge_read_case(case_id, adapter)
        return execution, records

    def test_case_executes_all_three_exact_public_paths_without_secret_evidence(self):
        replies = [envelope(friend_data(0)), envelope(friend_data(1)),
                   envelope(friend_data(2))]
        execution, requests = self.run_case(replies)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 3)
        self.assertEqual(execution.total_count, 3)
        self.assertEqual([request.full_url.split('http://127.0.0.1:21000', 1)[1]
                          for request in requests],
                         [PATH + '?direction=' + direction
                          for direction in ('received', 'sent', 'all')])
        self.assertEqual(execution.snapshot()['steps'][1]['data']['item_count'], 1)
        serialized = json.dumps(execution.snapshot(), sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn('Authorization', serialized)

    def test_invalid_profile_or_endpoint_is_refused_before_network(self):
        values = []
        base = profile()
        wrong_endpoint = copy.deepcopy(base)
        wrong_endpoint['entrypoint']['kind'] = 'gateway'
        values.append(wrong_endpoint)
        wrong_face = copy.deepcopy(base)
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        values.append(wrong_face)
        wrong_binding = copy.deepcopy(base)
        wrong_binding['edge_case_bindings'] = {}
        values.append(wrong_binding)
        wrong_caller = copy.deepcopy(base)
        wrong_caller['actors']['driver']['bot_id'] = 'another-bot'
        values.append(wrong_caller)
        for value in values:
            with self.subTest(value=value.get('entrypoint')), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteEdgeReadAdapter(value)
                factory.return_value.open.assert_not_called()

    def test_transport_accepts_only_three_directions_and_no_injected_query(self):
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for direction in ('', 'received&x=1', 'all ', 'unexpected', 'sent/../../admin'):
                with self.subTest(direction=direction), \
                     self.assertRaises(ObservationError):
                    client.read('edge.friend_requests.read', direction=direction)
            factory.return_value.open.assert_not_called()

    def test_friend_connections_repeat_reads_the_exact_public_page(self):
        responses = [envelope(connection_data(1)), envelope(connection_data(1))]
        execution, requests = self.run_case(responses, connections_profile(), FRIEND_CONNECTIONS_CASE_ID)
        expected = (CONNECTIONS_PATH
                    + '?actor_type=bot&actor_id=bot-driver&page=1&page_size=100')
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual([request.full_url.split('http://127.0.0.1:21000', 1)[1]
                          for request in requests], [expected] * 2)
        data = execution.snapshot()['steps'][0]['data']
        self.assertEqual(data, {'item_count': 1, 'page': 1, 'page_size': 100, 'total': 1})
        self.assertNotIn(TOKEN, json.dumps(execution.snapshot(), sort_keys=True))

    def test_friend_connections_rejects_page_shape_drift(self):
        for body in (
            envelope({'items': [], 'page': 0, 'page_size': 100, 'total': 0}),
            envelope({'items': [], 'page': 1, 'page_size': 20, 'total': 0}),
            envelope({'items': [], 'page': '1', 'page_size': 100, 'total': 0}),
            envelope({'items': [], 'page': 1, 'page_size': 100, 'total': -1}),
        ):
            with self.subTest(body=body), \
                 patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                           'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                factory.return_value.open.return_value = Reply(body)
                adapter = RemoteEdgeReadAdapter(connections_profile(), FRIEND_CONNECTIONS_CASE_ID)
                execution = execute_edge_read_case(FRIEND_CONNECTIONS_CASE_ID, adapter)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(len(factory.return_value.open.mock_calls), 1)

    def test_invalid_public_projection_or_body_blocks_the_remaining_case(self):
        invalid = [
            envelope({'items': {}, 'page': 0, 'page_size': 20, 'total': 0}),
            envelope({'items': [], 'page': '0', 'page_size': 20, 'total': 0}),
            envelope({'items': [], 'page': 0, 'page_size': True, 'total': 0}),
            envelope({'items': [], 'page': 0, 'page_size': 20, 'total': -1}),
            {'code': 20000, 'message': 'OK', 'request_id': 'server-request'},
            b'{',
            b'x' * 1025,
        ]
        for reply in invalid:
            with self.subTest(reply=reply), \
                 patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                          'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                factory.return_value.open.return_value = Reply(reply)
                adapter = RemoteEdgeReadAdapter(profile())
                execution = execute_edge_read_case(FRIEND_REQUESTS_CASE_ID, adapter)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual([step.status for step in execution.steps][0], 'NOT_RUN')
                self.assertLess(execution.completed_count, 3)
                self.assertEqual(len(factory.return_value.open.mock_calls), 1)

    def test_http_and_status_failures_are_never_normalized_to_pass(self):
        for reply in (Reply(envelope(friend_data()), code=401),
                      Reply(envelope(friend_data()), code=403),
                      Reply(b'', code=301)):
            with self.subTest(status=reply.code), \
                 patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                          'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                factory.return_value.open.return_value = reply
                adapter = RemoteEdgeReadAdapter(profile())
                execution = execute_edge_read_case(FRIEND_REQUESTS_CASE_ID, adapter)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)

    def test_public_case_ids_preserve_the_reviewed_edge_slices_without_credit(self):
        aliases = edge_read_public_aliases()
        self.assertEqual(aliases[FRIEND_REQUESTS_PUBLIC_CASE_ID], FRIEND_REQUESTS_CASE_ID)
        self.assertEqual(aliases[FRIEND_CONNECTIONS_PUBLIC_CASE_ID], FRIEND_CONNECTIONS_CASE_ID)

        requests_plan = friend_requests_public_adaptability()
        connections_plan = friend_connections_public_adaptability()
        self.assertEqual(requests_plan['case_id'], FRIEND_REQUESTS_PUBLIC_CASE_ID)
        self.assertEqual(connections_plan['case_id'], FRIEND_CONNECTIONS_PUBLIC_CASE_ID)
        self.assertEqual(requests_plan['selected_obligations'], ['received', 'sent', 'all'])
        self.assertEqual(connections_plan['selected_obligations'], ['list', 'list-repeat'])
        self.assertEqual(len(requests_plan['public_paths']), 3)
        self.assertEqual(len(connections_plan['public_paths']), 2)
        self.assertFalse(requests_plan['grants_release_credit'])
        self.assertFalse(connections_plan['grants_release_credit'])

    def test_public_case_aliases_execute_through_the_same_adapters(self):
        requests_execution, requests_records = self.run_case(
            [envelope(friend_data(0)), envelope(friend_data(1)), envelope(friend_data(2))],
            profile(), FRIEND_REQUESTS_PUBLIC_CASE_ID)
        connections_execution, connections_records = self.run_case(
            [envelope(connection_data(1)), envelope(connection_data(1))],
            connections_profile(), FRIEND_CONNECTIONS_PUBLIC_CASE_ID)

        self.assertEqual(requests_execution.snapshot()['case_id'], FRIEND_REQUESTS_PUBLIC_CASE_ID)
        self.assertEqual(connections_execution.snapshot()['case_id'], FRIEND_CONNECTIONS_PUBLIC_CASE_ID)
        self.assertEqual(len(requests_records), 3)
        self.assertEqual(len(connections_records), 2)
        self.assertFalse('Authorization' in json.dumps(requests_execution.snapshot(), sort_keys=True))
        self.assertFalse('Authorization' in json.dumps(connections_execution.snapshot(), sort_keys=True))

    def test_preprod_friend_connections_send_cookie_not_bot_authorization(self):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                records.append(request)
                return Reply(envelope(connection_data(0)))
            factory.return_value.open.side_effect = open
            client = ReadClient(preprod_connections_profile())
            observation = client.read('edge.friend_connections.read')
        request = records[0]
        expected = (CONNECTIONS_PATH
                    + '?actor_type=bot&actor_id=bot-driver&page=1&page_size=100')
        self.assertEqual(request.full_url.split('https://gateway.example.com', 1)[1], expected)
        self.assertEqual(request.headers.get('Cookie'), COOKIE)
        self.assertNotIn('Authorization', request.headers)
        evidence = observation.evidence
        self.assertEqual(evidence['credentials_sent'], True)
        self.assertEqual(evidence['authorization_sent'], False)
        self.assertEqual(evidence['identity_kind'], 'human_cookie')
        self.assertNotIn(TOKEN, json.dumps(evidence, sort_keys=True))
        self.assertNotIn(COOKIE, json.dumps(evidence, sort_keys=True))

    def test_preprod_gateway_profiles_admit_the_public_edge_alias_slices(self):
        for case_id, profile_value in ((FRIEND_REQUESTS_PUBLIC_CASE_ID, preprod_profile()),
                                        (FRIEND_CONNECTIONS_PUBLIC_CASE_ID, preprod_connections_profile())):
            with self.subTest(case_id=case_id), \
                 patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                           'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                adapter = RemoteEdgeReadAdapter(profile_value, case_id)
                self.assertEqual(adapter.case_id, case_id)
                self.assertEqual(adapter.client.base, 'https://gateway.example.com')
                self.assertTrue(adapter.binding['caller_actor_id'])
                factory.return_value.open.assert_not_called()

    def test_case_guard_accepts_the_new_adapter_and_inventory_has_no_remote_credit(self):
        result = subprocess.run(
            [sys.executable, str(GUARD),
             str(ROOT / 'bcs_e2e/adapters.py'),
             str(ROOT / 'bcs_e2e/edge_read_case.py')],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = migration_plan()['local_registered_executors']
        row = next(item for item in rows if item['case_id'] == FRIEND_REQUESTS_CASE_ID)
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])


if __name__ == '__main__':
    unittest.main()
