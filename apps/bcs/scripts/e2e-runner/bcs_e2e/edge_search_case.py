"""Environment-neutral read spine for the original Bot-search Edge case.

The parent is not read-only. Its one service-key fixture mutation is kept as a
separate admission record and is never represented as one of the read-case
adapter operations. This module performs no HTTP request and grants no remote,
Singlebox or release authority.
"""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .edge_search_contract import PARENT, bot_search_source_binding
from .run_state import RunStateError

CASE_ID = 'LOCAL-EDGE-BOT-SEARCH-READ-01'
READ_REQUIREMENTS = (
    'EDGE-BOT-SEARCH.name-fuzzy',
    'EDGE-BOT-SEARCH.public-online-not-friend',
    'EDGE-BOT-SEARCH.friend-true',
    'EDGE-BOT-SEARCH.hidden-status',
    'EDGE-BOT-SEARCH.private-visibility',
    'EDGE-BOT-SEARCH.empty-query',
    'EDGE-BOT-SEARCH.tc-true',
    'EDGE-BOT-SEARCH.tc-false',
    'EDGE-BOT-SEARCH.anonymous',
    'EDGE-BOT-SEARCH.bad-limit',
    'EDGE-BOT-SEARCH.bad-visibility',
    'EDGE-BOT-SEARCH.bad-status',
)
FIXTURE_REQUIREMENT_ID = 'EDGE-BOT-SEARCH.ensure-tc-fixture'
_READ_SOURCE = (
    ('EDGE-BOT-SEARCH.name-fuzzy', '/bots/search?q=CEO&offset=0&limit=20', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.public-online-not-friend', '/bots/search?visibility=public&status=online&is_friend=false&limit=50', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.friend-true', '/bots/search?is_friend=true&limit=50', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.hidden-status', '/bots/search?status=hidden&limit=10', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.private-visibility', '/bots/search?visibility=private&limit=10', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.empty-query', '/bots/search?limit=5', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.tc-true', '/bots/search?tc_bot=true&limit=50', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.tc-false', '/bots/search?tc_bot=false&limit=50', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.anonymous', '/bots/search?limit=10', 'UNAUTHENTICATED'),
    ('EDGE-BOT-SEARCH.bad-limit', '/bots/search?limit=999', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.bad-visibility', '/bots/search?visibility=bogus', 'CEO_BOT'),
    ('EDGE-BOT-SEARCH.bad-status', '/bots/search?status=bogus', 'CEO_BOT'),
)
ADAPTER_METHODS = (
    'read_bot_search_name_fuzzy',
    'read_bot_search_public_online_non_friend',
    'read_bot_search_friend_true',
    'read_bot_search_hidden_status',
    'read_bot_search_private_visibility',
    'read_bot_search_empty_query',
    'read_bot_search_tc_true',
    'read_bot_search_tc_false',
    'read_bot_search_anonymous',
    'read_bot_search_bad_limit',
    'read_bot_search_bad_visibility',
    'read_bot_search_bad_status',
)
_EXPECTED_STATUS = {
    'name-fuzzy': 200,
    'public-online-not-friend': 400,
    'friend-true': 400,
    'hidden-status': 200,
    'private-visibility': 200,
    'empty-query': 200,
    'tc-true': 200,
    'tc-false': 200,
    'anonymous': 200,
    'bad-limit': 400,
    'bad-visibility': 400,
    'bad-status': 400,
}
_RESPONSE_CONTRACT = {
    'name-fuzzy': 'BOUNDED_ITEM_PAGE',
    'public-online-not-friend': 'HTTP_400_VIEWER_REQUIRED',
    'friend-true': 'HTTP_400_VIEWER_REQUIRED',
    'hidden-status': 'BOUNDED_ITEM_PAGE',
    'private-visibility': 'BOUNDED_ITEM_PAGE',
    'empty-query': 'BOUNDED_ITEM_PAGE',
    'tc-true': 'BOUNDED_ITEM_PAGE',
    'tc-false': 'BOUNDED_ITEM_PAGE',
    'anonymous': 'NO_PRIVATE_OR_FRIENDSHIP_FIELDS',
    'bad-limit': 'HTTP_400_VALIDATION',
    'bad-visibility': 'HTTP_400_VALIDATION',
    'bad-status': 'HTTP_400_VALIDATION',
}


@dataclass(frozen=True)
class EdgeSearchEnvironment:
    name: str
    adapter_family: str
    status: str
    blocker: str

    def validate(self) -> None:
        if (self.name not in {'owned-local', 'singlebox', 'preprod'}
                or self.adapter_family not in {
                    'OwnedEdgeSearchAdapter', 'SingleboxCaseAdapter', 'RemoteCaseAdapter'}
                or self.status not in {'PENDING', 'BLOCKED'} or not self.blocker):
            raise RunStateError('Bot-search environment contract is invalid')


@dataclass(frozen=True)
class EdgeSearchReadRequirement:
    requirement_id: str
    method: str
    path: str
    actor: str
    adapter_method: str
    expected_http_status: int
    response_contract: str
    expectation: str

    def validate(self) -> None:
        if (not self.method or self.method != 'GET' or not self.path.startswith('/')
                or not self.actor or not self.adapter_method
                or self.expected_http_status not in {200, 400}
                or not self.response_contract or not self.expectation):
            raise RunStateError(f'Bot-search requirement {self.requirement_id!r} is invalid')


@dataclass(frozen=True)
class EdgeSearchFixtureAdmission:
    requirement_id: str
    method: str
    path: str
    actor: str
    effect: str
    ownership_scope: str
    status: str
    blocker: str

    def validate(self) -> None:
        if (self.requirement_id != FIXTURE_REQUIREMENT_ID or self.method != 'POST'
                or self.path != '/admin/bots/$tc_uuid/ensure' or self.actor != 'SERVICE_KEY'
                or self.effect != 'MUTATION' or self.ownership_scope != 'SEPARATE_ADMISSION'
                or self.status not in {'PENDING', 'BLOCKED'} or not self.blocker):
            raise RunStateError('Bot-search fixture admission is invalid')


@dataclass(frozen=True)
class EdgeSearchCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[EdgeSearchReadRequirement, ...]
    fixture_admission: EdgeSearchFixtureAdmission
    environments: tuple[EdgeSearchEnvironment, ...]

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(row.requirement_id for row in self.requirements)

    @property
    def adapters(self) -> dict[str, str]:
        return {row.requirement_id: row.adapter_method for row in self.requirements}

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.requirement_ids != READ_REQUIREMENTS
                or tuple(self.adapters.values()) != ADAPTER_METHODS
                or len(set(self.adapters.values())) != len(self.adapters)
                or [row.name for row in self.environments]
                   != ['owned-local', 'singlebox', 'preprod']):
            raise RunStateError('Bot-search read case is incomplete or reordered')
        for row in self.requirements:
            row.validate()
        self.fixture_admission.validate()
        for environment in self.environments:
            environment.validate()


