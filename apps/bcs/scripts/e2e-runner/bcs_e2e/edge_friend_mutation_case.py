"""Source binding and declarative rewrite plans for the seven reviewed
release-parent Edge friend-mutation entrypoints and two backlog TestCases.

Each legacy entrypoint in ``edge_permission.sh`` keeps its own CaseRewritePlan
(one obligation, one adapter seam) so the migration mapping stays per-story,
exactly like the four read-only Edge cases. The seven entrypoints share one
owned-local adapter implementation and one owned runtime lifecycle.

Documented correction versus the historical shell coverage: the historical
accept/reject/cancel case reused the QA->PM pair for all three branches, but
the service treats a re-add between already-connected bots as an idempotent
approved result, so its reject and cancel branches degraded to fake-id probes.
The owned-local seams drive each branch against a real pending row on a fresh
actor pair, and the duplicate-pending branch asserts the real idempotent
pending result instead of the stale 409 expectation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .case_rewrite import (
    CaseRewriteBinding,
    CaseRewriteEnvironment,
    CaseRewritePlan,
    CaseRewriteRequirement,
)
from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
E2E_TEST_DIR = ROOT.parent / 'e2e-test'
SOURCE_FILE = 'edge_permission.sh'
SUITE = 'owned-local-edge-friend-mutations-v1'

_ENVS = (
    CaseRewriteEnvironment(
        name='owned-local',
        adapter_family='OwnedLocalCaseAdapter',
        status='PENDING',
        blocker='Plan-time declaration only; the owned-local executor is the implemented realization.',
    ),
    CaseRewriteEnvironment(
        name='singlebox',
        adapter_family='SingleboxCaseAdapter',
        status='PENDING',
        blocker='Singlebox lifecycle and run-owned process ownership are still blocked.',
    ),
    CaseRewriteEnvironment(
        name='preprod',
        adapter_family='RemoteCaseAdapter',
        status='PENDING',
        blocker='Preprod friend-mutation write admission is an independent effect gate.',
    ),
)


class EdgeFriendMutationCase:
    """One legacy entrypoint: its plan identity and drift contract."""

    def __init__(self, *, parent: str, case_id: str, requirement_id: str,
                 kind: str, method: str, path: str, actor: str,
                 adapter_method: str, expectation: str, function_name: str,
                 required_literals: tuple[str, ...]):
        self.parent = parent
        self.case_id = case_id
        self.requirement_id = requirement_id
        self.kind = kind
        self.method = method
        self.path = path
        self.actor = actor
        self.adapter_method = adapter_method
        self.expectation = expectation
        self.function_name = function_name
        self.required_literals = required_literals


CASES = (
    EdgeFriendMutationCase(
        parent='test_ep_friend_connections_friend_accept_reject_cancel',
        case_id='LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.accept-reject-cancel',
        kind='http', method='POST',
        path='/collaboration/friend-connections/requests/{id}/accept',
        actor='QA_LOCAL', adapter_method='run_accept_reject_cancel',
        expectation=(
            'accept approves a real receiver-side pending row and inserts one '
            'forward edge_grant that lists the peer and admits the sender; '
            'reject marks a real pending row rejected leaving no edge; cancel '
            'withdraws a real initiator pending row leaving no edge'),
        function_name='test_ep_friend_connections_friend_accept_reject_cancel',
        required_literals=(
            '/collaboration/friend-connections/requests',
            '/accept', '/reject', '/cancel', 'direction=received',
            'direction=sent'),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_friend_connections_friend_revoke',
        case_id='LOCAL-EDGE-FRIEND-REVOKE-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.revoke',
        kind='http', method='DELETE',
        path='/collaboration/friend-connections/{actor}',
        actor='CEO_LOCAL', adapter_method='run_revoke',
        expectation=(
            'revoke deletes the built forward and reverse friend edges '
            'exactly; the actor pair is left with one fresh pending request'),
        function_name='test_ep_friend_connections_friend_revoke',
        required_literals=(
            'DELETE', '/collaboration/friend-connections/',
            '/collaboration/friend-connections/requests'),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_ensure_bot',
        case_id='LOCAL-EDGE-ENSURE-BOT-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.ensure-bot',
        kind='fixture', method='POST',
        path='/admin/bots/{bot_uuid}/ensure',
        actor='SERVICE_KEY', adapter_method='run_ensure_bot',
        expectation=(
            'admin ensure under a service credential is idempotent for an '
            'existing registered fixture bot and reports ensured=true, '
            'created=false and the same bot_uuid'),
        function_name='test_ep_ensure_bot',
        required_literals=('/admin/bots/', '/ensure', 'X-BCS-Service-Key'),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_friend_connections_full_lifecycle',
        case_id='LOCAL-EDGE-FRIEND-FULL-LIFECYCLE-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.full-lifecycle',
        kind='http', method='POST',
        path='/collaboration/friend-connections/requests',
        actor='CEO_LOCAL', adapter_method='run_full_lifecycle',
        expectation=(
            'lifecycle drives create, accept, friend-list data, authorized '
            'admission, three-direction request lists, exact edge revoke, '
            'public visibility restore and one fresh pending recreation'),
        function_name='test_ep_friend_connections_full_lifecycle',
        required_literals=(
            '/collaboration/friend-connections/requests', '/accept',
            '/admission', '/collaboration/friend-connections?actor=',
            'direction=received', 'direction=sent', 'direction=all'),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_friend_connections_mutual_auto_approve',
        case_id='LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.mutual-auto-approve',
        kind='http', method='POST',
        path='/collaboration/friend-connections/requests/{id}/accept',
        actor='ENG_LOCAL', adapter_method='run_mutual_auto_approve',
        expectation=(
            'one accept on a mutual Bot-to-Bot pending pair auto-approves '
            'the reverse pending request and builds both edges; both '
            'directions list the peer and both admissions are authorized'),
        function_name='test_ep_friend_connections_mutual_auto_approve',
        required_literals=(
            '/collaboration/friend-connections/requests', '/accept',
            'direction=received', 'direction=all', '/admission'),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_error_and_admission_branches',
        case_id='LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.error-admission-branches',
        kind='http', method='GET',
        path='/bots/{bot_uuid}/admission',
        actor='CEO_LOCAL', adapter_method='run_error_admission_branches',
        expectation=(
            'unknown bot admission reports bot_not_found; a protected '
            'no-edge admission reports no_edge; self-add is rejected 400 '
            'cannot_add_self; an unknown target is rejected 404 bot_not_found; '
            'a duplicate same-direction create is an idempotent pending '
            'result returning the same request_ids'),
        function_name='test_ep_error_and_admission_branches',
        required_literals=(
            '/admission', '/collaboration/friend-connections/requests'),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_connect_private_and_discover_branches',
        case_id='LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.connect-private-discover',
        kind='http', method='GET',
        path='/bots/discover',
        actor='CEO_LOCAL', adapter_method='run_connect_private_discover',
        expectation=(
            'a bot-to-private-target create is rejected 403; discover with a '
            'private collaborate_bot yields an empty result; a public '
            'collaborate_bot yields the friend-list-for-collaboration; role '
            'without organization_code is rejected 400 '
            'role_requires_organization_code; organization_code membership '
            'is enforced for an organization caller'),
        function_name='test_ep_connect_private_and_discover_branches',
        required_literals=(
            '/bots/discover', 'collaborate_bot=', 'role=contributor',
            'organization_code='),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_tc_friend_auth_sync_readdressed_principal',
        case_id='LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-readdress',
        kind='http', method='POST',
        path='/collaboration/friend-connections/requests/{id}/accept',
        actor='FORWARDED_HUMAN', adapter_method='run_tc_friend_auth_sync_readdress',
        expectation=(
            'a backend-issued forwarded Human principal on create and accept '
            'produces one TC friend-auth sync with the same approver identity '
            're-addressed as a bcs-issued backend token and no credential '
            'echo'),
        function_name='test_ep_tc_friend_auth_sync_readdressed_principal',
        required_literals=(
            'X-Avernet-Principal: $principal',
            '/admin/bots/${tc_bot}/ensure',
            '/collaboration/friend-connections/requests',
            'control/friend-auth-sync/requests',
            '("iss", "bcs"),',
            '("aud", "backend"),',
            '("owner_work_no", owner_work_no),',
            '("action", "grant"),',
        ),
    ),
    EdgeFriendMutationCase(
        parent='test_ep_dept_scope_parses_object_friend_ext_entries',
        case_id='LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01',
        requirement_id='EDGE_FRIEND_MUTATIONS.dept-scope-friend-ext',
        kind='http', method='PATCH',
        path='/openapi/v1/collaboration/bots/{bot_id}',
        actor='BOT_OWNER', adapter_method='run_dept_scope_friend_ext',
        expectation=(
            'the owner writes mixed object/string/garbage department-scope '
            'entries; both Human and Bot create paths execute the parser, '
            'and the re-patch of a non-empty user scope remains accepted '
            'without silently forgiving a missing scope'),
        function_name='test_ep_dept_scope_parses_object_friend_ext_entries',
        required_literals=(
            '/admin/bots/e2e-friend-ext-bot/ensure',
            'openapi/v1/collaboration/bots/e2e-friend-ext-bot',
            'DEPT_FREE',
            'X-Mock-User-Id: 511488',
            'F4858',
            'deptNo',
            'priority": 7',
        ),
    ),
)

CASE_IDS = tuple(row.case_id for row in CASES)
PARENTS = tuple(row.parent for row in CASES)
RELEASE_CASE_IDS = tuple(row.case_id for row in CASES[:7])
RELEASE_PARENT_IDS = tuple(row.parent for row in CASES[:7])
BACKLOG_CASE_IDS = tuple(row.case_id for row in CASES[7:])
BACKLOG_PARENT_IDS = tuple(row.parent for row in CASES[7:])

TC_REQUIREMENT_ROWS = (
    {
        'id': 'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-create-accept',
        'kind': 'http', 'method': 'POST',
        'path': '/collaboration/friend-connections/requests/{request_id}/accept',
        'actor': 'FORWARDED_HUMAN',
        'adapter_method': 'run_tc_friend_request_create_accept',
        'expectation': (
            'one forwarded Human request create and receiver-side accept build '
            'the TC friendship before any sync receipt is claimed'),
    },
    {
        'id': 'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-principal',
        'kind': 'http', 'method': 'GET',
        'path': '/control/friend-auth-sync/requests',
        'actor': 'MOCK_CONTROL',
        'adapter_method': 'run_tc_friend_auth_sync_readdress',
        'expectation': (
            'accept produces exactly one TC friend-auth sync call and its '
            'principal is re-addressed to bcs/backend while preserving the '
            'approver subject'),
    },
)

DEPT_REQUIREMENT_ROWS = (
    {
        'id': 'EDGE_FRIEND_MUTATIONS.dept-scope-mixed-owner-patch',
        'kind': 'http', 'method': 'PATCH',
        'path': '/openapi/v1/collaboration/bots/{bot_id}',
        'actor': 'BOT_OWNER',
        'adapter_method': 'run_dept_scope_mixed_owner_patch',
        'expectation': (
            'the owner writes mixed object, legacy-string and garbage '
            'department-scope entries through one accepted Bot profile patch'),
    },
    {
        'id': 'EDGE_FRIEND_MUTATIONS.dept-scope-human-no-check',
        'kind': 'http', 'method': 'POST',
        'path': '/collaboration/friend-connections/requests',
        'actor': 'FORWARDED_HUMAN',
        'adapter_method': 'run_dept_scope_human_no_check',
        'expectation': (
            'the Human create path runs no_check object/string parsing and '
            'lands a bounded pending request'),
    },
    {
        'id': 'EDGE_FRIEND_MUTATIONS.dept-scope-human-user-scope',
        'kind': 'http', 'method': 'POST',
        'path': '/collaboration/friend-connections/requests',
        'actor': 'FORWARDED_HUMAN',
        'adapter_method': 'run_dept_scope_human_user_scope',
        'expectation': (
            'the owner first writes non-empty user/agent object scopes, then '
            'the Human create path parses user object/garbage entries in '
            'isolation'),
    },
    {
        'id': 'EDGE_FRIEND_MUTATIONS.dept-scope-bot-agent-scope',
        'kind': 'http', 'method': 'POST',
        'path': '/collaboration/friend-connections/requests',
        'actor': 'BOT_CALLER',
        'adapter_method': 'run_dept_scope_bot_agent_scope',
        'expectation': (
            'the Bot create path parses agent object/garbage entries in '
            'isolation from the Human path'),
    },
)

_REQUIREMENTS_BY_CASE = {
    'LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01': TC_REQUIREMENT_ROWS,
    'LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01': DEPT_REQUIREMENT_ROWS,
}
TOTAL_ASSERTION_COUNT = sum(len(_REQUIREMENTS_BY_CASE.get(row.case_id, (1,))) for row in CASES)


def _source_digest() -> str:
    path = E2E_TEST_DIR / SOURCE_FILE
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise RunStateError('edge_permission.sh is missing, symlinked or oversized')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _function_body(text: str, function_name: str) -> str:
    marker = function_name + '() {'
    if text.count(marker) != 1:
        raise RunStateError(
            'edge_permission.sh drift: ' + function_name + ' is missing or duplicated')
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines)
                 if line.strip() == marker or line.startswith(marker))
    body: list[str] = []
    for line in lines[start:]:
        body.append(line)
        if line == '}':
            break
    else:
        raise RunStateError('edge_permission.sh drift: ' + function_name + ' is not brace-terminated')
    return '\n'.join(body)


def edge_friend_mutation_case_spec(case_id: str) -> EdgeFriendMutationCase:
    matches = [row for row in CASES if row.case_id == case_id]
    if len(matches) != 1:
        raise RunStateError('unknown edge friend-mutation case ID: ' + case_id)
    return matches[0]


def _case_requirements(spec: EdgeFriendMutationCase) -> tuple[dict, ...]:
    """Return the executed assertion rows for one case without padding them."""
    row = {
        'id': spec.requirement_id,
        'kind': spec.kind,
        'method': spec.method,
        'path': spec.path,
        'actor': spec.actor,
        'adapter_method': spec.adapter_method,
        'expectation': spec.expectation,
    }
    if spec.case_id in _REQUIREMENTS_BY_CASE:
        return _REQUIREMENTS_BY_CASE[spec.case_id]
    return (row,)


def edge_friend_mutation_source_binding(case_id: str) -> dict:
    """Bind one legacy entrypoint to its reviewed edge_permission.sh source."""
    spec = edge_friend_mutation_case_spec(case_id)
    digest = _source_digest()
    text = (E2E_TEST_DIR / SOURCE_FILE).read_text(encoding='utf-8')
    body = _function_body(text, spec.function_name)
    missing = [literal for literal in spec.required_literals
               if literal not in body]
    if missing:
        raise RunStateError(
            'edge_permission.sh drift: ' + spec.function_name
            + ' no longer contains ' + ', '.join(missing))
    return {
        'schema_version': 1,
        'source_revision': digest,
        'parent': spec.parent,
        'sources': {SOURCE_FILE: digest},
        'requirements': [dict(row) for row in _case_requirements(spec)],
    }


def edge_friend_mutation_case_plan(case_id: str) -> CaseRewritePlan:
    """Build the declarative rewrite plan for one legacy entrypoint."""
    spec = edge_friend_mutation_case_spec(case_id)
    binding = edge_friend_mutation_source_binding(case_id)
    rows = _case_requirements(spec)
    requirements = tuple(CaseRewriteRequirement(
        requirement_id=row['id'],
        kind=row['kind'],
        method=row['method'],
        path=row['path'],
        actor=row['actor'],
        adapter_method=row['adapter_method'],
        expectation=row['expectation'],
    ) for row in rows)
    plan = CaseRewritePlan(
        case_id=spec.case_id,
        parent_id=spec.parent,
        requirements=requirements,
        environments=_ENVS,
        binding=CaseRewriteBinding(
            case_id=spec.case_id,
            parent_id=spec.parent,
            requirement_ids=tuple(row['id'] for row in rows),
        ),
    )
    plan.validate()
    binding_ids = [row['id'] for row in binding['requirements']]
    if (binding_ids != list(plan.requirement_ids)
            or plan.adapters != {row['id']: row['adapter_method'] for row in rows}):
        raise RunStateError('edge friend-mutation source binding and case plan differ')
    return plan
