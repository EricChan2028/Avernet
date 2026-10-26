"""Public-v1 network Bot read Case adapter contracts."""
from __future__ import annotations

import hashlib
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
from bcs_e2e.adapters import (
    RemoteNetworkActorListAdapter,
    RemoteNetworkActorSearchAdapter,
    RemoteNetworkBotsMineAdapter,
    RemoteNetworkBotsPagedAdapter,
    RemoteNetworkBotsQueryAdapter,
    RemoteNetworkPlatformAssetAdapter,
)
from bcs_e2e.edge_read_case import (
    ADMISSION_PUBLIC_CASE_ID,
    FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
    FRIEND_REQUESTS_PUBLIC_CASE_ID,
)
from bcs_e2e.edge_search_case import EDGE_BOT_SEARCH_CANDIDATES_CASE_ID
from bcs_e2e.local_channel_public_contract import CASE_ID as CHANNEL_BINDINGS_CASE_ID
from bcs_e2e.local_friend_connection_decision_contract import CASE_ID as FRIEND_DECISION_CASE_ID
from bcs_e2e.local_friend_public_accept_contract import CASE_ID as FRIEND_PUBLIC_ACCEPT_CASE_ID
from bcs_e2e.local_network_actor_status_contract import CASE_ID as NETWORK_ACTOR_STATUS_CASE_ID
from bcs_e2e.local_webhook_subscription_contract import (
    CASE_ID as WEBHOOK_SUBSCRIPTION_CASE_ID,
)
from bcs_e2e.local_network_register_contract import (
    CASE_ID as NETWORK_REGISTER_CASE_ID,
)
from bcs_e2e.local_network_contract import (
    NETWORK_ACTOR_LIST_CASE_ID,
    NETWORK_ACTOR_LIST_PUBLIC_PATH,
    NETWORK_ACTOR_SEARCH_CASE_ID,
    NETWORK_ACTOR_SEARCH_PUBLIC_PATH,
    NETWORK_BOTS_MINE_CASE_ID,
    NETWORK_BOTS_MINE_PUBLIC_PATH,
    NETWORK_BOTS_PAGED_CASE_ID,
    NETWORK_BOTS_PAGED_PUBLIC_PATH,
    NETWORK_BOTS_QUERY_CASE_ID,
    NETWORK_PLATFORM_ASSET_ADAPTER_METHOD,
    NETWORK_PLATFORM_ASSET_CASE_ID,
    NETWORK_PLATFORM_ASSET_IDENTITY_PATH,
    NETWORK_PLATFORM_ASSET_MANIFEST_PATH,
    PUBLIC_PATH,
    execute_network_actor_list_case,
    execute_network_actor_search_case,
    execute_network_bots_mine_case,
    execute_network_bots_paged_case,
    execute_network_bots_query_case,
    execute_network_platform_asset_case,
    network_actor_list_adaptability,
    network_actor_list_case_plan,
    network_actor_search_adaptability,
    network_actor_search_case_plan,
    network_bots_mine_adaptability,
    network_bots_mine_case_plan,
    network_bots_paged_adaptability,
    network_bots_paged_case_plan,
    network_platform_asset_adaptability,
    network_platform_asset_case_plan,
    network_bots_query_adaptability,
    network_bots_query_case_plan,
)
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ObservationError, ReadClient

TOKEN = 'driver-bot-private-token'
COOKIE = 'human-cookie-private-value'
PATH = '/openapi/v1/collaboration/bots/query'
MINE_PATH = '/openapi/v1/collaboration/bots/mine?offset=0&limit=20'
ACTOR_PATH = '/openapi/v1/collaboration/bots/driver-bot/candidates?purpose=discovery&offset=0&limit=20'
SEARCH_PATH = '/api/v1/collaboration/bots/driver-bot/candidates/search?q=%E4%BA%A7%E5%93%81&purpose=discovery'
PAGED_PATH = '/openapi/v1/collaboration/bots/human_owner-1/candidates?purpose=discovery&offset=0&limit=5'
PLATFORM_ASSET_PATH = '/api/v1/collaboration/assets/bcsPanel/test-panel.umd.js'
PLATFORM_ASSET_URL = 'https://asset.example.com/bcsPanel/index.umd.js'
ASSET_BYTES = b'(function(){})()' + b'/*' + b'x' * 2048 + b'*/'
ASSET_SHA256 = hashlib.sha256(ASSET_BYTES).hexdigest()