def _fixture_admission(binding: dict) -> EdgeSearchFixtureAdmission:
    row = next((row for row in binding['requirements']
                if row['id'] == FIXTURE_REQUIREMENT_ID), None)
    if row is None:
        raise RunStateError('Bot-search fixture mutation was silently dropped')
    admission = EdgeSearchFixtureAdmission(
        requirement_id=row['id'], method=row['method'], path=row['path'], actor=row['actor'],
        effect=row['effect'], ownership_scope='SEPARATE_ADMISSION', status='PENDING',
        blocker='Each environment must separately admit the service-key fixture before tc_bot reads; no read adapter may perform it.',
    )
    admission.validate()
    return admission


def _environments() -> tuple[EdgeSearchEnvironment, ...]:
    blocker = ('Bot-search response contracts and the separate TC fixture admission '
                'remain pending in this environment.')
    return tuple(
        EdgeSearchEnvironment(
            name=name,
            adapter_family='OwnedEdgeSearchAdapter' if name == 'owned-local'
            else 'SingleboxCaseAdapter' if name == 'singlebox' else 'RemoteCaseAdapter',
            status='PENDING',
            blocker=('Singlebox and preprod route admission remain pending; '
                     'owned-local execution is separately registered and grants no release credit.'
                     if name == 'owned-local' else blocker),
        )
        for name in ('owned-local', 'singlebox', 'preprod')
    )


