"""Reviewed Agent-network source map and its public-v1 read slices."""
import hashlib
from dataclasses import dataclass
from pathlib import Path
import re

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = 'LOCAL-NETWORK-01'
PARENT = 'story_user_prepares_agent_network'
SUITE = 'owned-local-network-v1'
REQUIREMENTS = tuple('NETWORK.' + name for name in (
    'platform', 'actor-list', 'actor-search', 'actor-status', 'bots-my',
    'bots-paged', 'bots-query', 'register-onboard'))
FUNCTIONS = ('_story_platform_entrypoints', 'test_actor_list', 'test_actor_search',
             'test_actor_put_status', 'test_bots_my', 'test_bots_paged',
             'test_bots_query', '_story_register_and_onboard_owned_agent')


def network_source_binding() -> dict:
    """Fail on drift or missing/reordered obligations; never rewrite old receipts."""
    try:
        value = read(ROOT / 'network-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'sources', 'requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'stories.sh', 'actor.sh'}
                or [r['id'] for r in value['requirements']] != list(REQUIREMENTS)
                or [r['function'] for r in value['requirements']] != list(FUNCTIONS)):
            raise ValueError
        texts = {}
        for name, digest in value['sources'].items():
            path = ROOT.parent / 'e2e-test' / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError
            texts[name] = raw.decode('utf-8')
        for item in value['requirements']:
            if (set(item) != {'id', 'function', 'source', 'expectation'}
                    or item['source'] not in texts or not isinstance(item['expectation'], str)
                    or not 1 <= len(item['expectation']) <= 2048
                    or len(re.findall(r'^' + re.escape(item['function']) + r'\(\) \{$',
                                      texts[item['source']], re.M)) != 1):
                raise ValueError
        start = texts['stories.sh'].index(PARENT + '() {')
        end = texts['stories.sh'].index('\n}', start)
        actual = re.findall(r'^    ((?:test_|_story_)[A-Za-z0-9_]+)(?:\s|$)',
                            texts['stories.sh'][start:end], re.M)
        if actual != list(FUNCTIONS):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('Agent-network original source or complete obligation map changed') from None


# Public-v1 slice for the already-reviewed local Agent-network requirement.
NETWORK_BOTS_QUERY_CASE_ID = 'PUBLIC-NETWORK-BOTS-QUERY-01'
SOURCE_REQUIREMENT_ID = 'NETWORK.bots-query'
SOURCE_FUNCTION = 'test_bots_query'
LEGACY_PATH = '/bots/query'
PUBLIC_PATH = '/openapi/v1/collaboration/bots/query'
ADAPTER_METHOD = 'read_directory_bots'


@dataclass(frozen=True)
class NetworkBotsQueryRequirement:
    requirement_id: str
    source_function: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != SOURCE_REQUIREMENT_ID
                or self.source_function != SOURCE_FUNCTION
                or self.source_path != LEGACY_PATH
                or self.public_path != PUBLIC_PATH
                or self.adapter_method != ADAPTER_METHOD):
            raise RunStateError('public Bot bulk-query requirement is invalid')


@dataclass(frozen=True)
class NetworkBotsQueryCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkBotsQueryRequirement
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != NETWORK_BOTS_QUERY_CASE_ID or self.parent_id != PARENT
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('public Bot bulk-query case is incomplete')
        self.requirement.validate()


def network_bots_query_case_plan() -> NetworkBotsQueryCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == SOURCE_REQUIREMENT_ID and row['function'] == SOURCE_FUNCTION),
               None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('Bot bulk-query source obligation changed')
    plan = NetworkBotsQueryCasePlan(
        case_id=NETWORK_BOTS_QUERY_CASE_ID,
        parent_id=PARENT,
        requirement=NetworkBotsQueryRequirement(
            requirement_id=SOURCE_REQUIREMENT_ID,
            source_function=SOURCE_FUNCTION,
            source_path=LEGACY_PATH,
            public_path=PUBLIC_PATH,
            adapter_method=ADAPTER_METHOD,
        ),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_bots_query_adaptability() -> dict:
    plan = network_bots_query_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'environments': ['owned-local-full-story', 'singlebox-public-slice', 'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The local full Agent-network executor is already reviewed; public-v1 '
                    'execution requires the exact bots/query binding and Cookie context.'),
    }


