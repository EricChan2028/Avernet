"""Public-v1 webhook subscription slice from the reviewed Event Webhook Story.

The slice freezes the reviewed subscription lifecycle core of the Event
Webhook Story that transfers faithfully to the public-v1 Gateway/Singlebox
face: the Human principal creates a Group with an inline Event
Subscription whose webhook sink observes the owned receiver, lists and
reads the exact subscription, exercises the synchronous test and the
optimistic-concurrency disable and re-enable, creates and removes a
Session under the subscribed Group, lists and reads the successful
delivery history of the subscription, and finally tombstones the
subscription and removes the Session and Group fixtures. The reviewed
closures that do not transfer stay documented community-face obligations:
the owned receiver reset and principal minting are owned-local run
fixtures, the asynchronous event deliveries and their strict stream
ordering belong to the BCS event worker against the owned receiver, and
the community-message persist route has no public-v1 equivalent. Nothing
in this slice grants release credit: remote validation is not run.
"""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_event_contract import (
    PARENT,
    event_source_binding,
)
from .route_bindings import (
    EVENT_DELIVERY_PATH,
    EVENT_SUBSCRIPTION_DELIVERIES_PATH,
    EVENT_SUBSCRIPTION_DELETE_PATH,
    EVENT_SUBSCRIPTION_LIST_PATH,
    EVENT_SUBSCRIPTION_PATH,
    EVENT_SUBSCRIPTION_TEST_PATH,
    ROUTES,
)
from .run_state import RunStateError

CASE_ID = 'PUBLIC-WEBHOOK-SUBSCRIPTION-01'
SUITE = 'public-webhook-subscription-v1'
# The reviewed HTTP core of the Event Webhook Story in execution order.
CORE_REQUIREMENT_IDS = (
    'WEBHOOK.group-create',
    'WEBHOOK.subscription-list',
    'WEBHOOK.subscription-read',
    'WEBHOOK.subscription-test',
    'WEBHOOK.subscription-disable',
    'WEBHOOK.subscription-enable',
    'WEBHOOK.session-create',
    'WEBHOOK.deliveries-list',
    'WEBHOOK.delivery-read',
    'WEBHOOK.subscription-delete',
    'WEBHOOK.session-delete',
    'WEBHOOK.group-delete',
)
# The reviewed closures that stay on the community face or the owned-local
# run fixtures: receiver reset, local principal minting, the four BCS event
# worker deliveries, the receiver stream-order observation and the
# community message persist route.
COMMUNITY_ONLY_CLOSURES = (
    'WEBHOOK.receiver-empty',
    'WEBHOOK.local-principal',
    'WEBHOOK.group-event',
    'WEBHOOK.test-event',
    'WEBHOOK.session-event',
    'WEBHOOK.message-create',
    'WEBHOOK.message-event',
    'WEBHOOK.stream-order',
)
ADAPTER_METHODS = (
    'create_subscribed_group',
    'list_group_subscriptions',
    'read_event_subscription',
    'test_event_subscription',
    'disable_event_subscription',
    'enable_event_subscription',
    'create_webhook_session',
    'list_subscription_deliveries',
    'read_event_delivery',
    'delete_event_subscription',
    'delete_webhook_session',
    'delete_subscribed_group',
)
FROZEN_OPERATIONS = (
    'group.create',
    'webhook.subscription.list',
    'webhook.subscription.read',
    'webhook.subscription.test',
    'webhook.subscription.disable',
    'webhook.subscription.enable',
    'session.create',
    'webhook.subscription.deliveries.list',
    'webhook.delivery.read',
    'webhook.subscription.delete',
    'session.delete',
    'group.delete',
)
FROZEN_METHODS = ('POST', 'GET', 'GET', 'POST', 'PATCH', 'PATCH',
                  'POST', 'GET', 'GET', 'DELETE', 'DELETE', 'DELETE')
FROZEN_STATUS = (201, 200, 200, 200, 200, 200, 201, 200, 200, 200,
                 200, 200)