def profile():
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment']['id'] = 'local-singlebox-test'
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id='driver-bot', owner_id='owner-1')
    value['actors']['member'].update(bot_id='member-bot', owner_id='owner-1')
    value['observer'].update(server='local-sqlite', database='owned-local')
    value['auth']['header_env'].update(Authorization='BCS_E2E_DRIVER_BOT_TOKEN',
                                      Cookie='BCS_E2E_HUMAN_COOKIE')
    value['edge_case_bindings'] = {
        'network_bot_query.read': {
            'method': 'POST', 'path': PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
            'target_actor_ids': ['driver-bot', 'member-bot'],
        },
        'network_bot_mine.read': {
            'method': 'GET', 'path': MINE_PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
        },
        'network_actor_list.read': {
            'method': 'GET', 'path': ACTOR_PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
        },
        'network_actor_search.read': {
            'method': 'GET', 'path': SEARCH_PATH, 'api_face': 'internal-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled', 'caller_actor_id': 'driver-bot',
        },
        'network_bots_paged.read': {
            'method': 'GET', 'path': PAGED_PATH, 'api_face': 'public-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled',
        },
    }
    return validate(value)


def platform_profile(*, absolute=False):
    value = profile()
    value['edge_case_bindings']['network_platform_asset.read'] = {
        'method': 'GET', 'path': PLATFORM_ASSET_URL if absolute else PLATFORM_ASSET_PATH,
        'api_face': 'internal-v1', 'auth': 'unauthenticated',
        'response': 'javascript-asset', 'traceparent': 'disabled',
        'asset_name': 'bcsPanel', 'asset_sha256': ASSET_SHA256,
    }
    if absolute:
        value['entrypoint'].update(kind='gateway', origin='https://teamclawgw.example.com')
        value['environment'].update(tier='preprod', expected_bcs_env='pre', id='pre-profile',
                                  lifecycle='remote-attach', deployment='managed',
                                  provider='internal')
        value['observer'].update(kind='alisddbquery', server='observ',
                                  database='agentclawdb', environment_binding_confirmed=True)
    return validate(value)