def validate_network_bots_query_adapter(adapter: object) -> None:
    plan = network_bots_query_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('Bot bulk-query adapter is missing read_directory_bots')


def _validate_result(plan: NetworkBotsQueryCasePlan, result: AdapterCall) -> None:
    evidence = result.evidence
    data = result.data
    returned_ids = None if not isinstance(data, dict) else data.get('returned_ids')
    target_ids = evidence.get('target_actor_ids')
    if (not isinstance(data, dict)
            or data.get('item_count') != 2
            or not isinstance(returned_ids, list)
            or len(returned_ids) != 2
            or any(not isinstance(value, str) or not value for value in returned_ids)
            or not isinstance(target_ids, list)
            or len(target_ids) != 2
            or len(set(returned_ids)) != len(set(target_ids))
            or set(returned_ids) != set(target_ids)):
        raise RunStateError('Bot bulk-query result does not resolve both reviewed targets')
    if (evidence.get('http_method') != 'POST'
            or evidence.get('path') != plan.requirement.public_path
            or evidence.get('http_status') != 200
            or evidence.get('fixture_effect') != 'NONE'
            or evidence.get('identity') != 'AUTHENTICATED_HUMAN_COOKIE'):
        raise RunStateError('Bot bulk-query evidence violates the reviewed public contract')


def execute_network_bots_query_case(adapter: object) -> CaseExecution:
    plan = network_bots_query_case_plan()
    validate_network_bots_query_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('Bot bulk-query method did not return AdapterCall')
        result.validate()
        _validate_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=NETWORK_BOTS_QUERY_CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Second public-v1 slice from the same reviewed local Agent-network Story.
NETWORK_BOTS_MINE_CASE_ID = 'PUBLIC-NETWORK-BOTS-MINE-01'
NETWORK_BOTS_MINE_REQUIREMENT_ID = 'NETWORK.bots-my'
NETWORK_BOTS_MINE_SOURCE_FUNCTION = 'test_bots_my'
NETWORK_BOTS_MINE_LEGACY_PATH = '/bots/my'
NETWORK_BOTS_MINE_PUBLIC_PATH = '/openapi/v1/collaboration/bots/mine?offset=0&limit=20'
NETWORK_BOTS_MINE_ADAPTER_METHOD = 'read_owned_directory_bots'


@dataclass(frozen=True)
class NetworkBotsMineRequirement:
    requirement_id: str
    source_function: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != NETWORK_BOTS_MINE_REQUIREMENT_ID
                or self.source_function != NETWORK_BOTS_MINE_SOURCE_FUNCTION
                or self.source_path != NETWORK_BOTS_MINE_LEGACY_PATH
                or self.public_path != NETWORK_BOTS_MINE_PUBLIC_PATH
                or self.adapter_method != NETWORK_BOTS_MINE_ADAPTER_METHOD):
            raise RunStateError('public owned-Bot directory requirement is invalid')


@dataclass(frozen=True)
class NetworkBotsMineCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkBotsMineRequirement
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != NETWORK_BOTS_MINE_CASE_ID or self.parent_id != PARENT
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('public owned-Bot directory case is incomplete')
        self.requirement.validate()


