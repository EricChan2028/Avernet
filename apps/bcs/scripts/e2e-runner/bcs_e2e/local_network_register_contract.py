"""Public-v1 register-onboard slice from the reviewed Agent-network Story.

The slice freezes the reviewed register core of the Agent-network
Story that transfers faithfully to the public-v1 Gateway/Singlebox
face: the Human mints a register token, the minted token anonymously
registers a new agent (the token is its sole product credential), and
the owning Human reads the registered agent back to prove the reviewed
name and Human ownership.  On the deployed preprod Gateway, the create
may carry the Human Cookie/Origin only as explicit browser ingress
context; it still never carries a Bot Authorization header and the
reviewed product credential remains the minted register token. The reviewed BCN-only closures (Bot-token
capability onboarding, the browser onboarding URL, administrator
re-onboard, owner delete and the post-delete 404) have no public-v1
gateway route and stay documented community-face obligations instead
of being normalized to PASS; the registered temporary Bot is retained
as an owned Human resource on the public-v1 face because that face
has no Bot delete.
"""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_network_contract import (
    PARENT,
    network_source_binding,
)
from .route_bindings import (
    NETWORK_REGISTER_CREATE_PATH,
    NETWORK_REGISTER_READBACK_PATH,
    NETWORK_REGISTER_TOKEN_PATH,
)
from .run_state import RunStateError

CASE_ID = 'PUBLIC-NETWORK-REGISTER-01'
SUITE = 'public-network-register-v1'
SOURCE_REQUIREMENT_ID = 'NETWORK.register-onboard'
SOURCE_FUNCTION = '_story_register_and_onboard_owned_agent'
ADAPTER_METHOD = 'register_owned_agent'
# Only the reviewed register-token mint, the anonymous token-bound
# create and the owner read-back transfer to the public-v1 face.
PUBLIC_OPERATIONS = ('register.token.read', 'register.bot.create',
                     'register.bot.readback')
COMMUNITY_ONLY_CLOSURES = (
    'register.bot.onboard',
    'register.browser.onboard-url',
    'register.admin.reonboard',
    'register.bot.delete',
    'register.bot.deleted-404',
)
_PUBLIC_PATHS = (NETWORK_REGISTER_TOKEN_PATH, NETWORK_REGISTER_CREATE_PATH,
                 NETWORK_REGISTER_READBACK_PATH)
_REQUEST_METHODS = ('GET', 'POST', 'GET')
_REQUEST_STATUS = (200, 201, 200)
_REQUEST_IDENTITIES = ('AUTHENTICATED_HUMAN_COOKIE',
                        'ANONYMOUS_REGISTER_TOKEN',
                        'AUTHENTICATED_HUMAN_COOKIE')
FIXTURE_EFFECT = 'REGISTERED_TEMPORARY_BOT_RETAINED'


@dataclass(frozen=True)
class NetworkRegisterRequirement:
    requirement_id: str
    source_function: str
    adapter_method: str
    public_operations: tuple[str, ...]
    public_paths: tuple[str, ...]
    community_only_closures: tuple[str, ...]
    expectation: str

    def validate(self) -> None:
        if (self.requirement_id != SOURCE_REQUIREMENT_ID
                or self.source_function != SOURCE_FUNCTION
                or self.adapter_method != ADAPTER_METHOD
                or self.public_operations != PUBLIC_OPERATIONS
                or self.public_paths != _PUBLIC_PATHS
                or self.community_only_closures != COMMUNITY_ONLY_CLOSURES
                or not self.expectation):
            raise RunStateError('public register requirement is invalid')


@dataclass(frozen=True)
class NetworkRegisterCasePlan:
    case_id: str
    parent_id: str
    requirement: NetworkRegisterRequirement
    adapter_method: str
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.requirement.requirement_id != SOURCE_REQUIREMENT_ID
                or self.adapter_method != ADAPTER_METHOD
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False):
            raise RunStateError('public register case is incomplete')
        self.requirement.validate()


