"""Public-v1 channel-binding slice from the reviewed disabled-channel Story."""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_channel_contract import PARENT, channel_source_binding
from .run_state import RunStateError

CASE_ID = 'PUBLIC-CHANNEL-BINDINGS-01'
SUITE = 'public-channel-bindings-v1'
REQUIREMENT_IDS = (
    'CHANNEL.bind-denied',
    'CHANNEL.list-before',
    'CHANNEL.target-empty',
    'CHANNEL.list-after',
)
ADAPTER_METHOD = 'verify_uninstalled_channel_binding_is_rejected'
PUBLIC_CREATE_PATH = '/openapi/v1/collaboration/channels/bindings'
PUBLIC_LIST_PATH = '/openapi/v1/collaboration/channels/bindings'
PUBLIC_BY_TARGET_PREFIX = '/openapi/v1/collaboration/channels/bindings/by-target?target_type=bot&target_id='
UNINSTALLED_CHANNEL_TYPE = 'e2e-uninstalled-channel'
_REQUEST_METHODS = ('POST', 'GET', 'GET', 'GET')
_REQUEST_OPERATIONS = (
    'channel.bindings.create_denied',
    'channel.bindings.list_before',
    'channel.bindings.by_target_empty',
    'channel.bindings.list_after',
)
_REQUEST_STATUS = (400, 200, 200, 200)
_BLOCKED_REQUIREMENTS = ('CHANNEL.pause-missing', 'CHANNEL.delete-missing')


@dataclass(frozen=True)
class ChannelPublicRequirement:
    requirement_id: str
    path: str
    method: str
    expectation: str

    def validate(self) -> None:
        expected = {
            REQUIREMENT_IDS[0]: ('POST', PUBLIC_CREATE_PATH),
            REQUIREMENT_IDS[1]: ('GET', PUBLIC_LIST_PATH),
            REQUIREMENT_IDS[2]: ('GET', PUBLIC_BY_TARGET_PREFIX + '{driver_actor_id}'
                                 + '&channel_type=' + UNINSTALLED_CHANNEL_TYPE),
            REQUIREMENT_IDS[3]: ('GET', PUBLIC_LIST_PATH),
        }
        method, path = expected[self.requirement_id]
        if self.method != method or self.path != path or not self.expectation:
            raise RunStateError('public channel-binding requirement is invalid')


@dataclass(frozen=True)
class ChannelPublicCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[ChannelPublicRequirement, ...]
    adapter_method: str
    blocked_requirements: tuple[str, ...]
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.adapter_method != ADAPTER_METHOD
                or self.blocked_requirements != _BLOCKED_REQUIREMENTS
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False
                or tuple(row.requirement_id for row in self.requirements) != REQUIREMENT_IDS):
            raise RunStateError('public channel-binding case is incomplete')
        for requirement in self.requirements:
            requirement.validate()


def channel_bindings_case_plan() -> ChannelPublicCasePlan:
    binding = channel_source_binding()
    if binding['parent'] != PARENT:
        raise RunStateError('channel-binding source parent changed')
    source_ids = [row['id'] for row in binding['requirements']]
    if not set(REQUIREMENT_IDS).issubset(source_ids) or not set(_BLOCKED_REQUIREMENTS).issubset(source_ids):
        raise RunStateError('channel-binding source obligations changed')
    plan = ChannelPublicCasePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirements=(
            ChannelPublicRequirement(
                REQUIREMENT_IDS[0], PUBLIC_CREATE_PATH, 'POST',
                'POST for the uninstalled channel is rejected by the public 400 contract',
            ),
            ChannelPublicRequirement(
                REQUIREMENT_IDS[1], PUBLIC_LIST_PATH, 'GET',
                'The public global binding list is a valid array without a local-empty assumption',
            ),
            ChannelPublicRequirement(
                REQUIREMENT_IDS[2], PUBLIC_BY_TARGET_PREFIX + '{driver_actor_id}'
                + '&channel_type=' + UNINSTALLED_CHANNEL_TYPE, 'GET',
                'The dedicated driver and uninstalled channel combination has no binding',
            ),
            ChannelPublicRequirement(
                REQUIREMENT_IDS[3], PUBLIC_LIST_PATH, 'GET',
                'The global list remains valid and contains no binding for the exact tested pair',
            ),
        ),
        adapter_method=ADAPTER_METHOD,
        blocked_requirements=_BLOCKED_REQUIREMENTS,
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def channel_bindings_adaptability() -> dict:
    plan = channel_bindings_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(REQUIREMENT_IDS),
        'blocked_requirement_ids': list(_BLOCKED_REQUIREMENTS),
        'adapter_methods': [plan.adapter_method],
        'environments': ['owned-local-full-story', 'singlebox-public-slice', 'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The reviewed local missing-binding pause/delete success semantics are not '
                    'the public-v1 404 contract; they stay blocked instead of being normalized to PASS.'),
    }