def network_bots_mine_case_plan() -> NetworkBotsMineCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == NETWORK_BOTS_MINE_REQUIREMENT_ID
                and row['function'] == NETWORK_BOTS_MINE_SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('owned-Bot directory source obligation changed')
    plan = NetworkBotsMineCasePlan(
        case_id=NETWORK_BOTS_MINE_CASE_ID,
        parent_id=PARENT,
        requirement=NetworkBotsMineRequirement(
            requirement_id=NETWORK_BOTS_MINE_REQUIREMENT_ID,
            source_function=NETWORK_BOTS_MINE_SOURCE_FUNCTION,
            source_path=NETWORK_BOTS_MINE_LEGACY_PATH,
            public_path=NETWORK_BOTS_MINE_PUBLIC_PATH,
            adapter_method=NETWORK_BOTS_MINE_ADAPTER_METHOD,
        ),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_bots_mine_adaptability() -> dict:
    plan = network_bots_mine_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'environments': ['owned-local-full-story', 'singlebox-public-slice', 'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The local full Agent-network executor is already reviewed; public-v1 '
                    'execution requires the exact bots/mine binding and Cookie context.'),
    }


def validate_network_bots_mine_adapter(adapter: object) -> None:
    plan = network_bots_mine_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('owned-Bot directory adapter is missing read_owned_directory_bots')


def _validate_mine_result(plan: NetworkBotsMineCasePlan, result: AdapterCall) -> None:
    data = result.data
    evidence = result.evidence
    if (not isinstance(data, dict)
            or type(data.get('item_count')) is not int or not 1 <= data['item_count'] <= 20
            or type(data.get('total')) is not int or data['total'] < 1
            or type(data.get('offset')) is not int or data['offset'] != 0
            or type(data.get('limit')) is not int or data['limit'] != 20):
        raise RunStateError('owned-Bot directory result does not return a reviewed page')
    if (evidence.get('http_method') != 'GET'
            or evidence.get('path') != plan.requirement.public_path
            or evidence.get('http_status') != 200
            or evidence.get('fixture_effect') != 'NONE'
            or evidence.get('identity') != 'AUTHENTICATED_HUMAN_COOKIE'):
        raise RunStateError('owned-Bot directory evidence violates the reviewed public contract')


def execute_network_bots_mine_case(adapter: object) -> CaseExecution:
    plan = network_bots_mine_case_plan()
    validate_network_bots_mine_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('owned-Bot directory method did not return AdapterCall')
        result.validate()
        _validate_mine_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=NETWORK_BOTS_MINE_CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution



# Third public-v1 slice from the same reviewed local Agent-network Story. The
# legacy directory may be legitimately empty, so this contract validates page
# shape, not a nonempty result.
NETWORK_ACTOR_LIST_CASE_ID = 'PUBLIC-NETWORK-ACTOR-LIST-01'
NETWORK_ACTOR_LIST_REQUIREMENT_ID = 'NETWORK.actor-list'
NETWORK_ACTOR_LIST_SOURCE_FUNCTION = 'test_actor_list'
NETWORK_ACTOR_LIST_LEGACY_PATH = ('/actors/list?current_bot_uuid=$BOT_CEO_UUID'
                                  '&cooperatable_only=false&limit=20')
NETWORK_ACTOR_LIST_PUBLIC_PATH = ('/openapi/v1/collaboration/bots/{driver_actor_id}'
                                  '/candidates?purpose=discovery&offset=0&limit=20')
NETWORK_ACTOR_LIST_ADAPTER_METHOD = 'read_actor_directory'


@dataclass(frozen=True)
class NetworkActorListRequirement:
    requirement_id: str
    source_function: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != NETWORK_ACTOR_LIST_REQUIREMENT_ID
                or self.source_function != NETWORK_ACTOR_LIST_SOURCE_FUNCTION
                or self.source_path != NETWORK_ACTOR_LIST_LEGACY_PATH
                or self.public_path != NETWORK_ACTOR_LIST_PUBLIC_PATH
                or self.adapter_method != NETWORK_ACTOR_LIST_ADAPTER_METHOD):
            raise RunStateError('public actor-directory requirement is invalid')


@dataclass(frozen=True)
class NetworkActorListCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkActorListRequirement
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != NETWORK_ACTOR_LIST_CASE_ID or self.parent_id != PARENT
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('public actor-directory case is incomplete')
        self.requirement.validate()