class PublicBotQueryProfileTests(unittest.TestCase):
    def test_case_guard_accepts_the_remote_slice_and_network_case_code(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/adapters.py',
                 ROOT / 'bcs_e2e/remote_network_adapters.py',
                 ROOT / 'bcs_e2e/local_network_contract.py')
        completed = subprocess.run(
            [sys.executable, str(guard), *[str(path) for path in files]],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_the_public_binding_uses_the_two_dedicated_targets_and_cookie_context(self):
        value = profile()
        binding = value['edge_case_bindings']['network_bot_query.read']
        self.assertEqual(binding['method'], 'POST')
        self.assertEqual(binding['path'], PATH)
        self.assertEqual(binding['auth'], 'human_cookie')
        self.assertEqual(binding['target_actor_ids'], ['driver-bot', 'member-bot'])
        self.assertIn('Cookie', value['auth']['header_env'])

    def test_the_binding_rejects_method_target_and_identity_drift(self):
        mutations = {
            'method': lambda v: v['edge_case_bindings']['network_bot_query.read'].update(method='GET'),
            'path': lambda v: v['edge_case_bindings']['network_bot_query.read'].update(path=PATH + '/x'),
            'auth': lambda v: v['edge_case_bindings']['network_bot_query.read'].update(auth='authenticated_bot'),
            'targets': lambda v: v['edge_case_bindings']['network_bot_query.read'].update(target_actor_ids=['member-bot']),
            'order': lambda v: v['edge_case_bindings']['network_bot_query.read'].update(target_actor_ids=['member-bot', 'driver-bot']),
            'extra': lambda v: v['edge_case_bindings']['network_bot_query.read'].update(other={}),
            'empty': lambda v: v.update(edge_case_bindings={}),
            'missing-cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)


class PublicBotQueryCaseTests(unittest.TestCase):
    def test_plan_reuses_the_reviewed_network_requirement_without_credit(self):
        plan = network_bots_query_case_plan()
        adaptability = network_bots_query_adaptability()
        self.assertEqual(plan.case_id, NETWORK_BOTS_QUERY_CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.bots-query')
        self.assertEqual(plan.requirement.source_function, 'test_bots_query')
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        from bcs_e2e.migration_inventory import migration_plan
        inventory_row = next(row for row in migration_plan()['public_v1_case_slices']
                             if row['case_id'] == NETWORK_BOTS_QUERY_CASE_ID)
        self.assertEqual(inventory_row['requirement_id'], 'NETWORK.bots-query')
        self.assertEqual(inventory_row['local_executor_status'], 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def run_case(self, response, profile_value=None):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                records.append(request)
                return Reply(response)
            factory.return_value.open.side_effect = open
            adapter = RemoteNetworkBotsQueryAdapter(
                profile_value if profile_value is not None else profile())
            return execute_network_bots_query_case(adapter), records

    def test_the_case_posts_the_exact_public_body_and_omits_bot_authority(self):
        response = json.dumps({'code': 20000, 'message': 'OK',
                               'request_id': 'server-request',
                               'data': {'items': [
                                   {'bot_id': 'member-bot'},
                                   {'bot_id': 'driver-bot'},
                               ]}}).encode()
        execution, requests = self.run_case(response)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.data.decode(), '{"bot_ids":["driver-bot","member-bot"]}')
        self.assertEqual(request.full_url.split('http://127.0.0.1:21000', 1)[1], PUBLIC_PATH)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertNotIn('authorization', headers)
        self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data']['item_count'], 2)
        self.assertEqual(set(snapshot['steps'][0]['data']['returned_ids']),
                         {'driver-bot', 'member-bot'})
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_missing_or_wrong_items_block_no_partial_credit(self):
        rows = (
            {'items': [{'bot_id': 'driver-bot'}]},
            {'items': [{'bot_id': 'driver-bot'}, {'bot_id': 'driver-bot'}]},
            {'items': [{'bot_id': 'driver-bot'}, {'bot_id': 'different-bot'}]},
            {'items': []},
        )
        for row in rows:
            response = json.dumps({'code': 20000, 'message': 'OK',
                                   'request_id': 'server-request', 'data': row}).encode()
            with self.subTest(row=row):
                execution, requests = self.run_case(response)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_profile_and_operation_fail_before_network(self):
        wrong_face = profile()
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        missing_binding = profile()
        missing_binding.pop('edge_case_bindings')
        values = (wrong_face, missing_binding)
        for value in values:
            with self.subTest(value=value['entrypoint'].get('api_face')), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteNetworkBotsQueryAdapter(value)
                factory.return_value.open.assert_not_called()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for operation in ('', 'network.bots.query', 'network_bot_query.read'):
                with self.subTest(operation=operation), self.assertRaises(ObservationError):
                    client.read_bot_query(operation)
            factory.return_value.open.assert_not_called()


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


if __name__ == '__main__':
    unittest.main()


class PublicOwnedBotsMineTests(unittest.TestCase):
    def test_the_public_binding_is_the_fixed_first_page_and_cookie_context(self):
        value = profile()
        binding = value['edge_case_bindings']['network_bot_mine.read']
        self.assertEqual(binding['method'], 'GET')
        self.assertEqual(binding['path'], MINE_PATH)
        self.assertEqual(binding['auth'], 'human_cookie')
        self.assertEqual(binding['api_face'], 'public-v1')
        self.assertIn('Cookie', value['auth']['header_env'])

    def test_the_mine_binding_rejects_method_path_and_identity_drift(self):
        mutations = {
            'method': lambda v: v['edge_case_bindings']['network_bot_mine.read'].update(method='POST'),
            'path': lambda v: v['edge_case_bindings']['network_bot_mine.read'].update(path=MINE_PATH + '&x=1'),
            'auth': lambda v: v['edge_case_bindings']['network_bot_mine.read'].update(auth='authenticated_bot'),
            'extra': lambda v: v['edge_case_bindings']['network_bot_mine.read'].update(target_actor_ids=['driver-bot']),
            'missing-cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)

    def test_the_mine_plan_reuses_the_reviewed_requirement_without_credit(self):
        plan = network_bots_mine_case_plan()
        adaptability = network_bots_mine_adaptability()
        self.assertEqual(plan.case_id, NETWORK_BOTS_MINE_CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.bots-my')
        self.assertEqual(plan.requirement.source_function, 'test_bots_my')
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        from bcs_e2e.migration_inventory import migration_plan
        inventory_rows = migration_plan()['public_v1_case_slices']
        inventory_row = next(row for row in inventory_rows
                             if row['case_id'] == NETWORK_BOTS_MINE_CASE_ID)
        self.assertEqual(inventory_row['requirement_id'], 'NETWORK.bots-my')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])
        self.assertEqual([row['case_id'] for row in inventory_rows],
                         [NETWORK_BOTS_QUERY_CASE_ID, NETWORK_BOTS_MINE_CASE_ID,
                          NETWORK_ACTOR_LIST_CASE_ID, NETWORK_ACTOR_SEARCH_CASE_ID,
                          NETWORK_BOTS_PAGED_CASE_ID, NETWORK_PLATFORM_ASSET_CASE_ID,
                          NETWORK_ACTOR_STATUS_CASE_ID,
                          NETWORK_REGISTER_CASE_ID,
                          WEBHOOK_SUBSCRIPTION_CASE_ID,
                          CHANNEL_BINDINGS_CASE_ID, 'PUBLIC-STRUCTURED-TEMPLATES-01',
                          'PUBLIC-STRUCTURED-CORE-01',
                          'PUBLIC-SESSION-JWT-WS-01',
                          ADMISSION_PUBLIC_CASE_ID,
                          EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
                          FRIEND_REQUESTS_PUBLIC_CASE_ID,
                          FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
                          FRIEND_DECISION_CASE_ID,
                          FRIEND_PUBLIC_ACCEPT_CASE_ID])

    def run_mine_case(self, response, profile_value=None):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                records.append(request)
                return Reply(response)
            factory.return_value.open.side_effect = open
            adapter = RemoteNetworkBotsMineAdapter(
                profile_value if profile_value is not None else profile())
            return execute_network_bots_mine_case(adapter), records

    def test_the_mine_case_reads_the_exact_page_and_omits_bot_authority(self):
        row = {'items': [{'bot_id': 'driver-bot'}, {'bot_id': 'member-bot'}],
               'total': 2, 'offset': 0, 'limit': 20}
        response = json.dumps({'code': 20000, 'message': 'OK',
                              'request_id': 'server-request', 'data': row}).encode()
        execution, requests = self.run_mine_case(response)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.full_url.split('http://127.0.0.1:21000', 1)[1],
                         NETWORK_BOTS_MINE_PUBLIC_PATH)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertNotIn('authorization', headers)
        self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data'],
                         {'item_count': 2, 'total': 2, 'offset': 0, 'limit': 20})
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_invalid_mine_pages_block_without_partial_credit(self):
        rows = (
            {'items': [], 'total': 0, 'offset': 0, 'limit': 20},
            {'items': [{'bot_id': 'driver-bot'}], 'total': 1, 'offset': 1, 'limit': 20},
            {'items': [{'bot_id': 'driver-bot'}], 'total': 1, 'offset': 0, 'limit': 10},
            {'items': [{'bot_id': ''}], 'total': 1, 'offset': 0, 'limit': 20},
            {'items': [{'bot_id': 'x'} for _ in range(21)], 'total': 21,
             'offset': 0, 'limit': 20},
        )
        for row in rows:
            response = json.dumps({'code': 20000, 'message': 'OK',
                                  'request_id': 'server-request', 'data': row}).encode()
            with self.subTest(row=row):
                execution, _ = self.run_mine_case(response)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_mine_profile_and_operation_fail_before_network(self):
        wrong_face = profile()
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        missing_binding = profile()
        missing_binding['edge_case_bindings'].pop('network_bot_mine.read')
        for value in (wrong_face, missing_binding):
            with self.subTest(value=value['entrypoint'].get('api_face')), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteNetworkBotsMineAdapter(value)
                factory.return_value.open.assert_not_called()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for operation in ('', 'network.bots.mine', 'network_bot_mine.read'):
                with self.subTest(operation=operation), self.assertRaises(ObservationError):
                    client.read(operation)
            factory.return_value.open.assert_not_called()



class PublicActorListTests(unittest.TestCase):
    def test_the_public_binding_uses_the_dedicated_driver_perspective(self):
        value = profile()
        binding = value['edge_case_bindings']['network_actor_list.read']
        self.assertEqual(binding['method'], 'GET')
        self.assertEqual(binding['path'], ACTOR_PATH)
        self.assertEqual(binding['api_face'], 'public-v1')
        self.assertEqual(binding['auth'], 'human_cookie')
        self.assertIn('Cookie', value['auth']['header_env'])

    def test_the_actor_binding_rejects_route_method_and_identity_drift(self):
        mutations = {
            'method': lambda v: v['edge_case_bindings']['network_actor_list.read'].update(method='POST'),
            'route': lambda v: v['edge_case_bindings']['network_actor_list.read'].update(path='/openapi/v1/collaboration/bots/member-bot/candidates?purpose=discovery&offset=0&limit=20'),
            'purpose': lambda v: v['edge_case_bindings']['network_actor_list.read'].update(path='/openapi/v1/collaboration/bots/driver-bot/candidates?purpose=collaboration&offset=0&limit=20'),
            'limit': lambda v: v['edge_case_bindings']['network_actor_list.read'].update(path='/openapi/v1/collaboration/bots/driver-bot/candidates?purpose=discovery&offset=0&limit=10'),
            'extra': lambda v: v['edge_case_bindings']['network_actor_list.read'].update(caller_actor_id='driver-bot'),
            'missing-cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)

    def test_the_actor_plan_reuses_the_reviewed_requirement_without_credit(self):
        plan = network_actor_list_case_plan()
        adaptability = network_actor_list_adaptability()
        self.assertEqual(plan.case_id, NETWORK_ACTOR_LIST_CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.actor-list')
        self.assertEqual(plan.requirement.source_function, 'test_actor_list')
        self.assertEqual(plan.requirement.public_path, NETWORK_ACTOR_LIST_PUBLIC_PATH)
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        from bcs_e2e.migration_inventory import migration_plan
        inventory_rows = migration_plan()['public_v1_case_slices']
        inventory_row = next(row for row in inventory_rows
                             if row['case_id'] == NETWORK_ACTOR_LIST_CASE_ID)
        self.assertEqual(inventory_row['requirement_id'], 'NETWORK.actor-list')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def run_case(self, response, profile_value=None):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}),              patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                records.append(request)
                return Reply(response)
            factory.return_value.open.side_effect = open
            adapter = RemoteNetworkActorListAdapter(
                profile_value if profile_value is not None else profile())
            return execute_network_actor_list_case(adapter), records

    def test_the_actor_case_allows_the_reviewed_empty_candidate_page(self):
        row = {'items': [], 'total': 0, 'offset': 0, 'limit': 20}
        response = json.dumps({'code': 20000, 'message': 'OK',
                               'request_id': 'server-request', 'data': row}).encode()
        execution, requests = self.run_case(response)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.full_url.split('http://127.0.0.1:21000', 1)[1], ACTOR_PATH)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertNotIn('authorization', headers)
        self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data'],
                         {'item_count': 0, 'total': 0, 'offset': 0, 'limit': 20})
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_the_actor_case_does_not_persist_candidate_records_or_credentials(self):
        row = {'items': [{'bot': {'bot_id': 'member-bot'}, 'is_friend': False}],
               'total': 1, 'offset': 0, 'limit': 20}
        response = json.dumps({'code': 20000, 'message': 'OK',
                               'request_id': 'server-request', 'data': row}).encode()
        execution, _ = self.run_case(response)
        self.assertEqual(execution.status, 'PASS')
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data']['item_count'], 1)
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn('member-bot', serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_invalid_actor_pages_block_without_partial_credit(self):
        rows = (
            {'items': 'x', 'total': 0, 'offset': 0, 'limit': 20},
            {'items': [], 'total': 0, 'offset': 1, 'limit': 20},
            {'items': [], 'total': 0, 'offset': 0, 'limit': 10},
            {'items': [{}], 'total': 1, 'offset': 0, 'limit': 20},
            {'items': [{'bot': {'bot_id': 'member-bot'}}], 'total': 1, 'offset': 0, 'limit': 20},
            {'items': [{'bot': {'bot_id': 'member-bot'}, 'is_friend': 'false'}],
             'total': 1, 'offset': 0, 'limit': 20},
        )
        for row in rows:
            response = json.dumps({'code': 20000, 'message': 'OK',
                                   'request_id': 'server-request', 'data': row}).encode()
            with self.subTest(row=row):
                execution, _ = self.run_case(response)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_actor_profile_and_operation_fail_before_network(self):
        wrong_face = profile()
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        missing_binding = profile()
        missing_binding['edge_case_bindings'].pop('network_actor_list.read')
        for value in (wrong_face, missing_binding):
            with self.subTest(value=value['entrypoint'].get('api_face')),                  patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteNetworkActorListAdapter(value)
                factory.return_value.open.assert_not_called()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}),              patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for operation in ('', 'network.actor', 'network_actor_list.read'):
                with self.subTest(operation=operation), self.assertRaises(ObservationError):
                    client.read(operation)
            factory.return_value.open.assert_not_called()


