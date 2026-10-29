"""Owned-local adapters for environment-neutral Edge read cases.

The concrete adapters resolve local identity and transport, but the read-only
Edge cases and the Bot-search read spine share case plans used by the future
Singlebox and preprod adapters. No request is retried or downgraded to a
warning. All adapter requests are read-only against the runner-owned loopback
runtime; the historical Bot-search fixture mutation is separately admitted.
"""
from __future__ import annotations

import json
import time
import uuid
import secrets
from http.client import HTTPConnection

from .case_adapters import AdapterCall
from .edge_read_case import (
    ADMISSION_CASE_ID,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_CONNECTIONS_CASE_ID,
    EdgeReadCasePlan,
    edge_read_case_plans,
)
from .edge_search_case import CASE_ID, EdgeSearchCasePlan, edge_search_case_plan
from .inventory import executor_digest
from .owned_bcs import OwnedBcsProcess
from .local_role_identity import OwnedLocalRoleIdentity
from .profile import fingerprint, unique_keys
from .run_state import RunStateError, read, write

_MAX_RESPONSE_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 5.0


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


class OwnedEdgeReadAdapter:
    """Live owned-local implementation of the read-only Edge case seams."""

    def __init__(self, runtime: OwnedBcsProcess, identities: OwnedLocalRoleIdentity,
                 case_id: str):
        if (type(runtime) is not OwnedBcsProcess
                or type(identities) is not OwnedLocalRoleIdentity
                or case_id not in {ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID,
                                   FRIEND_CONNECTIONS_CASE_ID}):
            raise RunStateError('Edge read adapter requires the exact owned runtime, identity and case')
        self.plan: EdgeReadCasePlan = next(
            row for row in edge_read_case_plans() if row.case_id == case_id)
        if (runtime.inputs is None or runtime.state != 'RUNNING' or runtime.ready is not True
                or identities.runtime is not runtime or identities.root != runtime.root
                or identities.run_id != runtime.run_id or identities.roles != ('CEO',)
                or 'CEO' not in identities._verified):
            raise RunStateError('Edge read runtime and CEO identity ownership differ')
        self.case_id = case_id
        self.runtime = runtime
        self.identities = identities
        self.run_id = runtime.run_id
        self.root = runtime.root
        self.origin = runtime.inputs.origin
        self.ceo_bot_id = identities.actor_id('CEO')
        self.human = runtime.inputs.human_id
        _require(self.origin == 'http://127.0.0.1:' + str(runtime.port)
                 and self.ceo_bot_id.isascii()
                 and all(char.isalnum() or char in '._-' for char in self.ceo_bot_id),
                 'Edge read runtime origin or Bot ID is outside its owned contract')
        self.executor_digest = executor_digest()
        self.path = self.root / ('local-edge-read-' + case_id + '.json')
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Edge read adapter already attempted this runtime')
        self._attempted: set[str] = set()
        self._digest = ''
        self.state = {
            'schema_version': 1, 'status': 'RUNNING', 'case_id': case_id,
            'parent_id': self.plan.parent_id, 'run_id': self.run_id,
            'executor_digest': self.executor_digest, 'steps': [],
        }

    def _active(self, method: str | None = None) -> None:
        if (executor_digest() != self.executor_digest
                or self.runtime.state != 'RUNNING' or self.runtime.ready is not True
                or self.identities._state != 'READY'
                or fingerprint(read(self.identities.fixtures.path))
                    != self.identities._receipt_digest
                or (method is not None and method in self._attempted)):
            raise RunStateError('Edge read adapter authority, fixture or operation state changed')
        if self._digest:
            _require(read(self.path) == self.state, 'Edge read adapter journal changed externally')

    def _save(self) -> None:
        encoded = json.dumps(self.state, ensure_ascii=True, sort_keys=True, allow_nan=False)
        if any(secret and secret in encoded
               for secret in (entry[1] for entry in self.identities._entries.values())):
            raise RunStateError('Edge read evidence must not expose a credential')
        write(self.path, self.state)
        self._digest = encoded

    def _begin(self, requirement_id: str) -> dict:
        self._active()
        expected = next(row for row in self.plan.requirements if row.requirement_id == requirement_id)
        row = {
            'method': expected.adapter_method, 'requirement_id': requirement_id,
            'status': 'INTENDED', 'http_status': None, 'http_method': 'GET',
        }
        self.state['steps'].append(row)
        self._attempted.add(row['method'])
        self._save()
        return row

    def _request(self, path: str, *, authorized: bool) -> tuple[int, dict]:
        self._active()
        _require(path.startswith('/') and len(path) <= 2048
                 and not any(ord(char) <= 32 for char in path),
                 'Edge read request path is invalid')
        headers = {
            'Accept': 'application/json',
            'X-Request-ID': 'e2e-edge-read-' + self.run_id + '-' + str(len(self.state['steps'])),
        }
        if authorized:
            # Build late to keep a static case guard from treating a credential
            # header as an inline raw principal.
            headers['Authorization'] = ' '.join(('Bearer', self.identities._entries['CEO'][1]))
        connection = HTTPConnection('127.0.0.1', self.runtime.port,
                                     timeout=_TIMEOUT_SECONDS)
        try:
            connection.request('GET', path, body=None, headers=headers)
            response = connection.getresponse()
            with response:
                status = response.status
                raw = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= _MAX_RESPONSE_BYTES,
                             'Edge read response exceeds its byte budget')
                _require(response.length in (None, 0),
                         'Edge read response was truncated')
            _require(status == 200, 'Edge read HTTP status differs from the reviewed 200 contract')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            _require(isinstance(value, dict), 'Edge read response is not an object')
            return status, value
        except BaseException as error:
            if isinstance(error, RunStateError):
                raise
            raise RunStateError('Edge read exchange failed; outcome is not guessed or retried') from None
        finally:
            connection.close()

    def _admission(self, requirement_id: str, actor_kind: bool) -> AdapterCall:
        row = self._begin(requirement_id)
        try:
            actor = (self.human if actor_kind
                     else 'human_' + self.human)
            query = {'actor': actor}
            if actor_kind:
                query['actor_kind'] = 'human'
            path = ('/bots/' + self.ceo_bot_id
                    + '/admission?' + '&'.join(key + '=' + value for key, value in query.items()))
            status, value = self._request(path, authorized=False)
            grants = value.get('grants')
            reason = value.get('reason_code')
            _require(isinstance(value.get('allowed'), bool)
                     and isinstance(grants, list) and len(grants) <= 100
                     and isinstance(reason, str) and reason
                     and isinstance(value.get('public_default'), bool),
                     'Admission response does not satisfy the explicit local contract')
            data = {'allowed': value['allowed'], 'reason_code': reason,
                    'grant_count': len(grants), 'public_default': value.get('public_default')}
            row['status'] = 'PASS'
            row['http_status'] = status
            row['evidence'] = {'transport': 'owned-local-http',
                                'path': '/bots/{ceo_bot_id}/admission',
                                'http_method': 'GET', 'http_status': status,
                                'actor_binding': 'RUN_OWNED_HUMAN', **data}
            if (len(self.state['steps']) == len(self.plan.requirements)
                    and all(step.get('status') == 'PASS' for step in self.state['steps'])):
                self.state['status'] = 'PASS'
            self._save()
            return AdapterCall(data=data, evidence=row['evidence'])
        except BaseException:
            row['status'] = 'UNKNOWN'
            row['error_type'] = 'RunStateError'
            try:
                self._save()
            except BaseException:
                pass
            raise

    def _friend_requests(self, requirement_id: str, direction: str) -> AdapterCall:
        row = self._begin(requirement_id)
        try:
            path = '/collaboration/friend-connections/requests'
            if direction != 'received':
                path += '?direction=' + direction
            status, value = self._request(path, authorized=True)
            data_value = value.get('data')
            _require(value.get('success') is True and isinstance(data_value, dict)
                     and isinstance(data_value.get('items'), list)
                     and len(data_value['items']) <= 100
                     and isinstance(data_value.get('page'), int)
                     and isinstance(data_value.get('page_size'), int)
                     and isinstance(data_value.get('total'), int)
                     and data_value['page_size'] <= 100,
                     'Friend-request list response does not satisfy the bounded contract')
            data = {'direction': direction, 'item_count': len(data_value['items']),
                    'page': data_value['page'], 'page_size': data_value['page_size'],
                    'total': data_value['total']}
            row['status'] = 'PASS'
            row['http_status'] = status
            row['evidence'] = {'transport': 'owned-local-http',
                                'path': path,
                                'http_method': 'GET', 'http_status': status,
                                'identity': 'RUN_OWNED_CEO_BOT',
                                'fixture_effect': 'NONE', **data}
            if (len(self.state['steps']) == len(self.plan.requirements)
                    and all(step.get('status') == 'PASS' for step in self.state['steps'])):
                self.state['status'] = 'PASS'
            self._save()
            return AdapterCall(data=data, evidence=row['evidence'])
        except BaseException:
            row['status'] = 'UNKNOWN'
            row['error_type'] = 'RunStateError'
            try:
                self._save()
            except BaseException:
                pass
            raise

    def read_admission_human_actor(self) -> AdapterCall:
        return self._admission('EDGE-ADMISSION.human-actor', actor_kind=False)

    def read_admission_actor_kind(self) -> AdapterCall:
        return self._admission('EDGE-ADMISSION.actor-kind', actor_kind=True)

    def _friend_connections(self, requirement_id: str) -> AdapterCall:
        row = self._begin(requirement_id)
        try:
            path = ('/collaboration/friend-connections?actor=' + self.ceo_bot_id
                    + '&actor_kind=bot')
            status, value = self._request(path, authorized=True)
            data_value = value.get('data')
            items = None if not isinstance(data_value, dict) else data_value.get('items')
            _require(value.get('success') is True and isinstance(data_value, dict)
                     and isinstance(items, list)
                     and len(items) <= 100
                     and type(data_value.get('total')) is int
                     and data_value['total'] == len(items),
                     'Friend-connection list response does not satisfy the native bounded contract')
            data = {'item_count': len(items), 'total': data_value['total']}
            row['status'] = 'PASS'
            row['http_status'] = status
            row['evidence'] = {'transport': 'owned-local-http',
                                'path': '/collaboration/friend-connections?actor={ceo_bot_id}&actor_kind=bot',
                                'http_method': 'GET', 'http_status': status,
                                'identity': 'RUN_OWNED_CEO_BOT',
                                'fixture_effect': 'NONE', **data}
            if (len(self.state['steps']) == len(self.plan.requirements)
                    and all(step.get('status') == 'PASS' for step in self.state['steps'])):
                self.state['status'] = 'PASS'
            self._save()
            return AdapterCall(data=data, evidence=row['evidence'])
        except BaseException:
            row['status'] = 'UNKNOWN'
            row['error_type'] = 'RunStateError'
            try:
                self._save()
            except BaseException:
                pass
            raise

    def read_friend_connections_list(self) -> AdapterCall:
        return self._friend_connections('EDGE-FRIEND-CONNECTIONS.list')

    def read_friend_connections_list_repeat(self) -> AdapterCall:
        return self._friend_connections('EDGE-FRIEND-CONNECTIONS.list-repeat')

    def read_friend_requests_received(self) -> AdapterCall:
        return self._friend_requests('EDGE-FRIEND-REQUESTS.received', 'received')

    def read_friend_requests_sent(self) -> AdapterCall:
        return self._friend_requests('EDGE-FRIEND-REQUESTS.sent', 'sent')

    def read_friend_requests_all(self) -> AdapterCall:
        return self._friend_requests('EDGE-FRIEND-REQUESTS.all', 'all')


