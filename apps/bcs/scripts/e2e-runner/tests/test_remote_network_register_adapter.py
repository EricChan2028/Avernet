"""Public register-onboard core slice and adapter contracts."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import RemoteNetworkRegisterAdapter
from bcs_e2e.local_network_contract import (
    network_source_binding,
)
from bcs_e2e.local_network_register_contract import (
    CASE_ID,
    COMMUNITY_ONLY_CLOSURES,
    FIXTURE_EFFECT,
    PUBLIC_OPERATIONS,
    execute_network_register_case,
    network_register_adaptability,
    network_register_case_plan,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_network_register_adapter import (
    AGENT_NAME_PREFIX,
    RemoteNetworkRegisterAdapter,
)
from bcs_e2e.route_bindings import (
    NETWORK_REGISTER_CREATE_PATH,
    NETWORK_REGISTER_READBACK_PATH,
    NETWORK_REGISTER_TOKEN_PATH,
    _EDGE_CASES,
)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.transport import ObservationError, ReadClient
from bcs_e2e.write_transport import WriteClient, WriteTransportError

COOKIE = 'human-cookie-private-value'
TOKEN = 'driver-bot-private-token'
REGISTER_TOKEN = 'e2e-register-token-private-value'
BOT_TOKEN = 'registered-bot-token-private-value'
HUMAN = 'owner-1'
HUMAN_ACTOR = 'human_' + HUMAN
BOT_UUID = 'story-agent-bot-uuid-1'
DRIVER = 'driver-bot'
MEMBER = 'member-bot'
ORIGIN = 'http://127.0.0.1:21000'

BINDING_ROUTES = (
    ('register.token.read', NETWORK_REGISTER_TOKEN_PATH, 'human_cookie'),
    ('register.bot.create', NETWORK_REGISTER_CREATE_PATH, 'register_token'),
    ('register.bot.readback', NETWORK_REGISTER_READBACK_PATH, 'human_cookie'),
)


def base_profile(*, gateway=False):
    value = json.loads(
        (ROOT / 'profiles/local.singlebox.example.json').read_text())
    value['environment'].update(
        id='preprod-network-register' if gateway
        else 'local-singlebox-network-register')
    value['human']['user_id'] = HUMAN
    value['actors']['driver'].update(bot_id=DRIVER, owner_id=HUMAN)
    value['actors']['member'].update(bot_id=MEMBER, owner_id=HUMAN)
    value['auth']['header_env'].update(Authorization='BCS_E2E_DRIVER_BOT_TOKEN',
                                       Cookie='BCS_E2E_HUMAN_COOKIE')
    bindings = {}
    for operation, path, auth in BINDING_ROUTES:
        bindings[operation] = {
            'method': _EDGE_CASES[operation][0], 'path': path,
            'api_face': 'public-v1', 'auth': auth,
            'response': 'public-envelope', 'traceparent': 'disabled',
        }
    value['edge_case_bindings'] = bindings
    if gateway:
        value['entrypoint'].update(kind='gateway',
                                   origin='https://teamclawgw.example.com')
        value['environment'].update(tier='preprod', expected_bcs_env='pre',
                                 lifecycle='remote-attach', deployment='managed',
                                 provider='internal')
        value['observer'].update(kind='alisddbquery', server='observ',
                                 database='agentclawdb',
                                 environment_binding_confirmed=True)
    return validate(value)


class Reply(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else body.encode())
        self.code = code
        self.headers = headers or {}


def envelope(request, data, *, code=20000, message='OK', http=200):
    request_id = next((value for name, value in request.headers.items()
                       if name.lower() == 'x-request-id'),
                      'register-request')
    return Reply(json.dumps({'code': code, 'message': message,
                             'request_id': request_id, 'data': data}).encode(),
                 code=http, headers={'x-request-id': request_id})


class OpenRecorder:
    """Deterministic reviewed-route responder for the register flow."""

    def __init__(self, *, expires_at=None, created_by=None,
                 owner_actor_id=None, actor_kind='bot',
                 capabilities_name=None, create_code=20100, create_http=201,
                 create_data=None, token=None,
                 expected_create_token=None):
        self.requests = []
        self.expires_at = expires_at
        self.created_by = HUMAN_ACTOR if created_by is None else created_by
        self.owner_actor_id = (HUMAN_ACTOR if owner_actor_id is None
                               else owner_actor_id)
        self.actor_kind = actor_kind
        self.capabilities_name = capabilities_name
        self.create_code = create_code
        self.create_http = create_http
        self.create_data = create_data
        self.token = REGISTER_TOKEN if token is None else token
        self.expected_create_token = (self.token
                                       if expected_create_token is None
                                       else expected_create_token)
        self.registered_name = ''
        self.registered_uuid = ''

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        method = request.method
        parsed = urlsplit(request.full_url)
        path = parsed.path
        query = parse_qs(parsed.query)
        if method == 'GET' and path == NETWORK_REGISTER_TOKEN_PATH:
            expires_at = self.expires_at
            if expires_at is None:
                expires_at = int(time.time() * 1000) + 6 * 60 * 60 * 1000
            return envelope(request, {'token': self.token,
                                      'expires_at': expires_at,
                                      'note': 'Use this token for bot '
                                              'registration within 6 hours'})
        if method == 'POST' and path == NETWORK_REGISTER_CREATE_PATH:
            token = query.get('token', [''])[0]
            bot_name = query.get('bot-name', [''])[0]
            if token != self.expected_create_token:
                raise AssertionError('unexpected register token')
            if (not bot_name.startswith(AGENT_NAME_PREFIX)
                    or len(bot_name) != len(AGENT_NAME_PREFIX) + 16):
                raise AssertionError('unexpected registered Bot name')
            self.registered_name = bot_name
            self.registered_uuid = BOT_UUID
            if self.create_data is None:
                data = {'bot_name': bot_name, 'bot_uuid': BOT_UUID,
                        'bot_token': BOT_TOKEN}
            else:
                data = self.create_data
            return envelope(request, data, code=self.create_code,
                            message='Created', http=self.create_http)
        readback = NETWORK_REGISTER_READBACK_PATH.format(bot_uuid=BOT_UUID)
        if method == 'GET' and path == readback:
            if not self.registered_name:
                raise AssertionError('read-back before registration')
            name = (self.capabilities_name if self.capabilities_name is not None
                    else self.registered_name)
            return envelope(request, {
                'bot_uuid': BOT_UUID,
                'capabilities': {'name': name,
                                 'summary': 'Story-owned support agent'},
                'status': 'online', 'visibility': 'workspace',
                'owner_actor_id': self.owner_actor_id,
                'created_by': self.created_by,
                'actor_kind': self.actor_kind, 'env': 'local',
                'dynamic_status': {}})
        raise AssertionError('unexpected request ' + method + ' ' + path)


class RemoteNetworkRegisterContractTests(unittest.TestCase):
    def test_case_guard_accepts_the_register_slice(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/local_network_register_contract.py',
                 ROOT / 'bcs_e2e/remote_network_register_adapter.py')
        result = subprocess.run([sys.executable, str(guard),
                                 *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_slice_freezes_the_reviewed_register_obligation(self):
        plan = network_register_case_plan()
        binding = network_source_binding()
        row = next(item for item in binding['requirements']
                   if item['id'] == 'NETWORK.register-onboard')
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, 'story_user_prepares_agent_network')
        self.assertEqual(plan.requirement.requirement_id,
                         'NETWORK.register-onboard')
        self.assertEqual(plan.requirement.source_function,
                         '_story_register_and_onboard_owned_agent')
        self.assertEqual(plan.requirement.source_function, row['function'])
        self.assertEqual(plan.adapter_method, 'register_owned_agent')
        self.assertEqual(tuple(plan.requirement.public_operations),
                         PUBLIC_OPERATIONS)
        self.assertEqual(tuple(plan.requirement.community_only_closures),
                         COMMUNITY_ONLY_CLOSURES)
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)

    def test_the_adaptability_reports_the_community_only_closures(self):
        adaptability = network_register_adaptability()
        self.assertEqual(adaptability['case_id'], CASE_ID)
        self.assertEqual(adaptability['legacy_id'],
                         'story_user_prepares_agent_network')
        self.assertEqual(adaptability['requirement_id'],
                         'NETWORK.register-onboard')
        self.assertEqual(adaptability['adapter_methods'],
                         ['register_owned_agent'])
        self.assertEqual(adaptability['community_only_closures'],
                         list(COMMUNITY_ONLY_CLOSURES))
        self.assertEqual(adaptability['environments'],
                         ['owned-local-full-story', 'singlebox-public-slice',
                          'preprod-public-slice'])
        self.assertEqual(adaptability['remote_validation'], 'NOT_RUN')
        self.assertFalse(adaptability['grants_release_credit'])
        self.assertIn('no public-v1 gateway route', adaptability['blocker'])
        inventory_rows = migration_plan()['public_v1_case_slices']
        inventory_row = next(row for row in inventory_rows
                             if row['case_id'] == CASE_ID)
        self.assertEqual(inventory_row['requirement_id'],
                         'NETWORK.register-onboard')
        self.assertEqual(inventory_row['remote_validation'], 'NOT_RUN')
        self.assertFalse(inventory_row['grants_release_credit'])

    def test_bindings_declare_the_exact_reviewed_contract(self):
        for operation, path, auth in BINDING_ROUTES:
            declared = _EDGE_CASES[operation]
            self.assertEqual(declared[0],
                             {'register.token.read': 'GET',
                              'register.bot.create': 'POST',
                              'register.bot.readback': 'GET'}[operation])
            self.assertEqual(declared[1], path)
            self.assertEqual(declared[2], auth)

    def test_binding_drift_fails_before_network(self):
        mutations = {
            'method': lambda value: value['edge_case_bindings'][
                'register.token.read'].update(method='POST'),
            'path': lambda value: value['edge_case_bindings'][
                'register.token.read'].update(
                    path=NETWORK_REGISTER_TOKEN_PATH + '/extra'),
            'auth': lambda value: value['edge_case_bindings'][
                'register.bot.create'].update(auth='human_cookie'),
            'create-path': lambda value: value['edge_case_bindings'][
                'register.bot.create'].update(path='/register'),
            'api-face': lambda value: value['edge_case_bindings'][
                'register.bot.readback'].update(api_face='internal-v1'),
            'response': lambda value: value['edge_case_bindings'][
                'register.token.read'].update(response='admission-json'),
            'traceparent': lambda value: value['edge_case_bindings'][
                'register.bot.create'].update(traceparent='b3'),
            'extra-field': lambda value: value['edge_case_bindings'][
                'register.bot.create'].update(caller_actor_id=DRIVER),
            'unbound-readback-uuid': lambda value: value[
                'edge_case_bindings']['register.bot.readback'].update(
                    path=NETWORK_REGISTER_READBACK_PATH.format(
                        bot_uuid='other-bot')),
            'missing-cookie': lambda value: value['auth']['header_env'].pop(
                'Cookie'),
            'mock-entrypoint': lambda value: value['entrypoint'].update(
                kind='mock', origin='mock://local'),
        }
        for name, mutate in mutations.items():
            value = base_profile()
            mutate(value)
            with self.subTest(mutation=name), self.assertRaises(ConfigError):
                validate(value)

    def test_unadmitted_entrypoints_are_refused(self):
        for mutate, label in (
                (lambda value: value['entrypoint'].update(api_face='internal-v1'),
                 'internal face'),
                (lambda value: value['environment'].update(tier='preprod'),
                 'mismatched tier'),
                (lambda value: value['entrypoint'].update(kind='bcn'),
                 'BCN entrypoint')):
            value = base_profile()
            mutate(value)
            with self.subTest(entrypoint=label), \
                    self.assertRaises(RunStateError):
                RemoteNetworkRegisterAdapter(value)

    def test_missing_bindings_fail_in_the_adapter_before_network(self):
        for operation in PUBLIC_OPERATIONS:
            value = base_profile()
            value['edge_case_bindings'].pop(operation)
            with self.subTest(missing=operation), \
                    self.assertRaises(RunStateError):
                RemoteNetworkRegisterAdapter(value)

    def test_write_transport_admits_only_bounded_register_inputs(self):
        value = base_profile()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}):
            client = WriteClient(value)
        with self.assertRaises(WriteTransportError):
            client.register_bot_create(token=REGISTER_TOKEN, bot_name='x')
        with self.assertRaises(WriteTransportError):
            client.register_bot_create(token='bad token\n', bot_name='xy-valid')
        with self.assertRaises(WriteTransportError):
            client.register_bot_create(token=REGISTER_TOKEN,
                                       bot_name='x' * 65)

    def test_read_transport_refuses_unbound_readback_targets(self):
        value = base_profile()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
                patch('bcs_e2e.transport.build_opener') as factory:
            factory.return_value.open.side_effect = AssertionError(
                'no request may be issued')
            client = ReadClient(value)
            with self.assertRaises(ObservationError):
                client.read('register.bot.readback', '')

    def test_obligation_runs_at_most_once(self):
        recorder = OpenRecorder()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            adapter = RemoteNetworkRegisterAdapter(base_profile())
            first = adapter.register_owned_agent()
            first.validate()
            with self.assertRaises(RunStateError):
                adapter.register_owned_agent()


class RemoteNetworkRegisterFlowTests(unittest.TestCase):
    def run_case(self, recorder=None, profile_value=None):
        recorder = OpenRecorder() if recorder is None else recorder
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            execution = execute_network_register_case(
                RemoteNetworkRegisterAdapter(
                    profile_value if profile_value is not None
                    else base_profile()))
        return execution, recorder

    def test_ordered_flow_executes_the_reviewed_register_core(self):
        execution, recorder = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(execution.total_count, 1)
        self.assertIsNone(execution.error_type)
        self.assertEqual([row.status for row in execution.steps], ['PASS'])
        self.assertEqual([request.method for request in recorder.requests],
                         ['GET', 'POST', 'GET'])
        token_request, create_request, readback_request = recorder.requests
        self.assertEqual(
            token_request.full_url.split(ORIGIN, 1)[1],
            NETWORK_REGISTER_TOKEN_PATH)
        create_path = create_request.full_url.split(ORIGIN, 1)[1]
        self.assertTrue(create_path.startswith(
            NETWORK_REGISTER_CREATE_PATH + '?token='))
        create_query = parse_qs(urlsplit(create_request.full_url).query)
        self.assertEqual(create_query['token'], [REGISTER_TOKEN])
        bot_name = create_query['bot-name'][0]
        self.assertTrue(bot_name.startswith(AGENT_NAME_PREFIX))
        self.assertEqual(
            readback_request.full_url.split(ORIGIN, 1)[1],
            NETWORK_REGISTER_READBACK_PATH.format(bot_uuid=BOT_UUID))
        # The Human Cookie authenticates both reads; the create is
        # deliberately anonymous with the register token as its only
        # credential.
        for request in (token_request, readback_request):
            headers = {name.lower() for name in request.headers}
            self.assertIn('cookie', headers)
            self.assertNotIn('authorization', headers)
        create_headers = {name.lower() for name in create_request.headers}
        self.assertNotIn('cookie', create_headers)
        self.assertNotIn('authorization', create_headers)
        for request in recorder.requests:
            self.assertIn('accept', {name.lower() for name in request.headers})
        self.assertIsNotNone(execution.steps[0].data)
        self.assertEqual(execution.steps[0].data['bot_uuid'], BOT_UUID)
        self.assertEqual(execution.steps[0].data['bot_name'], bot_name)
        self.assertTrue(execution.steps[0].data['owner_readback'])

    def test_gateway_profile_reuses_the_same_route_contract(self):
        recorder = OpenRecorder()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            execution = execute_network_register_case(
                RemoteNetworkRegisterAdapter(base_profile(gateway=True)))
        self.assertEqual(execution.status, 'PASS')
        create_request = recorder.requests[1]
        self.assertEqual(
            create_request.full_url.split('https://teamclawgw.example.com', 1)[1],
            NETWORK_REGISTER_CREATE_PATH + '?token=' + REGISTER_TOKEN
            + '&bot-name=' + recorder.registered_name)

    def test_execution_evidence_exposes_no_credential(self):
        execution, _ = self.run_case()
        self.assertEqual(execution.status, 'PASS')
        snapshot = execution.snapshot()
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn(COOKIE, serialized)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn(REGISTER_TOKEN, serialized)
        self.assertNotIn(BOT_TOKEN, serialized)
        evidence = snapshot['steps'][0]['evidence']
        self.assertEqual(evidence['fixture_effect'], FIXTURE_EFFECT)
        create = evidence['requests'][1]
        self.assertIn('[REDACTED-register-token]', create['path'])
        self.assertNotIn(REGISTER_TOKEN, create['path'])
        self.assertEqual(create['credentials_sent'], False)
        self.assertEqual(create['authorization_sent'], False)
        self.assertEqual([row['identity'] for row in evidence['requests']],
                         ['AUTHENTICATED_HUMAN_COOKIE',
                          'ANONYMOUS_REGISTER_TOKEN',
                          'AUTHENTICATED_HUMAN_COOKIE'])
        for digest in (evidence['register_token_sha256'],
                       evidence['bot_token_sha256']):
            self.assertEqual(len(digest), 64)

    def test_business_failures_block_the_case_without_partial_credit(self):
        failures = (
            {'expires_at': int(time.time() * 1000) - 1},
            {'create_code': 20000, 'create_http': 200},
            {'create_http': 200},
            {'create_data': {'bot_name': 'other-name',
                             'bot_uuid': BOT_UUID, 'bot_token': BOT_TOKEN}},
            {'create_data': {'bot_name': None,
                             'bot_uuid': BOT_UUID, 'bot_token': BOT_TOKEN}},
            {'capabilities_name': 'renamed-out-of-band'},
            {'created_by': 'human_someone-else'},
            {'owner_actor_id': 'human_someone-else'},
            {'actor_kind': 'human'},
            {'expected_create_token': 'some-other-minted-token'},
        )
        for overrides in failures:
            with self.subTest(failure=overrides):
                execution, _ = self.run_case(recorder=OpenRecorder(**overrides))
                self.assertEqual(execution.status, 'BLOCKED')
                self.assertEqual(execution.completed_count, 0)
                self.assertEqual(execution.steps[0].status, 'NOT_RUN')
                self.assertIsNone(execution.steps[0].data)
                self.assertEqual(execution.steps[0].evidence, {})
                self.assertIsNotNone(execution.error_type)
                self.assertFalse(execution.ready_to_execute)

    def test_http_failures_block_the_case_without_partial_credit(self):
        class Unreachable:
            def __init__(self):
                self.requests = []

            def __call__(self, request, timeout=None):
                self.requests.append(request)
                raise OSError('network unreachable')

        recorder = Unreachable()
        with patch.dict('os.environ', {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                       'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = recorder
            write_factory.return_value.open.side_effect = recorder
            execution = execute_network_register_case(
                RemoteNetworkRegisterAdapter(base_profile()))
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')
        self.assertEqual(execution.error_type, 'ObservationError')


if __name__ == '__main__':
    unittest.main()
