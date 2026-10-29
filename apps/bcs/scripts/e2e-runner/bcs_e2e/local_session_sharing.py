"""Owned-local execution of the next five original Session sharing helpers.

The predecessor public/JWT/WebSocket helper has already completed in the same
foreground runtime.  This stage creates five fresh legacy Groups (and eight
Sessions), exercises exactly ten approved product-CLI calls plus the original
invite/list HTTP paths, then deletes only Ledger-bound resources.  Invite
credentials are consumed in memory and never enter durable evidence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request
import time
import uuid

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .ledger import Ledger, LedgerError
from .local_cli import require_cli_binary
from .local_role_identity import OwnedLocalRoleIdentity
from .local_session_contract import REQUIREMENTS, session_source_binding
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

CASE_ID = 'LOCAL-SESSION-SHARING-01'
CONTRACT = 'owned-local-session-sharing-v1'
SUITE = CONTRACT
ROLES = ('CEO', 'PM', 'ENG', 'QA')
JOURNAL_NAME = 'local-session-sharing.json'
LEDGER_NAME = 'session-sharing-resources.sqlite'
BINDING_NAME = 'session-sharing-binding.json'
PLAN_NAME = 'session-sharing-plan.json'
PUBLIC_BINDING_NAME = 'session-binding.json'
PUBLIC_JOURNAL_NAME = 'local-session-public.json'
MAX_RESPONSE_BYTES = 65_536
SELECTED = REQUIREMENTS[1:6]

OPERATIONS = (
    'eng-visibility-before', 'eng-visibility-public', 'eng-visibility-public-read',
    'h1-group-create', 'h1-session-create', 'h1-session-list', 'h1-session-get',
    'h1-group-delete', 'h1-group-deleted-404',
    'h2-group-create', 'h2-group-invite', 'h2-group-join', 'h2-group-delete', 'h2-group-deleted-404',
    'h3-group-create', 'h3-session-create', 'h3-session-invite', 'h3-group-delete', 'h3-group-deleted-404',
    'h4-group-create', 'h4-bot-groups', 'h4-group-delete', 'h4-group-deleted-404',
    'h5-group-create', 'h5-session-create', 'h5-session-invite', 'h5-session-join',
    'h5-group-delete', 'h5-group-deleted-404', 'eng-visibility-restore', 'eng-visibility-restored-read',
)
CLI_OPERATIONS = (
    'h1-group-create', 'h1-session-create', 'h1-session-list', 'h1-session-get',
    'h2-group-create', 'h3-group-create', 'h3-session-create', 'h3-session-invite',
    'h4-group-create', 'h5-group-create',
)
GROUP_HELPERS = ('h1', 'h2', 'h3', 'h4', 'h5')


class LocalSessionSharingError(RunStateError):
    pass


def _json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


class OwnedLocalSessionSharing:
    """Run requirements ``SESSION.cli-create-list-get`` through ``invite-join``."""

    ROLES = ROLES

    def __init__(self, identities, binary: Path, binary_sha256: str):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise LocalSessionSharingError(
                'Session sharing requires freshly verified CEO/PM/ENG/QA owned-local identities')
        identities._require_roles(ROLES)
        require_cli_binary(binary, binary_sha256)
        self.identities = identities
        self.runtime = identities.runtime
        if self.runtime.store_messages is not True:
            raise LocalSessionSharingError('Session sharing requires store_messages=True')
        parsed = urlsplit(identities.origin)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise LocalSessionSharingError('Session sharing is restricted to the owned loopback origin')
        self.root = self.runtime.root
        self.path = self.root / JOURNAL_NAME
        self.ledger_path = self.root / LEDGER_NAME
        self.binding_path = self.root / BINDING_NAME
        self.plan_path = self.root / PLAN_NAME
        self.public_binding_path = self.root / PUBLIC_BINDING_NAME
        self.public_path = self.root / PUBLIC_JOURNAL_NAME
        self.binary = binary.resolve()
        self.binary_sha256 = binary_sha256
        self.executor_digest = executor_digest()
        self.source_binding = session_source_binding()
        self.binding = self._validated_binding()
        self.binding_digest = fingerprint(self.binding)
        self.plan = self._validated_plan()
        self.plan_digest = fingerprint(self.plan)
        self.public_binding = read(self.public_binding_path)
        self.public_binding_digest = fingerprint(self.public_binding)
        self.public_journal = self._validated_public()
        self.public_digest = fingerprint(self.public_journal)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.state: dict | None = None
        self.ledger: Ledger | None = None
        self.journal_digest = ''
        self.invalidated = False
        self._sensitive: list[str] = []

    def _validated_binding(self) -> dict:
        keys = {'schema_version', 'contract', 'suite', 'case_id', 'run_id', 'executor_digest',
                'bcs_binary_sha256', 'cli_binary_sha256', 'source_binding',
                'selected_requirement_ids', 'parent_requirement_ids', 'legacy_inventory',
                'runtime_dir', 'store_messages', 'public_journal_digest'}
        try:
            if self.binding_path.is_symlink() or not self.binding_path.is_file():
                raise ValueError
            value = read(self.binding_path)
            if (set(value) != keys or value['schema_version'] != 1
                    or value['contract'] != CONTRACT or value['suite'] != SUITE
                    or value['case_id'] != CASE_ID or value['run_id'] != self.runtime.run_id
                    or value['executor_digest'] != self.executor_digest
                    or value['bcs_binary_sha256'] != self.runtime.binary_sha256
                    or value['cli_binary_sha256'] != self.binary_sha256
                    or value['source_binding'] != self.source_binding
                    or value['selected_requirement_ids'] != list(REQUIREMENTS[:6])
                    or value['parent_requirement_ids'] != list(REQUIREMENTS)
                    or value['legacy_inventory'] != current_manifest()['legacy_inventory']
                    or value['runtime_dir'] != str(self.root.resolve())
                    or value['store_messages'] is not True
                    or not isinstance(value['public_journal_digest'], str)
                    or len(value['public_journal_digest']) != 64):
                raise ValueError
            return value
        except (KeyError, OSError, TypeError, ValueError, RunStateError):
            raise LocalSessionSharingError('Session sharing execution binding is missing or changed') from None

    def _validated_plan(self) -> dict:
        try:
            if self.plan_path.is_symlink() or not self.plan_path.is_file():
                raise ValueError
            value = read(self.plan_path)
            expected = dict(self.binding)
            expected.pop('public_journal_digest')
            if value != expected:
                raise ValueError
            return value
        except (OSError, TypeError, ValueError, RunStateError):
            raise LocalSessionSharingError('Frozen Session sharing plan is missing or changed') from None

    def _validated_public(self) -> dict:
        try:
            if (self.public_path.is_symlink() or not self.public_path.is_file()
                    or self.public_binding_path.is_symlink() or not self.public_binding_path.is_file()):
                raise ValueError
            value = read(self.public_path)
            if (fingerprint(value) != self.binding['public_journal_digest']
                    or value.get('status') != 'LOCAL_SESSION_PUBLIC_PASS'
                    or value.get('run_id') != self.runtime.run_id
                    or value.get('case_id') != 'LOCAL-SESSION-PUBLIC-01'
                    or value.get('executor_digest') != self.executor_digest
                    or value.get('fixture_digest') != fingerprint(read(self.identities.fixtures.path))
                    or value.get('binding_digest') != fingerprint(self.public_binding)
                    or value.get('source_binding_digest') != fingerprint(self.source_binding)
                    or value.get('assertions') != [{'id': REQUIREMENTS[0], 'status': 'PASS'}]
                    or value.get('status') != 'LOCAL_SESSION_PUBLIC_PASS'
                    or value.get('storage_before_delete', {}).get('history', {}).get('row_count') != 4
                    or value.get('ledger', {}).get('resource_count') != 3
                    or any(row.get('state') != 'deleted'
                           for row in value.get('ledger', {}).get('resources', []))):
                raise ValueError
            return value
        except (OSError, TypeError, ValueError, RunStateError):
            raise LocalSessionSharingError('Completed public Session predecessor is missing or changed') from None

    @staticmethod
    def _exclusive(path: Path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise LocalSessionSharingError('Session sharing requires fresh journal and Ledger paths') from None
        os.close(fd)

    def _save(self):
        if self.journal_digest:
            try:
                if fingerprint(read(self.path)) != self.journal_digest:
                    raise ValueError
            except Exception:
                self.invalidated = True
                raise LocalSessionSharingError('Session sharing journal changed; preserve original evidence') from None
        serialized = json.dumps(self.state, sort_keys=True, separators=(',', ':'), allow_nan=False)
        secrets = [value for value in self._sensitive if value]
        secrets.extend(value for key, value in self.runtime.inputs.environment.items()
                       if isinstance(value, str) and value
                       and ('SECRET' in key or key.endswith('_TOKEN')))
        secrets.extend(entry[1] for entry in self.identities._entries.values() if entry[1])
        if any(secret in serialized for secret in secrets):
            self.invalidated = True
            raise LocalSessionSharingError('Session sharing credential leaked into durable evidence')
        write(self.path, self.state)
        self.journal_digest = fingerprint(self.state)

    def _active(self):
        if self.invalidated:
            raise LocalSessionSharingError('Session sharing mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        require_cli_binary(self.binary, self.binary_sha256)
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(session_source_binding()) != fingerprint(self.source_binding)
                or fingerprint(self._validated_binding()) != self.binding_digest
                or fingerprint(self._validated_plan()) != self.plan_digest
                or fingerprint(read(self.public_binding_path)) != self.public_binding_digest
                or fingerprint(self._validated_public()) != self.public_digest
                or self.runtime.store_messages is not True):
            raise LocalSessionSharingError('Session source, fixture, runtime, plan, or predecessor changed')
        if self.journal_digest and fingerprint(read(self.path)) != self.journal_digest:
            self.invalidated = True
            raise LocalSessionSharingError('Session sharing journal changed')

    def _id(self, role: str) -> str:
        self._active()
        return self.identities._entries[role][0]

    def _admit_intent(self, operation: str):
        self._active()
        index = len(self.state['operations'])
        if (index >= len(OPERATIONS) or OPERATIONS[index] != operation
                or operation not in {'h1-group-create', 'h1-session-create', 'h2-group-create',
                                     'h3-group-create', 'h3-session-create', 'h4-group-create',
                                     'h5-group-create', 'h5-session-create'}):
            raise LocalSessionSharingError('Resource intent is outside the finite operation plan')
        helper = operation[:2]
        if operation.endswith('group-create'):
            if helper in self.state['resources']['groups']:
                raise LocalSessionSharingError('Group intent already exists for helper')
        elif (helper not in self.state['resources']['groups']
              or helper in self.state['resources']['sessions']):
            raise LocalSessionSharingError('Session intent lacks one exact owned parent or is duplicated')

    def _ledger_row(self, handle: str) -> dict:
        if self.ledger is None:
            raise LocalSessionSharingError('Session sharing Ledger is unavailable')
        try:
            return dict(self.ledger._get(handle))
        except LedgerError:
            raise LocalSessionSharingError('Operation handle is not in the dedicated Ledger') from None

    def _admit_operation(self, *, operation: str, method: str, path: str, body: object,
                         auth_role: str | None, expected_status: int,
                         handles: tuple[str, ...], transport: str,
                         arguments: list[str] | None = None,
                         request_path: str | None = None, output_kind: str | None = None,
                         output_expected: dict | None = None):
        """Admit one exact finite local request; caller input never selects a route."""
        self._active()
        index = len(self.state['operations'])
        if index >= len(OPERATIONS) or OPERATIONS[index] != operation:
            raise LocalSessionSharingError('Operation is outside the finite ordered plan')
        pm, eng = self._id('PM'), self._id('ENG')
        expected = None
        cli_args = None
        cli_output = None
        if operation.startswith('eng-visibility-'):
            visibility = ('public' if operation == 'eng-visibility-public'
                          else 'protected' if operation == 'eng-visibility-restore' else None)
            verb = 'PUT' if visibility is not None else 'GET'
            expected = (verb, '/bots/' + quote(eng, safe='') + '/visibility',
                        {'visibility': visibility} if visibility else None, 'ENG', 200, ())
        elif operation.endswith('-group-create'):
            helper = operation[:2]
            topic = 'e2e-session-sharing-' + helper
            expected_body = {'id': None, 'label': None, 'driver_bot': pm,
                'participants': [{'bot_uuid': pm, 'role': None}, {'bot_uuid': eng, 'role': None}],
                'topic': topic, 'create_initial_session': True}
            expected = ('POST', '/groups', expected_body, 'PM', 200, handles)
            cli_args = ['create-group', '--driver', pm, '--participants', pm + ',' + eng,
                        '--topic', topic]
            cli_output = ('group-create-human', {'pm': pm, 'eng': eng})
            if len(handles) != 2:
                raise LocalSessionSharingError('Group create requires exact Group and initial Session handles')
            group_row, session_row = (self._ledger_row(item) for item in handles)
            if (group_row['kind'] != 'group' or group_row['parent'] is not None
                    or session_row['kind'] != 'session' or session_row['parent'] != handles[0]
                    or any(row['case_id'] != CASE_ID or row['attempt'] != 1
                           or row['origin'] != 'unknown' or row['state'] != 'intended'
                           or row['resource_id'] is not None for row in (group_row, session_row))):
                raise LocalSessionSharingError('Group create handles are not exact fresh owned intents')
        elif operation in ('h1-session-create', 'h3-session-create', 'h5-session-create'):
            helper = operation[:2]
            group = self._group(helper)
            title = 'cli-e2e-sess' if helper == 'h1' else 'share-sess'
            group_path = '/groups/' + quote(group['id'], safe='') + '/sessions'
            expected = ('POST', group_path, {'session_title': title}, 'PM', 201, handles)
            if transport == 'PRODUCT_CLI':
                cli_args = ['session', 'create', '--group', group['id'], '--title', title]
                cli_output = ('session-create-json', {'group_id': group['id'], 'title': title})
            if len(handles) != 1:
                raise LocalSessionSharingError('Session create requires one exact child handle')
            row = self._ledger_row(handles[0])
            if (row['kind'] != 'session' or row['parent'] != group['handle']
                    or row['case_id'] != CASE_ID or row['attempt'] != 1
                    or row['origin'] != 'unknown' or row['state'] != 'intended'
                    or row['resource_id'] is not None):
                raise LocalSessionSharingError('Session create handle is not an exact fresh owned intent')
        elif operation == 'h1-session-list':
            group, session = self._group('h1'), self._session('h1')
            expected = ('GET', '/groups/' + quote(group['id'], safe='') + '/sessions',
                        None, 'PM', 200, ())
            cli_args = ['session', 'list', '--group', group['id']]
            cli_output = ('session-list-json', {'session_id': session['id']})
        elif operation == 'h1-session-get':
            session = self._session('h1')
            expected = ('GET', '/sessions/' + quote(session['id'], safe=''), None, 'PM', 200, ())
            cli_args = ['session', 'get', session['id']]
            cli_output = ('session-get-json', {'session_id': session['id'], 'group_id': self._group('h1')['id']})
        elif operation == 'h2-group-invite':
            expected = ('POST', '/groups/' + quote(self._group('h2')['id'], safe='') + '/invite-link',
                        {'ttl_seconds': 300}, None, 200, ())
        elif operation == 'h2-group-join':
            expected = ('POST', '/groups/join/[REDACTED]', {}, None, 200, ())
        elif operation == 'h3-session-invite':
            session = self._session('h3')
            expected = ('POST', '/sessions/' + quote(session['id'], safe='') + '/invite-link',
                        {'ttl_seconds': 300}, 'PM', 200, ())
            cli_args = ['session', 'invite-link', session['id'], '--ttl-seconds', '300']
            cli_output = ('invite-json', {})
        elif operation == 'h4-bot-groups':
            expected = ('GET', '/bots/' + quote(pm, safe='') + '/groups?' +
                        urlencode({'limit': 50, 'group_kind': 'all'}), None, None, 200, ())
        elif operation == 'h5-session-invite':
            expected = ('POST', '/sessions/' + quote(self._session('h5')['id'], safe='') + '/invite-link',
                        {'ttl_seconds': 300}, 'PM', 200, ())
        elif operation == 'h5-session-join':
            expected = ('POST', '/sessions/join/[REDACTED]', {}, None, 200, ())
        elif operation.endswith('-group-delete'):
            helper = operation[:2]
            group = self._group(helper)
            row = self._ledger_row(group['handle'])
            if (row['kind'] != 'group' or row['resource_id'] != group['id']
                    or row['origin'] != 'created' or row['state'] != 'live'
                    or row['case_id'] != CASE_ID or row['attempt'] != 1):
                raise LocalSessionSharingError('Deletion target is not the exact live owned Ledger Group')
            expected = ('DELETE', '/groups/' + quote(group['id'], safe='') + '?' +
                        urlencode({'bot_id': pm}), None, None, 200, ())
        elif operation.endswith('-group-deleted-404'):
            helper = operation[:2]
            group = self._group(helper)
            expected = ('GET', '/groups/' + quote(group['id'], safe=''), None, None, 404, ())
        if expected is None:
            raise LocalSessionSharingError('Operation has no finite local admissibility contract')
        actual = (method, path, body, auth_role, expected_status, handles)
        if actual != expected or transport not in ('LEGACY_HTTP', 'PRODUCT_CLI'):
            raise LocalSessionSharingError('Operation method, path, body, auth, status, or handles differ')
        if transport == 'PRODUCT_CLI':
            if (operation not in CLI_OPERATIONS or arguments != cli_args
                    or (output_kind, output_expected) != cli_output):
                raise LocalSessionSharingError('CLI arguments or output contract differ from the finite product command')
        elif arguments is not None:
            raise LocalSessionSharingError('HTTP operation cannot carry CLI arguments')
        if operation in ('h2-group-join', 'h5-session-join'):
            if not self._sensitive:
                raise LocalSessionSharingError('Invite join lacks one in-memory owned credential')
            prefix = '/groups/join/' if operation == 'h2-group-join' else '/sessions/join/'
            if request_path != prefix + quote(self._sensitive[-1], safe=''):
                raise LocalSessionSharingError('Invite credential was rewritten or targeted outside the finite path')
        elif request_path not in (None, path):
            raise LocalSessionSharingError('Operation request path differs from durable admitted path')

    def _append_operation(self, operation: str, method: str, path: str, *, kind: str,
                          handles: tuple[str, ...] = (), request_id: str,
                          transport: str = 'LEGACY_HTTP') -> dict:
        self._active()
        index = len(self.state['operations'])
        if index >= len(OPERATIONS) or OPERATIONS[index] != operation:
            raise LocalSessionSharingError('Session sharing operation order differs from the finite plan')
        identifier(request_id, 'Session sharing request ID')
        event = {'operation': operation, 'transport': transport, 'method': method, 'path': path,
                 'submitted_request_id': request_id, 'kind': kind, 'status': 'INTENDED',
                 'transitions': [{'status': 'INTENDED'}]}
        if handles:
            event['resource_handles'] = list(handles)
            event['resource_binding'] = 'UNKNOWN'
        self.state['operations'].append(event)
        self._save()
        return event

    def _operation_failed(self, event: dict, error: BaseException, *, handles: tuple[str, ...],
                          mutation: bool):
        self.invalidated = True
        event['status'] = 'UNKNOWN' if mutation else 'FAILED'
        event['error_type'] = type(error).__name__
        event['transitions'].append({'status': event['status'], 'error_type': type(error).__name__})
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

    def _http(self, *, operation: str, method: str, evidence_path: str,
              request_path: str | None = None, body: object = None, auth_role: str | None,
              expected_status: int, mutation: bool, handles: tuple[str, ...] = (),
              request_id: str | None = None) -> dict:
        request_id = request_id or 'e2e-session-sharing-' + uuid.uuid4().hex
        try:
            self._admit_operation(operation=operation, method=method, path=evidence_path,
                body=body, auth_role=auth_role, expected_status=expected_status,
                handles=handles, transport='LEGACY_HTTP', request_path=request_path)
        except BaseException:
            if handles and self.ledger is not None:
                for handle in handles:
                    try: self.ledger.unknown(handle)
                    except BaseException: pass
            self.invalidated = True
            raise
        event = self._append_operation(operation, method, evidence_path,
            kind='MUTATION' if mutation else 'CHECK', handles=handles,
            request_id=request_id, transport='LEGACY_HTTP')
        headers = {'Accept': 'application/json', 'X-Request-ID': request_id}
        raw_body = None
        if auth_role is not None:
            token = self.identities._entries[auth_role][1]
            if not isinstance(token, str) or not token:
                self._operation_failed(event, LocalSessionSharingError('owned Bot token missing'),
                                       handles=handles, mutation=mutation)
                raise LocalSessionSharingError('owned Bot token missing')
            headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            headers['Content-Type'] = 'application/json'
            raw_body = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        try:
            request = Request(self.identities.origin + (request_path or evidence_path),
                              method=method, data=raw_body, headers=headers)
            try:
                response = self.identities.opener.open(request, timeout=5)
            except HTTPError as error:
                if error.code != expected_status:
                    raise
                response = error
            with response:
                status = response.status
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            self._active()
            if status != expected_status or len(raw) > MAX_RESPONSE_BYTES:
                raise LocalSessionSharingError('Session sharing HTTP status or byte budget differs')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(value, dict):
                raise LocalSessionSharingError('Session sharing HTTP response is not an object')
            event.update(status='ACKNOWLEDGED', http_status=status)
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': status})
            if handles:
                event['resource_binding'] = 'PENDING_RESPONSE_VALIDATION'
            self._save()
            return value
        except BaseException as error:
            if isinstance(error, HTTPError):
                error.close()
            self._operation_failed(event, error, handles=handles, mutation=mutation)
            raise LocalSessionSharingError(
                'Session sharing HTTP outcome is unconfirmed; no retry, adoption, or cleanup') from None

    def _record_check(self, check_id: str, evidence: dict):
        if any(row['id'] == check_id for row in self.state['checks']):
            raise LocalSessionSharingError('Session sharing check is duplicated')
        self.state['checks'].append({'id': check_id, 'status': 'PASS', 'evidence': evidence})
        self._save()

    def _visibility(self, operation: str, method: str, expected: str) -> str:
        eng = self._id('ENG')
        if expected not in ('public', 'protected'):
            raise LocalSessionSharingError('Visibility expectation is outside the finite contract')
        body = {'visibility': expected} if method == 'PUT' else None
        value = self._http(operation=operation, method=method,
            evidence_path='/bots/' + quote(eng, safe='') + '/visibility', body=body,
            auth_role='ENG', expected_status=200, mutation=method == 'PUT')
        data = value.get('data')
        if (value.get('success') is not True or not isinstance(data, dict)
                or data.get('bot_uuid') != eng or data.get('visibility') != expected):
            raise LocalSessionSharingError('ENG visibility response differs from the exact expectation')
        return expected

    def _group(self, helper: str) -> dict:
        return self.state['resources']['groups'][helper]

    def _session(self, helper: str) -> dict:
        return self.state['resources']['sessions'][helper]

    def _group_ids(self) -> list[str]:
        return [self._group(helper)['id'] for helper in GROUP_HELPERS if helper in self.state['resources']['groups']]

    def _session_ids(self) -> list[str]:
        result = []
        for helper in GROUP_HELPERS:
            if helper not in self.state['resources']['groups']:
                continue
            result.append(self._group(helper)['initial_session_id'])
            if helper in self.state['resources']['sessions']:
                result.append(self._session(helper)['id'])
        return result

    def _create_group(self, helper: str, cli) -> dict:
        request_id = 'e2e-session-sharing-' + helper + '-group-' + uuid.uuid4().hex
        self._admit_intent(helper + '-group-create')
        handles = self.ledger.group_creation_intent(CASE_ID, 1, request_id)
        pm, eng = self._id('PM'), self._id('ENG')
        body = {'id': None, 'label': None, 'driver_bot': pm,
                'participants': [{'bot_uuid': pm, 'role': None},
                                 {'bot_uuid': eng, 'role': None}],
                'topic': 'e2e-session-sharing-' + helper, 'create_initial_session': True}
        reply = cli.invoke(operation=helper + '-group-create', method='POST', path='/groups',
            body=body, arguments=['create-group', '--driver', pm, '--participants', pm + ',' + eng,
                                  '--topic', body['topic']], expected_status=200,
            output_kind='group-create-human', expected={'pm': pm, 'eng': eng},
            handles=handles, request_id=request_id)
        try:
            group_id = identifier(reply.get('id'), 'sharing Group ID')
            initial_id = identifier(reply.get('session_id'), 'sharing implicit Session ID')
            self.ledger.observed(handles[0], group_id, origin='created', actual_request_id=request_id)
            self.ledger.observed(handles[1], initial_id, origin='created')
            self.state['resources']['groups'][helper] = {
                'id': group_id, 'handle': handles[0], 'initial_session_id': initial_id,
                'initial_session_handle': handles[1], 'attempt': 1, 'helper': helper}
            event = self.state['operations'][-1]
            event['candidate_ids'] = {'group': group_id, 'initial_session': initial_id}
            event['resource_binding'] = 'BOUND'
            self._save()
            return self._group(helper)
        except BaseException as error:
            for handle in handles:
                try: self.ledger.unknown(handle)
                except BaseException: pass
            self.invalidated = True
            raise LocalSessionSharingError('CLI Group response could not be bound; retain unknown Ledger rows') from None

    def _create_cli_session(self, helper: str, title: str, cli) -> dict:
        group = self._group(helper)
        request_id = 'e2e-session-sharing-' + helper + '-session-' + uuid.uuid4().hex
        self._admit_intent(helper + '-session-create')
        handle = self.ledger.intent(CASE_ID, 1, 'session', request_id, group['handle'])
        path = '/groups/' + quote(group['id'], safe='') + '/sessions'
        reply = cli.invoke(operation=helper + '-session-create', method='POST', path=path,
            body={'session_title': title},
            arguments=['session', 'create', '--group', group['id'], '--title', title],
            expected_status=201, output_kind='session-create-json',
            expected={'group_id': group['id'], 'title': title}, handles=(handle,),
            request_id=request_id)
        try:
            session_id = identifier(reply.get('session_id'), 'sharing explicit Session ID')
            if session_id == group['initial_session_id']:
                raise ValueError
            self.ledger.observed(handle, session_id, origin='created', actual_request_id=request_id)
            self.state['resources']['sessions'][helper] = {
                'id': session_id, 'handle': handle, 'group_id': group['id'],
                'attempt': 1, 'title': title, 'helper': helper}
            event = self.state['operations'][-1]
            event['candidate_ids'] = {'session': session_id}
            event['resource_binding'] = 'BOUND'
            self._save()
            return self._session(helper)
        except BaseException:
            try: self.ledger.unknown(handle)
            except BaseException: pass
            self.invalidated = True
            raise LocalSessionSharingError('CLI Session response could not be bound; retain unknown Ledger row') from None

    def _create_api_session(self, helper: str, title: str) -> dict:
        group = self._group(helper)
        request_id = 'e2e-session-sharing-' + helper + '-session-' + uuid.uuid4().hex
        self._admit_intent(helper + '-session-create')
        handle = self.ledger.intent(CASE_ID, 1, 'session', request_id, group['handle'])
        path = '/groups/' + quote(group['id'], safe='') + '/sessions'
        reply = self._http(operation=helper + '-session-create', method='POST', evidence_path=path,
            body={'session_title': title}, auth_role='PM', expected_status=201, mutation=True,
            handles=(handle,), request_id=request_id)
        try:
            session_id = identifier(reply.get('session_id'), 'API explicit Session ID')
            if (reply.get('id') != session_id or reply.get('group_id') != group['id']
                    or reply.get('session_title') != title or session_id == group['initial_session_id']):
                raise ValueError
            self.ledger.observed(handle, session_id, origin='created', actual_request_id=request_id)
            self.state['resources']['sessions'][helper] = {
                'id': session_id, 'handle': handle, 'group_id': group['id'],
                'attempt': 1, 'title': title, 'helper': helper}
            event = self.state['operations'][-1]
            event['candidate_ids'] = {'session': session_id}
            event['resource_binding'] = 'BOUND'
            self._save()
            return self._session(helper)
        except BaseException:
            try: self.ledger.unknown(handle)
            except BaseException: pass
            self.invalidated = True
            raise LocalSessionSharingError('API Session response could not be bound; retain unknown Ledger row') from None

    def _consume_invite(self, value: dict, *, target: str, target_id: str) -> str:
        if set(value) != {'invite_token', 'expires_at', 'join_url'} or type(value['expires_at']) is not int:
            raise LocalSessionSharingError('Invite response fields differ from the product contract')
        token, url = value['invite_token'], value['join_url']
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096 or not token.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in token)
                or not isinstance(url, str) or len(url) > 8192):
            raise LocalSessionSharingError('Invite credential is missing or invalid')
        parsed, origin = urlsplit(url), urlsplit(self.identities.origin)
        if (parsed.scheme != origin.scheme or parsed.hostname != origin.hostname
                or parsed.port != origin.port or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path.rsplit('/', 1)[-1] != token):
            raise LocalSessionSharingError('Invite URL is not bound to the owned origin; it was not followed')
        last_operation = self.state['operations'][-1]['operation'] if self.state.get('operations') else ''
        selector = {'h2-group-invite': ('group', 'h2'),
                    'h3-session-invite': ('session', 'h3'),
                    'h5-session-invite': ('session', 'h5')}.get(last_operation)
        expected_target = None
        if selector is not None:
            kind, helper = selector
            resource = self._group(helper) if kind == 'group' else self._session(helper)
            expected_target = (kind, resource['id'])
        if expected_target != (target, target_id):
            raise LocalSessionSharingError('Invite validation target is not the exact minted owned resource')
        secret = self.runtime.inputs.environment.get('BCS_E2E_INVITE_SECRET')
        if (not isinstance(secret, str) or not secret or not secret.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in secret)):
            raise LocalSessionSharingError('Owned invite verification secret is missing or invalid')
        try:
            raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
            if len(raw) <= 32:
                raise ValueError
            if base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii') != token:
                raise ValueError
            payload_bytes, signature = raw[:-32], raw[-32:]
            expected_signature = hmac.new(secret.encode('ascii'), payload_bytes, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError
            payload = json.loads(payload_bytes.decode('utf-8'), object_pairs_hook=unique_keys)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise LocalSessionSharingError('Invite token signature or payload is invalid') from None
        now = int(time.time())
        expected_payload = {'v': 1, 'id': target_id, 'exp': value['expires_at'],
                            'target_type': target}
        if (payload != expected_payload or not now < payload['exp'] <= now + 305):
            raise LocalSessionSharingError('Invite token target, type, version, or expiry differs')
        self._sensitive.append(token)
        return token

    def _join(self, operation: str, target: str, target_id: str, token: str) -> dict:
        redacted = '/' + target + 's/join/[REDACTED]'
        actual = '/' + target + 's/join/' + quote(token, safe='')
        value = self._http(operation=operation, method='POST', evidence_path=redacted,
            request_path=actual, body={}, auth_role=None, expected_status=200, mutation=True)
        expected = {'joined': True, 'already_member': False, 'target_type': target,
                    'target_id': target_id, 'actor_id': 'human_' + self.identities.human}
        if value != expected:
            raise LocalSessionSharingError('Invite join response fields or target relation differ')
        return {'status': 'PASS', **expected}

    def _ledger_evidence(self) -> list[dict]:
        rows = self.ledger.resource_snapshot()
        return [{key: row[key] for key in ('handle', 'case_id', 'attempt', 'kind', 'parent',
                'request_id', 'origin', 'resource_id', 'state')} for row in rows]

    def _finish_helper(self, helper):
        from .local_session_sharing_storage import (
            owned_snapshot, owned_history_snapshot, group_history_map, deletion_admission, deletion_proof)
        pm = self._id('PM')
        owned_before = owned_snapshot(self, helper)
        histories = {'groups': group_history_map(self)}
        self.state['deletion_admissions'].append(deletion_admission(self))
        self.state['retained_histories']['before_delete'] = owned_before['history']
        self._save()
        group = self._group(helper)
        path = '/groups/' + quote(group['id'], safe='')
        deleted = self._http(operation=helper + '-group-delete', method='DELETE',
            evidence_path=path + '?' + urlencode({'bot_id': pm}), body=None,
            auth_role=None, expected_status=200, mutation=True)
        if deleted != {'deleted': True, 'id': group['id']}:
            raise LocalSessionSharingError('Group deletion acknowledgement is not exact')
        missing = self._http(operation=helper + '-group-deleted-404', method='GET',
            evidence_path=path, body=None, auth_role=None,
            expected_status=404, mutation=False)
        if (missing.get('status') != 404 or missing.get('code') != 'group_not_found'
                or missing.get('params') != {'group_id': group['id']}):
            raise LocalSessionSharingError('Deleted Group 404 code, params, or status differs')
        child_ids = [group['initial_session_id']]
        child_handles = [group['initial_session_handle']]
        if helper in self.state['resources']['sessions']:
            child_ids.append(self._session(helper)['id'])
            child_handles.append(self._session(helper)['handle'])
        proof = deletion_proof(self, group['id'], tuple(child_ids), histories)
        for handle in child_handles:
            self.ledger.mark_deleted(handle)
        self.ledger.mark_deleted(group['handle'])
        self.state['deletion_proofs'].append({
            'helper': helper, 'group_id': group['id'], 'http_status': 200,
            'not_found_http_status': 404, 'sqlite': proof,
            'ledger_mark_order': ['session'] * len(child_handles) + ['group']})
        requirement_index = GROUP_HELPERS.index(helper)
        self.state['helper_evidence'][helper]['cleanup_status'] = 'PASS'
        self.state['assertions'][requirement_index]['status'] = 'PASS'
        self._record_check(SELECTED[requirement_index], {
            **self.state['helper_evidence'][helper],
            'delete_http_status': 200, 'not_found_http_status': 404,
            'sqlite_absence': 'PASS', 'ledger_children_before_parent': True})


        after = owned_history_snapshot(self)
        after_groups = group_history_map(self)
        if after != owned_before['history'] or after_groups != histories['groups']:
            raise LocalSessionSharingError('Per-helper cleanup changed retained history')
        self.state['retained_histories'].update(after_delete=after, groups=after_groups)
        self._save()

    def run(self) -> dict:
        from .local_session_sharing_cli import SharingCliDriver
        from .local_session_sharing_storage import (
            deletion_admission, deletion_proof, fixture_snapshot, group_history_map,
            owned_history_snapshot, owned_snapshot, public_history_snapshot, unrelated_snapshot,
            verify_join_relation,
        )
        self._active()
        if (self.path.exists() or self.path.is_symlink()
                or self.ledger_path.exists() or self.ledger_path.is_symlink()):
            raise LocalSessionSharingError('Session sharing requires fresh evidence paths')
        self._exclusive(self.path)
        self.state = {
            'schema_version': 1, 'contract': CONTRACT, 'suite': SUITE, 'case_id': CASE_ID,
            'run_id': self.runtime.run_id, 'status': 'RUNNING',
            'executor_digest': self.executor_digest, 'binding_digest': self.binding_digest,
            'plan_digest': self.plan_digest, 'source_binding_digest': fingerprint(self.source_binding),
            'predecessor': {'status': 'LOCAL_SESSION_PUBLIC_PASS',
                            'journal_digest': self.public_digest,
                            'retained_history': self.public_journal['storage_before_delete']['history']},
            'operations': [], 'checks': [], 'helper_evidence': {},
            'resources': {'groups': {}, 'sessions': {}},
            'assertions': [{'id': requirement, 'status': 'NOT_RUN'} for requirement in SELECTED],
            'full_parent_assertions': [{'id': requirement, 'status': 'NOT_RUN'} for requirement in REQUIREMENTS[6:]],
            'visibility': {'before': 'NOT_READ', 'admitted': 'NOT_RUN',
                           'after': 'NOT_READ', 'restored': False},
            'deletion_admissions': [], 'deletion_proofs': [],
            'credential_contexts_cleared': {'count': 0, 'all_cleared': False},
            'fixture_state': {'preserved': False}, 'unrelated_state': {'preserved': False},
            'retained_histories': {'preserved': False},
            'execution_scope': {'runtime': 'OWNED_LOCAL_FOREGROUND', 'remote_capabilities': 'NONE',
                                'engine_started_by_executor': False, 'receiver_mode': 'NOT_OPENED',
                                'business_session_terminal': 'NOT_CLAIMED',
                                'remote_quiescence': 'NOT_APPLICABLE'},
            'orchestration_difference': {
                'cleanup_timing': 'ORIGINAL_PER_HELPER_BEFORE_NEXT_CREATE',
                'reason': 'preserve original cleanup order and default per-Bot active Group quota',
                'requirement_pass_gate': 'EACH_HELPER_ASSERTS_PASS_ONLY_AFTER_ITS_DELETE_404_SQLITE_PROOF',
            },
            'ready_to_execute': False, 'full_story_status': 'NOT_MIGRATED',
        }
        self._save()
        try:
            with Ledger(self.ledger_path, self.runtime.run_id, self.binding_digest, create=True) as ledger:
                self.ledger = ledger
                fixture_before = fixture_snapshot(self)
                unrelated_before = unrelated_snapshot(self)
                public_before = public_history_snapshot(self)
                self.state['fixture_state']['before'] = fixture_before
                self.state['unrelated_state']['before'] = unrelated_before
                self.state['predecessor']['retained_history'] = public_before
                self._save()

                cli = SharingCliDriver(self, self.binary, self.binary_sha256)
                cli.require_pm_token()

                self.state['visibility']['before'] = self._visibility(
                    'eng-visibility-before', 'GET', 'protected')
                self.state['visibility']['admitted'] = self._visibility(
                    'eng-visibility-public', 'PUT', 'public')
                self._visibility('eng-visibility-public-read', 'GET', 'public')
                self._save()

                self._create_group('h1', cli)
                h1 = self._create_cli_session('h1', 'cli-e2e-sess', cli)
                group = self._group('h1')
                cli.invoke(operation='h1-session-list', method='GET',
                    path='/groups/' + quote(group['id'], safe='') + '/sessions', body=None,
                    arguments=['session', 'list', '--group', group['id']], expected_status=200,
                    output_kind='session-list-json', expected={'session_id': h1['id']},
                    request_id='e2e-session-sharing-h1-list-' + uuid.uuid4().hex)
                cli.invoke(operation='h1-session-get', method='GET',
                    path='/sessions/' + quote(h1['id'], safe=''), body=None,
                    arguments=['session', 'get', h1['id']], expected_status=200,
                    output_kind='session-get-json',
                    expected={'session_id': h1['id'], 'group_id': group['id']},
                    request_id='e2e-session-sharing-h1-get-' + uuid.uuid4().hex)
                self.state['helper_evidence']['h1'] = {
                    'cli_calls': 4, 'soft_get_success': False, 'business_check': 'PASS',
                    'cleanup_status': 'PENDING'}
                self._save()

                self._finish_helper('h1')

                h2_group = self._create_group('h2', cli)
                invite = self._http(operation='h2-group-invite', method='POST',
                    evidence_path='/groups/' + quote(h2_group['id'], safe='') + '/invite-link',
                    body={'ttl_seconds': 300}, auth_role=None, expected_status=200, mutation=True)
                token = self._consume_invite(invite, target='group', target_id=h2_group['id'])
                joined = self._join('h2-group-join', 'group', h2_group['id'], token)
                token = ''; invite = {}
                relation = verify_join_relation(self, target='group', group_id=h2_group['id'])
                self.state['helper_evidence']['h2'] = {
                    'join': joined, 'relation': relation, 'invite_persisted': False,
                    'business_check': 'PASS', 'cleanup_status': 'PENDING'}
                self._save()

                self._finish_helper('h2')

                self._create_group('h3', cli)
                h3 = self._create_cli_session('h3', 'share-sess', cli)
                invite = cli.invoke(operation='h3-session-invite', method='POST',
                    path='/sessions/' + quote(h3['id'], safe='') + '/invite-link',
                    body={'ttl_seconds': 300},
                    arguments=['session', 'invite-link', h3['id'], '--ttl-seconds', '300'],
                    expected_status=200, output_kind='invite-json', expected={},
                    request_id='e2e-session-sharing-h3-invite-' + uuid.uuid4().hex)
                token = self._consume_invite(invite, target='session', target_id=h3['id'])
                token = ''; invite = {}
                self.state['helper_evidence']['h3'] = {
                    'cli_calls': 3, 'invite_fields': 'VALIDATED_IN_MEMORY',
                    'invite_persisted': False, 'business_check': 'PASS',
                    'cleanup_status': 'PENDING'}
                self._save()

                self._finish_helper('h3')

                h4_group = self._create_group('h4', cli)
                pm = self._id('PM')
                value = self._http(operation='h4-bot-groups', method='GET',
                    evidence_path='/bots/' + quote(pm, safe='') + '/groups?' +
                                  urlencode({'limit': 50, 'group_kind': 'all'}),
                    body=None, auth_role=None, expected_status=200, mutation=False)
                items = value.get('items')
                if (value.get('bot_uuid') != pm or not isinstance(items, list) or len(items) > 50
                        or not any(isinstance(row, dict)
                                   and (row.get('group_id') or row.get('id')) == h4_group['id']
                                   for row in items)):
                    raise LocalSessionSharingError('Bot Group listing omitted the exact fresh Group')
                self.state['helper_evidence']['h4'] = {
                    'contains_group': True, 'http_status': 200, 'business_check': 'PASS',
                    'cleanup_status': 'PENDING'}
                self._save()

                self._finish_helper('h4')

                h5_group = self._create_group('h5', cli)
                h5 = self._create_api_session('h5', 'share-sess')
                invite = self._http(operation='h5-session-invite', method='POST',
                    evidence_path='/sessions/' + quote(h5['id'], safe='') + '/invite-link',
                    body={'ttl_seconds': 300}, auth_role='PM', expected_status=200, mutation=True)
                token = self._consume_invite(invite, target='session', target_id=h5['id'])
                joined = self._join('h5-session-join', 'session', h5['id'], token)
                token = ''; invite = {}
                relation = verify_join_relation(self, target='session',
                                                group_id=h5_group['id'], session_id=h5['id'])
                self.state['helper_evidence']['h5'] = {
                    'join': joined, 'relation': relation, 'invite_persisted': False,
                    'business_check': 'PASS', 'cleanup_status': 'PENDING'}
                self._save()

                self._finish_helper('h5')

                self.state['visibility']['after'] = self._visibility(
                    'eng-visibility-restore', 'PUT', 'protected')
                restored = self._visibility(
                    'eng-visibility-restored-read', 'GET', 'protected')
                self.state['visibility']['restored'] = restored == 'protected'

                # Re-read both the overall rows and each Group after all deletes.
                after_groups = group_history_map(self)
                after_history = owned_history_snapshot(self)
                if after_groups != self.state['retained_histories']['groups'] or after_history != self.state['retained_histories']['before_delete']:
                    raise LocalSessionSharingError('Retained sharing history changed after Group deletion')
                fixture_after = fixture_snapshot(self)
                unrelated_after = unrelated_snapshot(self, tuple(self._group_ids()))
                public_after = public_history_snapshot(self)
                if fixture_after != fixture_before or unrelated_after != unrelated_before or public_after != public_before:
                    raise LocalSessionSharingError('Session sharing changed fixtures, predecessor history, or unrelated state')
                self.state['fixture_state'].update(after=fixture_after, preserved=True)
                self.state['unrelated_state'].update(after=unrelated_after, preserved=True)
                self.state['retained_histories'].update(
                    after_delete=after_history, preserved=True, groups=after_groups,
                    evidence_basis='OVERALL_ROW_DIGEST_AND_PER_GROUP_HASH_EQUALITY')

                ledger_rows = self._ledger_evidence()
                contexts = [row for row in self.state['operations'] if row['operation'] in CLI_OPERATIONS]
                self.state['credential_contexts_cleared'] = {
                    'count': len(contexts), 'all_cleared': len(contexts) == 10 and
                        all(row.get('credential_cleanup') == 'CLEARED' for row in contexts)}
                self.state['cli'] = {'operation_count': len(contexts),
                                     'acknowledged_count': sum(row['status'] == 'ACKNOWLEDGED'
                                                               for row in contexts)}
                self.state['resources'].update(
                    group_count=5, session_count=8, ledger_resource_count=len(ledger_rows),
                    ledger_deleted_count=sum(row['state'] == 'deleted' for row in ledger_rows),
                    ledger=ledger_rows)
                if (len(self.state['operations']) != len(OPERATIONS)
                        or [row['operation'] for row in self.state['operations']] != list(OPERATIONS)
                        or any(row['status'] != 'ACKNOWLEDGED' for row in self.state['operations'])
                        or self.state['cli'] != {'operation_count': 10, 'acknowledged_count': 10}
                        or len(ledger_rows) != 13
                        or any(row['origin'] != 'created' or row['state'] != 'deleted'
                               for row in ledger_rows)
                        or not self.state['credential_contexts_cleared']['all_cleared']
                        or not self.state['visibility']['restored']
                        or len(self.state['deletion_proofs']) != 5
                        or any(row['status'] != 'PASS' for row in self.state['assertions'])):
                    raise LocalSessionSharingError('Session sharing final evidence is incomplete')
                self.state.update(status='LOCAL_SESSION_SHARING_PASS',
                    disposition='FIVE_GROUPS_EIGHT_SESSIONS_DELETED_HISTORY_RETAINED',
                    resource_disposition='LEDGER_13_TOMBSTONES_HISTORY_RETAINED')
                self._save()
                return _json_copy(self.state)
        except BaseException as error:
            cancelled = has_cancellation(error)
            if self.state is not None:
                self.state['status'] = 'CANCELLED' if cancelled else 'BLOCKED'
                self.state['error_type'] = type(error).__name__
                for assertion in self.state['assertions']:
                    if assertion['status'] == 'NOT_RUN':
                        assertion['status'] = 'CANCELLED' if cancelled else 'FAIL'
                        break
                if self.ledger is not None:
                    try:
                        rows = self._ledger_evidence()
                        self.state['resources']['ledger'] = rows
                        self.state['resources']['ledger_resource_count'] = len(rows)
                    except BaseException:
                        pass
                try: self._save()
                except BaseException: pass
            if cancelled:
                raise KeyboardInterrupt() from None
            if isinstance(error, LocalSessionSharingError):
                raise
            raise LocalSessionSharingError('Session sharing failed; inspect retained evidence') from None
        finally:
            for index in range(len(self._sensitive)):
                self._sensitive[index] = ''