def network_actor_list_case_plan() -> NetworkActorListCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == NETWORK_ACTOR_LIST_REQUIREMENT_ID
                and row['function'] == NETWORK_ACTOR_LIST_SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('actor-directory source obligation changed')
    plan = NetworkActorListCasePlan(
        case_id=NETWORK_ACTOR_LIST_CASE_ID,
        parent_id=PARENT,
        requirement=NetworkActorListRequirement(
            requirement_id=NETWORK_ACTOR_LIST_REQUIREMENT_ID,
            source_function=NETWORK_ACTOR_LIST_SOURCE_FUNCTION,
            source_path=NETWORK_ACTOR_LIST_LEGACY_PATH,
            public_path=NETWORK_ACTOR_LIST_PUBLIC_PATH,
            adapter_method=NETWORK_ACTOR_LIST_ADAPTER_METHOD,
        ),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_actor_list_adaptability() -> dict:
    plan = network_actor_list_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'environments': ['owned-local-full-story', 'singlebox-public-slice', 'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The local full Agent-network executor is already reviewed; public-v1 '
                    'human-managed candidate execution requires the exact driver-Bot perspective.'),
    }


def validate_network_actor_list_adapter(adapter: object) -> None:
    plan = network_actor_list_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('actor-directory adapter is missing read_actor_directory')


def _validate_actor_list_result(plan: NetworkActorListCasePlan, result: AdapterCall) -> None:
    data = result.data
    evidence = result.evidence
    if (not isinstance(data, dict)
            or type(data.get('item_count')) is not int or not 0 <= data['item_count'] <= 20
            or type(data.get('total')) is not int or data['total'] < data['item_count']
            or type(data.get('offset')) is not int or data.get('offset') != 0
            or type(data.get('limit')) is not int or data.get('limit') != 20):
        raise RunStateError('actor-directory result does not return a reviewed page')
    path = evidence.get('path')
    prefix, suffix = ('/openapi/v1/collaboration/bots/', '/candidates?purpose=discovery&offset=0&limit=20')
    if (not isinstance(path, str) or not path.startswith(prefix) or not path.endswith(suffix)
            or not path[len(prefix):-len(suffix)]):
        raise RunStateError('actor-directory evidence path is outside the reviewed candidate route')
    if (evidence.get('http_method') != 'GET'
            or evidence.get('http_status') != 200
            or evidence.get('fixture_effect') != 'NONE'
            or evidence.get('identity') != 'AUTHENTICATED_HUMAN_COOKIE'):
        raise RunStateError('actor-directory evidence violates the reviewed public contract')


def execute_network_actor_list_case(adapter: object) -> CaseExecution:
    plan = network_actor_list_case_plan()
    validate_network_actor_list_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('actor-directory method did not return AdapterCall')
        result.validate()
        _validate_actor_list_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=NETWORK_ACTOR_LIST_CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Fourth remote slice from the same reviewed local Agent-network Story. OCB
# station deployment exposes the exact legacy search equivalent on internal-v1:
# /api/v1/collaboration/bots/{actor}/candidates/search. This is not silently
# rewritten to the public-v1 candidate page.
NETWORK_ACTOR_SEARCH_CASE_ID = 'PUBLIC-NETWORK-ACTOR-SEARCH-01'
NETWORK_ACTOR_SEARCH_REQUIREMENT_ID = 'NETWORK.actor-search'
NETWORK_ACTOR_SEARCH_SOURCE_FUNCTION = 'test_actor_search'
NETWORK_ACTOR_SEARCH_LEGACY_PATH = ('/actors/search?q=%E4%BA%A7%E5%93%81'
                                    '&current_bot_uuid=$BOT_CEO_UUID&cooperatable_only=false')
NETWORK_ACTOR_SEARCH_PUBLIC_PATH = ('/api/v1/collaboration/bots/{driver_actor_id}'
                                    '/candidates/search?q=%E4%BA%A7%E5%93%81&purpose=discovery')