# The Group and Session lifecycle routes are declared pilot operations;
# the subscription and delivery routes are the opt-in cross-environment
# Case bindings validated against their frozen public-v1 templates.
_PUBLIC_PATHS = {
    'WEBHOOK.group-create': ROUTES['group.create'][1],
    'WEBHOOK.subscription-list': EVENT_SUBSCRIPTION_LIST_PATH,
    'WEBHOOK.subscription-read': EVENT_SUBSCRIPTION_PATH,
    'WEBHOOK.subscription-test': EVENT_SUBSCRIPTION_TEST_PATH,
    'WEBHOOK.subscription-disable': EVENT_SUBSCRIPTION_PATH,
    'WEBHOOK.subscription-enable': EVENT_SUBSCRIPTION_PATH,
    'WEBHOOK.session-create': ROUTES['session.create'][1],
    'WEBHOOK.deliveries-list': EVENT_SUBSCRIPTION_DELIVERIES_PATH,
    'WEBHOOK.delivery-read': EVENT_DELIVERY_PATH,
    'WEBHOOK.subscription-delete': EVENT_SUBSCRIPTION_DELETE_PATH,
    'WEBHOOK.session-delete': ROUTES['session.delete'][1],
    'WEBHOOK.group-delete': ROUTES['group.delete'][1],
}
_STEP_EFFECTS = {
    'WEBHOOK.group-create': 'WEBHOOK_SUBSCRIPTION_GROUP_CREATED',
    'WEBHOOK.session-create': 'WEBHOOK_WEBHOOK_SESSION_CREATED',
    'WEBHOOK.subscription-delete': 'WEBHOOK_SUBSCRIPTION_TOMBSTONED',
    'WEBHOOK.session-delete': 'WEBHOOK_WEBHOOK_SESSION_FINALIZED',
    'WEBHOOK.group-delete': 'FINALIZED_WEBHOOK_SUBSCRIPTION_GROUP',
}
FIXTURE_EFFECT = 'FINALIZED_WEBHOOK_SUBSCRIPTION_GROUP'
SOURCE_ACTOR = 'HUMAN_PRINCIPAL'
# Evidence identity label follows the reviewed principal actor basis.
IDENTITY = 'AUTHENTICATED_HUMAN_PRINCIPAL'
TRANSPORT = 'public-v1-http'


@dataclass(frozen=True)
class WebhookSubscriptionRequirement:
    requirement_id: str
    source_method: str
    source_path: str
    source_actor: str
    public_path: str
    adapter_method: str
    expectation: str

    def validate(self) -> None:
        if (self.requirement_id not in _PUBLIC_PATHS
                or self.public_path != _PUBLIC_PATHS[self.requirement_id]
                or self.source_method not in FROZEN_METHODS
                or not self.source_path.startswith('$')
                or self.source_actor != SOURCE_ACTOR
                or self.adapter_method not in ADAPTER_METHODS
                or not self.expectation):
            raise RunStateError('webhook subscription requirement is invalid')


@dataclass(frozen=True)
class WebhookSubscriptionCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[WebhookSubscriptionRequirement, ...]
    adapter_methods: tuple[str, ...]
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(row.requirement_id for row in self.requirements)

    @property
    def adapters(self) -> dict[str, str]:
        return {row.requirement_id: row.adapter_method for row in self.requirements}

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.adapter_methods != ADAPTER_METHODS
                or len(self.requirements) != len(CORE_REQUIREMENT_IDS)
                or self.requirement_ids != CORE_REQUIREMENT_IDS
                or len(set(self.adapters.values())) != len(ADAPTER_METHODS)
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('webhook subscription case is incomplete')
        for requirement in self.requirements:
            requirement.validate()