def validate_channel_bindings_adapter(adapter: object) -> None:
    plan = channel_bindings_case_plan()
    method = getattr(adapter, plan.adapter_method, None)
    if not callable(method):
        raise RunStateError('channel-binding adapter is missing ' + ADAPTER_METHOD)


def _validate_result(plan: ChannelPublicCasePlan, result: AdapterCall) -> None:
    evidence = result.evidence
    requests = evidence.get('requests')
    if (not isinstance(evidence, dict)
            or evidence.get('fixture_effect') != 'NO_BINDING_CREATED'
            or not isinstance(requests, list) or len(requests) != 4
            or [row.get('operation') for row in requests] != list(_REQUEST_OPERATIONS)
            or [row.get('method') for row in requests] != list(_REQUEST_METHODS)
            or [row.get('http_status') for row in requests] != list(_REQUEST_STATUS)
            or evidence.get('blocked_requirement_ids') != list(_BLOCKED_REQUIREMENTS)
            or not all(row.get('credentials_sent') is True
                       and row.get('authorization_sent') is False for row in requests)):
        raise RunStateError('channel-binding evidence is outside the reviewed public slice')
    projection = evidence.get('projection')
    expected_projection = {
        'target_actor_id': evidence.get('target_actor_id'),
        'channel_type': UNINSTALLED_CHANNEL_TYPE,
        'rejected_error_code': 'invalid_channel_params',
        'before_item_count': evidence.get('before_item_count'),
        'target_item_count': evidence.get('target_item_count'),
        'after_item_count': evidence.get('after_item_count'),
        'ghost_item_count': evidence.get('ghost_item_count'),
    }
    if (not isinstance(projection, dict) or projection != expected_projection
            or projection.get('target_item_count') != 0
            or projection.get('ghost_item_count') != 0):
        raise RunStateError('channel-binding result does not prove rejection and no ghost binding')
    expected_paths = {
        REQUIREMENT_IDS[0]: plan.requirements[0].path,
        REQUIREMENT_IDS[1]: plan.requirements[1].path,
        REQUIREMENT_IDS[2]: (PUBLIC_BY_TARGET_PREFIX
                             + projection['target_actor_id'].replace(':', '%3A')
                             + '&channel_type=' + UNINSTALLED_CHANNEL_TYPE),
        REQUIREMENT_IDS[3]: plan.requirements[3].path,
    }
    for request, requirement_id in zip(requests, REQUIREMENT_IDS):
        if request.get('path') != expected_paths[requirement_id]:
            raise RunStateError('channel-binding request path differs from its frozen contract')


def execute_channel_bindings_case(adapter: object) -> CaseExecution:
    plan = channel_bindings_case_plan()
    validate_channel_bindings_adapter(adapter)
    method = getattr(adapter, plan.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('channel-binding method did not return AdapterCall')
        result.validate()
        _validate_result(plan, result)
        projection = result.evidence['projection']
        step_data = (
            {'target_actor_id': projection['target_actor_id'],
             'error_code': projection['rejected_error_code']},
            {'item_count': projection['before_item_count']},
            {'item_count': projection['target_item_count']},
            {'item_count': projection['after_item_count'],
             'ghost_item_count': projection['ghost_item_count']},
        )
        requests = result.evidence['requests']
        steps = tuple(
            StepOutcome(
                requirement_id=requirement_id,
                method=ADAPTER_METHOD,
                status='PASS',
                data=data,
                evidence={'request': dict(request)},
                error_type=None,
            )
            for requirement_id, data, request in zip(REQUIREMENT_IDS, step_data, requests)
        )
        completed, error_type = 4, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        steps = tuple(
            StepOutcome(
                requirement_id=requirement_id,
                method=ADAPTER_METHOD,
                status='NOT_RUN',
                data=None,
                evidence={},
                error_type=error_type,
            )
            for requirement_id in REQUIREMENT_IDS
        )
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=steps,
        completed_count=completed,
        total_count=4,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