NETWORK_ACTOR_SEARCH_ADAPTER_METHOD = 'read_actor_search'


@dataclass(frozen=True)
class NetworkActorSearchRequirement:
    requirement_id: str
    source_function: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != NETWORK_ACTOR_SEARCH_REQUIREMENT_ID
                or self.source_function != NETWORK_ACTOR_SEARCH_SOURCE_FUNCTION
                or self.source_path != NETWORK_ACTOR_SEARCH_LEGACY_PATH
                or self.public_path != NETWORK_ACTOR_SEARCH_PUBLIC_PATH
                or self.adapter_method != NETWORK_ACTOR_SEARCH_ADAPTER_METHOD):
            raise RunStateError('internal actor-search requirement is invalid')


@dataclass(frozen=True)
class NetworkActorSearchCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkActorSearchRequirement
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != NETWORK_ACTOR_SEARCH_CASE_ID or self.parent_id != PARENT
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('internal actor-search case is incomplete')
        self.requirement.validate()


def network_actor_search_case_plan() -> NetworkActorSearchCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == NETWORK_ACTOR_SEARCH_REQUIREMENT_ID
                and row['function'] == NETWORK_ACTOR_SEARCH_SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('actor-search source obligation changed')
    plan = NetworkActorSearchCasePlan(
        case_id=NETWORK_ACTOR_SEARCH_CASE_ID,
        parent_id=PARENT,
        requirement=NetworkActorSearchRequirement(
            requirement_id=NETWORK_ACTOR_SEARCH_REQUIREMENT_ID,
            source_function=NETWORK_ACTOR_SEARCH_SOURCE_FUNCTION,
            source_path=NETWORK_ACTOR_SEARCH_LEGACY_PATH,
            public_path=NETWORK_ACTOR_SEARCH_PUBLIC_PATH,
            adapter_method=NETWORK_ACTOR_SEARCH_ADAPTER_METHOD,
        ),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_actor_search_adaptability() -> dict:
    plan = network_actor_search_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'environments': ['owned-local-full-story', 'singlebox-internal-slice',
                          'preprod-internal-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The local full Agent-network executor is already reviewed; remote search '
                    'requires the exact internal-v1 candidates/search route and Cookie context.'),
    }


def validate_network_actor_search_adapter(adapter: object) -> None:
    plan = network_actor_search_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('actor-search adapter is missing read_actor_search')


def _validate_actor_search_result(plan: NetworkActorSearchCasePlan,
                                  result: AdapterCall) -> None:
    data = result.data
    evidence = result.evidence
    if (not isinstance(data, dict)
            or type(data.get('item_count')) is not int
            or not 0 <= data['item_count'] <= 20
            or data.get('search_mode') not in {'empty_query', 'semantic', 'name_fallback'}):
        raise RunStateError('actor-search result does not satisfy the reviewed contract')
    path = evidence.get('path')
    prefix = '/api/v1/collaboration/bots/'
    suffix = '/candidates/search?q=%E4%BA%A7%E5%93%81&purpose=discovery'
    if (not isinstance(path, str) or not path.startswith(prefix) or not path.endswith(suffix)
            or not path[len(prefix):-len(suffix)]):
        raise RunStateError('actor-search evidence path is outside the reviewed route')
    if (evidence.get('api_face') != 'internal-v1'
            or evidence.get('http_method') != 'GET'
            or evidence.get('http_status') != 200
            or evidence.get('fixture_effect') != 'NONE'
            or evidence.get('identity') != 'AUTHENTICATED_HUMAN_COOKIE'):
        raise RunStateError('actor-search evidence violates the internal-v1 contract')


def execute_network_actor_search_case(adapter: object) -> CaseExecution:
    plan = network_actor_search_case_plan()
    validate_network_actor_search_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('actor-search method did not return AdapterCall')
        result.validate()
        _validate_actor_search_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=NETWORK_ACTOR_SEARCH_CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Fifth remote slice from the same reviewed local Agent-network Story. The
