"""Cross-environment Bot-search candidate read slice contracts."""
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
REPO_ROOT = ROOT.parents[3]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import RemoteEdgeBotSearchCandidatesAdapter
from bcs_e2e.edge_search_case import (
    EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
    EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS,
    edge_bot_search_candidates_adaptability,
    edge_bot_search_candidates_case_plan,
    execute_edge_bot_search_candidates_case,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ObservationError, ReadClient

GUARD = REPO_ROOT / 'scripts/ci/e2e_runner_case_guard.py'
TOKEN = 'driver-bot-private-token'
COOKIE = 'human-cookie-private-value'
NAME_PATH = '/api/v1/collaboration/bots/driver-bot/candidates/search?q=CEO&purpose=discovery'
EMPTY_PATH = '/api/v1/collaboration/bots/driver-bot/candidates/search?purpose=discovery'


def profile(*, gateway=False):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment']['id'] = 'local-singlebox-test'
    value['human']['user_id'] = 'owner-1'
    value['actors']['driver'].update(bot_id='driver-bot', owner_id='owner-1')
    value['actors']['member'].update(bot_id='member-bot', owner_id='owner-1')
    value['observer'].update(server='local-sqlite', database='owned-local')
    value['auth']['header_env'].update(Authorization='BCS_E2E_DRIVER_BOT_TOKEN',
                                        Cookie='BCS_E2E_HUMAN_COOKIE')
    value['edge_case_bindings'] = {
        'edge_bot_search_name.read': {
            'method': 'GET', 'path': NAME_PATH, 'api_face': 'internal-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled', 'caller_actor_id': 'driver-bot',
        },
        'edge_bot_search_empty.read': {
            'method': 'GET', 'path': EMPTY_PATH, 'api_face': 'internal-v1',
            'auth': 'human_cookie', 'response': 'public-envelope',
            'traceparent': 'disabled', 'caller_actor_id': 'driver-bot',
        },
    }
    if gateway:
        value['entrypoint'].update(kind='gateway', origin='https://teamclawgw.example.com')
        value['environment'].update(tier='preprod', expected_bcs_env='pre', id='pre-profile',
                                  lifecycle='remote-attach', deployment='managed',
                                  provider='internal')
        value['observer'].update(kind='alisddbquery', server='observ',
                                  database='agentclawdb', environment_binding_confirmed=True)
    return validate(value)


def envelope(data):
    return {'code': 20000, 'message': 'OK', 'request_id': 'server-request', 'data': data}


def candidate_row(bot_id='member-bot', *, is_friend=False):
    return {'bot': {'bot_id': bot_id}, 'is_friend': is_friend, 'tags': {}}


def name_response(count=1):
    return envelope({'items': [candidate_row() for _ in range(count)],
                      'search_mode': 'name_fallback'})


def empty_response(count=0):
    return envelope({'items': [candidate_row() for _ in range(count)],
                      'search_mode': 'empty_query'})


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        super().__init__(body)
        self.code = code
        self.headers = headers or {}


class RemoteEdgeBotSearchProfileTests(unittest.TestCase):
    def test_case_guard_accepts_the_new_cross_environment_slice(self):
        files = (ROOT / 'bcs_e2e/adapters.py',
                 ROOT / 'bcs_e2e/remote_edge_search_adapter.py',
                 ROOT / 'bcs_e2e/edge_search_case.py',
                 ROOT / 'bcs_e2e/local_network_contract.py')
        completed = subprocess.run([sys.executable, str(GUARD), *[str(path) for path in files]],
                                    text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_both_bindings_are_the_exact_internal_driver_candidate_routes(self):
        value = profile()
        self.assertEqual(value['edge_case_bindings']['edge_bot_search_name.read']['path'], NAME_PATH)
        self.assertEqual(value['edge_case_bindings']['edge_bot_search_empty.read']['path'], EMPTY_PATH)
        for binding in value['edge_case_bindings'].values():
            self.assertEqual(binding['method'], 'GET')
            self.assertEqual(binding['api_face'], 'internal-v1')
            self.assertEqual(binding['auth'], 'human_cookie')
            self.assertEqual(binding['caller_actor_id'], 'driver-bot')
        self.assertEqual(value['entrypoint']['manifest_face'], 'internal-v1')

    def test_bindings_reject_route_authority_and_identity_drift(self):
        mutations = {
            'name-method': lambda v: v['edge_case_bindings']['edge_bot_search_name.read'].update(method='POST'),
            'empty-method': lambda v: v['edge_case_bindings']['edge_bot_search_empty.read'].update(method='POST'),
            'name-path': lambda v: v['edge_case_bindings']['edge_bot_search_name.read'].update(path=NAME_PATH + '&x=1'),
            'empty-path': lambda v: v['edge_case_bindings']['edge_bot_search_empty.read'].update(path=EMPTY_PATH + '&q='),
            'name-face': lambda v: v['edge_case_bindings']['edge_bot_search_name.read'].update(api_face='public-v1'),
            'name-auth': lambda v: v['edge_case_bindings']['edge_bot_search_name.read'].update(auth='authenticated_bot'),
            'empty-caller': lambda v: v['edge_case_bindings']['edge_bot_search_empty.read'].update(caller_actor_id='member-bot'),
            'manifest': lambda v: v['entrypoint'].update(manifest_face='legacy'),
            'cookie': lambda v: v['auth']['header_env'].pop('Cookie'),
            'mock': lambda v: v['entrypoint'].update(kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)


class RemoteEdgeBotSearchCandidateCaseTests(unittest.TestCase):
    def test_the_slice_plan_preserves_two_source_obligations_without_credit(self):
        plan = edge_bot_search_candidates_case_plan()
        adaptability = edge_bot_search_candidates_adaptability()
        self.assertEqual(plan.case_id, EDGE_BOT_SEARCH_CANDIDATES_CASE_ID)
        self.assertEqual(plan.parent_id, 'test_ep_bot_search')
        self.assertEqual([row.requirement_id for row in plan.requirements],
                         list(EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS))
        self.assertEqual(plan.selected_obligations, ('name-fuzzy', 'empty-query'))
        self.assertEqual(plan.local_executor_status, 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        self.assertFalse(adaptability['grants_release_credit'])
        inventory_row = next(row for row in migration_plan()['public_v1_case_slices']
                             if row['case_id'] == EDGE_BOT_SEARCH_CANDIDATES_CASE_ID)
        environments = inventory_row['environments']
        self.assertEqual(environments[1], 'singlebox-candidate-slice')
        self.assertEqual(environments[2], 'preprod-candidate-slice')
        self.assertEqual(inventory_row['selected_obligations'], ['name-fuzzy', 'empty-query'])
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def run_case(self, replies, profile_value=None):
        requests = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            iterator = iter(replies)
            def open(request, timeout=None):
                requests.append(request)
                body = next(iterator)
                code = 200
                if isinstance(body, tuple):
                    body, code = body
                return Reply(body, code=code)
            factory.return_value.open.side_effect = open
            adapter = RemoteEdgeBotSearchCandidatesAdapter(
                profile_value if profile_value is not None else profile())
            return execute_edge_bot_search_candidates_case(adapter), requests

    def test_local_singlebox_reads_the_two_reviewed_candidate_routes(self):
        execution, requests = self.run_case([name_response(), empty_response()])
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual([request.full_url for request in requests],
                         ['http://127.0.0.1:21000' + NAME_PATH,
                          'http://127.0.0.1:21000' + EMPTY_PATH])
        for request in requests:
            headers = {name.lower(): value for name, value in request.header_items()}
            self.assertNotIn('authorization', headers)
            self.assertIn(COOKIE, headers.values())
        snapshot = execution.snapshot()
        self.assertEqual([row['data'] for row in snapshot['steps']],
                         [{'item_count': 1, 'search_mode': 'name_fallback'},
                          {'item_count': 0, 'search_mode': 'empty_query'}])
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn('member-bot', serialized)

    def test_gateway_preprod_uses_the_same_internal_candidate_slice(self):
        value = profile(gateway=True)
        execution, requests = self.run_case([name_response(), empty_response()], value)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual([request.full_url for request in requests],
                         ['https://teamclawgw.example.com' + NAME_PATH,
                          'https://teamclawgw.example.com' + EMPTY_PATH])
        self.assertEqual(execution.snapshot()['case_id'], EDGE_BOT_SEARCH_CANDIDATES_CASE_ID)

    def test_invalid_first_response_blocks_the_second_read_without_credential_evidence(self):
        bad_first = envelope({'items': [], 'search_mode': 'empty_query'})
        execution, requests = self.run_case([bad_first, empty_response()])
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')
        self.assertEqual(execution.steps[1].status, 'NOT_RUN')
        self.assertEqual(len(requests), 1)
        serialized = json.dumps(execution.snapshot(), sort_keys=True)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(COOKIE, serialized)

    def test_invalid_empty_query_or_item_shape_blocks_without_carrying_candidate_records(self):
        cases = {
            'wrong-empty-mode': [name_response(), envelope({'items': [], 'search_mode': 'name_fallback'})],
            'oversized-name-page': [envelope({'items': [candidate_row(f'bot-{index}') for index in range(21)],
                                               'search_mode': 'name_fallback'}), empty_response()],
            'malformed-candidate': [envelope({'items': [{'bot': {'bot_id': 'member-bot'},
                                                          'is_friend': 'invalid', 'tags': {}}],
                                               'search_mode': 'semantic'}), empty_response()],
            'empty-http-failure': [name_response(), (empty_response(), 401)],
        }
        for name, replies in cases.items():
            with self.subTest(case=name):
                execution, requests = self.run_case(replies)
                self.assertEqual(execution.status, 'BLOCKED')
                last = execution.steps[-1]
                self.assertEqual(last.status, 'NOT_RUN')
                serialized = json.dumps(execution.snapshot(), sort_keys=True)
                self.assertNotIn(TOKEN, serialized)
                self.assertNotIn(COOKIE, serialized)

    def test_invalid_profiles_fail_before_network(self):
        wrong_face = profile()
        wrong_face['entrypoint']['api_face'] = 'internal-v1'
        wrong_manifest = profile()
        wrong_manifest['entrypoint']['manifest_face'] = 'legacy'
        without_name = profile()
        without_name['edge_case_bindings'].pop('edge_bot_search_name.read')
        without_empty = profile()
        without_empty['edge_case_bindings'].pop('edge_bot_search_empty.read')
        for value in (wrong_face, wrong_manifest, without_name, without_empty):
            with self.subTest(value=value['entrypoint']), \
                 patch('bcs_e2e.transport.build_opener') as factory:
                with self.assertRaises(RunStateError):
                    RemoteEdgeBotSearchCandidatesAdapter(value)
                factory.return_value.open.assert_not_called()

    def test_transport_supports_only_the_two_declared_search_reads(self):
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory:
            client = ReadClient(profile())
            for operation in ('', 'edge.bot_search.name', 'edge_bot_search_name.read',
                              'edge.bot_search_empty'):
                with self.subTest(operation=operation), self.assertRaises(ObservationError):
                    client.read(operation)
            factory.return_value.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