def network_register_case_plan() -> NetworkRegisterCasePlan:
    binding = network_source_binding()
    row = next((row for row in binding['requirements']
                if row['id'] == SOURCE_REQUIREMENT_ID
                and row['function'] == SOURCE_FUNCTION), None)
    if row is None or binding['parent'] != PARENT:
        raise RunStateError('Agent-network register source obligation changed')
    plan = NetworkRegisterCasePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirement=NetworkRegisterRequirement(
            requirement_id=SOURCE_REQUIREMENT_ID,
            source_function=SOURCE_FUNCTION,
            adapter_method=ADAPTER_METHOD,
            public_operations=PUBLIC_OPERATIONS,
            public_paths=_PUBLIC_PATHS,
            community_only_closures=COMMUNITY_ONLY_CLOSURES,
            expectation=row['expectation'],
        ),
        adapter_method=ADAPTER_METHOD,
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def network_register_adaptability() -> dict:
    plan = network_register_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_id': plan.requirement.requirement_id,
        'source_function': plan.requirement.source_function,
        'adapter_methods': [plan.adapter_method],
        'public_operations': list(PUBLIC_OPERATIONS),
        'public_paths': list(_PUBLIC_PATHS),
        'community_only_closures': list(COMMUNITY_ONLY_CLOSURES),
        'environments': ['owned-local-full-story', 'singlebox-public-slice',
                         'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('The reviewed register core (Human-minted register token, '
                      'anonymous token-bound create and owner read-back) transfers '
                      'to the public-v1 face. The BCN-only closures (Bot-token '
                      'capability onboarding, browser onboarding URL, administrator '
                      're-onboard, owner delete and the post-delete 404) have no '
                      'public-v1 gateway route and stay blocked community-face '
                      'obligations. The public-v1 face has no Bot delete, so the '
                      'registered temporary Bot is retained as an owned Human '
                      'resource; preprod execution requires an explicit retention or '
                      'reaping disposition before the slice can grant release credit.'),
    }


def validate_network_register_adapter(adapter: object) -> None:
    plan = network_register_case_plan()
    method = getattr(adapter, plan.adapter_method, None)
    if not callable(method):
        raise RunStateError('register adapter is missing ' + ADAPTER_METHOD)


def _validate_result(plan: NetworkRegisterCasePlan, result: AdapterCall) -> None:
    evidence = result.evidence
    data = result.data
    requests = evidence.get('requests')
    if (not isinstance(evidence, dict)
            or evidence.get('transport') != 'public-v1-http'
            or evidence.get('requirement_id') != SOURCE_REQUIREMENT_ID
            or evidence.get('operation') != ADAPTER_METHOD
            or evidence.get('fixture_effect') != FIXTURE_EFFECT
            or not isinstance(requests, list) or len(requests) != 3
            or [row.get('operation') for row in requests] != list(PUBLIC_OPERATIONS)
            or [row.get('method') for row in requests] != list(_REQUEST_METHODS)
            or [row.get('http_status') for row in requests] != list(_REQUEST_STATUS)):
        raise RunStateError('register evidence is outside the reviewed contract')
    create = requests[1]
    if ('[REDACTED-register-token]' not in create.get('path', '')
            or 'token=' not in create.get('path', '')
            or (create.get('credentials_sent') is not False
                and create.get('platform_gateway_browser_context_sent') is not True)
            or create.get('authorization_sent') is not False):
        raise RunStateError(
            'register create evidence must keep the register token as its product '
            'credential; only the explicit preprod Gateway browser context may carry '
            'the Human Cookie')
    for row, identity in zip(requests, _REQUEST_IDENTITIES):
        if row.get('identity') != identity:
            raise RunStateError('register request identity differs from the reviewed contract')
    register_token_digest = evidence.get('register_token_sha256')
    bot_token_digest = evidence.get('bot_token_sha256')
    for digest in (register_token_digest, bot_token_digest):
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in '0123456789abcdef' for character in digest)):
            raise RunStateError('register evidence must carry credential digests only')
    if (list(evidence.get('community_only_closures', [])) != list(COMMUNITY_ONLY_CLOSURES)):
        raise RunStateError('register evidence must document the community-only closures')
    if (not isinstance(data, dict)
            or not isinstance(data.get('bot_uuid'), str) or not data['bot_uuid']
            or not isinstance(data.get('bot_name'), str) or not data['bot_name']
            or data.get('owner_readback') is not True):
        raise RunStateError('register result is outside the reviewed contract')


def execute_network_register_case(adapter: object) -> CaseExecution:
    plan = network_register_case_plan()
    validate_network_register_adapter(adapter)
    method = getattr(adapter, plan.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('register method did not return AdapterCall')
        result.validate()
        _validate_result(plan, result)
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.adapter_method, status='PASS',
                           data=dict(result.data), evidence=dict(result.evidence),
                           error_type=None)
        completed, error_type = 1, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        step = StepOutcome(requirement_id=plan.requirement.requirement_id,
                           method=plan.adapter_method, status='NOT_RUN',
                           data=None, evidence={}, error_type=error_type)
    execution = CaseExecution(
        case_id=plan.case_id, parent_id=plan.parent_id,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
