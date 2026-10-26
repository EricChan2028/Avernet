"""Owned-local public Session API, connection JWT, and WebSocket closure.

This is the concrete first helper of the complete Session parent.  It exercises
only one foreground-owned loopback BCS, never starts an Engine or Bot receiver,
and never treats local control-path closure as business Session terminality or
remote quiescence.  Every request belongs to the finite operation table below;
mutation intent is durable before dispatch and an ambiguous response is never
retried, adopted, or automatically cleaned up.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request
import uuid

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .ledger import Ledger, LedgerError
from .local_role_identity import OwnedLocalRoleIdentity
from .local_session_contract import (
    PUBLIC_CASE as CASE_ID,
    PUBLIC_SUITE as SUITE,
    REQUIREMENTS,
    session_source_binding,
)
from .local_sqlite import OwnedSqliteDatabase
from .owned_bcs import file_sha256
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write
from .ws_transport import SessionWebSocketClient, WebSocketTransportError

ROLES = ('CEO', 'PM', 'ENG', 'QA')
CONTRACT = 'owned-local-session-public-v1'
BINDING_NAME = 'session-binding.json'
JOURNAL_NAME = 'local-session-public.json'
LEDGER_NAME = 'session-public-resources.sqlite'
PUBLIC_PREFIX = '/openapi/v1/collaboration'
MAX_RESPONSE_BYTES = 65_536

# Ordered, non-extensible operation inventory. Dynamic paths are derived only
# from IDs independently bound in the durable Ledger.
OPERATIONS = (
    'groups-missing-principal', 'groups-unknown-field',
    'bots-mine', 'bots-query', 'bot-detail',
    'group-create', 'group-list', 'group-detail', 'group-patch',
    'session-create', 'session-list', 'session-list-pm', 'session-detail', 'session-patch',
    'session-messages', 'session-token', 'session-ws-connect',
    'session-delete', 'session-deleted-404',
    'group-delete', 'group-deleted-404',
)
MUTATIONS = {'group-create', 'group-patch', 'session-create', 'session-patch',
             'session-delete', 'group-delete'}


class LocalSessionPublicError(RunStateError):
    pass


def _credential(value: object, label: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise LocalSessionPublicError(f'{label} is missing or invalid')
    return value


def _json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


class OwnedLocalSessionPublic:
    """Execute exactly ``SESSION.public-jwt-ws`` in one owned local runtime."""

    def __init__(self, identities: OwnedLocalRoleIdentity):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise LocalSessionPublicError(
                'Session public helper requires freshly verified CEO/PM/ENG/QA owned-local identities')
        identities._require_roles(ROLES)
        self.identities = identities
        self.runtime = identities.runtime
        if self.runtime.store_messages is not True:
            raise LocalSessionPublicError('Session public helper requires store_messages=True')
        parsed = urlsplit(identities.origin)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise LocalSessionPublicError('Session public helper is restricted to the owned loopback origin')
        self.root = self.runtime.root
        self.path = self.root / JOURNAL_NAME
        self.ledger_path = self.root / LEDGER_NAME
        self.binding_path = self.root / BINDING_NAME
        self.executor_digest = executor_digest()
        self.source_binding = session_source_binding()
        self.binding = self._validated_binding()
        self.binding_digest = fingerprint(self.binding)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.state: dict | None = None
        self.ledger: Ledger | None = None
        self._journal_digest = ''
        self._invalidated = False
        self._principal = ''
        self._connection_token = ''
        self._ws_connection = None
        self._ws_state = 'NOT_OPENED'
        self._group_handle = ''
        self._initial_session_handle = ''
        self._explicit_session_handle = ''

    def _validated_binding(self) -> dict:
        expected_keys = {
            'schema_version', 'contract', 'suite', 'case_id', 'run_id', 'executor_digest',
            'bcs_binary_sha256', 'source_binding', 'selected_requirement_ids',
            'parent_requirement_ids', 'legacy_inventory', 'runtime_dir', 'store_messages',
        }
        try:
            if self.binding_path.is_symlink() or not self.binding_path.is_file():
                raise ValueError
            binding = read(self.binding_path)
            if (set(binding) != expected_keys or binding['schema_version'] != 1
                    or binding['contract'] != CONTRACT or binding['suite'] != SUITE
                    or binding['case_id'] != CASE_ID or binding['run_id'] != self.runtime.run_id
                    or binding['executor_digest'] != self.executor_digest
                    or binding['bcs_binary_sha256'] != self.runtime.binary_sha256
                    or binding['source_binding'] != self.source_binding
                    or binding['selected_requirement_ids'] != [REQUIREMENTS[0]]
                    or binding['parent_requirement_ids'] != list(REQUIREMENTS)
                    or binding['legacy_inventory'] != current_manifest()['legacy_inventory']
                    or binding['runtime_dir'] != str(self.root.resolve())
                    or binding['store_messages'] is not True):
                raise ValueError
            return binding
        except (KeyError, OSError, TypeError, ValueError, RunStateError):
            raise LocalSessionPublicError('Session public execution binding is missing or changed') from None

    @staticmethod
    def _exclusive(path: Path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise LocalSessionPublicError('Session public helper requires fresh journal and ledger paths') from None
        os.close(fd)

    def _check_file(self, path: Path, digest: str, label: str):
        if not digest:
            return
        try:
            matches = fingerprint(read(path)) == digest
        except Exception:
            matches = False
        if not matches:
            self._invalidated = True
            raise LocalSessionPublicError(f'{label} changed; preserve the original artifact')

    def _save(self):
        self._check_file(self.path, self._journal_digest, 'Session public journal')
        serialized = json.dumps(self.state, sort_keys=True, separators=(',', ':'), allow_nan=False)
        sensitive = (self._principal, self._connection_token,
                     self.runtime.inputs.environment.get('AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE', ''),
                     self.runtime.inputs.environment.get('BCS_SECRET_BCN_GROUP_SESSION_WS_JWT', ''))
        if any(secret and secret in serialized for secret in sensitive):
            self._invalidated = True
            raise LocalSessionPublicError('Session public credential leaked into durable evidence')
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _active(self):
        if self._invalidated:
            raise LocalSessionPublicError('Session public mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(session_source_binding()) != fingerprint(self.source_binding)
                or fingerprint(self._validated_binding()) != self.binding_digest
                or self.runtime.store_messages is not True):
            raise LocalSessionPublicError('Session source, fixture, runtime, or binding changed')
        self._check_file(self.path, self._journal_digest, 'Session public journal')

    def _id(self, role: str) -> str:
        self._active()
        return self.identities._entries[role][0]

    def _sign_principal(self) -> str:
        """Create one short-lived Principal in memory from the owned config key."""
        self._active()
        key = _credential(
            self.runtime.inputs.environment.get('AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'),
            'owned local Principal signing key')
        now = int(time.time())
        header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
        claims = {'iss': 'gateway', 'aud': 'bcs', 'iat': now, 'exp': now + 60,
                  'principals': [{'type': 'user', 'tenant': 'e2e-local', 'subject': {
                      'id': self.identities.human, 'username': 'E2E local test human',
                      'display_name': None, 'full_name': None, 'tenant_id': 'e2e-local'}}]}

        def encode(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')

        parts = [encode(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
                 for value in (header, claims)]
        signing_input = '.'.join(parts)
        principal = signing_input + '.' + encode(
            hmac.new(key.encode(), signing_input.encode('ascii'), hashlib.sha256).digest())
        self._principal = principal
        return principal

    def _clear_principal(self):
        self._principal = ''

    def _resource(self, handle: str, *, kind: str, resource_id: str | None = None,
                  state: str = 'live') -> dict:
        if self.ledger is None or not handle:
            raise LocalSessionPublicError('Session public resource ledger is unavailable')
        try:
            row = dict(self.ledger._get(handle))
        except LedgerError:
            raise LocalSessionPublicError('Session public resource handle is missing') from None
        if (row['kind'] != kind or row['case_id'] != CASE_ID or row['attempt'] != 1
                or row['origin'] != 'created' or row['state'] != state
                or resource_id is not None and row['resource_id'] != resource_id):
            raise LocalSessionPublicError('Session public request target is not the exact owned Ledger resource')
        return row

    def _group_id(self) -> str:
        value = self.state.get('resources', {}).get('group', {}).get('id')
        identifier(value, 'Session public Group ID')
        self._resource(self._group_handle, kind='group', resource_id=value)
        return value

    def _initial_session_id(self) -> str:
        value = self.state.get('resources', {}).get('group', {}).get('initial_session_id')
        identifier(value, 'Session public initial Session ID')
        row = self._resource(self._initial_session_handle, kind='session', resource_id=value)
        if row['parent'] != self._group_handle:
            raise LocalSessionPublicError('Session public initial Session parent changed')
        return value

    def _session_id(self) -> str:
        value = self.state.get('resources', {}).get('session', {}).get('id')
        identifier(value, 'Session public explicit Session ID')
        row = self._resource(self._explicit_session_handle, kind='session', resource_id=value)
        if row['parent'] != self._group_handle:
            raise LocalSessionPublicError('Session public explicit Session parent changed')
        return value

    def _spec(self, operation: str) -> dict:
        """Return the sole reviewed request shape for an operation label."""
        if operation not in OPERATIONS or operation == 'session-ws-connect':
            raise LocalSessionPublicError('Session public operation is not a reviewed HTTP request')
        pm, eng = self._id('PM'), self._id('ENG')
        specs = {
            'groups-missing-principal': ('GET', PUBLIC_PREFIX + '/groups?' + urlencode({
                'view_bot_id': pm, 'kind': 'all'}), None, False, 401),
            'groups-unknown-field': ('POST', PUBLIC_PREFIX + '/groups', {
                'group_kind': 'dm', 'target_actor_id': pm, 'originator': 'untrusted'}, True, 400),
            'bots-mine': ('GET', PUBLIC_PREFIX + '/bots/mine?limit=20', None, True, 200),
            'bots-query': ('POST', PUBLIC_PREFIX + '/bots/query', {'bot_ids': [pm, eng]}, True, 200),
            'bot-detail': ('GET', PUBLIC_PREFIX + '/bots/' + quote(pm, safe=''), None, True, 200),
            'group-create': ('POST', PUBLIC_PREFIX + '/groups', {
                'group_kind': 'normal', 'name': 'JWT connection E2E',
                'context': 'Validate the public collaboration connection',
                'driver_bot_uuid': pm,
                'participants': [{'actor_id': pm, 'role': 'driver'},
                                 {'actor_id': eng, 'role': 'consultant'}],
                'collaboration': {'strategy': 'chat', 'delivery_policy': {
                    'bot_final_delivery': 'send_to_driver'}},
            }, True, 201),
        }
        if operation in specs:
            method, path, body, auth, status = specs[operation]
            return {'method': method, 'path': path, 'body': body, 'auth': auth,
                    'expected_status': status}
        group_id = self._group_id()
        session_id = None
        if operation.startswith('session-') and operation != 'session-create':
            session_id = self._session_id()
        dynamic = {
            'group-list': ('GET', PUBLIC_PREFIX + '/groups?' + urlencode({
                'view_bot_id': pm, 'kind': 'all', 'membership': 'direct', 'limit': 20}), None, 200),
            'group-detail': ('GET', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe=''), None, 200),
            'group-patch': ('PATCH', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe=''),
                            {'name': 'JWT connection E2E updated'}, 200),
            'session-create': ('POST', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe='') + '/sessions',
                               {'title': 'JWT connection E2E',
                                'input': {'query': 'Validate the public WebSocket connection'}}, 201),
            'group-delete': ('DELETE', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe=''), None, 200),
            'group-deleted-404': ('GET', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe=''), None, 404),
        }
        if operation not in dynamic:
            sid = quote(session_id, safe='')
            dynamic.update({
                'session-list': ('GET', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe='') +
                                 '/sessions?limit=20', None, 200),
                'session-list-pm': ('GET', PUBLIC_PREFIX + '/groups/' + quote(group_id, safe='') +
                                    '/sessions?' + urlencode({'limit': 20, 'view_bot_id': pm}), None, 200),
                'session-detail': ('GET', PUBLIC_PREFIX + '/sessions/' + sid, None, 200),
                'session-patch': ('PATCH', PUBLIC_PREFIX + '/sessions/' + sid,
                                  {'title': 'JWT connection E2E updated'}, 200),
                'session-messages': ('GET', PUBLIC_PREFIX + '/sessions/' + sid + '/messages?' +
                                     urlencode({'limit': 50, 'view_bot_id': pm}), None, 200),
                'session-token': ('POST', PUBLIC_PREFIX + '/sessions/' + sid + '/token', None, 200),
                'session-delete': ('DELETE', PUBLIC_PREFIX + '/sessions/' + sid, None, 200),
                'session-deleted-404': ('GET', PUBLIC_PREFIX + '/sessions/' + sid, None, 404),
            })
        method, path, body, status = dynamic[operation]
        return {'method': method, 'path': path, 'body': body, 'auth': True,
                'expected_status': status}

    def _append_operation(self, operation: str, spec: dict, *, handles: tuple[str, ...] = (),
                          request_id: str | None = None) -> dict:
        self._active()
        index = len(self.state['operations'])
        if index >= len(OPERATIONS) or OPERATIONS[index] != operation:
            raise LocalSessionPublicError('Session public operation order differs from the finite plan')
        request_id = request_id or 'e2e-session-public-' + uuid.uuid4().hex
        identifier(request_id, 'Session public request ID')
        event = {'operation': operation, 'method': spec['method'], 'path': spec['path'],
                 'submitted_request_id': request_id,
                 'status': 'INTENDED', 'kind': 'MUTATION' if operation in MUTATIONS else 'CHECK',
                 'transitions': [{'status': 'INTENDED'}]}
        if handles:
            event['resource_handles'] = list(handles)
            event['resource_binding'] = 'UNKNOWN'
        self.state['operations'].append(event)
        self._save()
        return event

    @staticmethod
    def _envelope(value: dict, *, code: int, message: str | None = None) -> object:
        if (set(value) != {'code', 'message', 'data', 'request_id'} or value.get('code') != code
                or not isinstance(value.get('message'), str)
                or message is not None and value['message'] != message
                or not isinstance(value.get('request_id'), str) or not value['request_id']):
            raise LocalSessionPublicError('Session public response envelope is not exact')
        return value['data']

    @classmethod
    def _error_envelope(cls, value: dict, *, code: int, error_code: str):
        data = cls._envelope(value, code=code)
        if data != {'error_code': error_code}:
            raise LocalSessionPublicError('Session public error envelope is not exact')

    @staticmethod
    def _page_contains(data: object, field: str, expected: str):
        if (not isinstance(data, dict) or not {'items', 'total', 'offset', 'limit'} <= set(data)
                or not isinstance(data['items'], list) or len(data['items']) > 50
                or not any(isinstance(row, dict) and row.get(field) == expected for row in data['items'])):
            raise LocalSessionPublicError('Session public page omitted the exact owned resource')

    @staticmethod
    def _token_response(data: object, headers) -> str:
        cache_control = headers.get('cache-control')
        pragma = headers.get('pragma')
        if (cache_control != 'no-store' or pragma != 'no-cache' or not isinstance(data, dict)
                or set(data) != {'token', 'expires_at'} or type(data['expires_at']) is not int):
            raise LocalSessionPublicError('Session connection token response is not exact or cache-safe')
        return _credential(data['token'], 'Session connection token')

    def _request(self, operation: str, *, handles: tuple[str, ...] = (),
                 request_id: str | None = None) -> tuple[dict, dict[str, str]]:
        spec = self._spec(operation)
        event = self._append_operation(operation, spec, handles=handles, request_id=request_id)
        headers = {'Accept': 'application/json', 'X-Request-ID': event['submitted_request_id']}
        payload = None
        try:
            if spec['auth']:
                headers['X-Avernet-Principal'] = self._sign_principal()
            if spec['body'] is not None:
                headers['Content-Type'] = 'application/json'
                payload = json.dumps(spec['body'], separators=(',', ':'), allow_nan=False).encode()
            request = Request(self.identities.origin + spec['path'], method=spec['method'],
                              data=payload, headers=headers)
            try:
                response = self.identities.opener.open(request, timeout=5)
            except HTTPError as error:
                if error.code != spec['expected_status']:
                    raise
                response = error
            with response:
                status = response.status
                selected_headers = {name: response.headers.get(name) for name in ('cache-control', 'pragma')}
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            self._active()
            if status != spec['expected_status'] or len(raw) > MAX_RESPONSE_BYTES:
                raise LocalSessionPublicError('Session public response status or byte budget differs')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(value, dict):
                raise LocalSessionPublicError('Session public response is not an object')
            event.update(status='ACKNOWLEDGED', http_status=status)
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': status})
            if handles:
                event['resource_binding'] = 'PENDING_RESPONSE_VALIDATION'
            if operation == 'session-token':
                event['cache_control'] = selected_headers['cache-control']
                event['pragma'] = selected_headers['pragma']
            self._save()
            return value, selected_headers
        except BaseException as error:
            self._invalidated = True
            event['status'] = 'UNKNOWN' if operation in MUTATIONS else 'FAILED'
            event['error_type'] = type(error).__name__
            event['transitions'].append({'status': event['status'], 'error_type': event['error_type']})
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            if handles and self.ledger is not None:
                for handle in handles:
                    try:
                        self.ledger.unknown(handle)
                    except BaseException:
                        pass
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise LocalSessionPublicError(
                'Session public request failed; retain unknown outcome without retry or adoption') from None
        finally:
            self._clear_principal()

    def _check(self, check_id: str, evidence: dict):
        if (not isinstance(check_id, str) or not check_id
                or any(row['id'] == check_id for row in self.state['checks'])):
            raise LocalSessionPublicError('Session public check ID is invalid or duplicated')
        self.state['checks'].append({'id': check_id, 'status': 'PASS', 'evidence': evidence})
        self._save()

    def _fixture_snapshot(self) -> dict:
        self._active()
        receipt = read(self.identities.fixtures.path)
        roles = receipt['roles']
        bot_ids = [roles[role]['bot_id'] for role in ROLES]
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT bot_uuid,env,status,created_by FROM bcs_bots '
                'WHERE bot_uuid IN (?,?,?,?) ORDER BY bot_uuid LIMIT 5', bot_ids).fetchall()]
        if (len(rows) != 4 or {row['bot_uuid'] for row in rows} != set(bot_ids)
                or any(row['env'] != 'local' or row['status'] != 'online'
                       or row['created_by'] != self.identities.human for row in rows)):
            raise LocalSessionPublicError('Session fixture snapshot is not the exact owned workspace set')
        return {'fixture_digest': fingerprint(receipt), 'role_ids': {
            role: roles[role]['bot_id'] for role in ROLES},
            'bot_rows_digest': fingerprint({'rows': rows}), 'bot_rows': len(rows)}

    @staticmethod
    def _bounded_rows(db, sql: str, params: tuple, maximum: int) -> list[dict]:
        rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
        if len(rows) > maximum:
            raise LocalSessionPublicError('Session SQLite snapshot exceeded its finite bound')
        return rows

    def _unrelated_snapshot(self, *, exclude_group_id: str | None = None) -> dict:
        """Finite control snapshot, excluding only this run's exact owned Group."""
        if exclude_group_id is not None:
            identifier(exclude_group_id, 'excluded owned Group ID')
            group_clause, params = ' AND group_id<>?', ('local', exclude_group_id)
        else:
            group_clause, params = '', ('local',)
        queries = {
            'groups': ('SELECT group_id,status,driver_bot,group_kind,group_strategy '
                       'FROM bcs_groups WHERE env=?' + group_clause + ' ORDER BY group_id', params, 8),
            'group_participants': ('SELECT group_id,bot_uuid,role,actor_kind,mode '
                                   'FROM bcs_group_participants WHERE env=?' + group_clause +
                                   ' ORDER BY group_id,bot_uuid', params, 32),
            'sessions': ('SELECT session_id,group_id,status,session_kind,session_title '
                         'FROM bcs_group_sessions WHERE env=?' + group_clause +
                         ' ORDER BY session_id', params, 16),
            'session_participants': ('SELECT session_id,group_id,bot_uuid,role '
                                     'FROM bcs_session_participants WHERE env=?' + group_clause +
                                     ' ORDER BY session_id,bot_uuid', params, 64),
            'messages': ('SELECT message_id,group_id,session_id,status FROM bcs_messages '
                         'WHERE env=?' + group_clause + ' ORDER BY message_id', params, 64),
            'deliveries': ('SELECT delivery_id,group_id,session_id,status FROM bcs_message_deliveries '
                           'WHERE env=?' + group_clause + ' ORDER BY delivery_id', params, 64),
            'state_machine_runs': ('SELECT run_id,group_id,session_id,status FROM bcs_state_machine_runs '
                                   'WHERE env=?' + group_clause + ' ORDER BY run_id', params, 16),
            'provider_bindings': ('SELECT bot_uuid FROM bcs_provider_bot_bindings '
                                  'WHERE env=? ORDER BY bot_uuid', ('local',), 16),
        }
        result = {}
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            for name, (sql, values, maximum) in queries.items():
                rows = self._bounded_rows(db, sql, values, maximum)
                result[name] = {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}
        return result

    def _owned_storage(self):
        from .local_session_storage import owned_storage
        return owned_storage(self)

    def _deletion_admission(self, target: str) -> dict:
        self._active()
        if target not in ('session', 'group'):
            raise LocalSessionPublicError('Session deletion target is not reviewed')
        if (self.runtime.state != 'RUNNING' or not self.runtime.ready
                or self.runtime.child is None or self.runtime.child.poll() is not None
                or not self.runtime._owns_listener(1)):
            raise LocalSessionPublicError('Session deletion requires live foreground-owned execution')
        if self._ws_state != 'CLOSED' or self._ws_connection is not None:
            raise LocalSessionPublicError('Session deletion requires the owned WebSocket to be closed first')
        config = json.loads(self.runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
        if (file_sha256(self.runtime.inputs.config_path) != self.identities.fixtures.config_digest
                or config.get('store_messages') is not True
                or config.get('eventing') != {'enabled': False, 'dispatcher_enabled': False}
                or config.get('collaboration', {}).get('loop_execution_enabled') is not False
                or config.get('llm') != {'type': 'none'}
                or config.get('human_notify') != {'providers': []}
                or config.get('security', {}).get('outbound_url') != {
                    'block_private_networks': True, 'allow_loopback': False}):
            raise LocalSessionPublicError('Session deletion requires isolated local transport-only configuration')
        group_id = self._group_id()
        bot_ids = [self._id(role) for role in ROLES]
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            providers = db.execute(
                'SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? '
                'AND bot_uuid IN (?,?,?,?) LIMIT 1', ('local', *bot_ids)).fetchall()
            deliveries = db.execute(
                'SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id=? LIMIT 1',
                ('local', group_id)).fetchall()
            runs = db.execute(
                'SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id=? LIMIT 1',
                ('local', group_id)).fetchall()
        if providers or deliveries or runs:
            raise LocalSessionPublicError('Session deletion cannot admit Provider, queued delivery, or state-machine work')
        return {'target': target, 'status': 'LOCAL_DELETION_ADMITTED',
                'contract': 'owned-local-foreground-transport-only-v1',
                'foreground_runtime': 'LIVE_OWNED_CHILD', 'websocket': 'CLOSED',
                'isolated_config': True, 'store_messages': True, 'provider_bindings': 0,
                'queued_deliveries': 0, 'state_machine_runs': 0,
                'receiver_mode': 'NOT_OPENED', 'engine_started_by_executor': False,
                'business_session_terminal': 'NOT_CLAIMED',
                'remote_quiescence': 'NOT_APPLICABLE'}

    def _verify_session_deleted_storage(self) -> dict:
        group_id, initial_id = self._group_id(), self._initial_session_id()
        session_id = self.state['resources']['session']['id']
        self._resource(self._explicit_session_handle, kind='session', resource_id=session_id)
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            counts = {
                'session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND session_id=?',
                                           ('local', session_id)).fetchone()[0],
                'session_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND session_id=?',
                                                       ('local', session_id)).fetchone()[0],
                'message_rows': db.execute('SELECT COUNT(*) FROM bcs_messages WHERE env=? AND session_id=?',
                                           ('local', session_id)).fetchone()[0],
                'delivery_rows': db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND session_id=?',
                                            ('local', session_id)).fetchone()[0],
                'state_machine_run_rows': db.execute('SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND session_id=?',
                                                     ('local', session_id)).fetchone()[0],
                'group_rows': db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?',
                                         ('local', group_id)).fetchone()[0],
                'initial_session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND session_id=?',
                                                   ('local', initial_id)).fetchone()[0],
            }
        expected = {'session_rows': 0, 'session_participant_rows': 0,
                    'message_rows': 2, 'delivery_rows': 0,
                    'state_machine_run_rows': 0, 'group_rows': 1,
                    'initial_session_rows': 1}
        if counts != expected:
            raise LocalSessionPublicError('Explicit Session deletion cascade is not exact')
        from .local_session_storage import history_snapshot
        if history_snapshot(self) != self.state['storage_before_delete']['history']:
            raise LocalSessionPublicError('Explicit Session deletion changed retained bootstrap history')
        return {'status': 'PASS', **counts, 'history_preserved': True}

    def _verify_group_deleted_storage(self) -> dict:
        group_id = self.state['resources']['group']['id']
        session_ids = (self.state['resources']['group']['initial_session_id'],
                       self.state['resources']['session']['id'])
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            counts = {
                'group_rows': db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?',
                                         ('local', group_id)).fetchone()[0],
                'group_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_group_participants WHERE env=? AND group_id=?',
                                                     ('local', group_id)).fetchone()[0],
                'session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND group_id=?',
                                           ('local', group_id)).fetchone()[0],
                'session_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND group_id=?',
                                                       ('local', group_id)).fetchone()[0],
                'message_rows': db.execute('SELECT COUNT(*) FROM bcs_messages WHERE env=? AND group_id=?',
                                           ('local', group_id)).fetchone()[0],
                'delivery_rows': db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND group_id=?',
                                            ('local', group_id)).fetchone()[0],
                'state_machine_run_rows': db.execute('SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND group_id=?',
                                                     ('local', group_id)).fetchone()[0],
            }
        if any(value != (4 if key == 'message_rows' else 0) for key, value in counts.items()):
            raise LocalSessionPublicError('Group deletion cascade or retained history is not exact')
        from .local_session_storage import history_snapshot
        if history_snapshot(self) != self.state['storage_before_delete']['history']:
            raise LocalSessionPublicError('Group deletion changed retained bootstrap history')
        if len(set(session_ids)) != 2:
            raise LocalSessionPublicError('Session deletion inventory lost distinct Session IDs')
        return {'status': 'PASS', **counts, 'history_preserved': True}

    def _connect_websocket(self, token: str):
        if len(self.state['operations']) != OPERATIONS.index('session-ws-connect'):
            raise LocalSessionPublicError('Session WebSocket operation order differs from the finite plan')
        self._connection_token = _credential(token, 'Session connection token')
        upgrade_request_id = 'e2e-session-public-upgrade-' + uuid.uuid4().hex
        connect_request_id = 'e2e-session-public-connect-' + uuid.uuid4().hex
        event = {'operation': 'session-ws-connect', 'method': 'WS',
                 'path': PUBLIC_PREFIX + '/messages/ws?token=[REDACTED]',
                 'submitted_request_id': upgrade_request_id,
                 'connect_request_id': connect_request_id,
                 'status': 'INTENDED', 'kind': 'CHECK',
                 'transitions': [{'status': 'INTENDED'}]}
        self.state['operations'].append(event)
        self._ws_state = 'CONNECTING'
        self.state['websocket'] = {'status': 'CONNECTING', 'messages_sent': 0,
                                   'business_messages_received': 0}
        self._save()
        connection = None
        try:
            client = SessionWebSocketClient(self.identities.origin, timeout_seconds=10)
            connection, record = client.connect(
                self._connection_token, self._group_id(), self._session_id(),
                upgrade_request_id=upgrade_request_id, connect_request_id=connect_request_id)
            self._ws_connection = connection
            self._ws_state = 'CONNECTED'
            if (record.get('http_status') != 101 or record.get('connected') is not True
                    or record.get('group_id') != self._group_id()
                    or record.get('session_id') != self._session_id()
                    or record.get('submitted_request_id') != upgrade_request_id
                    or record.get('connect_request_id') != connect_request_id):
                raise LocalSessionPublicError('Session WebSocket did not prove exact HTTP 101 binding')
            event.update(status='ACKNOWLEDGED', http_status=101,
                         request_id_echoed=record.get('request_id_echoed') is True)
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': 101})
            self.state['websocket'].update(status='CONNECTED', http_status=101,
                                           connect_authorization='ACKNOWLEDGED_NO_CHAT_MESSAGES')
            self._save()
            connection.close()
            if not connection.closed:
                raise LocalSessionPublicError('Session WebSocket close was not locally confirmed')
            self._ws_connection = None
            self._ws_state = 'CLOSED'
            self.state['websocket'].update(status='CLOSED', close_code=1000,
                                           closure='EXPLICIT_CLIENT_CLOSE')
            self._save()
        except BaseException as error:
            self._invalidated = True
            if connection is not None and not connection.closed:
                try:
                    connection.close()
                except BaseException:
                    pass
            self._ws_connection = None
            self._ws_state = 'FAILED'
            event['status'] = 'FAILED'
            event['error_type'] = type(error).__name__
            event['transitions'].append({'status': 'FAILED', 'error_type': type(error).__name__})
            self.state['websocket'] = {'status': 'FAILED', 'messages_sent': 0,
                                       'business_messages_received': 0,
                                       'socket_close_status': 'CLOSED' if connection and connection.closed
                                                              else 'UNCONFIRMED'}
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise LocalSessionPublicError('Session WebSocket failed; no reconnect or deletion attempted') from None
        finally:
            self._connection_token = ''

    def _ledger_evidence(self) -> dict:
        if self.ledger is None:
            raise LocalSessionPublicError('Session Ledger is unavailable')
        rows = self.ledger.resource_snapshot()
        readback = []
        for row in rows:
            readback.append({key: row[key] for key in (
                'handle', 'case_id', 'attempt', 'kind', 'parent', 'request_id',
                'origin', 'resource_id', 'state')})
        return {'path': LEDGER_NAME, 'status': 'READ_BACK', 'resource_count': len(readback),
                'resources': readback, 'cleanup_plan': self.ledger.cleanup_plan()}

    def run(self) -> dict:
        self._active()
        if (self.path.exists() or self.path.is_symlink()
                or self.ledger_path.exists() or self.ledger_path.is_symlink()):
            raise LocalSessionPublicError('Session public helper requires fresh evidence paths')
        self._exclusive(self.path)
        self.state = {
            'schema_version': 1, 'contract': CONTRACT, 'suite': SUITE,
            'case_id': CASE_ID, 'run_id': self.runtime.run_id, 'status': 'RUNNING',
            'executor_digest': self.executor_digest, 'binding_digest': self.binding_digest,
            'fixture_digest': self.fixture_digest, 'requirement_id': REQUIREMENTS[0],
            'source_binding_digest': fingerprint(self.source_binding),
            'operations': [], 'checks': [], 'resources': {},
            'assertions': [{'id': REQUIREMENTS[0], 'status': 'NOT_RUN'}],
            'websocket': {'status': 'NOT_OPENED', 'messages_sent': 0,
                          'business_messages_received': 0},
            'deletion_admission': [], 'ledger': {'status': 'NOT_OPENED'},
            'execution_scope': {'runtime': 'OWNED_LOCAL_FOREGROUND', 'remote_capabilities': 'NONE',
                                'receiver_mode': 'NOT_OPENED', 'engine_started_by_executor': False,
                                'business_session_terminal': 'NOT_CLAIMED',
                                'remote_quiescence': 'NOT_APPLICABLE'},
            'credential_persistence': {'gateway_principal': False, 'connection_token': False,
                                       'websocket_url': False, 'private_signing_key': False},
            'full_story_status': 'NOT_MIGRATED', 'ready_to_execute': False,
        }
        self._save()
        try:
            with Ledger(self.ledger_path, self.runtime.run_id, self.binding_digest, create=True) as ledger:
                self.ledger = ledger
                fixture_before = self._fixture_snapshot()
                unrelated_before = self._unrelated_snapshot()
                self.state['fixture_before'] = fixture_before
                self.state['unrelated_before'] = unrelated_before
                self._save()

                value, _ = self._request('groups-missing-principal')
                self._error_envelope(value, code=40_100, error_code='unauthenticated')
                self._check('missing-principal-401', {'http_status': 401, 'error_code': 'unauthenticated'})

                value, _ = self._request('groups-unknown-field')
                self._error_envelope(value, code=40_000, error_code='invalid_request')
                self._check('unknown-create-field-400', {'http_status': 400, 'error_code': 'invalid_request'})

                value, _ = self._request('bots-mine')
                mine = self._envelope(value, code=20_000, message='OK')
                if not isinstance(mine, dict) or not isinstance(mine.get('items'), list):
                    raise LocalSessionPublicError('Owned Bot list response is invalid')
                owned_ids = {row.get('bot_id') for row in mine['items'] if isinstance(row, dict)}
                if not {self._id(role) for role in ROLES} <= owned_ids:
                    raise LocalSessionPublicError('Owned Bot list omitted workspace fixtures')
                self._check('public-bots-mine', {'http_status': 200, 'owned_fixture_count': 4})

                value, _ = self._request('bots-query')
                queried = self._envelope(value, code=20_000, message='OK')
                if (not isinstance(queried, dict) or not isinstance(queried.get('items'), list)
                        or {row.get('bot_id') for row in queried['items'] if isinstance(row, dict)}
                        != {self._id('PM'), self._id('ENG')}):
                    raise LocalSessionPublicError('Public Bot query response differs from the exact request')
                self._check('public-bots-query', {'http_status': 200, 'bot_count': 2})

                value, _ = self._request('bot-detail')
                detail = self._envelope(value, code=20_000, message='OK')
                if not isinstance(detail, dict) or detail.get('bot_id') != self._id('PM'):
                    raise LocalSessionPublicError('Public Bot detail did not identify PM')
                self._check('public-bot-detail', {'http_status': 200, 'role': 'PM'})

                group_request = 'e2e-session-public-group-' + uuid.uuid4().hex
                handles = ledger.group_creation_intent(CASE_ID, 1, group_request)
                # The same durable request ID binds Ledger intent and HTTP dispatch.
                value, _ = self._request('group-create', handles=handles, request_id=group_request)
                event = self.state['operations'][-1]
                group = self._envelope(value, code=20_100, message='Created')
                if not isinstance(group, dict):
                    raise LocalSessionPublicError('Public Group create data is invalid')
                group_id = identifier(group.get('group_id'), 'created public Group ID')
                initial_id = identifier(group.get('initial_session_id'), 'created initial Session ID')
                if not initial_id.startswith(group_id + ':'):
                    raise LocalSessionPublicError('Initial Session is not bound to the created Group')
                ledger.observed(handles[0], group_id, origin='created')
                ledger.observed(handles[1], initial_id, origin='created')
                self._group_handle, self._initial_session_handle = handles
                self.state['resources']['group'] = {'id': group_id, 'handle': handles[0],
                                                    'initial_session_id': initial_id,
                                                    'initial_session_handle': handles[1]}
                event['resource_binding'] = 'BOUND'
                event['candidate_ids'] = {'group': group_id, 'initial_session': initial_id}
                self._save()
                self._check('group-create-201', {'http_status': 201, 'implicit_initial_session': True})

                value, _ = self._request('group-list')
                self._page_contains(self._envelope(value, code=20_000, message='OK'), 'group_id', group_id)
                self._check('group-list', {'http_status': 200, 'contains_owned_group': True})

                value, _ = self._request('group-detail')
                group_detail = self._envelope(value, code=20_000, message='OK')
                if not isinstance(group_detail, dict) or group_detail.get('group_id') != group_id:
                    raise LocalSessionPublicError('Public Group detail identity differs')
                self._check('group-detail', {'http_status': 200, 'group_id_match': True})

                value, _ = self._request('group-patch')
                patched = self._envelope(value, code=20_000, message='OK')
                if not isinstance(patched, dict) or patched.get('name') != 'JWT connection E2E updated':
                    raise LocalSessionPublicError('Public Group update did not retain the new name')
                self._check('group-patch', {'http_status': 200, 'name_updated': True})

                session_request = 'e2e-session-public-explicit-' + uuid.uuid4().hex
                session_handle = ledger.intent(CASE_ID, 1, 'session', session_request, parent=handles[0])
                value, _ = self._request('session-create', handles=(session_handle,),
                                         request_id=session_request)
                event = self.state['operations'][-1]
                session = self._envelope(value, code=20_100, message='Created')
                if not isinstance(session, dict):
                    raise LocalSessionPublicError('Public Session create data is invalid')
                session_id = identifier(session.get('session_id'), 'created explicit Session ID')
                if session.get('group_id') != group_id or session_id == initial_id:
                    raise LocalSessionPublicError('Explicit Session identity or Group binding differs')
                ledger.observed(session_handle, session_id, origin='created')
                self._explicit_session_handle = session_handle
                self.state['resources']['session'] = {'id': session_id, 'handle': session_handle,
                                                      'group_id': group_id}
                event['resource_binding'] = 'BOUND'
                event['candidate_ids'] = {'session': session_id}
                self._save()
                self._check('session-create-201', {'http_status': 201, 'explicit_session': True})

                value, _ = self._request('session-list')
                # The original no-view request is Human-relative. This Human owns
                # the Bots but is not a Session participant; ownership is not membership.
                if self._envelope(value, code=20_000, message='OK') != {
                        'items': [], 'total': 0, 'offset': 0, 'limit': 20}:
                    raise LocalSessionPublicError('Human-relative Session page is not the expected empty scope')
                self._check('session-human-view-empty', {'http_status': 200, 'total': 0})
                value, _ = self._request('session-list-pm')
                self._page_contains(self._envelope(value, code=20_000, message='OK'),
                                    'session_id', session_id)
                self._check('session-list', {'http_status': 200, 'contains_explicit_session': True, 'view_role': 'PM'})

                value, _ = self._request('session-detail')
                session_detail = self._envelope(value, code=20_000, message='OK')
                if not isinstance(session_detail, dict) or session_detail.get('session_id') != session_id:
                    raise LocalSessionPublicError('Public Session detail identity differs')
                self._check('session-detail', {'http_status': 200, 'session_id_match': True})

                value, _ = self._request('session-patch')
                patched = self._envelope(value, code=20_000, message='OK')
                if not isinstance(patched, dict) or patched.get('title') != 'JWT connection E2E updated':
                    raise LocalSessionPublicError('Public Session update did not retain the new title')
                self._check('session-patch', {'http_status': 200, 'title_updated': True})

                value, _ = self._request('session-messages')
                messages = self._envelope(value, code=20_000, message='OK')
                from .local_session_storage import verify_history
                self.state['history_readback'] = verify_history(self, messages)
                self._check('session-messages', {'http_status': 200, 'message_count': len(messages),
                                                'kind': 'PERSISTED_PER_RECIPIENT_BOOTSTRAP'})

                value, response_headers = self._request('session-token')
                token = self._token_response(self._envelope(value, code=20_000, message='OK'),
                                             response_headers)
                self._connection_token = token
                self._check('session-token-no-store', {'http_status': 200, 'cache_control': 'no-store',
                                                       'pragma': 'no-cache', 'token_persisted': False})
                self.state['storage_before_delete'] = self._owned_storage()
                self._save()

                self._connect_websocket(token)
                token = ''
                self._check('session-websocket-101-closed', {'http_status': 101, 'status': 'CLOSED',
                                                             'chat_messages_sent': 0})

                admission = self._deletion_admission('session')
                self.state['deletion_admission'].append(admission)
                self._save()
                value, _ = self._request('session-delete')
                deleted = self._envelope(value, code=20_000, message='OK')
                if deleted != {'deleted': True}:
                    raise LocalSessionPublicError('Explicit Session deletion acknowledgement is not exact')
                value, _ = self._request('session-deleted-404')
                self._error_envelope(value, code=40_400, error_code='session_not_found')
                session_cascade = self._verify_session_deleted_storage()
                ledger.mark_deleted(session_handle)
                self.state['session_delete_verification'] = {
                    'status': 'PASS', 'http_status': 200, 'not_found_http_status': 404,
                    'not_found_error_code': 'session_not_found', 'sqlite': session_cascade}
                self._save()
                self._check('session-delete-200-exact-404', {'deleted': True, 'http_status': 404,
                                                             'sqlite_cascade': 'PASS'})

                admission = self._deletion_admission('group')
                self.state['deletion_admission'].append(admission)
                self._save()
                value, _ = self._request('group-delete')
                deleted = self._envelope(value, code=20_000, message='OK')
                if deleted != {'deleted': True}:
                    raise LocalSessionPublicError('Group deletion acknowledgement is not exact')
                value, _ = self._request('group-deleted-404')
                self._error_envelope(value, code=40_400, error_code='group_not_found')
                group_cascade = self._verify_group_deleted_storage()
                ledger.mark_deleted(handles[1])
                ledger.mark_deleted(handles[0])
                self.state['group_delete_verification'] = {
                    'status': 'PASS', 'http_status': 200, 'not_found_http_status': 404,
                    'not_found_error_code': 'group_not_found', 'sqlite': group_cascade}
                self._save()
                self._check('group-delete-200-exact-404', {'deleted': True, 'http_status': 404,
                                                           'sqlite_cascade': 'PASS'})

                fixture_after = self._fixture_snapshot()
                unrelated_after = self._unrelated_snapshot(exclude_group_id=group_id)
                if fixture_after != fixture_before or unrelated_after != unrelated_before:
                    raise LocalSessionPublicError('Session public execution changed unrelated resources or fixtures')
                self.state['fixture_after'] = fixture_after
                self.state['unrelated_after'] = unrelated_after
                self.state['ledger'] = self._ledger_evidence()
                if (len(self.state['operations']) != len(OPERATIONS)
                        or [row['operation'] for row in self.state['operations']] != list(OPERATIONS)
                        or any(row['status'] != 'ACKNOWLEDGED' for row in self.state['operations'])
                        or self.state['websocket']['status'] != 'CLOSED'
                        or len(self.state['deletion_admission']) != 2
                        or self.state['ledger']['resource_count'] != 3
                        or any(row['state'] != 'deleted'
                               for row in self.state['ledger']['resources'])):
                    raise LocalSessionPublicError('Session public final operation or Ledger inventory is incomplete')
                self.state.update(
                    status='LOCAL_SESSION_PUBLIC_PASS',
                    assertions=[{'id': REQUIREMENTS[0], 'status': 'PASS'}],
                    disposition='GROUP_SESSIONS_DELETED_BOOTSTRAP_HISTORY_RETAINED',
                    resource_disposition='GROUP_SESSIONS_DELETED_BOOTSTRAP_HISTORY_RETAINED',
                    credential_persistence={'gateway_principal': False, 'connection_token': False,
                                            'websocket_url': False, 'private_signing_key': False},
                )
                self._save()
                return _json_copy(self.state)
        except BaseException as error:
            self._connection_token = ''
            self._principal = ''
            if self._ws_connection is not None:
                try:
                    self._ws_connection.close()
                except BaseException:
                    pass
                self._ws_connection = None
            if self.state is not None:
                self.state['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
                self.state['error_type'] = type(error).__name__
                self.state['assertions'] = [{'id': REQUIREMENTS[0],
                                             'status': 'CANCELLED' if has_cancellation(error) else 'FAIL'}]
                if self.ledger is not None:
                    try:
                        self.state['ledger'] = self._ledger_evidence()
                    except BaseException:
                        pass
                try:
                    self._save()
                except BaseException:
                    pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            if isinstance(error, LocalSessionPublicError):
                raise
            raise LocalSessionPublicError('Session public helper failed; inspect retained evidence') from None
        finally:
            self._connection_token = ''
            self._principal = ''
