"""Environment-neutral rewrite contracts for the finite read-only Edge cases.

The source binding freezes what the old suite actually did.  This module
separates mutable transport concerns from finite read obligations so
owned-local, Singlebox and preproduction adapters can implement the same method
order without redefining a case.  It still performs no request.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .edge_read_contract import (
    ADMISSION_PARENT,
    ADMISSION_REQUIREMENTS,
    FRIEND_REQUESTS_PARENT,
    FRIEND_REQUESTS_REQUIREMENTS,
    FRIEND_CONNECTIONS_PARENT,
    FRIEND_CONNECTIONS_REQUIREMENTS,
    admission_source_binding,
    friend_requests_list_source_binding,
    friend_connections_list_source_binding,
)
from .run_state import RunStateError

ADMISSION_CASE_ID = 'LOCAL-EDGE-ADMISSION-01'
FRIEND_REQUESTS_CASE_ID = 'LOCAL-EDGE-FRIEND-REQUESTS-LIST-01'
FRIEND_CONNECTIONS_CASE_ID = 'LOCAL-EDGE-FRIEND-CONNECTIONS-LIST-01'
ADMISSION_PUBLIC_CASE_ID = 'PUBLIC-EDGE-ADMISSION-01'
FRIEND_REQUESTS_PUBLIC_CASE_ID = 'PUBLIC-EDGE-FRIEND-REQUESTS-LIST-01'
FRIEND_CONNECTIONS_PUBLIC_CASE_ID = 'PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01'
ADMISSION_ADAPTER_METHODS = (
    'read_admission_human_actor',
    'read_admission_actor_kind',
)
FRIEND_REQUESTS_ADAPTER_METHODS = (
    'read_friend_requests_received',
    'read_friend_requests_sent',
    'read_friend_requests_all',
)
FRIEND_CONNECTIONS_ADAPTER_METHODS = (
    'read_friend_connections_list',
    'read_friend_connections_list_repeat',
)
FRIEND_REQUESTS_PUBLIC_PATHS = {
    'EDGE-FRIEND-REQUESTS.received': '/openapi/v1/collaboration/friend-connections/requests?direction=received',
    'EDGE-FRIEND-REQUESTS.sent': '/openapi/v1/collaboration/friend-connections/requests?direction=sent',
    'EDGE-FRIEND-REQUESTS.all': '/openapi/v1/collaboration/friend-connections/requests?direction=all',
}
FRIEND_CONNECTIONS_PUBLIC_PATH = ('/openapi/v1/collaboration/friend-connections'
                                  '?actor_type=bot&actor_id={driver_actor_id}'
                                  '&page=1&page_size=100')
_ADAPTERS = {
    ADMISSION_REQUIREMENTS: ADMISSION_ADAPTER_METHODS,
    FRIEND_REQUESTS_REQUIREMENTS: FRIEND_REQUESTS_ADAPTER_METHODS,
    FRIEND_CONNECTIONS_REQUIREMENTS: FRIEND_CONNECTIONS_ADAPTER_METHODS,
}


@dataclass(frozen=True)
class EdgeReadEnvironment:
    name: str
    adapter_family: str
    status: str
    blocker: str

    def validate(self) -> None:
        if (self.name not in {'owned-local', 'singlebox', 'preprod'}
                or self.adapter_family not in {
                    'OwnedEdgeReadAdapter', 'SingleboxCaseAdapter', 'RemoteCaseAdapter'}
                or self.status != 'PENDING'
                or not isinstance(self.blocker, str) or not self.blocker):
            raise RunStateError('Edge read environment contract is invalid')


@dataclass(frozen=True)
class EdgeReadRequirement:
    requirement_id: str
    method: str
    path: str
    actor: str
    adapter_method: str
    expectation: str

    def validate(self) -> None:
        if (not self.method or self.method != 'GET' or not self.path.startswith('/')
                or not self.actor or not self.adapter_method or not self.expectation):
            raise RunStateError(f'Edge read requirement {self.requirement_id!r} is invalid')


@dataclass(frozen=True)
class EdgeReadCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[EdgeReadRequirement, ...]
    environments: tuple[EdgeReadEnvironment, ...]

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(row.requirement_id for row in self.requirements)

    @property
    def adapters(self) -> Mapping[str, str]:
        return {row.requirement_id: row.adapter_method for row in self.requirements}

    def validate(self) -> None:
        expected_ids = _ADAPTERS[self.requirement_ids]
        if (set(self.adapters) != set(self.requirement_ids)
                or tuple(self.adapters[row] for row in self.requirement_ids) != expected_ids
                or len(set(self.adapters.values())) != len(self.adapters)
                or [row.name for row in self.environments]
                   != ['owned-local', 'singlebox', 'preprod']):
            raise RunStateError(f'Edge read case {self.case_id!r} is incomplete')
        for row in self.requirements:
            row.validate()
        for environment in self.environments:
            environment.validate()


def _environments(parent_id: str) -> tuple[EdgeReadEnvironment, ...]:
    shared = 'Exact %s route, fixture identity and response contract remain unadmitted.' % parent_id
    return (
        EdgeReadEnvironment(name='owned-local', adapter_family='OwnedEdgeReadAdapter',
                            status='PENDING', blocker=shared + ' A live owned adapter has not been registered.'),
        EdgeReadEnvironment(name='singlebox', adapter_family='SingleboxCaseAdapter',
                            status='PENDING', blocker=shared + ' Singlebox fixture admission is pending.'),
        EdgeReadEnvironment(name='preprod', adapter_family='RemoteCaseAdapter',
                            status='PENDING', blocker=shared + ' Preprod route and identity admission are independent.'),
    )


def _plan(case_id: str, parent_id: str, binding: dict,
          requirement_ids: tuple[str, ...]) -> EdgeReadCasePlan:
    if binding.get('parent') != parent_id or tuple(row['id'] for row in binding['requirements']) != requirement_ids:
        raise RunStateError('Edge source and case rewrite bindings differ')
    rows = tuple(
        EdgeReadRequirement(
            requirement_id=row['id'], method=row['method'], path=row['path'],
            actor=row['actor'], adapter_method=_ADAPTERS[requirement_ids][index],
            expectation=row['expectation'],
        )
        for index, row in enumerate(binding['requirements'])
    )
    plan = EdgeReadCasePlan(case_id=case_id, parent_id=parent_id,
                             requirements=rows, environments=_environments(parent_id))
    plan.validate()
    return plan


def edge_read_case_plans() -> tuple[EdgeReadCasePlan, ...]:
    """Return the finite read-only case plans in stable inventory order."""
    return (
        _plan(ADMISSION_CASE_ID, ADMISSION_PARENT,
              admission_source_binding(), ADMISSION_REQUIREMENTS),
        _plan(FRIEND_REQUESTS_CASE_ID, FRIEND_REQUESTS_PARENT,
              friend_requests_list_source_binding(), FRIEND_REQUESTS_REQUIREMENTS),
        _plan(FRIEND_CONNECTIONS_CASE_ID, FRIEND_CONNECTIONS_PARENT,
              friend_connections_list_source_binding(), FRIEND_CONNECTIONS_REQUIREMENTS),
    )


def validate_edge_read_adapter(adapter: object, plan: EdgeReadCasePlan) -> None:
    expected = tuple(plan.adapters[row] for row in plan.requirement_ids)
    if expected != _ADAPTERS[plan.requirement_ids]:
        raise RunStateError('Edge read case and adapter method contract differ')
    missing = [name for name in expected if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError('Edge read adapter is missing methods: ' + ', '.join(missing))


def edge_read_public_aliases() -> dict[str, str]:
    return {
        ADMISSION_PUBLIC_CASE_ID: ADMISSION_CASE_ID,
        FRIEND_REQUESTS_PUBLIC_CASE_ID: FRIEND_REQUESTS_CASE_ID,
        FRIEND_CONNECTIONS_PUBLIC_CASE_ID: FRIEND_CONNECTIONS_CASE_ID,
    }


def admission_public_adaptability() -> dict:
    plan = next(row for row in edge_read_case_plans()
                if row.case_id == ADMISSION_CASE_ID)
    return {
        'case_id': ADMISSION_PUBLIC_CASE_ID,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(plan.requirement_ids),
        'selected_obligations': ['human-actor', 'actor-kind'],
        'public_paths': [
            '/bots/{driver_actor_id}/admission?actor=human_{human_actor_id}',
            '/bots/{driver_actor_id}/admission?actor={human_actor_id}&actor_kind=human',
        ],
        'environments': ['owned-local-full-story', 'singlebox-native-slice',
                         'preprod-native-slice'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': ('The reviewed native admission route is read-only, but Singlebox and '
                    'Gateway still need live fixture/route admission and explicit 200 '
                    'response evidence; no release credit is granted without it.'),
    }


def friend_requests_public_adaptability() -> dict:
    plan = next(row for row in edge_read_case_plans()
                if row.case_id == FRIEND_REQUESTS_CASE_ID)
    return {
        'case_id': FRIEND_REQUESTS_PUBLIC_CASE_ID,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(plan.requirement_ids),
        'selected_obligations': ['received', 'sent', 'all'],
        'public_paths': [FRIEND_REQUESTS_PUBLIC_PATHS[row.requirement_id]
                         for row in plan.requirements],
        'environments': ['owned-local-full-story', 'singlebox-public-slice',
                         'preprod-public-slice'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': ('The public friend-request list slice is contract-tested, but a real Singlebox or '
                    'Gateway run still needs live fixture admission; no release credit is '
                    'granted without it.'),
    }


def friend_connections_public_adaptability() -> dict:
    plan = next(row for row in edge_read_case_plans()
                if row.case_id == FRIEND_CONNECTIONS_CASE_ID)
    return {
        'case_id': FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(plan.requirement_ids),
        'selected_obligations': ['list', 'list-repeat'],
        'public_paths': [FRIEND_CONNECTIONS_PUBLIC_PATH] * 2,
        'environments': ['owned-local-read-spine', 'singlebox-public-slice',
                         'preprod-public-slice'],
        'local_executor_status': 'LOCAL_READ_SLICE_ONLY',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': ('The repeated public friend-connection read slice is contract-tested, but the '
                    "parent's POST write remains outside this slice; a live Singlebox or "
                    'Gateway fixture still has to be admitted.'),
    }


def execute_edge_read_case(case_id: str, adapter: object) -> CaseExecution:
    """Run one finite read-only plan without retry or environment fallback."""
    aliases = edge_read_public_aliases()
    execution_case_id = aliases.get(case_id, case_id)
    plans = edge_read_case_plans()
    plan = next((row for row in plans if row.case_id == execution_case_id), None)
    if plan is None:
        raise RunStateError('unknown Edge read case ID')
    validate_edge_read_adapter(adapter, plan)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for requirement in plan.requirements:
        method_name = requirement.adapter_method
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN', data=None,
                                     evidence={}, error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + ' did not return AdapterCall')
            result.validate()
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='PASS',
                                     data=result.data, evidence=dict(result.evidence),
                                     error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN', data=None,
                                     evidence={}, error_type=error_type))
    execution = CaseExecution(case_id=plan.case_id, parent_id=plan.parent_id,
                              status='PASS' if error_type is None else 'BLOCKED',
                              steps=tuple(steps), completed_count=completed,
                              total_count=len(steps), error_type=error_type,
                              ready_to_execute=error_type is None)
    execution.validate()
    if case_id != execution.case_id:
        execution = replace(execution, case_id=case_id)
        execution.validate()
    return execution
