"""Complete owned-local executor for the original Direct chat parent Story.

The executor opens one transport-only PM receiver, submits the original hidden
``ping`` Run and the original release-checklist Run as the owned CEO, and
cancels both exact returned Runs.  It never starts an engine, acknowledges Bot
execution, discovers an ambiguous Run, retries a mutation, deletes a Group or
Session, or claims remote-engine quiescence.
"""
from __future__ import annotations

import copy
from http.client import HTTPConnection
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import uuid
from urllib.parse import quote, urlsplit

from .correlation import record_response_id
from .inventory import current_manifest, executor_digest
from .local_direct_chat_contract import (
    CASE_ID,
    REQUIREMENTS,
    SUITE,
    direct_chat_source_binding,
)
from .local_direct_chat_storage import fixture_snapshot
from .local_direct_receiver import OwnedLocalDirectReceiver
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO', 'PM', 'ENG', 'QA')
CONTRACT = 'owned-local-direct-chat-v1'
LEDGER_CONTRACT = 'owned-local-direct-chat-ledger-v1'
JOURNAL_NAME = 'local-direct-chat.json'
LEDGER_NAME = 'local-direct-chat-ledger.json'
BINDING_NAME = 'direct-chat-binding.json'
MAX_RESPONSE_BYTES = 65_536
HTTP_DEADLINE_SECONDS = 5
FULL_OPERATIONS = (
    'pm-visibility-before',
    'pm-visibility-public',
    'pm-visibility-public-read',
    'ping-submit',
    'main-submit',
    'main-read',
    'main-cancel',
    'main-terminal',
    'ping-cancel',
    'ping-terminal',
    'pm-visibility-restore',
    'pm-visibility-restored-read',
)
_STATUS_FIELDS = {
    'run_id', 'bot_uuid', 'from_bot_id', 'session_id', 'state', 'response',
    'error_message', 'created_at_ms', 'updated_at_ms', 'completed_at_ms',
    'expires_at_ms', 'version', 'content_truncated', 'is_terminal',
}
_CANCEL_FIELDS = {
    'run_id', 'cancelled', 'state', 'response', 'error_message', 'version',
    'content_truncated',
}


class LocalDirectChatError(RunStateError):
    """Sanitized finite-contract failure from the Direct chat executor."""


def _require(value: object, message: str) -> None:
    if not value:
        raise LocalDirectChatError(message)