# station has no legacy /bots/paged route. Its reviewed public operation that
# pages visible physical Bots is the Human-actor candidate page. This is not
# mapped to /bots/mine and does not invent a global provider page.
NETWORK_BOTS_PAGED_CASE_ID = 'PUBLIC-NETWORK-BOTS-PAGED-01'
NETWORK_BOTS_PAGED_REQUIREMENT_ID = 'NETWORK.bots-paged'
NETWORK_BOTS_PAGED_SOURCE_FUNCTION = 'test_bots_paged'
NETWORK_BOTS_PAGED_LEGACY_PATH = '/bots/paged?limit=5&offset=0'
NETWORK_BOTS_PAGED_PUBLIC_PATH = ('/openapi/v1/collaboration/bots/{human_actor_id}'
                                   '/candidates?purpose=discovery&offset=0&limit=5')
NETWORK_BOTS_PAGED_ADAPTER_METHOD = 'read_bots_paged'


@dataclass(frozen=True)
class NetworkBotsPagedRequirement:
    requirement_id: str
    source_function: str
    source_path: str
    public_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != NETWORK_BOTS_PAGED_REQUIREMENT_ID
                or self.source_function != NETWORK_BOTS_PAGED_SOURCE_FUNCTION
                or self.source_path != NETWORK_BOTS_PAGED_LEGACY_PATH
                or self.public_path != NETWORK_BOTS_PAGED_PUBLIC_PATH
                or self.adapter_method != NETWORK_BOTS_PAGED_ADAPTER_METHOD):
            raise RunStateError('public Bot page requirement is invalid')


@dataclass(frozen=True)
class NetworkBotsPagedCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkBotsPagedRequirement
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != NETWORK_BOTS_PAGED_CASE_ID or self.parent_id != PARENT
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('public Bot page case is incomplete')
        self.requirement.validate()


def network_bots_paged_case_plan() -> NetworkBotsPagedCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == NETWORK_BOTS_PAGED_REQUIREMENT_ID
                and row['function'] == NETWORK_BOTS_PAGED_SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('Bot page source obligation changed')
    plan = NetworkBotsPagedCasePlan(
        case_id=NETWORK_BOTS_PAGED_CASE_ID,
        parent_id=PARENT,
        requirement=NetworkBotsPagedRequirement(
            requirement_id=NETWORK_BOTS_PAGED_REQUIREMENT_ID,
            source_function=NETWORK_BOTS_PAGED_SOURCE_FUNCTION,
            source_path=NETWORK_BOTS_PAGED_LEGACY_PATH,
            public_path=NETWORK_BOTS_PAGED_PUBLIC_PATH,
            adapter_method=NETWORK_BOTS_PAGED_ADAPTER_METHOD,
        ),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_bots_paged_adaptability() -> dict:
    plan = network_bots_paged_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'environments': ['owned-local-full-story', 'singlebox-public-slice', 'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The local full Agent-network executor is already reviewed; the station Bot-page '
                    'slice requires the exact human-actor candidate page and Cookie context.'),
    }


def validate_network_bots_paged_adapter(adapter: object) -> None:
    plan = network_bots_paged_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('Bot page adapter is missing read_bots_paged')


def _validate_bots_paged_result(plan: NetworkBotsPagedCasePlan, result: AdapterCall) -> None:
    data = result.data
    evidence = result.evidence
    if (not isinstance(data, dict)
            or type(data.get('item_count')) is not int or not 1 <= data['item_count'] <= 5
            or type(data.get('total')) is not int or data['total'] < 1
            or type(data.get('offset')) is not int or data.get('offset') != 0
            or type(data.get('limit')) is not int or data.get('limit') != 5):
        raise RunStateError('Bot page result does not return the reviewed first page')
    path = evidence.get('path')
    prefix = '/openapi/v1/collaboration/bots/'
    suffix = '/candidates?purpose=discovery&offset=0&limit=5'
    if (not isinstance(path, str) or not path.startswith(prefix) or not path.endswith(suffix)
            or not path[len(prefix):-len(suffix)].startswith('human_')):
        raise RunStateError('Bot page evidence path is outside the reviewed human-actor candidate page')
    if (evidence.get('http_method') != 'GET'
            or evidence.get('http_status') != 200
            or evidence.get('fixture_effect') != 'NONE'
            or evidence.get('identity') != 'AUTHENTICATED_HUMAN_COOKIE'):
        raise RunStateError('Bot page evidence violates the reviewed public contract')