def edge_search_case_plan() -> EdgeSearchCasePlan:
    """Freeze the 12 read obligations and the excluded fixture mutation."""
    binding = bot_search_source_binding()
    if binding.get('parent') != PARENT:
        raise RunStateError('Bot-search source parent changed')
    read_rows = tuple(row for row in binding['requirements'] if row['effect'] == 'READ')
    actual = tuple((row['id'], row['path'], row['actor'], row['method'])
                   for row in read_rows)
    expected = tuple((row_id, path, actor, 'GET') for row_id, path, actor in _READ_SOURCE)
    if actual != expected:
        raise RunStateError('Bot-search source and read-case bindings differ')
    requirements = tuple(
        EdgeSearchReadRequirement(
            requirement_id=row['id'], method=row['method'], path=row['path'], actor=row['actor'],
            adapter_method=ADAPTER_METHODS[index],
            expected_http_status=_EXPECTED_STATUS[row['id'].split('.', 1)[1]],
            response_contract=_RESPONSE_CONTRACT[row['id'].split('.', 1)[1]],
            expectation=row['expectation'],
        )
        for index, row in enumerate(read_rows)
    )
    plan = EdgeSearchCasePlan(
        case_id=CASE_ID, parent_id=PARENT, requirements=requirements,
        fixture_admission=_fixture_admission(binding), environments=_environments(),
    )
    plan.validate()
    return plan


def edge_search_adaptability() -> dict:
    plan = edge_search_case_plan()
    return {
        'cases': [{
            'legacy_id': plan.parent_id,
            'effect_scope': 'READ_SPINE_WITH_SEPARATE_FIXTURE_MUTATION',
            'requirements': list(plan.requirement_ids),
            'excluded_mutation_ids': [plan.fixture_admission.requirement_id],
            'environments': [row.name for row in plan.environments],
            'status': 'PENDING',
            'blocker': 'Admit the exact search routes, CEO identity, response contracts, and the separately owned TC fixture in each environment.',
            'grants_release_credit': False,
        }],
        'ready_to_execute': False,
    }