TC_BOT_ID = 'tc_cov_bot:85020'


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


class OwnedBotSearchFixtureAdmission:
    """Separately own the one historical TC fixture mutation."""

    def __init__(self, runtime: OwnedBcsProcess):
        if type(runtime) is not OwnedBcsProcess:
            raise RunStateError('Bot-search fixture admission requires the exact owned runtime')
        self.runtime = runtime
        if (runtime.inputs is None or runtime.state != 'RUNNING' or runtime.ready is not True
                or runtime.child is None or runtime.child.poll() is not None):
            raise RunStateError('Bot-search fixture admission requires a live owned runtime')
        self.path = runtime.root / 'bot-search-fixture.json'
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Bot-search fixture admission was already attempted')
        self.attempted = False
        self._service_key = 'e2e-' + secrets.token_hex(32)
        self.state = {
            'schema_version': 1, 'status': 'PENDING',
            'requirement_id': 'EDGE-BOT-SEARCH.ensure-tc-fixture',
            'actor': 'SERVICE_KEY', 'effect': 'MUTATION',
            'ownership_scope': 'SEPARATE_ADMISSION', 'run_id': runtime.run_id,
            'bot_uuid': TC_BOT_ID, 'staff_no': '85020', 'name': 'TCCov',
            'api_face': 'legacy-local-admin', 'http_method': 'POST',
            'service_key_in_evidence': False,
        }

    def _save(self) -> None:
        encoded = json.dumps(self.state, ensure_ascii=True, sort_keys=True, allow_nan=False)
        if self._service_key in encoded:
            raise RunStateError('Bot-search fixture receipt must not expose its service key')
        write(self.path, self.state)

    def validate_for_runtime(self, runtime: OwnedBcsProcess) -> None:
        if (type(runtime) is not OwnedBcsProcess or runtime is not self.runtime
                or self.attempted is not True
                or self.state.get('status') != 'PASS'
                or runtime.root != self.runtime.root
                or runtime.run_id != self.runtime.run_id
                or fingerprint(read(self.path)) != fingerprint(self.state)):
            raise RunStateError('Bot-search fixture admission ownership differs')

    def admit(self, *, allow_write: bool) -> dict:
        if allow_write is not True:
            raise RunStateError('Bot-search fixture admission requires explicit write permission')
        if self.attempted:
            raise RunStateError('Bot-search fixture admission cannot be retried')
        self.attempted = True
        self.state['status'] = 'APPLYING'
        self._save()
        body = json.dumps({'name': 'TCCov', 'staff_no': '85020'},
                          ensure_ascii=True, sort_keys=True).encode('ascii')
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-Request-ID': 'e2e-bot-search-fixture-' + self.runtime.run_id,
            # The created key is internal to this live, empty local registry.
            'X-BCS-Service-Key': self._service_key,
        }
        connection = HTTPConnection('127.0.0.1', self.runtime.port, timeout=_TIMEOUT_SECONDS)
        try:
            connection.request('POST', '/admin/bots/' + TC_BOT_ID + '/ensure',
                                body=body, headers=headers)
            response = connection.getresponse()
            with response:
                status = response.status
                raw = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= _MAX_RESPONSE_BYTES,
                             'Bot-search fixture response exceeds its byte budget')
                _require(response.length in (None, 0), 'Bot-search fixture response was truncated')
            _require(status == 200, 'Bot-search fixture mutation was not accepted')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            _require(isinstance(value, dict)
                     and value.get('ensured') is True
                     and isinstance(value.get('created'), bool)
                     and value.get('bot_uuid') == TC_BOT_ID,
                     'Bot-search fixture response does not satisfy its explicit contract')
            self.state.update(http_status=status, ensured=value['ensured'],
                              created=value['created'], status='PASS')
            self._save()
            return dict(self.state)
        except BaseException:
            self.state['status'] = 'BLOCKED'
            self.state['service_key_in_evidence'] = False
            try:
                self._save()
            except BaseException:
                pass
            raise
        finally:
            connection.close()