def execute_network_bots_paged_case(adapter: object) -> CaseExecution:
    plan = network_bots_paged_case_plan()
    validate_network_bots_paged_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('Bot page method did not return AdapterCall')
        result.validate()
        _validate_bots_paged_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=NETWORK_BOTS_PAGED_CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Sixth cross-environment slice from the same reviewed local Agent-network
# Story. OCB exposes the panel manifest and asset under internal-v1, so one
# binding works for local BCS and the station gateway. This slice deliberately
# owns only manifest, Human identity, and the bound JavaScript asset; health,
# repair inspection, and missing-secret handling remain local full-Story gaps.
NETWORK_PLATFORM_ASSET_CASE_ID = 'PUBLIC-NETWORK-PLATFORM-ASSET-01'
NETWORK_PLATFORM_ASSET_REQUIREMENT_ID = 'NETWORK.platform'
NETWORK_PLATFORM_ASSET_SOURCE_FUNCTION = '_story_platform_entrypoints'
NETWORK_PLATFORM_ASSET_SOURCE_PATHS = (
    '/manifest', '/me',
    '/api/v1/collaboration/assets/bcsPanel/index.umd.js',
)
NETWORK_PLATFORM_ASSET_MANIFEST_PATH = '/api/v1/collaboration/manifest'
NETWORK_PLATFORM_ASSET_IDENTITY_PATH = '/openapi/v1/auth/user'
NETWORK_PLATFORM_ASSET_ADAPTER_METHOD = 'read_platform_manifest_identity_asset'


@dataclass(frozen=True)
class NetworkPlatformAssetRequirement:
    requirement_id: str
    source_function: str
    source_paths: tuple[str, ...]
    manifest_path: str
    identity_path: str
    adapter_method: str

    def validate(self) -> None:
        if (self.requirement_id != NETWORK_PLATFORM_ASSET_REQUIREMENT_ID
                or self.source_function != NETWORK_PLATFORM_ASSET_SOURCE_FUNCTION
                or self.source_paths != NETWORK_PLATFORM_ASSET_SOURCE_PATHS
                or self.manifest_path != NETWORK_PLATFORM_ASSET_MANIFEST_PATH
                or self.identity_path != NETWORK_PLATFORM_ASSET_IDENTITY_PATH
                or self.adapter_method != NETWORK_PLATFORM_ASSET_ADAPTER_METHOD):
            raise RunStateError('platform asset requirement is invalid')


@dataclass(frozen=True)
class NetworkPlatformAssetCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkPlatformAssetRequirement
    selected_obligations: tuple[str, ...]
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != NETWORK_PLATFORM_ASSET_CASE_ID or self.parent_id != PARENT
                or self.selected_obligations != ('manifest', 'identity', 'asset')
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('platform asset case is incomplete')
        self.requirement.validate()