def webhook_subscription_case_plan() -> WebhookSubscriptionCasePlan:
    binding = event_source_binding()
    if binding['parent'] != PARENT:
        raise RunStateError('Event Webhook source parent changed')
    selected = {row['id']: row for row in binding['requirements']
                if row['id'] in CORE_REQUIREMENT_IDS}
    if [row['id'] for row in binding['requirements']
            if row['id'] in CORE_REQUIREMENT_IDS] != list(CORE_REQUIREMENT_IDS):
        raise RunStateError('Event Webhook subscription obligations changed')
    plan = WebhookSubscriptionCasePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirements=tuple(
            WebhookSubscriptionRequirement(
                requirement_id=row['id'], source_method=row['method'],
                source_path=row['path'], source_actor=row['actor'],
                public_path=_PUBLIC_PATHS[row['id']],
                adapter_method=ADAPTER_METHODS[index],
                expectation=row['expectation'],
            )
            for index, row in enumerate(selected.values())
        ),
        adapter_methods=ADAPTER_METHODS,
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def webhook_subscription_adaptability() -> dict:
    plan = webhook_subscription_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(plan.requirement_ids),
        'selected_obligations': [row.requirement_id.split('.', 1)[1]
                                 for row in plan.requirements],
        'public_paths': [row.public_path for row in plan.requirements],
        'environments': ['owned-local-full-story', 'singlebox-public-slice',
                         'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'community_only_closures': list(COMMUNITY_ONLY_CLOSURES),
        'blocker': (
            'This is the stateful subscription lifecycle core slice. It '
            'creates one owned Group with an inline subscription and a '
            'Session per execution and finalizes every fixture. A live '
            'Singlebox run must bind the owned webhook receiver before the '
            'delivery listing step; Gateway remains blocked by the reviewed '
            'normal-group effect admission and by the operator-provisioned '
            'principal signing key until explicit preproduction authorization. '
            'This slice does not migrate the receiver, event-worker '
            'delivery or community message obligations and grants no '
            'release credit.'),
    }


def validate_webhook_subscription_adapter(adapter: object) -> None:
    plan = webhook_subscription_case_plan()
    missing = [name for name in plan.adapter_methods
               if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError('webhook subscription adapter is missing methods: '
                            + ', '.join(missing))


def _validate_result(requirement: WebhookSubscriptionRequirement, result: AdapterCall,
                     index: int) -> None:
    evidence = result.evidence
    expected_effect = _STEP_EFFECTS.get(requirement.requirement_id, 'NONE')
    if (not isinstance(evidence, dict)
            or evidence.get('requirement_id') != requirement.requirement_id
            or evidence.get('operation') != FROZEN_OPERATIONS[index]
            or evidence.get('http_method') != FROZEN_METHODS[index]
            or evidence.get('http_status') != FROZEN_STATUS[index]
            or evidence.get('transport') != TRANSPORT
            or evidence.get('identity') != IDENTITY
            or evidence.get('principal_sent') is not True
            or evidence.get('credentials_sent') is not False
            or evidence.get('authorization_sent') is not False
            or evidence.get('fixture_effect') != expected_effect
            or not isinstance(result.data, dict)):
        raise RunStateError('webhook subscription evidence is outside the '
                            'reviewed slice')


def execute_webhook_subscription_case(adapter: object) -> CaseExecution:
    """Run the 12 reviewed Event Webhook subscription obligations in order."""
    plan = webhook_subscription_case_plan()
    validate_webhook_subscription_adapter(adapter)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for index, requirement in enumerate(plan.requirements):
        method_name = requirement.adapter_method
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN',
                                     data=None, evidence={}, error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + ' did not return AdapterCall')
            result.validate()
            _validate_result(requirement, result, index)
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='PASS',
                                     data=dict(result.data),
                                     evidence=dict(result.evidence),
                                     error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN',
                                     data=None, evidence={},
                                     error_type=error_type))
    execution = CaseExecution(case_id=plan.case_id, parent_id=plan.parent_id,
                              status='PASS' if error_type is None else 'BLOCKED',
                              steps=tuple(steps), completed_count=completed,
                              total_count=len(CORE_REQUIREMENT_IDS),
                              error_type=error_type,
                              ready_to_execute=error_type is None)
    execution.validate()
    return execution
