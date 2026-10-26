"""Public-v1 actor-status slice from the reviewed Agent-network Story."""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_network_contract import PARENT, network_source_binding
from .run_state import RunStateError

CASE_ID = 'PUBLIC-NETWORK-ACTOR-STATUS-01'
REQUIREMENT_ID = 'NETWORK.actor-status'
SOURCE_FUNCTION = 'test_actor_put_status'
SOURCE_PATH = '/actors/$BOT_CEO_UUID/status'
PUBLIC_PATH = '/openapi/v1/collaboration/bots/{driver_actor_id}'
ADAPTER_METHOD = 'toggle_actor_status'
_REQUEST_METHODS = ('GET', 'PATCH', 'GET', 'PATCH', 'GET')
_REQUEST_OPERATIONS = (
    'network.actor_status.read',
    'network.actor_status.write',
    'network.actor_status.read',
    'network.actor_status.write',
    'network.actor_status.read',
)


@dataclass(frozen=True)
class NetworkActorStatusRequirement:
    requirement_id: str
    source_function: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != REQUIREMENT_ID
                or self.source_function != SOURCE_FUNCTION
                or self.source_path != SOURCE_PATH
                or self.public_path != PUBLIC_PATH
                or self.adapter_method != ADAPTER_METHOD):
            raise RunStateError('public actor-status requirement is invalid')


@dataclass(frozen=True)
class NetworkActorStatusCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkActorStatusRequirement
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('public actor-status case is incomplete')
        self.requirement.validate()


def network_actor_status_case_plan() -> NetworkActorStatusCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == REQUIREMENT_ID and row['function'] == SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('actor-status source obligation changed')
    plan = NetworkActorStatusCasePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirement=NetworkActorStatusRequirement(
            requirement_id=REQUIREMENT_ID,
            source_function=SOURCE_FUNCTION,
            source_path=SOURCE_PATH,
            public_path=PUBLIC_PATH,
            adapter_method=ADAPTER_METHOD,
        ),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_actor_status_adaptability() -> dict:
    plan = network_actor_status_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'selected_obligations': ['before-online', 'hidden', 'online', 'restored'],
        'environments': ['owned-local-full-story', 'singlebox-public-slice',
                        'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('This slice mutates only the dedicated driver Bot, reads back each raw '
                    'persisted status, and restores online before completion. It does not '
                    'grant release credit or cover registration/onboarding.'),
    }


def validate_network_actor_status_adapter(adapter: object) -> None:
    plan = network_actor_status_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('actor-status adapter is missing toggle_actor_status')


def _validate_result(plan: NetworkActorStatusCasePlan, result: AdapterCall) -> None:
    evidence = result.evidence
    requests = evidence.get('requests')
    if (not isinstance(evidence, dict)
            or evidence.get('fixture_effect') != 'BOUNDED_OWNED_BOT_STATUS_RESTORE'
            or not isinstance(requests, list) or len(requests) != 5
            or [row.get('operation') for row in requests] != list(_REQUEST_OPERATIONS)
            or [row.get('method') for row in requests] != list(_REQUEST_METHODS)
            or [row.get('http_status') for row in requests] != [200] * 5
            or not all(row.get('credentials_sent') is True
                       and row.get('authorization_sent') is False for row in requests)):
        raise RunStateError('actor-status evidence is outside the reviewed bounded mutation')
    data = result.data
    target = evidence.get('target_actor_id')
    expected_path = (plan.requirement.public_path.replace('{driver_actor_id}', target)
                     if isinstance(target, str) and target else None)
    if (not isinstance(data, dict)
            or set(data) != {'target_actor_id', 'before_status', 'hidden_status', 'online_status'}
            or data.get('target_actor_id') != target
            or expected_path is None
            or any(row.get('path') != expected_path for row in requests)
            or data != evidence.get('projection')):
        raise RunStateError('actor-status result does not prove the hidden/online round trip')
    if (data.get('before_status') != 'online'
            or data.get('hidden_status') != 'hidden'
            or data.get('online_status') != 'online'):
        raise RunStateError('actor-status result did not restore the reviewed online state')
    if requests[1].get('requested_status') != 'hidden' or requests[3].get('requested_status') != 'online':
        raise RunStateError('actor-status write bodies differ from the reviewed finite plan')


def execute_network_actor_status_case(adapter: object) -> CaseExecution:
    plan = network_actor_status_case_plan()
    validate_network_actor_status_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('actor-status method did not return AdapterCall')
        result.validate()
        _validate_result(plan, result)
        step = StepOutcome(requirement_id=REQUIREMENT_ID,
                           method=ADAPTER_METHOD,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=REQUIREMENT_ID,
                           method=ADAPTER_METHOD,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