def network_platform_asset_case_plan() -> NetworkPlatformAssetCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == NETWORK_PLATFORM_ASSET_REQUIREMENT_ID
                and row['function'] == NETWORK_PLATFORM_ASSET_SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('platform asset source obligation changed')
    plan = NetworkPlatformAssetCasePlan(
        case_id=NETWORK_PLATFORM_ASSET_CASE_ID,
        parent_id=PARENT,
        requirement=NetworkPlatformAssetRequirement(
            requirement_id=NETWORK_PLATFORM_ASSET_REQUIREMENT_ID,
            source_function=NETWORK_PLATFORM_ASSET_SOURCE_FUNCTION,
            source_paths=NETWORK_PLATFORM_ASSET_SOURCE_PATHS,
            manifest_path=NETWORK_PLATFORM_ASSET_MANIFEST_PATH,
            identity_path=NETWORK_PLATFORM_ASSET_IDENTITY_PATH,
            adapter_method=NETWORK_PLATFORM_ASSET_ADAPTER_METHOD,
        ),
        selected_obligations=('manifest', 'identity', 'asset'),
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_platform_asset_adaptability() -> dict:
    plan = network_platform_asset_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'selected_obligations': list(plan.selected_obligations),
        'environments': ['owned-local-full-story', 'singlebox-internal-slice',
                          'preprod-internal-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The local full Agent-network executor is reviewed, but this remote slice covers only '
                    'manifest, Human identity, and the bound asset. Health, repair inspection, missing-secret, '
                    'and mutation obligations remain unqualified.'),
    }


def validate_network_platform_asset_adapter(adapter: object) -> None:
    plan = network_platform_asset_case_plan()
    method = getattr(adapter, plan.requirement.adapter_method, None)
    if not callable(method):
        raise RunStateError('platform asset adapter is missing read_platform_manifest_identity_asset')


def _validate_platform_asset_result(plan: NetworkPlatformAssetCasePlan,
                                    result: AdapterCall) -> None:
    data = result.data
    evidence = result.evidence
    if (not isinstance(data, dict)
            or set(data) != {'schema_version', 'env', 'panel_bundle_name', 'panel_asset_url',
                              'panel_asset_size', 'panel_asset_sha256', 'user_id'}
            or data.get('schema_version') != 1
            or not isinstance(data.get('env'), str) or not data['env']
            or data.get('panel_bundle_name') != 'bcsPanel'
            or not isinstance(data.get('panel_asset_url'), str)
            or not data['panel_asset_url']
            or type(data.get('panel_asset_size')) is not int
            or not 1024 <= data['panel_asset_size'] <= 10_000_000
            or not isinstance(data.get('panel_asset_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', data['panel_asset_sha256'])
            or not isinstance(data.get('user_id'), str) or not data['user_id']):
        raise RunStateError('platform asset result does not satisfy the reviewed slice')
    records = evidence.get('requests')
    if (evidence.get('fixture_effect') != 'NONE'
            or not isinstance(records, list) or len(records) != 3
            or [row.get('operation') for row in records] != [
                'environment.read', 'identity.read', 'network.platform_asset.read']
            or records[0].get('path') != plan.requirement.manifest_path
            or records[1].get('path') != plan.requirement.identity_path
            or records[2].get('path') != data['panel_asset_url']
            or any(row.get('http_status') != 200 for row in records)):
        raise RunStateError('platform asset evidence is outside the reviewed transport contract')
    if (records[0].get('credentials_sent') is not True
            or records[1].get('credentials_sent') is not True
            or records[2].get('credentials_sent') is not False
            or any(row.get('authorization_sent') for row in records)):
        raise RunStateError('platform slice requires Human Cookie JSON reads and an unauthenticated asset')
    if any(row.get('method') != 'GET' for row in records):
        raise RunStateError('platform slice must remain read-only')


def execute_network_platform_asset_case(adapter: object) -> CaseExecution:
    plan = network_platform_asset_case_plan()
    validate_network_platform_asset_adapter(adapter)
    method = getattr(adapter, plan.requirement.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('platform asset method did not return AdapterCall')
        result.validate()
        _validate_platform_asset_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='PASS', data=result.data,
                           evidence=dict(result.evidence), error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.requirement.adapter_method,
                           status='NOT_RUN', data=None, evidence={},
                           error_type=error_type)
    execution = CaseExecution(
        case_id=NETWORK_PLATFORM_ASSET_CASE_ID, parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