def _exclusive(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise LocalDirectChatError('Direct chat requires fresh journal and ledger paths')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise LocalDirectChatError('Direct chat requires fresh journal and ledger paths') from None
    os.close(fd)
    write(path, value)


def _safe_json(value: bytes) -> dict:
    try:
        parsed = json.loads(value.decode('utf-8'), object_pairs_hook=unique_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise LocalDirectChatError('Direct chat response is not a unique-key JSON object') from None
    _require(isinstance(parsed, dict), 'Direct chat response is not an object')
    return parsed


class OwnedLocalDirectChat:
    """Execute the twelve finite HTTP operations and two exact WS receipts."""

    ROLES = ROLES

    def __init__(self, identities: OwnedLocalRoleIdentity):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise LocalDirectChatError(
                'Direct chat requires freshly verified CEO/PM/ENG/QA owned-local identities')
        identities._require_roles(ROLES)
        runtime = identities.runtime
        if type(runtime) is not OwnedBcsProcess or runtime.provider_sink is not None:
            raise LocalDirectChatError('Direct chat requires one unextended owned BCS runtime')
        parsed = urlsplit(identities.origin)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.port is None or not 1024 <= parsed.port <= 65535
                or parsed.netloc != f'127.0.0.1:{parsed.port}'
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None):
            raise LocalDirectChatError('Direct chat requires the exact owned loopback origin')
        self.identities = identities
        self.runtime = runtime
        self.root = runtime.root
        self.port = parsed.port
        self.path = self.root / JOURNAL_NAME
        self.ledger_path = self.root / LEDGER_NAME
        self.binding_path = self.root / BINDING_NAME
        self.executor_digest = executor_digest()
        self.source_binding = direct_chat_source_binding()
        self.binding = self._validated_binding()
        self.binding_digest = fingerprint(self.binding)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.config_digest = file_sha256(runtime.inputs.config_path)
        self.state: dict | None = None
        self.ledger: dict | None = None
        self._journal_digest = ''
        self._ledger_digest = ''
        self._claimed_request_ids: set[str] = set()
        self._attempted = False
        self._invalidated = False
        self._public_confirmed = False
        self._restored_confirmed = False
        self._active()
        _require(not self.path.exists() and not self.path.is_symlink(),
                 'Direct chat journal already exists')
        _require(not self.ledger_path.exists() and not self.ledger_path.is_symlink(),
                 'Direct chat ledger already exists')

    def _validated_binding(self) -> dict:
        try:
            binding = read(self.binding_path)
            expected = {
                'schema_version': 1,
                'contract': SUITE,
                'suite': SUITE,
                'case_id': CASE_ID,
                'run_id': self.runtime.run_id,
                'executor_digest': self.executor_digest,
                'bcs_binary_sha256': self.runtime.binary_sha256,
                'store_messages': False,
                'source_binding': self.source_binding,
                'selected_requirement_ids': list(REQUIREMENTS),
                'legacy_inventory': current_manifest()['legacy_inventory'],
                'runtime_dir': str(self.root.resolve()),
            }
            if self.binding_path.is_symlink() or binding != expected:
                raise ValueError
            return binding
        except (KeyError, OSError, TypeError, ValueError, RunStateError):
            raise LocalDirectChatError('Direct chat execution binding is missing or changed') from None

    def _check_file(self, path: Path, digest: str, label: str) -> None:
        if not digest:
            return
        try:
            matches = fingerprint(read(path)) == digest
        except Exception:
            matches = False
        if not matches:
            self._invalidated = True
            raise LocalDirectChatError(f'{label} changed; preserve the original artifact')

    def _active(self) -> None:
        if self._invalidated:
            raise LocalDirectChatError('Direct chat mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        config = json.loads(self.runtime.inputs.config_path.read_text())
        if (self.runtime.store_messages is not False
                or config.get('store_messages') is not False
                or config.get('async_chat_run_store') not in (None, 'memory')
                or config.get('allowed_switch_provider_ids') != []
                or config.get('llm') != {'type': 'none'}
                or config.get('human_notify') != {'providers': []}
                or config.get('eventing') != {'enabled': False, 'dispatcher_enabled': False}
                or executor_digest() != self.executor_digest
                or direct_chat_source_binding() != self.source_binding
                or self._validated_binding() != self.binding
                or file_sha256(self.runtime.inputs.config_path) != self.config_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest):
            raise LocalDirectChatError('Direct chat runtime, source, fixtures, or memory-store boundary changed')
        self._check_file(self.path, self._journal_digest, 'Direct chat journal')
        self._check_file(self.ledger_path, self._ledger_digest, 'Direct chat ledger')

    def _save(self) -> None:
        _require(self.state is not None, 'Direct chat journal is not initialized')
        self._check_file(self.path, self._journal_digest, 'Direct chat journal')
        raw = json.dumps(self.state, sort_keys=True, allow_nan=False)
        _require(not any(token in raw for _, token in self.identities._entries.values()),
                 'Direct chat journal must not contain credentials')
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _save_ledger(self) -> None:
        _require(self.ledger is not None, 'Direct chat effect ledger is not initialized')
        self._check_file(self.ledger_path, self._ledger_digest, 'Direct chat ledger')
        raw = json.dumps(self.ledger, sort_keys=True, allow_nan=False)
        _require(not any(token in raw for _, token in self.identities._entries.values()),
                 'Direct chat ledger must not contain credentials')
        write(self.ledger_path, self.ledger)
        self._ledger_digest = fingerprint(self.ledger)

    def _role(self, role: str) -> tuple[str, str]:
        self._active()
        _require(role in ROLES, 'Direct chat HTTP authority role is invalid')
        return self.identities._entries[role]

    def _effect(self, name: str) -> dict:
        _require(self.ledger is not None and name in ('ping', 'main'),
                 'Direct chat effect name is invalid')
        effect = self.ledger['effects'].get(name)
        _require(isinstance(effect, dict), 'Direct chat effect is not present in the owned ledger')
        return effect

    def _spec(self, name: str) -> tuple[str, str, dict | None, str, int]:
        pm = self.identities._entries['PM'][0]
        effects = self.ledger.get('effects', {}) if self.ledger else {}
        main = effects.get('main', {})
        ping = effects.get('ping', {})
        mapping = {
            'pm-visibility-before': ('GET', f'/bots/{quote(pm, safe="")}/visibility', None, 'PM', 200),
            'pm-visibility-public': ('PUT', f'/bots/{quote(pm, safe="")}/visibility',
                                     {'visibility': 'public'}, 'PM', 200),
            'pm-visibility-public-read': ('GET', f'/bots/{quote(pm, safe="")}/visibility', None, 'PM', 200),
            'ping-submit': ('POST', f'/bots/{quote(pm, safe="")}/chat-async',
                            {'message': 'ping'}, 'CEO', 202),
            'main-submit': ('POST', f'/bots/{quote(pm, safe="")}/chat-async',
                            {'message': 'Prepare a release checklist', 'timeout_ms': 60000,
                             'tags': ['e2e', 'release']}, 'CEO', 202),
            'main-read': ('GET', f'/chat/runs/{quote(str(main.get("run_id", "")), safe="")}',
                          None, 'CEO', 200),
            'main-cancel': ('POST', f'/chat/runs/{quote(str(main.get("run_id", "")), safe="")}/cancel',
                            None, 'CEO', 200),
            'main-terminal': ('GET', f'/chat/runs/{quote(str(main.get("run_id", "")), safe="")}',
                              None, 'CEO', 200),
            'ping-cancel': ('POST', f'/chat/runs/{quote(str(ping.get("run_id", "")), safe="")}/cancel',
                            None, 'CEO', 200),
            'ping-terminal': ('GET', f'/chat/runs/{quote(str(ping.get("run_id", "")), safe="")}',
                              None, 'CEO', 200),
            'pm-visibility-restore': ('PUT', f'/bots/{quote(pm, safe="")}/visibility',
                                      {'visibility': 'protected'}, 'PM', 200),
            'pm-visibility-restored-read': ('GET', f'/bots/{quote(pm, safe="")}/visibility', None, 'PM', 200),
        }
        _require(name in mapping, 'Direct chat operation is outside the finite HTTP plan')
        return mapping[name]

    def _begin(self, name: str) -> dict:
        self._active()
        _require(self.state is not None, 'Direct chat journal is not initialized')
        index = len(self.state['operations'])
        _require(index < len(FULL_OPERATIONS) and FULL_OPERATIONS[index] == name,
                 'Direct chat HTTP operation order differs from the original Story')
        method, path, body, role, expected = self._spec(name)
        request_id = 'e2e-direct-chat-' + uuid.uuid4().hex
        event = {
            'operation': name,
            'method': method,
            'path': path,
            'request_body': copy.deepcopy(body),
            'auth_role': role,
            'submitted_request_id': request_id,
            'expected_http_status': expected,
            'status': 'INTENDED',
            'transitions': ['INTENDED'],
        }
        self.state['operations'].append(event)
        self._save()
        return event

    def _wire(self, event: dict) -> tuple[int, dict]:
        self._active()
        _require(self.state is not None and self.state['operations'][-1] is event
                 and event['status'] == 'INTENDED',
                 'Direct chat wire dispatch lacks the current persisted intent')
        name = event['operation']
        method, path, body, role, _expected = self._spec(name)
        _require((event['method'], event['path'], event['request_body'], event['auth_role'])
                 == (method, path, body, role),
                 'Direct chat wire method/path/body/auth differs from the finite plan')
        request_id = event['submitted_request_id']
        _require(request_id not in self._claimed_request_ids,
                 'Direct chat request ID cannot be replayed')
        self._claimed_request_ids.add(request_id)
        bot_id, token = self._role(role)
        _require(bot_id == self.identities._entries[role][0],
                 'Direct chat HTTP authority changed before dispatch')
        encoded = (json.dumps(body, separators=(',', ':'), ensure_ascii=True).encode()
                   if body is not None else None)
        headers = {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + token,
            'X-BCS-Bot-Token': token,
            'X-Request-ID': request_id,
            'Connection': 'close',
        }
        if encoded is not None:
            headers['Content-Type'] = 'application/json'
        connection = HTTPConnection('127.0.0.1', self.port, timeout=HTTP_DEADLINE_SECONDS)
        deadline = time.monotonic() + HTTP_DEADLINE_SECONDS
        dispatched = False
        try:
            connection.request(method, path, body=encoded, headers=headers)
            dispatched = True
            response = connection.getresponse()
            record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                               sensitive_values=(token, 'Bearer ' + token))
            raw = bytearray()
            sock = connection.sock
            with response:
                while True:
                    remaining = deadline - time.monotonic()
                    _require(remaining > 0, 'Direct chat response exceeded its absolute deadline')
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= MAX_RESPONSE_BYTES,
                             'Direct chat response exceeds its byte budget')
                _require(response.length in (None, 0),
                         'Direct chat response ended before its declared length')
                status = response.status
            return status, _safe_json(bytes(raw))
        except (LocalDirectChatError, socket.timeout, OSError, ValueError):
            if dispatched:
                event['status'] = 'UNKNOWN'
                event['transitions'].append('UNKNOWN')
                try:
                    self._save()
                except BaseException:
                    self._invalidated = True
            raise LocalDirectChatError(
                'Direct chat request outcome is unknown; no retry or rediscovery is permitted') from None
        finally:
            connection.close()
            token = ''
            headers.clear()

    def _ack(self, event: dict, status: int, observed: dict) -> None:
        _require(status == event['expected_http_status'],
                 'Direct chat HTTP status differs from the finite contract')
        event.update(http_status=status, status='ACKNOWLEDGED', observed=observed)
        event['transitions'].append('ACKNOWLEDGED')
        self._save()

    def _unknown(self, event: dict) -> None:
        if event.get('status') == 'INTENDED':
            event['status'] = 'UNKNOWN'
            event['transitions'].append('UNKNOWN')
            self._save()

    def _visibility(self, name: str, expected: str) -> str:
        event = self._begin(name)
        try:
            status, value = self._wire(event)
            data = value.get('data') if value.get('success') is True else None
            pm = self.identities._entries['PM'][0]
            _require(set(value) == {'success', 'data'} and isinstance(data, dict)
                     and set(data) == {'bot_uuid', 'visibility'}
                     and data.get('bot_uuid') == pm and data.get('visibility') == expected,
                     'Direct chat PM visibility response differs')
            self._ack(event, status, {'bot_uuid': pm, 'visibility': expected})
            if name == 'pm-visibility-public':
                self._public_confirmed = True
            if name == 'pm-visibility-restored-read':
                self._restored_confirmed = True
            return expected
        except BaseException:
            self._unknown(event)
            raise

    def _ledger_intent(self, name: str, event: dict) -> dict:
        _require(self.ledger is not None and name in ('ping', 'main')
                 and name not in self.ledger['effects'],
                 'Direct chat submission effect cannot be duplicated')
        owner = self.identities._entries['CEO'][0]
        target = self.identities._entries['PM'][0]
        effect = {
            'name': name,
            'case_id': CASE_ID,
            'source': 'ORIGINAL_DIRECT_CHAT_STORY',
            'owner_id': owner,
            'target_id': target,
            'request_id': event['submitted_request_id'],
            'run_id': None,
            'session_id': None,
            'origin': 'created',
            'state': 'INTENDED',
            'transitions': ['INTENDED'],
        }
        self.ledger['effects'][name] = effect
        self._save_ledger()
        return effect

    def _submit(self, name: str, receiver: OwnedLocalDirectReceiver) -> tuple[dict, dict]:
        operation = name + '-submit'
        event = self._begin(operation)
        effect = self._ledger_intent(name, event)
        try:
            status, value = self._wire(event)
            _require(set(value) == {'run_id', 'bot_uuid', 'session_id', 'status', 'expires_at_ms'},
                     'Direct chat submission response fields differ')
            run_id = identifier(value.get('run_id'), 'Direct chat returned Run ID')
            session_id = identifier(value.get('session_id'), 'Direct chat returned Session ID')
            owner = self.identities._entries['CEO'][0]
            target = self.identities._entries['PM'][0]
            _require(value.get('bot_uuid') == target and value.get('status') == 'running'
                     and type(value.get('expires_at_ms')) is int and value['expires_at_ms'] > 0,
                     'Direct chat submission identity or lifecycle differs')
            if self.ledger['effects'].keys() == {'ping', 'main'}:
                other = self.ledger['effects']['ping' if name == 'main' else 'main']
                _require(run_id != other.get('run_id') and session_id != other.get('session_id'),
                         'Direct chat submissions returned duplicate Run or Session IDs')
            effect.update(run_id=run_id, session_id=session_id, state='ADMITTED')
            effect['transitions'].append('ADMITTED')
            self._save_ledger()
            self._ack(event, status, {'run_id': run_id, 'session_id': session_id,
                                      'bot_uuid': target, 'status': 'running'})
            text = 'ping' if name == 'ping' else 'Prepare a release checklist'
            tags = [] if name == 'ping' else ['e2e', 'release']
            receipt = receiver.receive_run(run_id=run_id, session_id=session_id,
                                           caller_id=owner, text=text, tags=tags)
            self.state['checks'].append({'id': name + '-transport-receipt', 'status': 'PASS',
                                         'evidence': receipt})
            self._save()
            return copy.deepcopy(effect), receipt
        except BaseException:
            if effect.get('state') == 'INTENDED':
                effect['state'] = 'UNKNOWN'
                effect['transitions'].append('UNKNOWN')
                self._save_ledger()
            self._unknown(event)
            raise

    @staticmethod
    def _response_empty(value: object) -> bool:
        return isinstance(value, dict) and value == {'content': ''}

    def _require_effect_provenance(self, name: str, effect: dict, states: tuple[str, ...]) -> None:
        _require(effect.get('name') == name
                 and effect.get('case_id') == CASE_ID
                 and effect.get('source') == 'ORIGINAL_DIRECT_CHAT_STORY'
                 and effect.get('owner_id') == self.identities._entries['CEO'][0]
                 and effect.get('target_id') == self.identities._entries['PM'][0]
                 and effect.get('origin') == 'created'
                 and isinstance(effect.get('request_id'), str) and effect['request_id']
                 and isinstance(effect.get('run_id'), str) and effect['run_id']
                 and isinstance(effect.get('session_id'), str) and effect['session_id']
                 and effect.get('state') in states,
                 'Direct chat effect provenance or lifecycle is not exact')

    def _status_projection(self, value: dict, effect: dict, *, terminal: bool) -> dict:
        _require(set(value) == _STATUS_FIELDS,
                 'Direct chat Run read response fields differ')
        expected_state = 'cancelled' if terminal else 'running'
        _require(value.get('run_id') == effect['run_id']
                 and value.get('session_id') == effect['session_id']
                 and value.get('from_bot_id') == effect['owner_id']
                 and value.get('bot_uuid') == effect['target_id']
                 and value.get('state') == expected_state
                 and value.get('is_terminal') is terminal
                 and self._response_empty(value.get('response'))
                 and value.get('error_message') is None
                 and type(value.get('version')) is int and value['version'] > 0
                 and type(value.get('created_at_ms')) is int and value['created_at_ms'] > 0
                 and type(value.get('updated_at_ms')) is int
                 and value['updated_at_ms'] >= value['created_at_ms']
                 and type(value.get('expires_at_ms')) is int
                 and value['expires_at_ms'] >= value['created_at_ms']
                 and value.get('content_truncated') is False,
                 'Direct chat Run read is not identity-bound to the expected lifecycle')
        if terminal:
            _require(type(value.get('completed_at_ms')) is int
                     and value['completed_at_ms'] >= value['created_at_ms'],
                     'Direct chat terminal read lacks a meaningful completion time')
        else:
            _require(value.get('completed_at_ms') is None,
                     'Direct chat running read unexpectedly claims terminal completion')
        return {
            'run_id': effect['run_id'],
            'session_id': effect['session_id'],
            'bot_uuid': effect['target_id'],
            'from_bot_id': effect['owner_id'],
            'state': expected_state,
            'is_terminal': terminal,
            'version': value['version'],
        }

    def _read_run(self, operation: str, name: str, *, terminal: bool) -> dict:
        effect = self._effect(name)
        expected_state = 'CANCEL_REQUESTED' if terminal else 'ADMITTED'
        self._require_effect_provenance(name, effect, (expected_state,))
        event = self._begin(operation)
        try:
            status, value = self._wire(event)
            observed = self._status_projection(value, effect, terminal=terminal)
            if terminal:
                effect['state'] = 'CANCELLED'
                effect['transitions'].append('CANCELLED')
                self._save_ledger()
            self._ack(event, status, observed)
            return observed
        except BaseException:
            self._unknown(event)
            raise

    def _request_cancel(self, operation: str, name: str) -> dict:
        effect = self._effect(name)
        self._require_effect_provenance(name, effect, ('ADMITTED',))
        event = self._begin(operation)
        _require(event['path'] == f'/chat/runs/{quote(effect["run_id"], safe="")}/cancel',
                 'Direct chat cancellation path is not the exact owned Run')
        effect['state'] = 'CANCEL_REQUESTED'
        effect['transitions'].append('CANCEL_REQUESTED')
        self._save_ledger()
        try:
            status, value = self._wire(event)
            _require(set(value) == _CANCEL_FIELDS
                     and value.get('run_id') == effect['run_id']
                     and value.get('cancelled') is True
                     and value.get('state') == 'cancelled'
                     and self._response_empty(value.get('response'))
                     and value.get('error_message') is None
                     and type(value.get('version')) is int and value['version'] > 0
                     and value.get('content_truncated') is False,
                     'Direct chat cancellation acknowledgement differs')
            observed = {'run_id': effect['run_id'], 'cancelled': True,
                        'state': 'cancelled', 'version': value['version']}
            # API acceptance alone is deliberately not a ledger terminal state.
            self._ack(event, status, observed)
            return observed
        except BaseException:
            effect['cancel_outcome'] = 'UNKNOWN'
            self._save_ledger()
            self._unknown(event)
            raise

    def _append_assertion(self, requirement: str, evidence: dict) -> None:
        expected = REQUIREMENTS[len(self.state['assertions'])]
        _require(requirement == expected,
                 'Direct chat requirement credit order differs from the original Story')
        self.state['assertions'].append({'id': requirement, 'status': 'PASS'})
        self.state['checks'].append({'id': requirement, 'status': 'PASS',
                                     'evidence': copy.deepcopy(evidence)})
        self._save()

    def run(self) -> dict:
        _require(not self._attempted, 'Direct chat execution cannot be repeated')
        self._attempted = True
        self._active()
        self.state = {
            'schema_version': 1,
            'run_id': self.runtime.run_id,
            'case_id': CASE_ID,
            'status': 'RUNNING',
            'executor_digest': self.executor_digest,
            'binding_digest': self.binding_digest,
            'source_binding_digest': fingerprint(self.source_binding),
            'requirements': list(REQUIREMENTS),
            'operations': [],
            'checks': [],
            'assertions': [],
            'resources': {},
            'receiver': {'state': 'NOT_OPENED'},
            'visibility': {},
            'fixture_preservation': {},
            'execution_scope': {
                'runtime': 'OWNED_LOCAL_FOREGROUND',
                'transport': 'NATIVE_HTTP_AND_BOT_WEBSOCKET',
                'async_chat_run_store': 'MEMORY_DEFAULT',
                'store_messages': False,
                'provider': 'NOT_CONFIGURED',
                'engine': 'NOT_STARTED',
                'success_ack': 'NOT_SENT',
                'remote_validation': 'NOT_RUN',
                'remote_engine_quiescence': 'NOT_CLAIMED',
                'group_or_session_delete': 'NOT_PERFORMED',
            },
            'mock_boundary': {
                'proven': ['owned loopback BCS HTTP lifecycle', 'exact PM WebSocket transport receipt',
                           'memory Run cancellation and terminal read'],
                'not_proven': ['Bot engine reasoning', 'external engine termination',
                               'persistent chat-run database', 'remote direct-chat lifecycle'],
            },
            'resource_disposition': 'UNVERIFIED',
        }
        self.ledger = {
            'schema_version': 1,
            'contract': LEDGER_CONTRACT,
            'run_id': self.runtime.run_id,
            'case_id': CASE_ID,
            'executor_digest': self.executor_digest,
            'binding_digest': self.binding_digest,
            'effects': {},
            'status': 'ACTIVE',
        }
        _exclusive(self.path, self.state)
        self._journal_digest = fingerprint(self.state)
        _exclusive(self.ledger_path, self.ledger)
        self._ledger_digest = fingerprint(self.ledger)
        receiver = None
        try:
            before = fixture_snapshot(self)
            self.state['fixture_preservation']['before'] = before
            self.state['checks'].append({'id': 'owned-local-boundary-before', 'status': 'PASS',
                                         'evidence': before})
            self._save()
            self.state['visibility']['before'] = self._visibility(
                'pm-visibility-before', 'protected')
            self.state['visibility']['public_write'] = self._visibility(
                'pm-visibility-public', 'public')
            self.state['visibility']['public_read'] = self._visibility(
                'pm-visibility-public-read', 'public')

            receiver = OwnedLocalDirectReceiver(self.identities, role='PM')
            with receiver:
                ping, ping_receipt = self._submit('ping', receiver)
                self._append_assertion('DM.ping-submit', {
                    'http_status': 202, 'run_id': ping['run_id'],
                    'session_id': ping['session_id'], 'receiver': ping_receipt})
                main, main_receipt = self._submit('main', receiver)
                self._append_assertion('DM.main-submit', {
                    'http_status': 202, 'run_id': main['run_id'],
                    'session_id': main['session_id'], 'receiver': main_receipt})
                running = self._read_run('main-read', 'main', terminal=False)
                self._append_assertion('DM.main-read', running)
                cancelled = self._request_cancel('main-cancel', 'main')
                self._append_assertion('DM.main-cancel', cancelled)
                terminal = self._read_run('main-terminal', 'main', terminal=True)
                _require(terminal['version'] >= cancelled['version'],
                         'Direct chat main terminal version regressed')
                self._append_assertion('DM.main-terminal', terminal)
                ping_cancelled = self._request_cancel('ping-cancel', 'ping')
                ping_terminal = self._read_run('ping-terminal', 'ping', terminal=True)
                _require(ping_terminal['version'] >= ping_cancelled['version'],
                         'Direct chat ping terminal version regressed')
                self.state['checks'].append({
                    'id': 'supplementary-ping-cancel-and-terminal-read',
                    'status': 'PASS',
                    'evidence': {'cancel': ping_cancelled, 'terminal': ping_terminal,
                                 'parent_requirement_credit': False},
                })
                self._save()
            # Success cannot be journalled while the Bot transport remains open.
            self.state['receiver'] = receiver.summary()
            _require(self.state['receiver'].get('state') == 'CLOSED'
                     and self.state['receiver'].get('success_ack_sent') is False
                     and self.state['receiver'].get('engine') == 'NOT_STARTED'
                     and len(self.state['receiver'].get('deliveries', [])) == 2,
                     'Direct chat receiver did not close with two exact transport receipts')

            self.state['visibility']['restore_write'] = self._visibility(
                'pm-visibility-restore', 'protected')
            self.state['visibility']['restored_read'] = self._visibility(
                'pm-visibility-restored-read', 'protected')
            self.state['visibility']['restored'] = self._restored_confirmed
            after = fixture_snapshot(self)
            self.state['fixture_preservation']['after'] = after
            self._save()
            _require(after == before, 'Direct chat fixture or friendship state changed')
            self.state['fixture_preservation'].update(after=after, preserved=True)
            self.state['checks'].append({'id': 'owned-local-boundary-after', 'status': 'PASS',
                                         'evidence': after})

            effects = [copy.deepcopy(self.ledger['effects'][name]) for name in ('ping', 'main')]
            _require([row['name'] for row in effects] == ['ping', 'main']
                     and all(row['state'] == 'CANCELLED'
                             and row['origin'] == 'created'
                             and row['transitions'] == [
                                 'INTENDED', 'ADMITTED', 'CANCEL_REQUESTED', 'CANCELLED']
                             for row in effects),
                     'Direct chat effect ledger is not terminal and identity-bound')
            self.ledger['status'] = 'TWO_OWNED_RUNS_CANCELLED'
            self._save_ledger()
            self.state['resources'] = {
                'ledger_name': LEDGER_NAME,
                'ledger_digest': fingerprint(self.ledger),
                'effects': effects,
                'disposition': 'TWO_OWNED_MEMORY_RUNS_CANCELLED_RETAINED_UNTIL_PROCESS_STOP',
            }
            _require([row['operation'] for row in self.state['operations']] == list(FULL_OPERATIONS)
                     and len({row['submitted_request_id'] for row in self.state['operations']}) == 12
                     and all(row['status'] == 'ACKNOWLEDGED'
                             and row['transitions'] == ['INTENDED', 'ACKNOWLEDGED']
                             for row in self.state['operations'])
                     and self.state['assertions'] == [
                         {'id': requirement, 'status': 'PASS'} for requirement in REQUIREMENTS],
                     'Direct chat final operation or requirement evidence is incomplete')
            self.state.update(
                status='LOCAL_DIRECT_CHAT_PASS',
                resource_disposition=self.state['resources']['disposition'],
            )
            self._save()
            return copy.deepcopy(self.state)
        except BaseException as error:
            if receiver is not None:
                try:
                    receiver.close()
                    if self.state is not None:
                        self.state['receiver'] = receiver.summary()
                except BaseException:
                    if self.state is not None:
                        self.state['receiver'] = {'state': 'CLOSE_UNCONFIRMED'}
            if self.state is not None:
                self.state['visibility']['unrestored'] = (
                    self._public_confirmed and not self._restored_confirmed)
                self.state['status'] = ('CANCELLED'
                                        if isinstance(error, (KeyboardInterrupt, SystemExit))
                                        else 'BLOCKED')
                self.state['error_type'] = type(error).__name__
                if self.ledger is not None:
                    self.state['resources'] = {
                        'ledger_name': LEDGER_NAME,
                        'ledger_digest': fingerprint(self.ledger),
                        'effects': [copy.deepcopy(row) for row in self.ledger['effects'].values()],
                        'disposition': 'OWNED_MEMORY_RUN_OUTCOMES_RETAINED_FOR_PROCESS_STOP',
                    }
                try:
                    self._save()
                except BaseException:
                    pass
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, LocalDirectChatError):
                raise
            raise LocalDirectChatError(
                'Direct chat Story blocked; inspect retained bounded evidence without retry') from None
