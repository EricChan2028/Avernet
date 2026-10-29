"""Dual-environment adapter for the reviewed Event Webhook subscription core.

The adapter executes the reviewed subscription lifecycle core of the
Event Webhook Story from one admitted public-v1 profile. The Human
principal creates a Group with an inline webhook subscription, proves
the exact subscription through the listing and read routes, exercises
the synchronous test and the optimistic-concurrency lifecycle, creates
and removes a Session, and lists and reads the successful delivery
history before finalizing every fixture. The webhook sink always points
at the exact receiver origin supplied by the composition root; on the
owned-local Singlebox runtime that is the owned event receiver, and on
the gateway face it is an operator-provisioned public receiver. The
owned-local runtime mints the principal from its live signing capability
and a gateway profile mints from the operator-provisioned deployment
signing key; neither credential ever leaves the transport layer or the
evidence.
"""
from __future__ import annotations

import hashlib
import uuid

from .case_adapters import AdapterCall
from .local_webhook_subscription_contract import (
    COMMUNITY_ONLY_CLOSURES,
    execute_webhook_subscription_case,
    FIXTURE_EFFECT,
    FROZEN_OPERATIONS,
    IDENTITY,
    TRANSPORT,
    webhook_subscription_case_plan,
)
from .profile import ConfigError, origin, parsed_origin
from .remote_effects import require_group_effect_admission
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

GROUP_NAME_PREFIX = 'Event Webhook E2E '
SUBSCRIPTION_NAME_PREFIX = 'E2E lifecycle observer '
NAME_SUFFIX_HEX = 12
SUBSCRIPTION_FILTERS = ('group.*', 'session.*', 'message.*')
GROUP_BODY_CONTEXT = 'Observe collaboration lifecycle events'
SESSION_TITLE = 'Event Webhook E2E session'
SESSION_INPUT_SOURCE = 'e2e'
SINK_TIMEOUT_MS = 5000
EFFECTIVE_REVISION = {
    'webhook.subscription.disable': 2,
    'webhook.subscription.enable': 3,
    'webhook.subscription.delete': 4,
}
_STEP_FIXTURE_EFFECT = {
    'WEBHOOK.group-create': 'WEBHOOK_SUBSCRIPTION_GROUP_CREATED',
    'WEBHOOK.session-create': 'WEBHOOK_WEBHOOK_SESSION_CREATED',
    'WEBHOOK.subscription-delete': 'WEBHOOK_SUBSCRIPTION_TOMBSTONED',
    'WEBHOOK.session-delete': 'WEBHOOK_WEBHOOK_SESSION_FINALIZED',
    'WEBHOOK.group-delete': FIXTURE_EFFECT,
}
WEBHOOK_EDGE_OPERATIONS = (
    'webhook.subscription.list',
    'webhook.subscription.read',
    'webhook.subscription.test',
    'webhook.subscription.disable',
    'webhook.subscription.enable',
    'webhook.subscription.deliveries.list',
    'webhook.delivery.read',
    'webhook.subscription.delete',
)