def validate_edge_search_adapter(adapter: object, plan: EdgeSearchCasePlan) -> None:
    expected = tuple(plan.adapters[row] for row in plan.requirement_ids)
    if expected != ADAPTER_METHODS:
        raise RunStateError('Bot-search case and adapter method contract differ')
    missing = [name for name in expected if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError('Bot-search adapter is missing methods: ' + ', '.join(missing))


def _validate_result(requirement: EdgeSearchReadRequirement, result: AdapterCall) -> None:
    evidence = result.evidence
    if (evidence.get('http_method') != 'GET'
            or evidence.get('path') != requirement.path
            or evidence.get('http_status') != requirement.expected_http_status
            or evidence.get('response_contract') != requirement.response_contract
            or not isinstance(evidence.get('transport'), str)
            or not evidence['transport']
            or not isinstance(evidence.get('identity'), str)
            or not evidence['identity']):
        raise RunStateError('Bot-search adapter evidence violates the reviewed contract')


def execute_edge_search_case(adapter: object) -> CaseExecution:
    """Run only the 12 read obligations; the fixture mutation has no adapter method."""
    plan = edge_search_case_plan()
    validate_edge_search_adapter(adapter, plan)
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
            _validate_result(requirement, result)
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
    return execution


# Cross-environment slice from the same reviewed Bot-search Edge parent. The
# station gateway does not expose the legacy `/bots/search` control-plane
# route, but its internal-v1 candidate search exactly covers the name-fuzzy and
# empty-query read obligations at a bounded limit of 20. The other ten reads
# and the separate TC fixture mutation remain outside this slice.
EDGE_BOT_SEARCH_CANDIDATES_CASE_ID = 'PUBLIC-EDGE-BOT-SEARCH-CANDIDATES-01'
EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS = (
    'EDGE-BOT-SEARCH.name-fuzzy',
    'EDGE-BOT-SEARCH.empty-query',
)
EDGE_BOT_SEARCH_CANDIDATES_SELECTED_OBLIGATIONS = ('name-fuzzy', 'empty-query')
EDGE_BOT_SEARCH_NAME_SOURCE_PATH = '/bots/search?q=CEO&offset=0&limit=20'
EDGE_BOT_SEARCH_EMPTY_SOURCE_PATH = '/bots/search?limit=5'
EDGE_BOT_SEARCH_NAME_PUBLIC_PATH = ('/api/v1/collaboration/bots/{driver_actor_id}'
                                    '/candidates/search?q=CEO&purpose=discovery')
EDGE_BOT_SEARCH_EMPTY_PUBLIC_PATH = ('/api/v1/collaboration/bots/{driver_actor_id}'
                                     '/candidates/search?purpose=discovery')
EDGE_BOT_SEARCH_CANDIDATES_ADAPTER_METHODS = (
    'read_bot_search_name_fuzzy',
    'read_bot_search_empty_query',
)


@dataclass(frozen=True)
class EdgeBotSearchCandidatesRequirement:
    requirement_id: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        expected_source = (EDGE_BOT_SEARCH_NAME_SOURCE_PATH
                            if self.requirement_id == EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0]
                            else EDGE_BOT_SEARCH_EMPTY_SOURCE_PATH)
        expected_public = (EDGE_BOT_SEARCH_NAME_PUBLIC_PATH
                           if self.requirement_id == EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0]
                           else EDGE_BOT_SEARCH_EMPTY_PUBLIC_PATH)
        expected_method = (EDGE_BOT_SEARCH_CANDIDATES_ADAPTER_METHODS[0]
                           if self.requirement_id == EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0]
                           else EDGE_BOT_SEARCH_CANDIDATES_ADAPTER_METHODS[1])
        if (self.requirement_id not in EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS
                or self.source_path != expected_source
                or self.public_path != expected_public
                or self.adapter_method != expected_method):
            raise RunStateError(f'Edge Bot-search candidate requirement {self.requirement_id!r} is invalid')


@dataclass(frozen=True)
class EdgeBotSearchCandidatesCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[EdgeBotSearchCandidatesRequirement, ...]
    selected_obligations: tuple[str, ...]
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != EDGE_BOT_SEARCH_CANDIDATES_CASE_ID or self.parent_id != PARENT
                or tuple(row.requirement_id for row in self.requirements)
                   != EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS
                or tuple(row.adapter_method for row in self.requirements)
                   != EDGE_BOT_SEARCH_CANDIDATES_ADAPTER_METHODS
                or self.selected_obligations != EDGE_BOT_SEARCH_CANDIDATES_SELECTED_OBLIGATIONS
                or self.local_executor_status != 'LOCAL_EXECUTOR_ONLY'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('Edge Bot-search candidate slice is incomplete')
        for requirement in self.requirements:
            requirement.validate()


