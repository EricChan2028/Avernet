"""Public-v1 webhook subscription slice contracts and dual admission."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_adapter import resolve_owned_profile
from bcs_e2e.local_auth import OwnedLocalAuth
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_event_contract import PARENT
from bcs_e2e.local_webhook_subscription_contract import (
    CASE_ID,
    COMMUNITY_ONLY_CLOSURES,
    CORE_REQUIREMENT_IDS,
    execute_webhook_subscription_case,
    FIXTURE_EFFECT,
    webhook_subscription_adaptability,
    webhook_subscription_case_plan,
)
from bcs_e2e.local_fixtures import LocalFixtureError
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.remote_effects import RemoteBootstrapAdmissionError
from bcs_e2e.remote_webhook_subscription_adapter import (
    RemoteWebhookSubscriptionAdapter,
)
from bcs_e2e.route_bindings import (
    EVENT_DELIVERY_PATH,
    EVENT_SUBSCRIPTION_DELIVERIES_PATH,
    EVENT_SUBSCRIPTION_DELETE_PATH,
    EVENT_SUBSCRIPTION_LIST_PATH,
    EVENT_SUBSCRIPTION_PATH,
    EVENT_SUBSCRIPTION_TEST_PATH,
    _EDGE_CASES,
)
from bcs_e2e.run_state import RunStateError, write
from test_write_transport import Reply

COOKIE = 'human-cookie-private-value'
TOKEN = 'driver-bot-token'
PRINCIPAL_KEY_ENV = 'BCS_E2E_PRINCIPAL_SIGNING_KEY_VALUE'
PRINCIPAL_KEY = 'operator-provisioned-gateway-principal-key-material'
PRINCIPAL_HEADER = 'X-avernet-principal'
RECEIVER_ORIGIN = 'http://127.0.0.1:45555'
GROUP_ID = 'grp-webhook-e2e'
SUBSCRIPTION_ID = 'sub-webhook-e2e'
SESSION_ID = 'ses-webhook-e2e'
DELIVERY_ID = 'del-webhook-e2e'
WEBHOOK_BINDINGS = {
    'webhook.subscription.list': ('GET', EVENT_SUBSCRIPTION_LIST_PATH),
    'webhook.subscription.read': ('GET', EVENT_SUBSCRIPTION_PATH),
    'webhook.subscription.test': ('POST', EVENT_SUBSCRIPTION_TEST_PATH),
    'webhook.subscription.disable': ('PATCH', EVENT_SUBSCRIPTION_PATH),
    'webhook.subscription.enable': ('PATCH', EVENT_SUBSCRIPTION_PATH),
    'webhook.subscription.deliveries.list': (
        'GET', EVENT_SUBSCRIPTION_DELIVERIES_PATH),
    'webhook.delivery.read': ('GET', EVENT_DELIVERY_PATH),
    'webhook.subscription.delete': ('DELETE', EVENT_SUBSCRIPTION_DELETE_PATH),
}


def envelope(code: int, message: str, data, request_id: str) -> bytes:
    return json.dumps({'code': code, 'message': message,
                       'request_id': request_id, 'data': data}).encode()


def bindings_value():
    return {operation: {'method': method, 'path': path,
                        'api_face': 'public-v1',
                        'auth': 'gateway_principal',
                        'response': 'public-envelope',
                        'traceparent': 'disabled'}
            for operation, (method, path) in WEBHOOK_BINDINGS.items()}


def gateway_profile(*, principal_key: bool = True):
    value = json.loads((ROOT / 'profiles/local.singlebox.example.json'
                        ).read_text())
    value['environment'].update(id='preprod-webhook', tier='preprod',
                                expected_bcs_env='pre', lifecycle='remote-attach',
                                deployment='managed', provider='internal')
    value['entrypoint'].update(kind='gateway',
                               origin='https://teamclawgw.example.com')
    value['human']['user_id'] = '39464'
    value['actors']['driver'].update(bot_id='driver-bot', owner_id='39464')
    value['actors']['member'].update(bot_id='member-bot', owner_id='39464')
    value['auth'] = {'header_env': {'Authorization': 'BCS_E2E_DRIVER_BOT_TOKEN',
                                    'Cookie': 'BCS_E2E_HUMAN_COOKIE'}}
    if principal_key:
        value['auth']['principal_signing_key_env'] = PRINCIPAL_KEY_ENV
        value['observer'].update(kind='alisddbquery', server='observ',
                                 database='agentclawdb',
                                 environment_binding_confirmed=True)
        value['edge_case_bindings'] = bindings_value()
        return validate(value)
    # The negative case is deliberately left unvalidated: the adapter
    # itself must refuse the admitted profile before any request.
    value['observer'].update(kind='alisddbquery', server='observ',
                             database='agentclawdb',
                             environment_binding_confirmed=True)
    value['edge_case_bindings'] = bindings_value()
    return value


class LocalWebhookTests(unittest.TestCase):
    """Owned-local Singlebox composition root."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Mock(spec=OwnedBcsProcess)
        inputs = prepare_local_bcs_inputs(Path(tmp.name) / 'runtime',
                                          run_id='webhook-test', port=45000)
        self.runtime.inputs, self.runtime.root = inputs, inputs.root
        self.runtime.run_id = 'webhook-test'
        self.runtime.state = 'RUNNING'
        self.runtime.ready = True
        self.runtime.child = Mock()
        self.runtime.child.poll.return_value = None
        self.runtime._owns_listener.return_value = True
        self.roles = {role: {'bot_id': 'bot-' + role, 'owner_id': inputs.human_id,
                             'dedicated': True}
                      for role in ('driver', 'member')}
        write(inputs.root / 'local-fixtures.json',
              {'schema_version': 1, 'roles': self.roles})
        check = patch('bcs_e2e.local_fixtures.OwnedLocalFixtures.check',
                      return_value={'roles': self.roles})
        check.start()
        self.addCleanup(check.stop)
        self.profile = resolve_owned_profile(self.runtime)
        self.profile['edge_case_bindings'] = bindings_value()
        self.auth = OwnedLocalAuth(self.runtime, self.profile)
        self.runtime_key = inputs.environment[
            'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE']

    def local_adapter(self):
        return RemoteWebhookSubscriptionAdapter(
            self.profile, local_auth=self.auth,
            receiver_origin=RECEIVER_ORIGIN)