class InternalActorSearchTests(unittest.TestCase):
    def test_the_internal_binding_uses_station_route_and_driver_perspective(self):
        value = profile()
        binding = value['edge_case_bindings']['network_actor_search.read']
        self.assertEqual(binding['method'], 'GET')
        self.assertEqual(binding['path'], SEARCH_PATH)
        self.assertEqual(binding['api_face'], 'internal-v1')
        self.assertEqual(binding['caller_actor_id'], 'driver-bot')
        self.assertEqual(binding['auth'], 'human_cookie')
        self.assertEqual(value['entrypoint']['api_face'], 'public-v1')
        self.assertEqual(value['entrypoint']['manifest_face'], 'internal-v1')

    def test_the_search_binding_rejects_route_face_and_perspective_drift(self):
        mutations = {
            'method': lambda v: v['edge_case_bindings']['network_actor_search.read'].update(method='POST'),
            'route': lambda v: v['edge_case_bindings']['network_actor_search.read'].update(
                path='/openapi/v1/collaboration/bots/driver-bot/candidates'),
            'face': lambda v: v['edge_case_bindings']['network_actor_search.read'].update(api_face='public-v1'),
            'viewpoint': lambda v: v['edge_case_bindings']['network_actor_search.read'].update(caller_actor_id='member-bot'),
            'manifest': lambda v: v['entrypoint'].update(manifest_face='legacy'),
            'missing-cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)

    def test_the_search_plan_reuses_the_reviewed_requirement_without_credit(self):
        plan = network_actor_search_case_plan()
        adaptability = network_actor_search_adaptability()
        self.assertEqual(plan.case_id, NETWORK_ACTOR_SEARCH_CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.actor-search')
        self.assertEqual(plan.requirement.source_function, 'test_actor_search')
        self.assertEqual(plan.requirement.public_path, NETWORK_ACTOR_SEARCH_PUBLIC_PATH)
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        from bcs_e2e.migration_inventory import migration_plan
        inventory_row = next(row for row in migration_plan()['public_v1_case_slices']
                             if row['case_id'] == NETWORK_ACTOR_SEARCH_CASE_ID)
        self.assertEqual(inventory_row['requirement_id'], 'NETWORK.actor-search')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def run_case(self, response, profile_value=None):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                records.append(request)
                return Reply(response)
            factory.return_value.open.side_effect = open
            adapter = RemoteNetworkActorSearchAdapter(
                profile_value if profile_value is not None else profile())
            return execute_network_actor_search_case(adapter), records

    def test_the_search_case_uses_the_exact_internal_route_without_bot_authority(self):
        row = {'items': [{'bot': {'bot_id': 'member-bot'}, 'is_friend': False,
                          'tags': {}, 'short_profile': 'candidate'}],
               'search_mode': 'name_fallback'}
        response = json.dumps({'code': 20000, 'message': 'OK',
                               'request_id': 'server-request', 'data': row}).encode()
        execution, requests = self.run_case(response)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.full_url.split('http://127.0.0.1:21000', 1)[1], SEARCH_PATH)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertNotIn('authorization', headers)
        self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data'],
                         {'item_count': 1, 'search_mode': 'name_fallback'})
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn('member-bot', serialized)
        self.assertNotIn('short_profile', serialized)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_invalid_search_results_block_without_partial_credit(self):
        rows = (
            {'items': [{}], 'search_mode': 'semantic'},
            {'items': [{'bot': {}, 'is_friend': False, 'tags': {}}], 'search_mode': 'semantic'},
            {'items': [{'bot': {'bot_id': 'x'}}], 'search_mode': 'name_fallback'},
            {'items': ['x'], 'search_mode': 'name_fallback'},
            {'items': [], 'search_mode': 'recommendation'},
        )
        for row in rows:
            response = json.dumps({'code': 20000, 'message': 'OK',
                                   'request_id': 'server-request', 'data': row}).encode()
            with self.subTest(row=row):
                execution, _ = self.run_case(response)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_search_profile_and_operation_fail_before_network(self):
        wrong_manifest = profile()
        wrong_manifest['entrypoint']['manifest_face'] = 'legacy'
        missing_binding = profile()
        missing_binding['edge_case_bindings'].pop('network_actor_search.read')
        for value in (wrong_manifest, missing_binding):
            with self.subTest(value=value['entrypoint'].get('manifest_face')), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteNetworkActorSearchAdapter(value)
                factory.return_value.open.assert_not_called()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for operation in ('', 'network.actor.search', 'network_actor_search.read'):
                with self.subTest(operation=operation), self.assertRaises(ObservationError):
                    client.read(operation)
            factory.return_value.open.assert_not_called()