def edge_bot_search_candidates_case_plan() -> EdgeBotSearchCandidatesCasePlan:
    full_plan = edge_search_case_plan()
    source_by_id = {row.requirement_id: row for row in full_plan.requirements}
    requirements = []
    for requirement_id, source_path in (
        (EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0], EDGE_BOT_SEARCH_NAME_SOURCE_PATH),
        (EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[1], EDGE_BOT_SEARCH_EMPTY_SOURCE_PATH),
    ):
        source = source_by_id.get(requirement_id)
        if source is None or source.path != source_path:
            raise RunStateError('Edge Bot-search candidate source obligation changed')
        public_path = (EDGE_BOT_SEARCH_NAME_PUBLIC_PATH
                       if requirement_id == EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0]
                       else EDGE_BOT_SEARCH_EMPTY_PUBLIC_PATH)
        requirements.append(EdgeBotSearchCandidatesRequirement(
            requirement_id=requirement_id, source_path=source.path,
            public_path=public_path, adapter_method=source.adapter_method,
        ))
    plan = EdgeBotSearchCandidatesCasePlan(
        case_id=EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
        parent_id=PARENT,
        requirements=tuple(requirements),
        selected_obligations=EDGE_BOT_SEARCH_CANDIDATES_SELECTED_OBLIGATIONS,
        local_executor_status='LOCAL_EXECUTOR_ONLY',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def edge_bot_search_candidates_adaptability() -> dict:
    plan = edge_bot_search_candidates_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_ids': [row.requirement_id for row in plan.requirements],
        'selected_obligations': list(plan.selected_obligations),
        'environments': ['owned-local-read-spine', 'singlebox-candidate-slice',
                          'preprod-candidate-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The candidate-search slice covers only name-fuzzy and empty-query; the other ten '
                    'read contracts and the separately admitted TC fixture mutation remain unqualified.'),
    }


def validate_edge_bot_search_candidates_adapter(adapter: object) -> None:
    plan = edge_bot_search_candidates_case_plan()
    missing = [method for method in EDGE_BOT_SEARCH_CANDIDATES_ADAPTER_METHODS
               if not callable(getattr(adapter, method, None))]
    if missing:
        raise RunStateError('Edge Bot-search candidate adapter is missing methods: ' + ', '.join(missing))


def _validate_edge_bot_search_candidates_result(
        requirement: EdgeBotSearchCandidatesRequirement,
        result: AdapterCall) -> None:
    data = result.data
    evidence = result.evidence
    expected_modes = ({'semantic', 'name_fallback'}
                      if requirement.requirement_id == EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0]
                      else {'empty_query'})
    path = evidence.get('path')
    path_suffix = ('/candidates/search?q=CEO&purpose=discovery'
                   if requirement.requirement_id == EDGE_BOT_SEARCH_CANDIDATES_REQUIREMENT_IDS[0]
                   else '/candidates/search?purpose=discovery')
    if (not isinstance(data, dict)
            or type(data.get('item_count')) is not int
            or not 0 <= data['item_count'] <= 20
            or data.get('search_mode') not in expected_modes):
        raise RunStateError('Edge Bot-search candidate result does not satisfy the bounded contract')
    if (not isinstance(path, str)
            or not path.startswith('/api/v1/collaboration/bots/')
            or not path.endswith(path_suffix)):
        raise RunStateError('Edge Bot-search candidate evidence path is outside the reviewed route')
    if (evidence.get('api_face') != 'internal-v1'
            or evidence.get('http_method') != 'GET'
            or evidence.get('http_status') != 200
            or evidence.get('fixture_effect') != 'NONE'
            or evidence.get('identity') != 'AUTHENTICATED_HUMAN_COOKIE'):
        raise RunStateError('Edge Bot-search candidate evidence violates the internal-v1 contract')


def execute_edge_bot_search_candidates_case(adapter: object) -> CaseExecution:
    """Run only the two candidate-search obligations in reviewed order."""
    plan = edge_bot_search_candidates_case_plan()
    validate_edge_bot_search_candidates_adapter(adapter)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for requirement in plan.requirements:
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
            _validate_edge_bot_search_candidates_result(requirement, result)
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='PASS',
                                     data=result.data, evidence=dict(result.evidence),
                                     error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(requirement_id=requirement.requirement_id,
                                     method=method_name, status='NOT_RUN',
                                     data=None, evidence={}, error_type=error_type))
    execution = CaseExecution(
        case_id=plan.case_id, parent_id=plan.parent_id,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=tuple(steps), completed_count=completed, total_count=len(steps),
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