class WebhookContractTests(LocalWebhookTests):
    def test_case_guard_accepts_the_contract_and_adapter(self):
        guard = ROOT.parents[3] / 'scripts/ci/e2e_runner_case_guard.py'
        files = (ROOT / 'bcs_e2e/local_webhook_subscription_contract.py',
                 ROOT / 'bcs_e2e/remote_webhook_subscription_adapter.py')
        result = subprocess.run([sys.executable, str(guard),
                                 *[str(path) for path in files]],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_slice_freezes_the_reviewed_subscription_core(self):
        plan = webhook_subscription_case_plan()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, PARENT)
        self.assertEqual(plan.requirement_ids, CORE_REQUIREMENT_IDS)
        self.assertEqual(len(plan.requirements), 12)
        self.assertEqual(plan.adapter_methods, (
            'create_subscribed_group', 'list_group_subscriptions',
            'read_event_subscription', 'test_event_subscription',
            'disable_event_subscription', 'enable_event_subscription',
            'create_webhook_session', 'list_subscription_deliveries',
            'read_event_delivery', 'delete_event_subscription',
            'delete_webhook_session', 'delete_subscribed_group'))
        # Every reviewed obligation maps to one distinct adapter method
        # in the frozen execution order.
        self.assertEqual(len(set(plan.adapters.values())), 12)
        self.assertEqual(plan.local_executor_status, 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(plan.remote_validation, 'NOT_RUN')
        self.assertFalse(plan.grants_release_credit)
        for row in plan.requirements:
            self.assertEqual(row.source_actor, 'HUMAN_PRINCIPAL')
            self.assertTrue(row.source_path.startswith('${'))

    def test_the_adaptability_reports_the_community_only_closures(self):
        adaptability = webhook_subscription_adaptability()
        self.assertEqual(adaptability['case_id'], CASE_ID)
        self.assertEqual(adaptability['legacy_id'], PARENT)
        self.assertEqual(adaptability['requirement_ids'],
                         list(CORE_REQUIREMENT_IDS))
        self.assertEqual(adaptability['community_only_closures'],
                         list(COMMUNITY_ONLY_CLOSURES))
        self.assertEqual(len(COMMUNITY_ONLY_CLOSURES), 8)
        self.assertEqual(adaptability['environments'],
                         ['owned-local-full-story', 'singlebox-public-slice',
                          'preprod-public-slice'])
        self.assertEqual(adaptability['remote_validation'], 'NOT_RUN')
        self.assertFalse(adaptability['grants_release_credit'])
        self.assertIn('owned webhook receiver', adaptability['blocker'])
        self.assertIn('principal signing key', adaptability['blocker'])
        inventory_rows = migration_plan()['public_v1_case_slices']
        row = next(item for item in inventory_rows
                   if item['case_id'] == CASE_ID)
        self.assertEqual(row['legacy_id'], PARENT)
        self.assertEqual(row['requirement_ids'], list(CORE_REQUIREMENT_IDS))
        self.assertFalse(row['grants_release_credit'])

    def test_the_documented_closures_have_no_public_v1_route(self):
        closures = set(COMMUNITY_ONLY_CLOSURES)
        frozen_map = json.loads((ROOT / 'webhook-migration-contract.json'
                                 ).read_text())
        mapped = {row['id'] for row in frozen_map['requirements']}
        self.assertTrue(closures <= mapped)
        self.assertEqual(len(CORE_REQUIREMENT_IDS) + len(closures), 20)
        # The webhook edge routes are declared only through the opt-in
        # cross-environment Case bindings, never as ambient routes.
        for operation in WEBHOOK_BINDINGS:
            self.assertIn(operation, _EDGE_CASES)


class WebhookAdmissionTests(LocalWebhookTests):
    def test_local_execution_requires_the_owned_live_signing_capability(self):
        with self.assertRaises(RunStateError):
            RemoteWebhookSubscriptionAdapter(
                self.profile, receiver_origin=RECEIVER_ORIGIN)

    def test_local_execution_refuses_shared_fixture_pools(self):
        value = json.loads(json.dumps(self.profile))
        value['policy']['exclusive_use_confirmed'] = False
        with self.assertRaises(RunStateError):
            RemoteWebhookSubscriptionAdapter(
                value, local_auth=self.auth,
                receiver_origin=RECEIVER_ORIGIN)

    def test_local_execution_refuses_off_loopback_receivers(self):
        with self.assertRaises(ConfigError):
            RemoteWebhookSubscriptionAdapter(
                self.profile, local_auth=self.auth,
                receiver_origin='https://receiver.example.com')

    def test_missing_webhook_bindings_fail_in_the_adapter_before_network(self):
        for operation in WEBHOOK_BINDINGS:
            value = json.loads(json.dumps(self.profile))
            value['edge_case_bindings'].pop(operation)
            with self.subTest(missing=operation), \
                    self.assertRaises(RunStateError):
                RemoteWebhookSubscriptionAdapter(
                    value, local_auth=self.auth,
                    receiver_origin=RECEIVER_ORIGIN)

    def test_gateway_without_a_principal_signing_key_is_not_admitted(self):
        value = gateway_profile(principal_key=False)
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}):
            with self.assertRaises(RunStateError):
                RemoteWebhookSubscriptionAdapter(
                    value, receiver_origin='https://receiver.example.com')

    def test_gateway_admits_with_the_operator_provisioned_key(self):
        value = gateway_profile()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE,
                                     PRINCIPAL_KEY_ENV: PRINCIPAL_KEY}):
            adapter = RemoteWebhookSubscriptionAdapter(
                value, receiver_origin='https://receiver.example.com')
        self.assertIsNotNone(adapter)

    def test_gateway_execution_fails_closed_on_the_group_effect_admission(self):
        value = gateway_profile()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE,
                                     PRINCIPAL_KEY_ENV: PRINCIPAL_KEY}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = AssertionError(
                'no remote request may be issued')
            write_factory.return_value.open.side_effect = AssertionError(
                'no remote request may be issued')
            adapter = RemoteWebhookSubscriptionAdapter(
                value, receiver_origin='https://receiver.example.com')
            execution = adapter.execute()
        self.assertEqual(execution.case_id, CASE_ID)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')
        self.assertEqual(execution.steps[0].error_type,
                         'RemoteBootstrapAdmissionError')
        self.assertNotIsInstance(execution.steps[0].error_type,
                                 RemoteBootstrapAdmissionError)

    def test_gateway_principal_minting_resolves_the_key_at_request_time(self):
        from bcs_e2e.local_auth import minted_principal_headers
        value = gateway_profile()
        with patch.dict(os.environ, {PRINCIPAL_KEY_ENV: PRINCIPAL_KEY}):
            headers = minted_principal_headers(value)
        self.assertEqual(set(headers), {'X-Avernet-Principal'})
        token = headers['X-Avernet-Principal']
        header_b64, claims_b64, signature_b64 = token.split('.')
        claims = json.loads(base64.urlsafe_b64decode(
            claims_b64 + '=' * (-len(claims_b64) % 4)))
        self.assertEqual(claims['iss'], 'gateway')
        self.assertEqual(claims['aud'], 'bcs')
        self.assertEqual(claims['exp'] - claims['iat'], 60)
        self.assertEqual(claims['principals'][0]['type'], 'user')
        self.assertEqual(claims['principals'][0]['subject']['id'], '39464')
        expected = hmac.new(PRINCIPAL_KEY.encode(),
                            (header_b64 + '.' + claims_b64).encode(),
                            hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(
            signature_b64 + '=' * (-len(signature_b64) % 4)), expected)

    def test_unprovisioned_gateway_principal_key_blocks_before_send(self):
        from bcs_e2e.local_auth import minted_principal_headers
        value = gateway_profile()
        environment = dict(os.environ)
        environment.pop(PRINCIPAL_KEY_ENV, None)
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigError):
                minted_principal_headers(value)