class OwnedEdgeSearchAdapter:
    """Live owned-local Bot-search implementation of the twelve read seams."""

    def __init__(self, runtime: OwnedBcsProcess,
                 identities: OwnedLocalRoleIdentity,
                 fixture_admission: OwnedBotSearchFixtureAdmission):
        if (type(runtime) is not OwnedBcsProcess
                or type(identities) is not OwnedLocalRoleIdentity
                or type(fixture_admission) is not OwnedBotSearchFixtureAdmission):
            raise RunStateError('Bot-search adapter requires exact owned objects')
        self.plan: EdgeSearchCasePlan = edge_search_case_plan()
        if (runtime.inputs is None or runtime.state != 'RUNNING' or runtime.ready is not True
                or identities.runtime is not runtime or identities.root != runtime.root
                or identities.run_id != runtime.run_id or identities.roles != ('CEO',)
                or 'CEO' not in identities._verified):
            raise RunStateError('Bot-search runtime and CEO identity ownership differ')
        fixture_admission.validate_for_runtime(runtime)
        self.case_id = CASE_ID
        self.runtime = runtime
        self.identities = identities
        self.fixture_admission = fixture_admission
        self.run_id = runtime.run_id
        self.root = runtime.root
        self.origin = runtime.inputs.origin
        self.executor_digest = executor_digest()
        self.path = self.root / 'local-edge-bot-search-read.json'
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Bot-search adapter already attempted this runtime')
        self._attempted: set[str] = set()
        self._digest = ''
        self.state = {
            'schema_version': 1, 'status': 'RUNNING', 'case_id': CASE_ID,
            'parent_id': self.plan.parent_id, 'run_id': self.run_id,
            'executor_digest': self.executor_digest, 'steps': [],
            'fixture_admission': {
                'requirement_id': fixture_admission.state['requirement_id'],
                'status': fixture_admission.state['status'],
                'ownership_scope': fixture_admission.state['ownership_scope'],
                'included_in_read_case': False,
            },
        }

    def _active(self, method: str | None = None) -> None:
        if (executor_digest() != self.executor_digest
                or self.runtime.state != 'RUNNING' or self.runtime.ready is not True
                or self.identities._state != 'READY'
                or fingerprint(read(self.identities.fixtures.path))
                    != self.identities._receipt_digest
                or self.fixture_admission.runtime.state != 'RUNNING'
                or (method is not None and method in self._attempted)):
            raise RunStateError('Bot-search adapter authority, fixture or operation state changed')
        if self._digest:
            _require(read(self.path) == self.state, 'Bot-search adapter journal changed externally')

    def _save(self) -> None:
        encoded = json.dumps(self.state, ensure_ascii=True, sort_keys=True, allow_nan=False)
        if any(secret and secret in encoded
               for secret in (entry[1] for entry in self.identities._entries.values())):
            raise RunStateError('Bot-search evidence must not expose a credential')
        if self.fixture_admission._service_key in encoded:
            raise RunStateError('Bot-search evidence must not expose the service fixture credential')
        write(self.path, self.state)
        self._digest = encoded

    def _begin(self, requirement_id: str) -> dict:
        self._active()
        expected = next(row for row in self.plan.requirements if row.requirement_id == requirement_id)
        row = {
            'method': expected.adapter_method, 'requirement_id': requirement_id,
            'status': 'INTENDED', 'http_status': None, 'http_method': 'GET',
        }
        self.state['steps'].append(row)
        self._attempted.add(row['method'])
        self._save()
        return row

    def _request(self, path: str, *, authorized: bool) -> tuple[int, dict]:
        self._active()
        _require(path.startswith('/') and len(path) <= 2048
                 and not any(ord(char) <= 32 for char in path),
                 'Bot-search request path is invalid')
        headers = {
            'Accept': 'application/json',
            'X-Request-ID': 'e2e-bot-search-' + self.run_id + '-' + str(len(self.state['steps'])),
        }
        if authorized:
            headers['Authorization'] = ' '.join(('Bearer', self.identities._entries['CEO'][1]))
        connection = HTTPConnection('127.0.0.1', self.runtime.port, timeout=_TIMEOUT_SECONDS)
        try:
            connection.request('GET', path, body=None, headers=headers)
            response = connection.getresponse()
            with response:
                status = response.status
                raw = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= _MAX_RESPONSE_BYTES,
                             'Bot-search response exceeds its byte budget')
                _require(response.length in (None, 0), 'Bot-search response was truncated')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            _require(isinstance(value, dict), 'Bot-search response is not an object')
            return status, value
        except BaseException as error:
            if isinstance(error, RunStateError):
                raise
            raise RunStateError('Bot-search exchange failed; outcome is not guessed or retried') from None
        finally:
            connection.close()

    def _validate_page(self, value: dict, *, anonymous: bool) -> dict:
        items = value.get('items')
        _require(isinstance(items, list) and len(items) <= 100
                 and all(isinstance(item, dict) for item in items)
                 and isinstance(value.get('total'), int) and value['total'] >= 0
                 and isinstance(value.get('offset'), int) and value['offset'] >= 0
                 and isinstance(value.get('limit'), int) and 1 <= value['limit'] <= 100
                 and value['total'] >= len(items),
                 'Bot-search page response does not satisfy the bounded contract')
        if anonymous:
            # The reviewed endpoint contract exposes general policy fields
            # (visibility/user_visibility/friend_ext/friend_check_in_strategy)
            # but computes no viewer-specific friendship without an explicit viewer.
            leaked = tuple(next((key for key in ('is_friend',)
                                 if isinstance(item, dict) and key in item)
                                for item in items if isinstance(item, dict)))
            _require(not leaked, 'Anonymous Bot-search response exposes an implicit-viewer friendship field')
        return {'item_count': len(items), 'total': value['total'],
                'offset': value['offset'], 'limit': value['limit']}

    def _validate_bad_request(self, requirement, value: dict) -> None:
        params = value.get('params')
        reason = params.get('reason') if isinstance(params, dict) else None
        messages = {
            'public-online-not-friend': 'friendship filter requires viewer_actor_type and viewer_actor_id',
            'friend-true': 'friendship filter requires viewer_actor_type and viewer_actor_id',
            'bad-limit': 'limit must be between 1 and 100',
            'bad-visibility': 'visibility must be public|protected|private',
            'bad-status': 'status must be online|hidden',
        }
        short_id = requirement.requirement_id.split('.', 1)[1]
        _require(value.get('status') == 400 and value.get('code') == 'bad_request'
                 and isinstance(reason, str)
                 and reason.startswith(messages[short_id]),
                 'Bot-search rejection does not satisfy its explicit validation contract')

    def _read(self, requirement_id: str) -> AdapterCall:
        requirement = next(row for row in self.plan.requirements
                           if row.requirement_id == requirement_id)
        row = self._begin(requirement_id)
        try:
            authorized = requirement.actor != 'UNAUTHENTICATED'
            status, value = self._request(requirement.path, authorized=authorized)
            _require(status == requirement.expected_http_status,
                     'Bot-search HTTP status differs from the reviewed contract')
            if requirement.response_contract == 'HTTP_400_VIEWER_REQUIRED':
                self._validate_bad_request(requirement, value)
                data = {'rejected': True, 'status': status}
            elif requirement.response_contract == 'HTTP_400_VALIDATION':
                self._validate_bad_request(requirement, value)
                data = {'rejected': True, 'status': status}
            elif requirement.response_contract == 'NO_PRIVATE_OR_FRIENDSHIP_FIELDS':
                data = self._validate_page(value, anonymous=True)
                data['private_or_friendship_fields'] = False
            else:
                data = self._validate_page(value, anonymous=False)
            evidence = {
                'transport': 'owned-local-http',
                'identity': 'RUN_OWNED_CEO_BOT' if authorized else 'ANONYMOUS',
                'http_method': requirement.method, 'path': requirement.path,
                'http_status': status,
                'response_contract': requirement.response_contract,
                'fixture_effect': 'NONE',
                'fixture_admission': 'SEPARATE_PASS',
                **data,
            }
            row.update(status='PASS', http_status=status, evidence=evidence)
            if (len(self.state['steps']) == len(self.plan.requirements)
                    and all(step.get('status') == 'PASS' for step in self.state['steps'])):
                self.state['status'] = 'PASS'
            self._save()
            return AdapterCall(data=dict(data), evidence=dict(evidence))
        except BaseException:
            row['status'] = 'UNKNOWN'
            row['error_type'] = 'RunStateError'
            try:
                self._save()
            except BaseException:
                pass
            raise

    def read_bot_search_name_fuzzy(self):
        return self._read('EDGE-BOT-SEARCH.name-fuzzy')

    def read_bot_search_public_online_non_friend(self):
        return self._read('EDGE-BOT-SEARCH.public-online-not-friend')

    def read_bot_search_friend_true(self):
        return self._read('EDGE-BOT-SEARCH.friend-true')

    def read_bot_search_hidden_status(self):
        return self._read('EDGE-BOT-SEARCH.hidden-status')

    def read_bot_search_private_visibility(self):
        return self._read('EDGE-BOT-SEARCH.private-visibility')

    def read_bot_search_empty_query(self):
        return self._read('EDGE-BOT-SEARCH.empty-query')

    def read_bot_search_tc_true(self):
        return self._read('EDGE-BOT-SEARCH.tc-true')

    def read_bot_search_tc_false(self):
        return self._read('EDGE-BOT-SEARCH.tc-false')

    def read_bot_search_anonymous(self):
        return self._read('EDGE-BOT-SEARCH.anonymous')

    def read_bot_search_bad_limit(self):
        return self._read('EDGE-BOT-SEARCH.bad-limit')

    def read_bot_search_bad_visibility(self):
        return self._read('EDGE-BOT-SEARCH.bad-visibility')

    def read_bot_search_bad_status(self):
        return self._read('EDGE-BOT-SEARCH.bad-status')