class PublicBotsPagedTests(unittest.TestCase):
    def test_the_paged_binding_uses_the_exact_human_actor_page(self):
        value = profile()
        binding = value['edge_case_bindings']['network_bots_paged.read']
        self.assertEqual(binding['method'], 'GET')
        self.assertEqual(binding['path'], PAGED_PATH)
        self.assertEqual(binding['api_face'], 'public-v1')
        self.assertEqual(binding['auth'], 'human_cookie')
        self.assertIn('Cookie', value['auth']['header_env'])

    def test_the_paged_binding_rejects_route_face_and_identity_drift(self):
        mutations = {
            'method': lambda v: v['edge_case_bindings']['network_bots_paged.read'].update(method='POST'),
            'path': lambda v: v['edge_case_bindings']['network_bots_paged.read'].update(
                path='/openapi/v1/collaboration/bots/mine?offset=0&limit=5'),
            'limit': lambda v: v['edge_case_bindings']['network_bots_paged.read'].update(
                path=PAGED_PATH.replace('limit=5', 'limit=20')),
            'actor': lambda v: v['edge_case_bindings']['network_bots_paged.read'].update(
                path=PAGED_PATH.replace('human_owner-1', 'driver-bot')),
            'face': lambda v: v['edge_case_bindings']['network_bots_paged.read'].update(api_face='internal-v1'),
            'auth': lambda v: v['edge_case_bindings']['network_bots_paged.read'].update(auth='authenticated_bot'),
            'missing-cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)

    def test_the_paged_plan_reuses_the_reviewed_requirement_without_credit(self):
        plan = network_bots_paged_case_plan()
        adaptability = network_bots_paged_adaptability()
        self.assertEqual(plan.case_id, NETWORK_BOTS_PAGED_CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id, 'NETWORK.bots-paged')
        self.assertEqual(plan.requirement.source_function, 'test_bots_paged')
        self.assertEqual(plan.requirement.source_path, '/bots/paged?limit=5&offset=0')
        self.assertEqual(plan.requirement.public_path, NETWORK_BOTS_PAGED_PUBLIC_PATH)
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        from bcs_e2e.migration_inventory import migration_plan
        inventory_row = next(row for row in migration_plan()['public_v1_case_slices']
                             if row['case_id'] == NETWORK_BOTS_PAGED_CASE_ID)
        self.assertEqual(inventory_row['requirement_id'], 'NETWORK.bots-paged')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def run_paged_case(self, response, profile_value=None):
        records = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            def open(request, timeout=None):
                records.append(request)
                return Reply(response)
            factory.return_value.open.side_effect = open
            adapter = RemoteNetworkBotsPagedAdapter(
                profile_value if profile_value is not None else profile())
            return execute_network_bots_paged_case(adapter), records

    def test_the_paged_case_reads_the_exact_page_and_omits_bot_authority(self):
        row = {'items': [{'bot': {'bot_id': 'member-bot'}, 'is_friend': True}],
               'total': 1, 'offset': 0, 'limit': 5}
        response = json.dumps({'code': 20000, 'message': 'OK',
                               'request_id': 'server-request', 'data': row}).encode()
        execution, requests = self.run_paged_case(response)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.full_url.split('http://127.0.0.1:21000', 1)[1], PAGED_PATH)
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertNotIn('authorization', headers)
        self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['data'],
                         {'item_count': 1, 'total': 1, 'offset': 0, 'limit': 5})
        self.assertEqual(snapshot['steps'][0]['evidence']['acting_actor_id'], 'human_owner-1')
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn('member-bot', serialized)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_invalid_paged_pages_block_without_partial_credit(self):
        rows = (
            {'items': [], 'total': 0, 'offset': 0, 'limit': 5},
            {'items': [{'bot': {'bot_id': 'driver-bot'}, 'is_friend': True}],
             'total': 1, 'offset': 1, 'limit': 5},
            {'items': [{'bot': {'bot_id': 'driver-bot'}, 'is_friend': True}],
             'total': 1, 'offset': 0, 'limit': 20},
            {'items': [{'bot': {}, 'is_friend': True}], 'total': 1, 'offset': 0, 'limit': 5},
            {'items': [{'bot': {'bot_id': 'driver-bot'}}], 'total': 1, 'offset': 0, 'limit': 5},
            {'items': [{'bot': {'bot_id': 'x'}, 'is_friend': False} for _ in range(6)],
             'total': 6, 'offset': 0, 'limit': 5},
        )
        for row in rows:
            response = json.dumps({'code': 20000, 'message': 'OK',
                                   'request_id': 'server-request', 'data': row}).encode()
            with self.subTest(row=row):
                execution, requests = self.run_paged_case(response)
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')

    def test_invalid_profile_and_operation_fail_before_network(self):
        wrong_face = profile()
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        missing_binding = profile()
        missing_binding.pop('edge_case_bindings')
        for value in (wrong_face, missing_binding):
            with self.subTest(value=value['entrypoint'].get('api_face')), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteNetworkBotsPagedAdapter(value)
                factory.return_value.open.assert_not_called()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                      'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for operation in ('network.bot_paged.read', 'network_bot_paged.read'):
                with self.subTest(operation=operation), self.assertRaises(ObservationError):
                    client.read(operation)
            factory.return_value.open.assert_not_called()

    def test_the_paged_case_does_not_map_to_the_owned_directory(self):
        self.assertNotEqual(PAGED_PATH, MINE_PATH)
        self.assertNotIn('/bots/mine', PAGED_PATH)


if __name__ == '__main__':
    unittest.main()