class WebhookExecutionTests(LocalWebhookTests):
    def run_local_case(self, replies):
        requests = []
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = replies
            write_factory.return_value.open.side_effect = replies
            adapter = self.local_adapter()
            execution = adapter.execute()
        return adapter, execution, requests

    def test_local_execution_runs_the_twelve_reviewed_obligations(self):
        state = {'seen': []}

        def open(request, timeout=None):
            requests.append(request)
            index = len(requests) - 1
            request_id = request.headers.get('X-Request-ID', 'server-request')
            if index == 0:
                body = json.loads(request.data)
                state['group_name'] = body['name']
                subscription = body['event_subscriptions'][0]
                state['subscription_name'] = subscription['name']
                return Reply(envelope(20100, 'Created', {
                    'group_id': GROUP_ID, 'name': body['name'],
                    'event_subscriptions': [{
                        'subscription_id': SUBSCRIPTION_ID,
                        'name': subscription['name'],
                        'event_filters': subscription['event_filters'],
                    }]}, request_id), code=201,
                    headers={'x-request-id': request_id})
            if index == 1:
                return Reply(envelope(20000, 'OK', {'items': [{
                    'subscription_id': SUBSCRIPTION_ID, 'status': 'active',
                    'revision': 1}]}, request_id),
                    headers={'x-request-id': request_id})
            if index == 2:
                return Reply(envelope(20000, 'OK', {
                    'subscription_id': SUBSCRIPTION_ID,
                    'name': state['subscription_name'], 'status': 'active',
                    'revision': 1,
                    'event_filters': ['group.*', 'session.*', 'message.*'],
                    'scope': {'scope_type': 'group', 'id': GROUP_ID}},
                    request_id), headers={'x-request-id': request_id})
            if index == 3:
                return Reply(envelope(20000, 'OK', {'delivered': True},
                                      request_id),
                             headers={'x-request-id': request_id})
            if index in (4, 5):
                revision, status = (2, 'disabled') if index == 4 else (3, 'active')
                return Reply(envelope(20000, 'OK', {
                    'revision': revision, 'status': status}, request_id),
                    headers={'x-request-id': request_id})
            if index == 6:
                return Reply(envelope(20100, 'Created', {'session_id': SESSION_ID},
                                      request_id), code=201,
                             headers={'x-request-id': request_id})
            if index == 7:
                return Reply(envelope(20000, 'OK', {'items': [{
                    'delivery_id': DELIVERY_ID,
                    'subscription_id': SUBSCRIPTION_ID}]}, request_id),
                    headers={'x-request-id': request_id})
            if index == 8:
                return Reply(envelope(20000, 'OK', {'delivery': {
                    'delivery_id': DELIVERY_ID,
                    'subscription_id': SUBSCRIPTION_ID}}, request_id),
                    headers={'x-request-id': request_id})
            if index == 9:
                return Reply(envelope(20000, 'OK', {
                    'revision': 4, 'status': 'deleted'}, request_id),
                    headers={'x-request-id': request_id})
            return Reply(b'', code=200, headers={'x-request-id': request_id})

        requests: list = []
        open.side_effect = None
        replies = open
        # The adapter must issue exactly the twelve reviewed requests in
        # the frozen order with the frozen methods and statuses.
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), \
             patch('bcs_e2e.transport.build_opener') as factory, \
             patch('bcs_e2e.write_transport.build_opener') as write_factory:
            factory.return_value.open.side_effect = replies
            write_factory.return_value.open.side_effect = replies
            adapter = self.local_adapter()
            execution = adapter.execute()
        methods = [request.get_method() for request in requests]
        self.assertEqual(methods, ['POST', 'GET', 'GET', 'POST', 'PATCH',
                                   'PATCH', 'POST', 'GET', 'GET',
                                   'DELETE', 'DELETE', 'DELETE'])
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 12)
        self.assertEqual(execution.steps[-1].requirement_id, 'WEBHOOK.group-delete')
        # The inline subscription is created with the owned receiver sink.
        create_body = json.loads(requests[0].data)
        subscription = create_body['event_subscriptions'][0]
        self.assertEqual(subscription['sink'], {
            'type': 'webhook', 'url': RECEIVER_ORIGIN + '/event-webhook',
            'request_timeout_ms': 5000})
        self.assertEqual(subscription['event_filters'],
                         ['group.*', 'session.*', 'message.*'])
        self.assertEqual(create_body['participants'], [
            {'actor_id': 'bot-driver', 'role': 'driver'},
            {'actor_id': 'bot-member', 'role': 'consultant'}])
        # Every request authenticates with the runtime-minted principal
        # and never sends a Cookie or a Bot token.
        for request in requests:
            token = request.headers.get(PRINCIPAL_HEADER)
            self.assertTrue(token)
            self.assertIsNone(request.headers.get('Cookie'))
            self.assertIsNone(request.headers.get('Authorization'))
            header_b64, claims_b64, signature_b64 = token.split('.')
            expected = hmac.new(self.runtime_key.encode(),
                                (header_b64 + '.' + claims_b64).encode(),
                                hashlib.sha256).digest()
            self.assertEqual(base64.urlsafe_b64decode(
                signature_b64 + '=' * (-len(signature_b64) % 4)), expected)
        # The optimistic concurrency bodies carry the frozen revisions.
        self.assertEqual(json.loads(requests[4].data),
                         {'revision': 1,
                          'name': state['subscription_name'] + ' paused',
                          'status': 'disabled'})
        self.assertEqual(json.loads(requests[5].data),
                         {'revision': 2,
                          'name': state['subscription_name'],
                          'status': 'active'})
        # The group and session fixtures are finalized in the last two steps.
        self.assertTrue(requests[11].full_url.endswith(
            '/openapi/v1/collaboration/groups/' + GROUP_ID))
        self.assertTrue(requests[10].full_url.endswith(
            '/openapi/v1/collaboration/sessions/' + SESSION_ID))
        self.assertTrue(requests[9].full_url.endswith(
            '/openapi/v1/collaboration/event-subscriptions/'
            + SUBSCRIPTION_ID + '?revision=3'))

    def test_evidence_proves_the_reviewed_identity_for_every_step(self):
        adapter = self.local_adapter()
        evidence = adapter._evidence('WEBHOOK.group-create',
                                     'group.create', 'POST', 201,
                                     {'method': 'POST', 'http_status': 201,
                                      'principal_sent': True})
        self.assertEqual(evidence['transport'], 'public-v1-http')
        self.assertEqual(evidence['identity'], 'AUTHENTICATED_HUMAN_PRINCIPAL')
        self.assertTrue(evidence['principal_sent'])
        self.assertFalse(evidence['credentials_sent'])
        self.assertFalse(evidence['authorization_sent'])
        self.assertEqual(evidence['fixture_effect'],
                         'WEBHOOK_SUBSCRIPTION_GROUP_CREATED')

    def test_transport_evidence_mismatch_blocks_the_step(self):
        adapter = self.local_adapter()
        with self.assertRaises(RunStateError):
            adapter._evidence('WEBHOOK.group-create', 'group.create',
                              'POST', 201,
                              {'method': 'POST', 'http_status': 500,
                               'principal_sent': True})

    def test_failed_lifecycle_transition_blocks_the_case(self):
        state = {}

        def open(request, timeout=None):
            request_id = request.headers.get('X-Request-ID',
                                              'server-request')
            index = open.calls
            open.calls = index + 1
            if index == 0:
                body = json.loads(request.data)
                subscription = body['event_subscriptions'][0]
                state['subscription_name'] = subscription['name']
                return Reply(envelope(20100, 'Created', {
                    'group_id': GROUP_ID, 'name': body['name'],
                    'event_subscriptions': [{
                        'subscription_id': SUBSCRIPTION_ID,
                        'name': subscription['name'],
                        'event_filters': subscription['event_filters']}]},
                    request_id), code=201)
            if index == 1:
                return Reply(envelope(20000, 'OK', {'items': [{
                    'subscription_id': SUBSCRIPTION_ID, 'status': 'active',
                    'revision': 1}]}, request_id))
            if index == 2:
                return Reply(envelope(20000, 'OK', {
                    'subscription_id': SUBSCRIPTION_ID,
                    'name': state['subscription_name'], 'status': 'active',
                    'revision': 1,
                    'event_filters': ['group.*', 'session.*', 'message.*'],
                    'scope': {'scope_type': 'group', 'id': GROUP_ID}},
                    request_id))
            if index == 3:
                return Reply(envelope(20000, 'OK', {'delivered': True},
                                      request_id))
            # The disable transition must land on revision two with the
            # disabled status; anything else is drift outside the slice.
            return Reply(envelope(20000, 'OK', {
                'revision': 2, 'status': 'active'}, request_id))
        open.calls = 0
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), (
             patch('bcs_e2e.transport.build_opener')) as factory, (
             patch('bcs_e2e.write_transport.build_opener')) as write_factory:
            factory.return_value.open.side_effect = open
            write_factory.return_value.open.side_effect = open
            adapter = self.local_adapter()
            execution = adapter.execute()
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 4)
        self.assertEqual(execution.steps[4].status, 'NOT_RUN')
        self.assertEqual(execution.steps[4].error_type, 'RunStateError')
        self.assertEqual(execution.steps[5].status, 'NOT_RUN')

    def test_delivery_listing_without_a_successful_delivery_blocks(self):
        state = {}

        def open(request, timeout=None):
            request_id = request.headers.get('X-Request-ID',
                                              'server-request')
            index = len(state.setdefault('seen', []))
            state['seen'].append(request)
            if index == 0:
                body = json.loads(request.data)
                state['subscription_name'] = (
                    body['event_subscriptions'][0]['name'])
                return Reply(envelope(20100, 'Created', {
                    'group_id': GROUP_ID, 'name': body['name'],
                    'event_subscriptions': [{
                        'subscription_id': SUBSCRIPTION_ID,
                        'name': state['subscription_name'],
                        'event_filters': ['group.*', 'session.*',
                                          'message.*']}]},
                    request_id), code=201)
            if index == 1:
                return Reply(envelope(20000, 'OK', {'items': [{
                    'subscription_id': SUBSCRIPTION_ID, 'status': 'active',
                    'revision': 1}]}, request_id))
            if index == 2:
                return Reply(envelope(20000, 'OK', {
                    'subscription_id': SUBSCRIPTION_ID,
                    'name': state['subscription_name'], 'status': 'active',
                    'revision': 1,
                    'event_filters': ['group.*', 'session.*', 'message.*'],
                    'scope': {'scope_type': 'group', 'id': GROUP_ID}},
                    request_id))
            if index == 3:
                return Reply(envelope(20000, 'OK', {'delivered': True},
                                      request_id))
            if index == 4:
                return Reply(envelope(20000, 'OK', {
                    'revision': 2, 'status': 'disabled'}, request_id))
            if index == 5:
                return Reply(envelope(20000, 'OK', {
                    'revision': 3, 'status': 'active'}, request_id))
            if index == 6:
                return Reply(envelope(20100, 'Created', {
                    'session_id': SESSION_ID}, request_id), code=201)
            # The delivery listing obligation refuses an empty succeeded
            # page: the slice never fabricates a delivery from ambition.
            return Reply(envelope(20000, 'OK', {'items': []},
                                  request_id))
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), (
             patch('bcs_e2e.transport.build_opener')) as factory, (
             patch('bcs_e2e.write_transport.build_opener')) as write_factory:
            factory.return_value.open.side_effect = open
            write_factory.return_value.open.side_effect = open
            adapter = self.local_adapter()
            execution = adapter.execute()
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 7)
        self.assertEqual(execution.steps[7].status, 'NOT_RUN')
        self.assertEqual(execution.steps[7].error_type, 'RunStateError')
        self.assertEqual(execution.steps[8].status, 'NOT_RUN')

    def test_execution_evidence_never_carries_the_minted_principal(self):
        replies = []
        state = {}

        def open(request, timeout=None):
            token = request.headers.get(PRINCIPAL_HEADER)
            self.assertTrue(token)
            replies.append(token)
            request_id = request.headers.get('X-Request-ID',
                                             'server-request')
            index = len(replies) - 1
            if index == 0:
                body = json.loads(request.data)
                state['subscription_name'] = body['event_subscriptions'][0]['name']
                return Reply(envelope(20100, 'Created', {
                    'group_id': GROUP_ID, 'name': body['name'],
                    'event_subscriptions': [{
                        'subscription_id': SUBSCRIPTION_ID,
                        'name': state['subscription_name'],
                        'event_filters': ['group.*', 'session.*', 'message.*']}]},
                    request_id), code=201,
                    headers={'x-request-id': request_id})
            if index == 1:
                return Reply(envelope(20000, 'OK', {'items': [{
                    'subscription_id': SUBSCRIPTION_ID, 'status': 'active',
                    'revision': 1}]}, request_id),
                    headers={'x-request-id': request_id})
            if index == 2:
                return Reply(envelope(20000, 'OK', {
                    'subscription_id': SUBSCRIPTION_ID,
                    'name': state['subscription_name'], 'status': 'active',
                    'revision': 1,
                    'event_filters': ['group.*', 'session.*', 'message.*'],
                    'scope': {'scope_type': 'group', 'id': GROUP_ID}},
                    request_id),
                    headers={'x-request-id': request_id})
            if index == 3:
                return Reply(envelope(20000, 'OK', {'delivered': True},
                                      request_id),
                             headers={'x-request-id': request_id})
            if index in (4, 5):
                revision, status = (2, 'disabled') if index == 4 else (3, 'active')
                return Reply(envelope(20000, 'OK', {
                    'revision': revision, 'status': status}, request_id),
                    headers={'x-request-id': request_id})
            if index == 6:
                return Reply(envelope(20100, 'Created', {'session_id': SESSION_ID},
                                      request_id), code=201,
                             headers={'x-request-id': request_id})
            if index == 7:
                return Reply(envelope(20000, 'OK', {'items': [{
                    'delivery_id': DELIVERY_ID,
                    'subscription_id': SUBSCRIPTION_ID}]}, request_id),
                    headers={'x-request-id': request_id})
            if index == 8:
                return Reply(envelope(20000, 'OK', {'delivery': {
                    'delivery_id': DELIVERY_ID,
                    'subscription_id': SUBSCRIPTION_ID}}, request_id),
                    headers={'x-request-id': request_id})
            if index == 9:
                return Reply(envelope(20000, 'OK', {
                    'revision': 4, 'status': 'deleted'}, request_id),
                    headers={'x-request-id': request_id})
            return Reply(b'', code=200,
                         headers={'x-request-id': request_id})
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), (
             patch('bcs_e2e.transport.build_opener')) as factory, (
             patch('bcs_e2e.write_transport.build_opener')) as write_factory:
            factory.return_value.open.side_effect = open
            write_factory.return_value.open.side_effect = open
            adapter = self.local_adapter()
            execution = adapter.execute()
        self.assertEqual(execution.status, 'PASS')
        dumped = json.dumps([step.evidence for step in execution.steps])
        for token in replies:
            self.assertNotIn(token, dumped)

    def test_gateway_stored_credentials_are_guarded_against_leaks(self):
        value = gateway_profile()
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE,
                                     PRINCIPAL_KEY_ENV: PRINCIPAL_KEY}), (
             patch('bcs_e2e.transport.build_opener')) as factory, (
             patch('bcs_e2e.write_transport.build_opener')) as write_factory:
            factory.return_value.open.side_effect = AssertionError(
                'no request may be issued')
            write_factory.return_value.open.side_effect = AssertionError(
                'no request may be issued')
            adapter = RemoteWebhookSubscriptionAdapter(
                value, receiver_origin='https://receiver.example.com')
            with self.assertRaises(RunStateError):
                adapter._assert_no_credential_leak({'header': COOKIE})
            adapter._assert_no_credential_leak({'header': '[REDACTED]'})

    def test_obligation_runs_at_most_once(self):
        subscription_read = Reply(envelope(
            20000, 'OK', {'subscription_id': SUBSCRIPTION_ID,
                          'name': 'E2E lifecycle observer once',
                          'status': 'active', 'revision': 1,
                          'event_filters': ['group.*', 'session.*', 'message.*'],
                          'scope': {'scope_type': 'group', 'id': GROUP_ID}},
            'server-request'))
        body = json.loads(json.dumps(self.profile))

        def open(request, timeout=None):
            return Reply(subscription_read.body, code=200,
                         headers={'x-request-id': 'server-request'})
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': TOKEN,
                                     'BCS_E2E_HUMAN_COOKIE': COOKIE}), (
             patch('bcs_e2e.transport.build_opener')) as factory, (
             patch('bcs_e2e.write_transport.build_opener')) as write_factory:
            factory.return_value.open.side_effect = open
            write_factory.return_value.open.side_effect = open
            adapter = RemoteWebhookSubscriptionAdapter(
                body, local_auth=self.auth,
                receiver_origin=RECEIVER_ORIGIN)
            adapter._subscription_id = SUBSCRIPTION_ID
            adapter._group_id = GROUP_ID
            adapter._subscription_name = 'E2E lifecycle observer once'
            first = adapter.read_event_subscription()
            first.validate()
            with self.assertRaises(RunStateError):
                adapter.read_event_subscription()
        self.assertEqual(first.data['subscription_id'], SUBSCRIPTION_ID)



if __name__ == '__main__':
    unittest.main()