class RemoteWebhookSubscriptionAdapter:
    """Execute the reviewed webhook subscription lifecycle core."""

    def __init__(self, profile: dict, *, local_auth=None,
                 receiver_origin: str):
        if not isinstance(profile, dict):
            raise RunStateError('webhook subscription adapter requires an admitted profile')
        entrypoint = profile.get('entrypoint', {})
        environment = profile.get('environment', {})
        valid_entrypoint = (
            entrypoint.get('kind') == 'singlebox' and environment.get('tier') == 'local'
            or entrypoint.get('kind') == 'gateway' and environment.get('tier') == 'preprod'
        )
        if not valid_entrypoint or entrypoint.get('api_face') != 'public-v1':
            raise RunStateError(
                'webhook subscription adapter requires an admitted public-v1 '
                'Singlebox or gateway profile')
        self.plan = webhook_subscription_case_plan()
        self.bindings: dict[str, dict] = {}
        try:
            for operation in WEBHOOK_EDGE_OPERATIONS:
                self.bindings[operation] = require_edge_case_binding(profile, operation)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.profile = profile
        self.local_auth = local_auth
        self.local_execution = entrypoint.get('kind') == 'singlebox'
        if self.local_execution:
            if local_auth is None:
                raise RunStateError(
                    'local Singlebox webhook execution requires the owned-local '
                    'runtime signing capability')
        else:
            if local_auth is not None:
                raise RunStateError('owned-local signing cannot be used with remote profiles')
            auth = profile.get('auth')
            key_ref = auth.get('principal_signing_key_env') if isinstance(auth, dict) else None
            if not isinstance(key_ref, str) or not key_ref:
                raise RunStateError(
                    'gateway webhook subscription adapter requires an '
                    'operator-provisioned principal signing key reference')
        if not profile['policy']['exclusive_use_confirmed']:
            raise RunStateError('webhook subscription lifecycle writes require exclusive fixture use')
        self.receiver_origin = origin(receiver_origin, environment['tier'])
        if self.local_execution:
            receiver = parsed_origin(self.receiver_origin,
                                     environment['tier'])
            if (receiver.scheme != 'http'
                    or receiver.hostname not in {'127.0.0.1', 'localhost',
                                                  '::1'}):
                raise ConfigError(
                    'webhook subscription receiver: owned-local Singlebox '
                    'execution requires the owned loopback receiver origin')
        self.receiver_url = self.receiver_origin + '/event-webhook'
        self.read_client = ReadClient(profile, local_auth=local_auth)
        self.write_client = WriteClient(profile, local_auth=local_auth)
        self._completed: set[str] = set()
        self._group_name = (GROUP_NAME_PREFIX + hashlib.sha256(
            uuid.uuid4().hex.encode()).hexdigest()[:NAME_SUFFIX_HEX])
        self._subscription_name = (SUBSCRIPTION_NAME_PREFIX + hashlib.sha256(
            uuid.uuid4().hex.encode()).hexdigest()[:NAME_SUFFIX_HEX])
        self._group_id = ''
        self._subscription_id = ''
        self._session_id = ''
        self._delivery_id = ''

    # -- shared request plumbing -------------------------------------

    def _once(self, step: str) -> None:
        if step in self._completed:
            raise RunStateError('webhook subscription obligation was repeated')
        self._completed.add(step)

    @staticmethod
    def _bound_id(value: object, where: str) -> str:
        if not isinstance(value, str) or not value:
            raise RunStateError(where + ' response does not carry the bound identifier')
        return value

    def _group_body(self) -> dict:
        driver = self.profile['actors']['driver']['bot_id']
        member = self.profile['actors']['member']['bot_id']
        return {
            'group_kind': 'normal',
            'name': self._group_name,
            'context': GROUP_BODY_CONTEXT,
            'driver_bot_uuid': driver,
            'participants': [
                {'actor_id': driver, 'role': 'driver'},
                {'actor_id': member, 'role': 'consultant'},
            ],
            'collaboration': {
                'strategy': 'chat',
                'delivery_policy': {'bot_final_delivery': 'send_to_driver'},
            },
            'event_subscriptions': [{
                'name': self._subscription_name,
                'event_filters': list(SUBSCRIPTION_FILTERS),
                'payload': {'mode': 'full'},
                'sink': {
                    'type': 'webhook',
                    'url': self.receiver_url,
                    'request_timeout_ms': SINK_TIMEOUT_MS,
                },
            }],
        }

    def _assert_contract_evidence(self, record: dict, method: str,
                                  status: int) -> None:
        if (record.get('method') != method or record.get('http_status') != status
                or record.get('principal_sent') is not True):
            raise RunStateError(
                'webhook subscription transport evidence differs from the '
                'fixed contract')

    def _evidence(self, requirement_id: str, operation: str, method: str,
                  status: int, record: dict, **extra: object) -> dict:
        self._assert_contract_evidence(record, method, status)
        evidence = {
            'transport': TRANSPORT,
            'requirement_id': requirement_id,
            'operation': operation,
            'http_method': method,
            'http_status': status,
            'identity': IDENTITY,
            'principal_sent': True,
            'credentials_sent': False,
            'authorization_sent': False,
            'fixture_effect': _STEP_FIXTURE_EFFECT.get(requirement_id, 'NONE'),
            'request': record,
        }
        evidence.update(extra)
        self._assert_no_credential_leak(evidence)
        return evidence

    def _assert_no_credential_leak(self, evidence: dict) -> None:
        secrets = [secret for client in (self.read_client, self.write_client)
                   for secret in client.headers.values()]
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError('webhook subscription evidence must not expose a credential')

    # -- reviewed obligations in execution order ---------------------

    def create_subscribed_group(self) -> AdapterCall:
        self._once('create_subscribed_group')
        # Creating a normal Group with an inline subscription can bootstrap
        # Bot execution and public-Bot relation edges; the reviewed effect
        # admission fails closed on persistent remote targets.
        require_group_effect_admission(self.profile)
        data, record = self.write_client.call('group.create',
                                               body=self._group_body(),
                                               expected=(201,))
        payload = data or {}
        group_id = self._bound_id(payload.get('group_id'), 'subscribed Group create')
        subscriptions = payload.get('event_subscriptions')
        if (not isinstance(subscriptions, list) or len(subscriptions) != 1
                or not isinstance(subscriptions[0], dict)):
            raise RunStateError(
                'subscribed Group create does not return exactly one inline '
                'Event Subscription')
        subscription = subscriptions[0]
        subscription_id = self._bound_id(subscription.get('subscription_id'),
                                         'inline Event Subscription')
        if (payload.get('name') != self._group_name
                or subscription.get('name') != self._subscription_name
                or subscription.get('event_filters') != list(SUBSCRIPTION_FILTERS)):
            raise RunStateError(
                'subscribed Group create is outside the reviewed create contract')
        self._group_id, self._subscription_id = group_id, subscription_id
        evidence = self._evidence(
            'WEBHOOK.group-create', FROZEN_OPERATIONS[0], 'POST', 201, record,
            bound_group_id=group_id, bound_subscription_id=subscription_id)
        return AdapterCall(data={'group_id': group_id,
                                 'subscription_id': subscription_id,
                                 'group_name': self._group_name,
                                 'subscription_name': self._subscription_name,
                                 'event_filters': list(SUBSCRIPTION_FILTERS)},
                           evidence=evidence)

    def list_group_subscriptions(self) -> AdapterCall:
        self._once('list_group_subscriptions')
        observation = self.read_client.read('webhook.subscription.list',
                                            group_id=self._group_id)
        data, record = observation.data, observation.evidence
        items = data.get('items') if isinstance(data, dict) else None
        if (not isinstance(items, list) or not items
                or not isinstance(items[0], dict)):
            raise RunStateError(
                'subscription listing does not return the Group subscription page')
        first = items[0]
        if (first.get('subscription_id') != self._subscription_id
                or first.get('status') != 'active'
                or first.get('revision') != 1):
            raise RunStateError(
                'subscription listing does not match the exact inline subscription')
        evidence = self._evidence(
            'WEBHOOK.subscription-list', FROZEN_OPERATIONS[1], 'GET', 200, record,
            bound_group_id=self._group_id, bound_subscription_id=self._subscription_id)
        return AdapterCall(data={'matched': True,
                                 'subscription_id': self._subscription_id,
                                 'status': 'active',
                                 'revision': 1},
                           evidence=evidence)

    def read_event_subscription(self) -> AdapterCall:
        self._once('read_event_subscription')
        observation = self.read_client.read('webhook.subscription.read',
                                            subscription_id=self._subscription_id)
        data, record = observation.data, observation.evidence
        scope = data.get('scope') if isinstance(data, dict) else None
        if (not isinstance(data, dict)
                or data.get('subscription_id') != self._subscription_id
                or data.get('name') != self._subscription_name
                or data.get('status') != 'active' or data.get('revision') != 1
                or data.get('event_filters') != list(SUBSCRIPTION_FILTERS)
                or not isinstance(scope, dict)
                or scope.get('scope_type') != 'group'
                or scope.get('id') != self._group_id):
            raise RunStateError(
                'subscription read does not prove the active revision-one '
                'Group-scoped subscription')
        evidence = self._evidence(
            'WEBHOOK.subscription-read', FROZEN_OPERATIONS[2], 'GET', 200, record,
            bound_subscription_id=self._subscription_id)
        return AdapterCall(data={'subscription_id': self._subscription_id,
                                 'name': self._subscription_name,
                                 'status': 'active', 'revision': 1,
                                 'group_scope': True,
                                 'event_filters': list(SUBSCRIPTION_FILTERS)},
                           evidence=evidence)

    def test_event_subscription(self) -> AdapterCall:
        self._once('test_event_subscription')
        data, record = self.write_client.event_subscription_call(
            'webhook.subscription.test',
            subscription_id=self._subscription_id)
        if data.get('delivered') is not True:
            raise RunStateError(
                'subscription test does not report a delivered synchronous test')
        evidence = self._evidence(
            'WEBHOOK.subscription-test', FROZEN_OPERATIONS[3], 'POST', 200, record,
            bound_subscription_id=self._subscription_id)
        return AdapterCall(data={'delivered': True},
                           evidence=evidence)

    def _patch_subscription(self, operation: str, requirement_id: str,
                            operation_index: int, revision: int,
                            body: dict, expected_revision: int,
                            expected_status: str) -> AdapterCall:
        data, record = self.write_client.event_subscription_call(
            operation, subscription_id=self._subscription_id, body=body)
        if data is None or data.get('revision') != expected_revision \
                or data.get('status') != expected_status \
                or data.get('revision') == revision:
            raise RunStateError(
                f'{operation} does not implement the reviewed optimistic '
                'concurrency transition')
        evidence = self._evidence(
            requirement_id, FROZEN_OPERATIONS[operation_index], 'PATCH', 200,
            record, bound_subscription_id=self._subscription_id)
        return AdapterCall(data={'revision': expected_revision,
                                 'status': expected_status},
                           evidence=evidence)

    def disable_event_subscription(self) -> AdapterCall:
        self._once('disable_event_subscription')
        return self._patch_subscription(
            'webhook.subscription.disable', 'WEBHOOK.subscription-disable', 4,
            1, {'revision': 1,
                'name': self._subscription_name + ' paused',
                'status': 'disabled'}, 2, 'disabled')

    def enable_event_subscription(self) -> AdapterCall:
        self._once('enable_event_subscription')
        return self._patch_subscription(
            'webhook.subscription.enable', 'WEBHOOK.subscription-enable', 5,
            2, {'revision': 2,
                'name': self._subscription_name,
                'status': 'active'}, 3, 'active')

    def create_webhook_session(self) -> AdapterCall:
        self._once('create_webhook_session')
        data, record = self.write_client.call(
            'session.create', group_id=self._group_id,
            body={'title': SESSION_TITLE,
                  'input': {'source': SESSION_INPUT_SOURCE}},
            expected=(201,))
        session_id = self._bound_id((data or {}).get('session_id'),
                                    'Event Webhook Session create')
        self._session_id = session_id
        evidence = self._evidence(
            'WEBHOOK.session-create', FROZEN_OPERATIONS[6], 'POST', 201, record,
            bound_group_id=self._group_id, bound_session_id=session_id)
        return AdapterCall(data={'session_id': session_id},
                           evidence=evidence)

    def list_subscription_deliveries(self) -> AdapterCall:
        self._once('list_subscription_deliveries')
        observation = self.read_client.read(
            'webhook.subscription.deliveries.list',
            subscription_id=self._subscription_id)
        data, record = observation.data, observation.evidence
        items = data.get('items') if isinstance(data, dict) else None
        if (not isinstance(items, list) or not items
                or not isinstance(items[0], dict)):
            raise RunStateError(
                'successful delivery listing does not return the reviewed '
                'delivery page')
        first = items[0]
        delivery_id = self._bound_id(first.get('delivery_id'),
                                     'successful Event delivery')
        if first.get('subscription_id') != self._subscription_id:
            raise RunStateError(
                'successful Event delivery does not belong to the reviewed '
                'subscription')
        self._delivery_id = delivery_id
        evidence = self._evidence(
            'WEBHOOK.deliveries-list', FROZEN_OPERATIONS[7], 'GET', 200, record,
            bound_subscription_id=self._subscription_id,
            bound_delivery_id=delivery_id, delivery_status_filter='succeeded')
        return AdapterCall(data={'delivery_id': delivery_id,
                                 'subscription_id': self._subscription_id,
                                 'status_filter': 'succeeded'},
                           evidence=evidence)

    def read_event_delivery(self) -> AdapterCall:
        self._once('read_event_delivery')
        observation = self.read_client.read('webhook.delivery.read',
                                            delivery_id=self._delivery_id)
        data, record = observation.data, observation.evidence
        delivery = data.get('delivery') if isinstance(data, dict) else None
        if (not isinstance(delivery, dict)
                or delivery.get('delivery_id') != self._delivery_id
                or delivery.get('subscription_id') != self._subscription_id):
            raise RunStateError(
                'Event delivery read does not prove the delivery belongs to '
                'the inline subscription')
        evidence = self._evidence(
            'WEBHOOK.delivery-read', FROZEN_OPERATIONS[8], 'GET', 200, record,
            bound_delivery_id=self._delivery_id,
            bound_subscription_id=self._subscription_id)
        return AdapterCall(data={'delivery_id': self._delivery_id,
                                 'subscription_id': self._subscription_id},
                           evidence=evidence)

    def delete_event_subscription(self) -> AdapterCall:
        self._once('delete_event_subscription')
        data, record = self.write_client.event_subscription_call(
            'webhook.subscription.delete',
            subscription_id=self._subscription_id)
        if data is None or data.get('revision') != 4 or data.get('status') != 'deleted':
            raise RunStateError(
                'subscription delete does not tombstone the subscription at '
                'revision four')
        evidence = self._evidence(
            'WEBHOOK.subscription-delete', FROZEN_OPERATIONS[9], 'DELETE', 200,
            record, bound_subscription_id=self._subscription_id)
        return AdapterCall(data={'revision': 4, 'status': 'deleted',
                                 'subscription_id': self._subscription_id},
                           evidence=evidence)

    def delete_webhook_session(self) -> AdapterCall:
        self._once('delete_webhook_session')
        data, record = self.write_client.call('session.delete',
                                              session_id=self._session_id,
                                              expected=(200,))
        evidence = self._evidence(
            'WEBHOOK.session-delete', FROZEN_OPERATIONS[10], 'DELETE', 200,
            record, bound_session_id=self._session_id)
        return AdapterCall(data={'session_id': self._session_id,
                                 'session_deleted': True},
                           evidence=evidence)

    def delete_subscribed_group(self) -> AdapterCall:
        self._once('delete_subscribed_group')
        data, record = self.write_client.call('group.delete',
                                              group_id=self._group_id,
                                              expected=(200,))
        evidence = self._evidence(
            'WEBHOOK.group-delete', FROZEN_OPERATIONS[11], 'DELETE', 200,
            record, bound_group_id=self._group_id)
        return AdapterCall(data={'group_id': self._group_id,
                                 'group_deleted': True},
                           evidence=evidence)

    def execute(self):
        return execute_webhook_subscription_case(self)
