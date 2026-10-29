"""Owned-local source and model contracts for the two backlog Edge cases.

The legacy ``edge_permission.sh`` suite calls direct BCS routes.  The Avernet
station binary exposes those routes through two different faces: the native
``bcs-http`` router and the ``bcs-api-http`` OpenAPI router.  Each case
therefore declares an explicit owned-local profile and remote-public profile;
no path is treated as a universal community-edition route.  The remote
adapter is a deliberate placeholder: preprod is ``NOT_RUN`` and never inherits
a local model PASS.

This one focused module carries both independent TestCase slices so the
runner's frozen focused-module inventory is not inflated.  The two root JSON
contracts remain separate and each keeps its own requirement, obligations,
failure semantics and expected values.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import quote

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
E2E_TEST_DIR = ROOT.parent / 'e2e-test'
REPO_ROOT = ROOT.parents[3]
SOURCE_FILE = 'edge_permission.sh'
SOURCE_PATH = E2E_TEST_DIR / SOURCE_FILE
SUITE = 'owned-local-edge-friend-mutations-v1'
PROFILE_NAMES = ('owned-local', 'remote-public')
STATE_DIR_PREFIX = 'edge-backlog-'
_ROUTE_KEYS = {'method', 'path', 'success_statuses', 'actor', 'availability'}
_PLACEHOLDER = re.compile(r'\{([a-z][a-z0-9_]*)\}')


class EdgeBacklogFailure(RunStateError):
    """A per-obligation failure with deterministic expected/observed values."""

    def __init__(self, code: str, detail: str, *, expected: object = None,
                 observed: object = None):
        if (not isinstance(code, str) or not code
                or not isinstance(detail, str) or not detail):
            raise ValueError('backlog failure code and detail are required')
        self.code = code
        self.detail = detail
        self.expected = expected
        self.observed = observed
        suffix = ''
        if expected is not None:
            suffix += ' expected=' + repr(expected)
        if observed is not None:
            suffix += ' observed=' + repr(observed)
        super().__init__(code + ': ' + detail + suffix)


@dataclass(frozen=True)
class RouteBinding:
    operation: str
    method: str
    path: str
    success_statuses: tuple[int, ...]
    actor: str
    availability: str = ''

    def validate(self) -> None:
        if (not self.operation or self.method not in {'GET', 'POST', 'PATCH', 'PUT', 'DELETE'}
                or not isinstance(self.path, str) or not self.path.startswith('/')
                or len(self.path) > 2048
                or any(ord(char) <= 32 or ord(char) == 127 for char in self.path)
                or not self.success_statuses
                or any(type(status) is not int or not 100 <= status <= 599
                       for status in self.success_statuses)
                or len(set(self.success_statuses)) != len(self.success_statuses)
                or not isinstance(self.actor, str) or not self.actor
                or self.availability not in {'', 'STATION_NATIVE_ONLY'}):
            raise EdgeBacklogFailure(
                'ROUTE_BINDING_INVALID', 'route binding is incomplete',
                observed=self.operation)

    def render(self, **parameters: object) -> str:
        names = set(_PLACEHOLDER.findall(self.path))
        if set(parameters) != names:
            raise EdgeBacklogFailure(
                'ROUTE_PARAMETERS_MISMATCH', 'route parameters differ from its template',
                expected=sorted(names), observed=sorted(parameters))
        rendered = self.path
        for name in names:
            rendered = rendered.replace('{' + name + '}', quote(str(parameters[name]), safe=''))
        if '{' in rendered or '}' in rendered:
            raise EdgeBacklogFailure(
                'ROUTE_PARAMETERS_MISMATCH', 'route template was not fully rendered',
                observed=rendered)
        return rendered


@dataclass(frozen=True)
class RouteProfile:
    name: str
    entrypoint_kind: str
    routes: Mapping[str, RouteBinding]

    def route(self, operation: str) -> RouteBinding:
        try:
            return self.routes[operation]
        except KeyError:
            raise EdgeBacklogFailure(
                'ROUTE_OPERATION_UNKNOWN', 'route operation is not in this profile',
                expected=sorted(self.routes), observed=operation) from None

    def validate(self) -> None:
        if (self.name not in PROFILE_NAMES or not isinstance(self.entrypoint_kind, str)
                or not self.entrypoint_kind or not isinstance(self.routes, Mapping)
                or not self.routes):
            raise EdgeBacklogFailure(
                'ROUTE_PROFILE_INVALID', 'route profile is incomplete', observed=self.name)
        if set(self.routes) != {route.operation for route in self.routes.values()}:
            raise EdgeBacklogFailure(
                'ROUTE_PROFILE_INVALID', 'route operation keys and values differ',
                observed=self.name)
        for route in self.routes.values():
            route.validate()


class RemoteEdgeBacklogAdapter:
    """Remote adaptation slot: declares NOT_RUN and refuses to invent a run."""

    def __init__(self, case_id: str, profile: RouteProfile):
        if (not isinstance(case_id, str) or not case_id
                or type(profile) is not RouteProfile
                or profile.name != 'remote-public'):
            raise EdgeBacklogFailure(
                'REMOTE_ADAPTER_INVALID', 'remote adapter requires its public profile',
                observed=case_id)
        self.case_id = case_id
        self.profile = profile

    def preflight(self) -> dict:
        unavailable = [operation for operation, route in self.profile.routes.items()
                       if route.availability == 'STATION_NATIVE_ONLY']
        return {
            'case_id': self.case_id,
            'status': 'REMOTE_PLACEHOLDER',
            'entrypoint_kind': self.profile.entrypoint_kind,
            'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
            'unavailable_operations': sorted(unavailable),
            'reason': 'preprod execution requires an independently admitted profile',
        }

    def execute(self, *_args, **_kwargs):
        raise EdgeBacklogFailure(
            'REMOTE_UNSUPPORTED',
            'remote adapter is a placeholder; no preprod transport is admitted',
            observed=self.case_id)


def _function_body(text: str, function_name: str) -> str:
    marker = function_name + '() {'
    if text.count(marker) != 1:
        raise EdgeBacklogFailure(
            'SOURCE_FUNCTION_DRIFT', 'legacy function is missing or duplicated',
            expected=1, observed=text.count(marker))
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines)
                 if line.strip() == marker or line.startswith(marker))
    body: list[str] = []
    for line in lines[start:]:
        body.append(line)
        if line == '}':
            return '\n'.join(body)
    raise EdgeBacklogFailure('SOURCE_FUNCTION_DRIFT', 'legacy function is not brace-terminated')


def _safe_source(relative: str) -> Path:
    if (not isinstance(relative, str) or not relative
            or Path(relative).is_absolute() or '..' in Path(relative).parts):
        raise EdgeBacklogFailure(
            'EVIDENCE_PATH_INVALID', 'evidence source must be repository-relative',
            observed=relative)
    path = REPO_ROOT / relative
    if path.is_symlink() or not path.is_file():
        raise EdgeBacklogFailure(
            'EVIDENCE_PATH_INVALID', 'evidence source is missing or symlinked',
            observed=relative)
    return path


def _validate_source_evidence(rows: object, field: str) -> None:
    if not isinstance(rows, list) or not rows:
        raise EdgeBacklogFailure(
            'EVIDENCE_MISSING', field + ' must be a non-empty list')
    for row in rows:
        if (not isinstance(row, dict)
                or set(row) != {'operation', 'profile', 'method', 'path', 'sources'}
                or not all(isinstance(row[name], str) and row[name]
                           for name in ('operation', 'profile', 'method', 'path'))
                or not isinstance(row['sources'], list) or not row['sources']):
            raise EdgeBacklogFailure(
                'EVIDENCE_INVALID', field + ' row is incomplete', observed=row)
        for source in row['sources']:
            if (not isinstance(source, dict)
                    or set(source) != {'file', 'line_start', 'line_end', 'contains'}
                    or type(source.get('line_start')) is not int
                    or type(source.get('line_end')) is not int
                    or not isinstance(source.get('contains'), list)
                    or not source['contains']
                    or any(not isinstance(item, str) or not item
                           for item in source['contains'])):
                raise EdgeBacklogFailure(
                    'EVIDENCE_INVALID', field + ' source row is incomplete',
                    observed=source)
            path = _safe_source(source['file'])
            lines = path.read_text(encoding='utf-8').splitlines()
            start, end = source['line_start'], source['line_end']
            if not 1 <= start <= end <= len(lines):
                raise EdgeBacklogFailure(
                    'EVIDENCE_LINE_RANGE_INVALID', 'evidence line range is outside its source',
                    expected=[1, len(lines)], observed=[start, end])
            selected = '\n'.join(lines[start - 1:end])
            missing = [fragment for fragment in source['contains']
                       if fragment not in selected]
            if missing:
                raise EdgeBacklogFailure(
                    'EVIDENCE_DRIFT', 'cited source lines no longer contain the route fact',
                    expected=missing, observed=source['file'] + ':' + str(start) + '-' + str(end))


def _validate_contract(
        payload: dict, *, case_id: str, parent: str, requirement_id: str,
        function_name: str, requirement_method: str, requirement_path: str,
        expected_route_profiles: Mapping[str, RouteProfile],
        expected_anchors: tuple[str, ...], expected_failure_codes: tuple[str, ...],
) -> dict:
    expected_keys = {
        'schema_version', 'source_revision', 'source_git_revision', 'parent', 'suite',
        'case_id', 'function_name', 'sources', 'environment', 'route_profiles',
        'station_path_evidence', 'implementation_evidence', 'requirements',
        'source_anchors', 'obligations', 'failure_semantics', 'expected_values',
        'remote_validation', 'grants_release_credit', 'release_parent_denominator',
        'independent_slice', 'evidence_granularity',
    }
    try:
        if (not isinstance(payload, dict) or set(payload) != expected_keys
                or payload['schema_version'] != 1
                or payload['suite'] != SUITE
                or payload['case_id'] != case_id
                or payload['parent'] != parent
                or payload['function_name'] != function_name
                or payload['remote_validation'] != 'NOT_RUN'
                or payload['grants_release_credit'] is not False
                or payload['release_parent_denominator'] is not False
                or payload['independent_slice'] is not True
                or payload['evidence_granularity'] != 'OWNED_LOCAL_RECEIPT'
                or not re.fullmatch('[0-9a-f]{40}', str(payload['source_git_revision']))
                or not re.fullmatch('[0-9a-f]{64}', str(payload['source_revision']))
                or payload['sources'] != {SOURCE_FILE: payload['source_revision']}
                or payload['source_anchors'] != list(expected_anchors)
                or [row.get('id') for row in payload['requirements']] != [requirement_id]
                or [row.get('method') for row in payload['requirements']] != [requirement_method]
                or [row.get('path') for row in payload['requirements']] != [requirement_path]):
            raise ValueError
        if (SOURCE_PATH.is_symlink() or not SOURCE_PATH.is_file()
                or SOURCE_PATH.stat().st_size > 1_048_576):
            raise ValueError
        digest = hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()
        if digest != payload['source_revision']:
            raise EdgeBacklogFailure(
                'SOURCE_DIGEST_DRIFT', 'edge_permission.sh differs from its frozen digest',
                expected=payload['source_revision'], observed=digest)
        body = _function_body(SOURCE_PATH.read_text(encoding='utf-8'), function_name)
        for anchor in expected_anchors:
            if anchor not in body:
                raise EdgeBacklogFailure(
                    'SOURCE_ANCHOR_DRIFT', 'legacy obligation anchor disappeared',
                    expected=anchor, observed=function_name)
        local = payload['environment'].get('local', {})
        remote = payload['environment'].get('remote', {})
        if (set(payload['environment']) != {'local', 'remote'}
                or set(local) != {'adapter_family', 'entrypoint_kind', 'status'}
                or set(remote) != {'adapter_family', 'entrypoint_kind', 'status',
                                   'remote_validation'}
                or local.get('adapter_family') != 'OwnedLocalEdgeBacklogAdapter'
                or local.get('entrypoint_kind') != 'owned-local'
                or local.get('status') != 'LOCAL_MODEL_READY'
                or remote.get('adapter_family') != 'RemoteEdgeBacklogAdapter'
                or remote.get('entrypoint_kind') != 'preprod-public-v1'
                or remote.get('status') != 'REMOTE_PLACEHOLDER'
                or remote.get('remote_validation') != 'NOT_RUN'):
            raise ValueError
        profiles = _load_profiles(payload['route_profiles'])
        if profiles != dict(expected_route_profiles):
            raise EdgeBacklogFailure(
                'ROUTE_PROFILE_DRIFT', 'route profiles differ from the reviewed profiles',
                expected=sorted(expected_route_profiles), observed=sorted(profiles))
        _validate_source_evidence(payload['station_path_evidence'], 'station_path_evidence')
        _validate_source_evidence(payload['implementation_evidence'], 'implementation_evidence')
        obligations = payload['obligations']
        if (not isinstance(obligations, list) or not obligations
                or any(not isinstance(row, dict) or set(row) != {
                    'id', 'method', 'path', 'expectation', 'failure_codes'}
                    or not all(isinstance(row[name], str) and row[name]
                               for name in ('id', 'method', 'path', 'expectation'))
                    or not isinstance(row['failure_codes'], list) or not row['failure_codes']
                    or any(not isinstance(code, str) or not code
                           for code in row['failure_codes'])
                    for row in obligations)):
            raise ValueError
        defined = [row['code'] for row in payload['failure_semantics']]
        if (not isinstance(payload['failure_semantics'], list) or not payload['failure_semantics']
                or defined != list(expected_failure_codes)
                or any(not isinstance(row, dict) or set(row) != {
                    'code', 'stage', 'expected', 'observed_if_mismatch'}
                    or not all(isinstance(row[name], str) and row[name]
                               for name in row) for row in payload['failure_semantics'])):
            raise ValueError
        referenced = {code for row in obligations for code in row['failure_codes']}
        if not referenced <= set(defined):
            raise EdgeBacklogFailure(
                'FAILURE_SEMANTICS_INCOMPLETE', 'obligation failure codes are undefined',
                expected=sorted(referenced - set(defined)))
        if not isinstance(payload['expected_values'], dict):
            raise ValueError
        return payload
    except EdgeBacklogFailure:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise EdgeBacklogFailure(
            'CONTRACT_INVALID', 'backlog migration contract is incomplete or inconsistent',
            observed=type(error).__name__) from None


def _load_profiles(value: object) -> dict[str, RouteProfile]:
    if not isinstance(value, dict) or set(value) != set(PROFILE_NAMES):
        raise EdgeBacklogFailure(
            'ROUTE_PROFILE_INVALID', 'both owned-local and remote-public profiles are required',
            observed=sorted(value) if isinstance(value, dict) else type(value).__name__)
    profiles: dict[str, RouteProfile] = {}
    for name in PROFILE_NAMES:
        row = value[name]
        if (not isinstance(row, dict)
                or set(row) != {'entrypoint_kind', 'routes'}
                or not isinstance(row['routes'], dict)):
            raise EdgeBacklogFailure(
                'ROUTE_PROFILE_INVALID', 'route profile is incomplete', observed=name)
        routes: dict[str, RouteBinding] = {}
        for operation, route in row['routes'].items():
            if (not isinstance(operation, str) or not operation
                    or not isinstance(route, dict)
                    or not {'method', 'path', 'success_statuses', 'actor'} <= set(route)
                    or not set(route) <= _ROUTE_KEYS
                    or not isinstance(route.get('success_statuses'), list)):
                raise EdgeBacklogFailure(
                    'ROUTE_BINDING_INVALID', 'route binding key set is invalid',
                    observed=operation)
            binding = RouteBinding(
                operation=operation, method=route['method'], path=route['path'],
                success_statuses=tuple(route['success_statuses']), actor=route['actor'],
                availability=route.get('availability', ''))
            binding.validate()
            routes[operation] = binding
        profile = RouteProfile(name=name, entrypoint_kind=row['entrypoint_kind'],
                               routes=routes)
        profile.validate()
        profiles[name] = profile
    return profiles


def _load_profile_contract(filename: str, **expected) -> tuple[dict, dict[str, RouteProfile]]:
    payload = read(ROOT / filename)
    _validate_contract(payload, **expected)
    return payload, _load_profiles(payload['route_profiles'])


def require_status(status: object, binding: RouteBinding, code: str) -> int:
    if type(status) is not int or status not in binding.success_statuses:
        raise EdgeBacklogFailure(
            code, 'HTTP status differs from the reviewed route profile',
            expected=list(binding.success_statuses), observed=status)
    return status


def require_equal(observed: object, expected: object, code: str, detail: str) -> None:
    if observed != expected:
        raise EdgeBacklogFailure(code, detail, expected=expected, observed=observed)


def allocate_state_dir(parent: Path, case_id: str) -> Path:
    """Create one unique ``edge-backlog-`` Run directory under ``parent``."""
    base = Path(parent)
    if (not base.is_absolute() or base.is_symlink() or not base.is_dir()
            or not isinstance(case_id, str) or not case_id):
        raise EdgeBacklogFailure(
            'STATE_DIR_INVALID', 'backlog state parent must be an existing absolute directory',
            observed=str(base))
    slug = re.sub(r'[^a-z0-9]+', '-', case_id.lower()).strip('-')[:48]
    if not slug:
        raise EdgeBacklogFailure('STATE_DIR_INVALID', 'case id cannot form a state slug')
    for _ in range(8):
        candidate = base / (STATE_DIR_PREFIX + slug + '-' + secrets.token_hex(6))
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            raise EdgeBacklogFailure(
                'STATE_DIR_INVALID', 'backlog state directory is not a real directory',
                observed=str(candidate))
        return candidate
    raise EdgeBacklogFailure(
        'STATE_DIR_EXHAUSTED', 'could not reserve a unique backlog state directory')


# ---------------------------------------------------------------------------
# Case A: TC friend-auth-sync re-addressed principal
# ---------------------------------------------------------------------------

TC_CASE_ID = 'LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01'
TC_PARENT = 'test_ep_tc_friend_auth_sync_readdressed_principal'
TC_REQUIREMENT_ID = 'EDGE_FRIEND_MUTATIONS.tc-friend-auth-sync-readdress'
TC_FUNCTION_NAME = 'test_ep_tc_friend_auth_sync_readdressed_principal'
TC_CONTRACT_FILE = 'tc-friend-auth-sync-migration-contract.json'

TC_BOT = '2026e2e-tc-bot:85020'
TC_BOT_API_ID = '2026e2e-tc-bot'
TC_OWNER_WORK_NO = '85020'
TC_APPROVER_SUBJECT_ID = '153998'

TC_SOURCE_ANCHORS = (
    'local tc_bot="2026e2e-tc-bot:85020"',
    '/admin/bots/${tc_bot}/ensure',
    'X-Avernet-Principal: $principal',
    '/collaboration/friend-connections/requests',
    '/collaboration/friend-connections/requests/${request_id}/accept',
    'control/friend-auth-sync/requests',
    '("iss", "bcs"),',
    '("aud", "backend"),',
    'if subject.get("id") != "153998":',
    'expected exactly 1 sync call',
    '("owner_work_no", owner_work_no),',
    '("action", "grant"),',
)

TC_FAILURE_CODES = (
    'TRANSCRIPT_INVALID',
    'ENSURE_STATUS_MISMATCH',
    'CREATE_STATUS_MISMATCH',
    'PENDING_ROW_MISSING',
    'ACCEPT_STATUS_MISMATCH',
    'EDGE_NOT_BUILT',
    'SYNC_CALL_COUNT_MISMATCH',
    'SYNC_BODY_MISMATCH',
    'SYNC_PRINCIPAL_UNDECODABLE',
    'SYNC_PRINCIPAL_MISMATCH',
)

TC_OWNED_LOCAL_PROFILE = RouteProfile(
    name='owned-local',
    entrypoint_kind='owned-local',
    routes={
        'ensure': RouteBinding(
            operation='ensure', method='POST',
            path='/admin/bots/{bot_uuid}/ensure', success_statuses=(200,),
            actor='SERVICE_KEY'),
        'create': RouteBinding(
            operation='create', method='POST',
            path='/collaboration/friend-connections/requests',
            success_statuses=(200,), actor='FORWARDED_HUMAN'),
        'accept': RouteBinding(
            operation='accept', method='POST',
            path='/collaboration/friend-connections/requests/{request_id}/accept',
            success_statuses=(200,), actor='FORWARDED_HUMAN'),
        'revoke': RouteBinding(
            operation='revoke', method='DELETE',
            path='/collaboration/friend-connections/{actor}',
            success_statuses=(200,), actor='FORWARDED_HUMAN'),
        'sync_control_clear': RouteBinding(
            operation='sync_control_clear', method='POST',
            path='/control/friend-auth-sync/clear', success_statuses=(200,),
            actor='MOCK_CONTROL'),
        'sync_control_view': RouteBinding(
            operation='sync_control_view', method='GET',
            path='/control/friend-auth-sync/requests', success_statuses=(200,),
            actor='MOCK_CONTROL'),
    },
)

TC_REMOTE_PUBLIC_PROFILE = RouteProfile(
    name='remote-public',
    entrypoint_kind='preprod-public-v1',
    routes={
        'ensure': RouteBinding(
            operation='ensure', method='POST',
            path='/admin/bots/{bot_uuid}/ensure', success_statuses=(200,),
            actor='SERVICE_KEY', availability='STATION_NATIVE_ONLY'),
        'create': RouteBinding(
            operation='create', method='POST',
            path='/openapi/v1/collaboration/friend-connections/requests',
            success_statuses=(201,), actor='FORWARDED_HUMAN'),
        'accept': RouteBinding(
            operation='accept', method='POST',
            path='/openapi/v1/collaboration/friend-connections/requests/{request_id}/accept',
            success_statuses=(200,), actor='FORWARDED_HUMAN'),
    },
)

TC_ROUTE_PROFILES = {
    'owned-local': TC_OWNED_LOCAL_PROFILE,
    'remote-public': TC_REMOTE_PUBLIC_PROFILE,
}

TC_EXPECTED_VALUES = {
    'tc_bot': TC_BOT,
    'tc_bot_api_id': TC_BOT_API_ID,
    'owner_work_no': TC_OWNER_WORK_NO,
    'approver_subject_id': TC_APPROVER_SUBJECT_ID,
    'sync_action': 'grant',
    'sync_call_count': 1,
    'readdressed_issuer': 'bcs',
    'readdressed_audience': 'backend',
    'owned_local_statuses': {'ensure': 200, 'create': 200, 'accept': 200},
    'upstream_failure': 're-addressed principal subject/claims differ from the expected approver identity',
}


def load_tc_contract() -> tuple[dict, dict[str, RouteProfile]]:
    return _load_profile_contract(
        TC_CONTRACT_FILE,
        case_id=TC_CASE_ID,
        parent=TC_PARENT,
        requirement_id=TC_REQUIREMENT_ID,
        function_name=TC_FUNCTION_NAME,
        requirement_method='POST',
        requirement_path='/collaboration/friend-connections/requests/{request_id}/accept',
        expected_route_profiles=TC_ROUTE_PROFILES,
        expected_anchors=TC_SOURCE_ANCHORS,
        expected_failure_codes=TC_FAILURE_CODES,
    )


def _object(value: object, code: str, detail: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EdgeBacklogFailure(code, detail, observed=type(value).__name__)
    return value


def decode_principal_claims(token: object) -> Mapping[str, Any]:
    """Decode a principal JWT without ever including the credential in evidence."""
    try:
        if not isinstance(token, str) or not token:
            raise ValueError
        segments = token.split('.')
        if len(segments) != 3:
            raise ValueError
        payload = segments[1] + '=' * (-len(segments[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload.encode('ascii')))
        if not isinstance(value, Mapping):
            raise ValueError
        return value
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise EdgeBacklogFailure(
            'SYNC_PRINCIPAL_UNDECODABLE',
            'friend-auth-sync principal must be a three-segment JWT',
            observed=type(error).__name__) from None


def assert_tc_ensure(status: object, *,
                     binding: RouteBinding = TC_OWNED_LOCAL_PROFILE.route('ensure')) -> int:
    return require_status(status, binding, 'ENSURE_STATUS_MISMATCH')


def assert_tc_create(status: object, response: object, *,
                     binding: RouteBinding = TC_OWNED_LOCAL_PROFILE.route('create')) -> str:
    require_status(status, binding, 'CREATE_STATUS_MISMATCH')
    envelope = _object(response, 'PENDING_ROW_MISSING',
                       'create response must be a JSON object')
    data = _object(envelope.get('data'), 'PENDING_ROW_MISSING',
                   'create response must carry data')
    request_ids = data.get('request_ids')
    if (not isinstance(request_ids, list) or len(request_ids) != 1
            or not isinstance(request_ids[0], str) or not request_ids[0]):
        raise EdgeBacklogFailure(
            'PENDING_ROW_MISSING', 'create must return exactly one pending request id',
            expected=[TC_BOT], observed=request_ids)
    require_equal(data.get('status'), 'pending', 'PENDING_ROW_MISSING',
                  'create must leave the request pending')
    return request_ids[0]


def assert_tc_accept(status: object, response: object, *,
                     binding: RouteBinding = TC_OWNED_LOCAL_PROFILE.route('accept')) -> None:
    require_status(status, binding, 'ACCEPT_STATUS_MISMATCH')
    envelope = _object(response, 'EDGE_NOT_BUILT',
                       'accept response must be a JSON object')
    data = _object(envelope.get('data'), 'EDGE_NOT_BUILT',
                   'accept response must carry data')
    edge_ids = data.get('edge_ids')
    if not isinstance(edge_ids, list) or not edge_ids or not all(
            isinstance(edge_id, str) and edge_id for edge_id in edge_ids):
        raise EdgeBacklogFailure(
            'EDGE_NOT_BUILT', 'accept must return at least one non-empty edge id',
            observed=edge_ids)


def assert_tc_sync_view(view: object) -> dict:
    """Assert the upstream expectations, including the re-addressed approver."""
    payload = _object(view, 'SYNC_BODY_MISMATCH',
                      'friend-auth-sync control view must be an object')
    requests = payload.get('requests')
    if not isinstance(requests, list):
        raise EdgeBacklogFailure(
            'SYNC_CALL_COUNT_MISMATCH', 'control view requests must be a list',
            observed=type(requests).__name__)
    require_equal(len(requests), TC_EXPECTED_VALUES['sync_call_count'],
                  'SYNC_CALL_COUNT_MISMATCH',
                  'friend-auth-sync must issue exactly one call')
    record = _object(requests[0], 'SYNC_BODY_MISMATCH',
                     'sync record must be an object')
    body = _object(record.get('body'), 'SYNC_BODY_MISMATCH',
                   'sync record body must be an object')
    require_equal(body.get('bot_id'), TC_BOT_API_ID, 'SYNC_BODY_MISMATCH',
                  'sync body must carry the bare TC bot id')
    require_equal(body.get('owner_work_no'), TC_OWNER_WORK_NO, 'SYNC_BODY_MISMATCH',
                  'sync body must carry the TC owner work number')
    require_equal(body.get('action'), TC_EXPECTED_VALUES['sync_action'],
                  'SYNC_BODY_MISMATCH', 'sync body must be a grant')
    claims = decode_principal_claims(record.get('principal'))
    require_equal(claims.get('iss'), TC_EXPECTED_VALUES['readdressed_issuer'],
                  'SYNC_PRINCIPAL_MISMATCH',
                  'sync principal must be re-addressed with iss=bcs')
    require_equal(claims.get('aud'), TC_EXPECTED_VALUES['readdressed_audience'],
                  'SYNC_PRINCIPAL_MISMATCH',
                  'sync principal must be addressed to the backend')
    principals = claims.get('principals')
    subject = (principals[0].get('subject')
               if isinstance(principals, list) and principals
               and isinstance(principals[0], Mapping) else None)
    subject_id = subject.get('id') if isinstance(subject, Mapping) else None
    require_equal(subject_id, TC_APPROVER_SUBJECT_ID, 'SYNC_PRINCIPAL_MISMATCH',
                  'sync principal must preserve the approver subject id')
    return {
        'sync_call_count': len(requests),
        'bot_id': body.get('bot_id'),
        'owner_work_no': body.get('owner_work_no'),
        'action': body.get('action'),
        'issuer': claims.get('iss'),
        'audience': claims.get('aud'),
        'approver_subject_id': subject_id,
    }


def assert_tc_owned_local_transcript(transcript: Mapping[str, Any]) -> dict:
    """Validate one owned-local exchange transcript without touching transport."""
    value = _object(transcript, 'TRANSCRIPT_INVALID',
                    'owned-local transcript must be an object')
    required = {'ensure_status', 'create_status', 'create_response',
                'accept_status', 'accept_response', 'sync_view'}
    if set(value) != required:
        raise EdgeBacklogFailure(
            'TRANSCRIPT_INVALID', 'owned-local transcript has unexpected keys',
            expected=sorted(required), observed=sorted(value))
    assert_tc_ensure(value['ensure_status'])
    request_id = assert_tc_create(value['create_status'], value['create_response'])
    assert_tc_accept(value['accept_status'], value['accept_response'])
    sync = assert_tc_sync_view(value['sync_view'])
    return {
        'status': 'LOCAL_MODEL_PASS',
        'case_id': TC_CASE_ID,
        'request_id': request_id,
        'sync': sync,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }


class RemoteTcFriendAuthSyncAdapter(RemoteEdgeBacklogAdapter):
    """Remote slot for this case; intentionally has no transport."""

    def __init__(self):
        super().__init__(TC_CASE_ID, TC_REMOTE_PUBLIC_PROFILE)


# ---------------------------------------------------------------------------
# Case B: friend_ext department-scope object-entry parsing
# ---------------------------------------------------------------------------

DEPT_CASE_ID = 'LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01'
DEPT_PARENT = 'test_ep_dept_scope_parses_object_friend_ext_entries'
DEPT_REQUIREMENT_ID = 'EDGE_FRIEND_MUTATIONS.dept-scope-friend-ext'
DEPT_FUNCTION_NAME = 'test_ep_dept_scope_parses_object_friend_ext_entries'
DEPT_CONTRACT_FILE = 'dept-scope-friend-ext-migration-contract.json'

FRIEND_EXT_BOT = 'e2e-friend-ext-bot'
DEPT_OWNER_STAFF_NO = '85020'
SECOND_HUMAN_STAFF_NO = '511488'
SCOPE_KEYS = (
    'no_check_scope_friend_deps',
    'view_scope_user_friend_deps',
    'view_scope_agent_friend_deps',
)

MIXED_SCOPE = {
    'friend_check_in_strategy': 'DEPT_FREE',
    'friend_ext': {
        'public_user_approval': {'status': 'AGREE'},
        'no_check_scope_friend_deps': [
            {'deptNo': 'L3480', 'deptName': '蚂蚁集团-信贷事业群'},
            'F4858',
        ],
        'view_scope_user_friend_deps': [],
        'view_scope_agent_friend_deps': [
            {'deptNo': 'L3480'},
            {'priority': 7},
        ],
    },
}

SCOPED_SHAPE = {
    'friend_ext': {
        'public_user_approval': {'status': 'AGREE'},
        'no_check_scope_friend_deps': [
            {'deptNo': 'L3480', 'deptName': '蚂蚁集团-信贷事业群'},
            'F4858',
        ],
        'view_scope_user_friend_deps': [
            {'deptNo': 'L3480'},
            {'priority': 7},
        ],
        'view_scope_agent_friend_deps': [
            {'deptNo': 'L3480'},
            {'priority': 7},
        ],
    },
}

DEPT_SOURCE_ANCHORS = (
    '/admin/bots/e2e-friend-ext-bot/ensure',
    'openapi/v1/collaboration/bots/e2e-friend-ext-bot',
    '"friend_check_in_strategy": "DEPT_FREE"',
    '"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"',
    '"F4858"',
    '"view_scope_user_friend_deps"',
    '"view_scope_agent_friend_deps"',
    'X-Mock-User-Id: 511488',
    '"priority": 7',
    'no_check_scope_friend_deps',
    'bot request runs agent-scope parser',
)

DEPT_FAILURE_CODES = (
    'TRANSCRIPT_INVALID',
    'ENSURE_STATUS_MISMATCH',
    'PATCH_STATUS_MISMATCH',
    'FRIEND_EXT_SHAPE_INVALID',
    'FRIEND_EXT_PARSE_MISMATCH',
    'HUMAN_REQUEST_STATUS_MISMATCH',
    'SCOPED_PATCH_STATUS_MISMATCH',
    'SCOPED_REQUEST_STATUS_MISMATCH',
    'BOT_SCOPE_STATUS_MISMATCH',
)

DEPT_OWNED_LOCAL_PROFILE = RouteProfile(
    name='owned-local',
    entrypoint_kind='owned-local',
    routes={
        'ensure': RouteBinding(
            operation='ensure', method='POST',
            path='/admin/bots/{bot_uuid}/ensure', success_statuses=(200,),
            actor='SERVICE_KEY'),
        'mixed_patch': RouteBinding(
            operation='mixed_patch', method='PATCH',
            path='/openapi/v1/collaboration/bots/{bot_id}',
            success_statuses=(200,), actor='BOT_OWNER'),
        'no_check_create': RouteBinding(
            operation='no_check_create', method='POST',
            path='/collaboration/friend-connections/requests',
            success_statuses=(200,), actor='HUMAN_APPLICANT'),
        'scoped_patch': RouteBinding(
            operation='scoped_patch', method='PATCH',
            path='/openapi/v1/collaboration/bots/{bot_id}',
            success_statuses=(200,), actor='BOT_OWNER'),
        'human_scoped_create': RouteBinding(
            operation='human_scoped_create', method='POST',
            path='/collaboration/friend-connections/requests',
            success_statuses=(200, 403), actor='HUMAN_APPLICANT'),
        'bot_scoped_create': RouteBinding(
            operation='bot_scoped_create', method='POST',
            path='/collaboration/friend-connections/requests',
            success_statuses=(200, 403), actor='BOT_CALLER'),
    },
)

DEPT_REMOTE_PUBLIC_PROFILE = RouteProfile(
    name='remote-public',
    entrypoint_kind='preprod-public-v1',
    routes={
        'ensure': RouteBinding(
            operation='ensure', method='POST',
            path='/admin/bots/{bot_uuid}/ensure', success_statuses=(200,),
            actor='SERVICE_KEY', availability='STATION_NATIVE_ONLY'),
        'mixed_patch': RouteBinding(
            operation='mixed_patch', method='PATCH',
            path='/openapi/v1/collaboration/bots/{bot_id}',
            success_statuses=(200,), actor='BOT_OWNER'),
        'no_check_create': RouteBinding(
            operation='no_check_create', method='POST',
            path='/openapi/v1/collaboration/friend-connections/requests',
            success_statuses=(201,), actor='HUMAN_APPLICANT'),
        'scoped_patch': RouteBinding(
            operation='scoped_patch', method='PATCH',
            path='/openapi/v1/collaboration/bots/{bot_id}',
            success_statuses=(200,), actor='BOT_OWNER'),
        'human_scoped_create': RouteBinding(
            operation='human_scoped_create', method='POST',
            path='/openapi/v1/collaboration/friend-connections/requests',
            success_statuses=(201, 403), actor='HUMAN_APPLICANT'),
        'bot_scoped_create': RouteBinding(
            operation='bot_scoped_create', method='POST',
            path='/openapi/v1/collaboration/friend-connections/requests',
            success_statuses=(201, 403), actor='BOT_CALLER'),
    },
)

DEPT_ROUTE_PROFILES = {
    'owned-local': DEPT_OWNED_LOCAL_PROFILE,
    'remote-public': DEPT_REMOTE_PUBLIC_PROFILE,
}

DEPT_EXPECTED_VALUES = {
    'bot_id': FRIEND_EXT_BOT,
    'owner_staff_no': DEPT_OWNER_STAFF_NO,
    'second_human_staff_no': SECOND_HUMAN_STAFF_NO,
    'mixed_no_check_scope_friend_deps': ['F4858', '蚂蚁集团-信贷事业群'],
    'mixed_view_scope_user_friend_deps': [],
    'mixed_view_scope_agent_friend_deps': ['L3480'],
    'scoped_view_scope_user_friend_deps': ['L3480'],
    'scoped_view_scope_agent_friend_deps': ['L3480'],
    'dropped_garbage': '{"priority": 7}',
    'owned_local_statuses': {
        'ensure': 200,
        'mixed_patch': 200,
        'no_check_create': 200,
        'scoped_patch': 200,
        'human_scoped_create': [200, 403],
        'bot_scoped_create': [200, 403],
    },
    'upstream_failure': 'friend_ext object entries are not parsed into the expected scope labels',
}


def load_dept_contract() -> tuple[dict, dict[str, RouteProfile]]:
    return _load_profile_contract(
        DEPT_CONTRACT_FILE,
        case_id=DEPT_CASE_ID,
        parent=DEPT_PARENT,
        requirement_id=DEPT_REQUIREMENT_ID,
        function_name=DEPT_FUNCTION_NAME,
        requirement_method='PATCH',
        requirement_path='/openapi/v1/collaboration/bots/{bot_id}',
        expected_route_profiles=DEPT_ROUTE_PROFILES,
        expected_anchors=DEPT_SOURCE_ANCHORS,
        expected_failure_codes=DEPT_FAILURE_CODES,
    )


def scope_entry_label(item: object) -> str | None:
    """Mirror ``friend_ext_scope_entry_match_label`` in the Rust service."""
    if isinstance(item, str):
        value = item.strip()
        return value or None
    if not isinstance(item, Mapping):
        return None
    for key in ('deptName', 'dept_name', 'name', 'deptNo', 'dept_no'):
        value = item.get(key)
        if isinstance(value, str):
            label = value.strip()
            if label:
                return label
    return None


def friend_ext_scope_deps(friend_ext: Mapping[str, Any], key: str) -> list[str]:
    """Resolve one scope list exactly as the production parser does."""
    if not isinstance(friend_ext, Mapping):
        raise EdgeBacklogFailure(
            'FRIEND_EXT_SHAPE_INVALID', 'friend_ext must be an object',
            observed=type(friend_ext).__name__)
    if key not in SCOPE_KEYS:
        raise EdgeBacklogFailure(
            'FRIEND_EXT_SHAPE_INVALID', 'unknown friend_ext scope key', observed=key)
    items = friend_ext.get(key)
    if items is None:
        return []
    if not isinstance(items, list):
        raise EdgeBacklogFailure(
            'FRIEND_EXT_SHAPE_INVALID', key + ' must be an array',
            observed=type(items).__name__)
    labels = []
    for item in items:
        label = scope_entry_label(item)
        if label is not None:
            labels.append(label)
    return sorted(labels)


def parse_friend_ext_scopes(friend_ext: object) -> dict:
    """Return normalized scope labels and the indexes of dropped entries."""
    if not isinstance(friend_ext, Mapping):
        raise EdgeBacklogFailure(
            'FRIEND_EXT_SHAPE_INVALID', 'friend_ext must be an object',
            observed=type(friend_ext).__name__)
    result = {}
    dropped = {}
    for key in SCOPE_KEYS:
        result[key] = friend_ext_scope_deps(friend_ext, key)
        raw = friend_ext.get(key)
        if isinstance(raw, list):
            dropped[key] = [index for index, item in enumerate(raw)
                            if scope_entry_label(item) is None]
    return {'scopes': result, 'dropped': dropped}


def assert_dept_scope_parse(friend_ext: object, expected: Mapping[str, object]) -> dict:
    parsed = parse_friend_ext_scopes(friend_ext)
    require_equal(parsed['scopes'], {
        'no_check_scope_friend_deps': list(expected['no_check_scope_friend_deps']),
        'view_scope_user_friend_deps': list(expected['view_scope_user_friend_deps']),
        'view_scope_agent_friend_deps': list(expected['view_scope_agent_friend_deps']),
    }, 'FRIEND_EXT_PARSE_MISMATCH',
        'friend_ext scope labels differ from the production parser contract')
    return parsed


def assert_dept_owned_local_transcript(transcript: Mapping[str, Any]) -> dict:
    """Validate the mixed/object/string/garbage parser branches and statuses."""
    if not isinstance(transcript, Mapping):
        raise EdgeBacklogFailure(
            'TRANSCRIPT_INVALID', 'owned-local transcript must be an object',
            observed=type(transcript).__name__)
    required = {'ensure_status', 'mixed_patch_status', 'mixed_scope',
                'no_check_status', 'scoped_patch_status', 'scoped_scope',
                'human_scoped_status', 'bot_scoped_status'}
    if set(transcript) != required:
        raise EdgeBacklogFailure(
            'TRANSCRIPT_INVALID', 'owned-local transcript has unexpected keys',
            expected=sorted(required), observed=sorted(transcript))
    require_status(transcript['ensure_status'], DEPT_OWNED_LOCAL_PROFILE.route('ensure'),
                   'ENSURE_STATUS_MISMATCH')
    require_status(transcript['mixed_patch_status'], DEPT_OWNED_LOCAL_PROFILE.route('mixed_patch'),
                   'PATCH_STATUS_MISMATCH')
    mixed = assert_dept_scope_parse(transcript['mixed_scope'], {
        'no_check_scope_friend_deps': DEPT_EXPECTED_VALUES['mixed_no_check_scope_friend_deps'],
        'view_scope_user_friend_deps': DEPT_EXPECTED_VALUES['mixed_view_scope_user_friend_deps'],
        'view_scope_agent_friend_deps': DEPT_EXPECTED_VALUES['mixed_view_scope_agent_friend_deps'],
    })
    require_status(transcript['no_check_status'], DEPT_OWNED_LOCAL_PROFILE.route('no_check_create'),
                   'HUMAN_REQUEST_STATUS_MISMATCH')
    require_status(transcript['scoped_patch_status'], DEPT_OWNED_LOCAL_PROFILE.route('scoped_patch'),
                   'SCOPED_PATCH_STATUS_MISMATCH')
    scoped = assert_dept_scope_parse(transcript['scoped_scope'], {
        'no_check_scope_friend_deps': DEPT_EXPECTED_VALUES['mixed_no_check_scope_friend_deps'],
        'view_scope_user_friend_deps': DEPT_EXPECTED_VALUES['scoped_view_scope_user_friend_deps'],
        'view_scope_agent_friend_deps': DEPT_EXPECTED_VALUES['scoped_view_scope_agent_friend_deps'],
    })
    require_status(transcript['human_scoped_status'],
                   DEPT_OWNED_LOCAL_PROFILE.route('human_scoped_create'),
                   'SCOPED_REQUEST_STATUS_MISMATCH')
    require_status(transcript['bot_scoped_status'],
                   DEPT_OWNED_LOCAL_PROFILE.route('bot_scoped_create'),
                   'BOT_SCOPE_STATUS_MISMATCH')
    return {
        'status': 'LOCAL_MODEL_PASS',
        'case_id': DEPT_CASE_ID,
        'bot_id': FRIEND_EXT_BOT,
        'mixed_scopes': mixed['scopes'],
        'mixed_dropped': mixed['dropped'],
        'scoped_scopes': scoped['scopes'],
        'scoped_dropped': scoped['dropped'],
        'human_scoped_status': transcript['human_scoped_status'],
        'bot_scoped_status': transcript['bot_scoped_status'],
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }


class RemoteDeptScopeFriendExtAdapter(RemoteEdgeBacklogAdapter):
    """Remote slot for this case; intentionally has no transport."""

    def __init__(self):
        super().__init__(DEPT_CASE_ID, DEPT_REMOTE_PUBLIC_PROFILE)
