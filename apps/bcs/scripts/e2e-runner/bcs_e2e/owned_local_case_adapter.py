"""Owned-local adapter for the reviewed slow-Judge case.

This concrete adapter keeps the business obligation order frozen while mapping
all 22 obligations to a live, runner-owned BCS and Provider/Judge sandbox.
Each operation fails closed; no environment may reinterpret a shared obligation.
"""
from __future__ import annotations

import hashlib
import json
import time
from http.client import HTTPConnection
from urllib.parse import quote, urlsplit

from .case_adapters import ADAPTER_METHOD_NAMES, AdapterCall
from .case_rewrite import slow_judge_case_rewrite
from .local_judge_sandbox import OwnedLocalJudgeSandbox
from .local_provider_storage import OwnedLocalProviderStorage
from .local_role_identity import LocalRoleIdentityError, OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier, unique_keys
from .run_state import RunStateError, read, write


_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_TIMEOUT_SECONDS = 5.0


def _require(value: object, message: str = 'Owned-local slow-Judge contract was not confirmed') -> None:
    if not value:
        raise RunStateError(message)


def _safe_token(value: object, label: str) -> str:
    _require(isinstance(value, str) and 1 <= len(value) <= 4096 and value.isascii()
             and not any(ord(char) <= 32 or ord(char) == 127 for char in value),
             f'owned-local slow-Judge {label} is missing or invalid')
    return value


class OwnedLocalCaseAdapter:
    """Live local implementation of the slow-Judge case seams."""

    def __init__(self, runtime: OwnedBcsProcess, sandbox: OwnedLocalJudgeSandbox,
                 identities: OwnedLocalRoleIdentity):
        self.plan = slow_judge_case_rewrite()
        if (type(runtime) is not OwnedBcsProcess or type(sandbox) is not OwnedLocalJudgeSandbox
                or type(identities) is not OwnedLocalRoleIdentity):
            raise RunStateError('Owned-local slow-Judge case requires exact owned runtime, sandbox and identity objects')
        if (runtime.inputs is None or runtime.state != 'RUNNING' or runtime.ready is not True
                or runtime.store_messages is not True or runtime.judge_sandbox is not sandbox
                or identities.runtime is not runtime or identities.root != runtime.root
                or runtime.run_id != identities.run_id):
            raise RunStateError('Owned-local slow-Judge runtime and identity ownership differ')
        sandbox.validate_live(runtime.run_id)
        self.runtime, self.sandbox, self.identities = runtime, sandbox, identities
        self.run_id, self.root = runtime.run_id, runtime.root
        self.origin = runtime.inputs.origin
        parsed = urlsplit(self.origin)
        _require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                 and parsed.port is not None and not parsed.path and not parsed.query and not parsed.fragment,
                 'Owned-local slow-Judge runtime origin is outside its loopback contract')
        self._host, self._port = parsed.hostname, parsed.port
        try:
            self.ceo_bot_id = identities.actor_id('CEO')
        except LocalRoleIdentityError:
            raise RunStateError('Owned-local slow-Judge CEO identity is not ready') from None
        self.human = runtime.inputs.human_id
        self.storage = OwnedLocalProviderStorage(runtime)
        self.path = self.root / 'local-slow-judge-adapter.json'
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Owned-local slow-Judge adapter already attempted this runtime')
        self._credentials: dict[str, str] = {}
        self._ids: dict[str, str] = {}
        self._secrets = tuple()
        self._attempted: set[str] = set()
        self._digest = ''
        self.state = {
            'schema_version': 1, 'status': 'RUNNING', 'case_id': self.plan.case_id,
            'parent_id': self.plan.parent_id, 'run_id': self.run_id,
            'adapter_family': self.plan.environments[0].adapter_family, 'steps': [],
        }

    def _no_secrets(self, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
        _require(not any(secret and secret in encoded for secret in self._secrets),
                 'Owned-local slow-Judge evidence contains non-public data')

    def _save(self) -> None:
        self._no_secrets(self.state)
        write(self.path, self.state)
        self._digest = json.dumps(self.state, sort_keys=True, separators=(',', ':'))

    def _check_integrity(self) -> None:
        if self._digest:
            _require(read(self.path) == self.state
                     and json.dumps(read(self.path), sort_keys=True, separators=(',', ':')) == self._digest,
                     'Owned-local slow-Judge adapter journal changed externally')

    def _begin(self, method: str) -> dict:
        if method not in ADAPTER_METHOD_NAMES:
            raise RunStateError('Owned-local slow-Judge adapter operation is unmapped')
        self._check_integrity()
        _require(method not in self._attempted,
                 'Owned-local slow-Judge operation cannot be replayed')
        index = ADAPTER_METHOD_NAMES.index(method)
        _require([row['method'] for row in self.state['steps']] == list(ADAPTER_METHOD_NAMES[:index]),
                 'Owned-local slow-Judge operation is outside the contract order')
        if index:
            _require(self.state['steps'][-1]['status'] == 'PASS',
                     'Owned-local slow-Judge operation follows an unfinished predecessor')
        row = {'method': method, 'requirement_id': self.plan.requirements[index].requirement_id,
               'status': 'INTENDED', 'http_status': None}
        self.state['steps'].append(row)
        self._attempted.add(method)
        self._save()
        return row

    def _acknowledge(self, row: dict, status: int) -> None:
        row['status'] = 'ACKNOWLEDGED'
        row['http_status'] = status
        self._save()

    def _pass(self, row: dict, evidence: dict) -> AdapterCall:
        _require(isinstance(evidence, dict))
        self._no_secrets(evidence)
        row['status'] = 'PASS'
        row['evidence'] = evidence
        self._save()
        return AdapterCall(data=evidence, evidence=evidence)

    def _blocked(self, row: dict, error: BaseException) -> None:
        row['status'] = 'UNKNOWN'
        row['error_type'] = type(error).__name__
        try:
            self._save()
        except BaseException:
            pass

    def _wire(self, *, method: str, path: str, body: dict | None,
              authorization: str = '', exact_status: int,
              accepted_statuses: tuple[int, ...] | None = None,
              provider_id: str = '') -> tuple[int, dict]:
        _require(path.startswith('/') and len(path) <= 2048
                 and not any(ord(char) < 32 for char in path),
                 'Owned-local slow-Judge path is invalid')
        accepted = accepted_statuses or (exact_status,)
        _require(exact_status in accepted,
                 'Owned-local slow-Judge accepted status set is invalid')
        payload = None if body is None else json.dumps(body, allow_nan=False,
                                                       separators=(',', ':')).encode()
        _require(payload is None or len(payload) <= _MAX_BODY_BYTES,
                 'Owned-local slow-Judge request exceeds its byte budget')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'X-Request-ID': 'e2e-slow-judge-' + self.run_id + '-' + str(len(self.state['steps']))}
        if authorization:
            headers['Authorization'] = authorization
        if provider_id:
            headers['X-BCN-Provider-Id'] = provider_id
        connection = HTTPConnection(self._host, self._port, timeout=_TRANSPORT_TIMEOUT_SECONDS)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            with response:
                status = response.status
                raw = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= _MAX_BODY_BYTES,
                             'Owned-local slow-Judge response exceeds its byte budget')
                _require(response.length in (None, 0),
                         'Owned-local slow-Judge response was truncated')
            _require(status in accepted,
                     'Owned-local slow-Judge HTTP status differs from the original contract')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            _require(isinstance(value, dict),
                     'Owned-local slow-Judge response is not an object')
            return status, value
        except BaseException as error:
            raise RunStateError('Owned-local slow-Judge exchange failed; outcome unknown, no retry or cleanup') from None
        finally:
            connection.close()

    def _credential(self, key: str, value: object) -> str:
        token = _safe_token(value, key + ' token')
        _require(key not in self._credentials and token not in self._credentials.values()
                 and token not in (self._ids.get('provider_id', ''), self._ids.get('provider_bot_uuid', '')),
                 'Owned-local slow-Judge credential is missing, reused or borrowed')
        self._credentials[key] = token
        self._secrets = tuple(set(self._credentials.values())
                              | {entry[1] for entry in self.identities._entries.values()})
        return token

    def ensure_empty_provider_sandbox(self) -> AdapterCall:
        row = self._begin('ensure_empty_provider_sandbox')
        try:
            self.sandbox.validate_live(self.run_id)
            snapshot = self.sandbox.snapshot()
            _require(snapshot['state'] == 'LIVE' and snapshot['failed'] is False
                     and snapshot['provider_receipts'] == []
                     and snapshot['judge_started'] is False
                     and snapshot['judge_released'] is False
                     and snapshot['judge_finished'] is False,
                     'Owned-local Provider/Judge sandbox is not new and empty')
            evidence = {
                'sandbox_state': 'LIVE', 'provider_receipt_count': 0,
                'judge_started': False, 'judge_released': False,
                'judge_finished': False, 'connection_total': snapshot['connection_total'],
                'ownership': 'run-owned-new-sandbox',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local slow-Judge sandbox admission failed') from None

    def register_provider(self) -> AdapterCall:
        row = self._begin('register_provider')
        try:
            request_id = self._ids.get('provider_request_id', '')
            body = {
                'name': 'Slow Judge E2E Provider', 'webhook_url': self.sandbox.provider_url,
                'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0',
                'coordination': {'mode': 'native_tool'},
            }
            status, value = self._wire(method='POST', path='/providers', body=body,
                                       exact_status=200)
            self._acknowledge(row, status)
            provider_id = identifier(value.get('provider_id'), 'owned Provider ID')
            admin_token = self._credential('provider_admin', value.get('provider_admin_token'))
            downlink_token = self._credential('downlink', value.get('bcs_to_provider_token'))
            self._ids['provider_id'] = provider_id
            self._ids['provider_bot_ref'] = self.run_id
            stored = self.storage.provider(provider_id)
            provider, credentials = stored['provider'], stored['credentials']
            _require(provider['provider_id'] == provider_id
                     and provider['created_by'] == self.human
                     and provider['owners'] == [self.human]
                     and provider['name'] == 'Slow Judge E2E Provider'
                     and provider['disabled'] == 0
                     and provider['auth_mode'] == 'static_bearer'
                     and provider['protocol_version'] == '2.0'
                     and provider['downlink_enabled'] is True
                     and provider['coordination_mode'] == 'native_tool'
                     and provider['sink_url_sha256'] == hashlib.sha256(
                         self.sandbox.provider_url.encode()).hexdigest()
                     and credentials == [{'credential_kind': 'downlink_bcs_to_provider', 'disabled': 0},
                                         {'credential_kind': 'provider_admin', 'disabled': 0}],
                     'Owned Provider storage does not match its registration contract')
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST', 'path': '/providers',
                'http_status': status, 'provider': {
                    'provider_id': provider_id, 'created_by': self.human,
                    'owner_count': 1, 'disabled': 0,
                }, 'credential_kinds_returned': ['provider_admin', 'downlink'],
                'credential_memory': 'HELD',
                'storage': {'source': 'local-sqlite', 'credentials': credentials},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local Provider registration was not confirmed') from None

    def publish_provider_bot(self) -> AdapterCall:
        row = self._begin('publish_provider_bot')
        try:
            provider_id = self._ids['provider_id']
            body = {
                'name': 'Slow Judge Worker', 'summary': 'Returns before Judge completion',
                'owners': [self.human], 'provider_bot_ref': self._ids['provider_bot_ref'],
                'domains': ['release'], 'skills': ['review'], 'scopes': ['local'],
            }
            status, value = self._wire(
                method='POST', path='/providers/' + quote(provider_id, safe='') + '/bots',
                body=body, authorization='Bearer ' + self._credentials['provider_admin'],
                exact_status=200)
            self._acknowledge(row, status)
            bot_uuid = identifier(value.get('bot_uuid'), 'owned Provider Bot ID')
            _require(value.get('provider_id') == provider_id
                     and value.get('provider_bot_ref') == self._ids['provider_bot_ref']
                     and bot_uuid != provider_id)
            runtime_token = self._credential('bot_runtime', value.get('bot_runtime_token'))
            self._ids['provider_bot_uuid'] = bot_uuid
            self.sandbox.arm_provider(provider_id, bot_uuid, self._credentials['downlink'])
            stored = self.storage.bot(provider_id, bot_uuid, self._ids['provider_bot_ref'], runtime_token)
            binding, bot = stored['binding'], stored['bot']
            _require(binding['provider_id'] == provider_id
                     and binding['provider_bot_ref'] == self._ids['provider_bot_ref']
                     and binding['disabled'] == 0
                     and bot['bot_uuid'] == bot_uuid and bot['created_by'] == self.human
                     and bot['actor_kind'] == 'bot' and bot['is_deleted'] == 0
                     and bot['credential_matches'] == 1,
                     'Owned Provider Bot storage does not match its publication contract')
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST',
                'path': '/providers/{provider_id}/bots', 'http_status': status,
                'provider_bot': {'provider_id': provider_id, 'provider_bot_ref': self._ids['provider_bot_ref'],
                                 'bot_uuid': bot_uuid, 'created_by': self.human, 'actor_kind': 'bot'},
                'provider_sandbox_armed': True, 'credential_kinds_returned': ['bot_runtime'],
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local Provider Bot publication was not confirmed') from None

    def publish_provider_bot_visibility(self) -> AdapterCall:
        row = self._begin('publish_provider_bot_visibility')
        try:
            path = '/bots/' + quote(self._ids['provider_bot_uuid'], safe='') + '/visibility'
            status, value = self._wire(method='PUT', path=path,
                                       body={'visibility': 'public'}, exact_status=200)
            self._acknowledge(row, status)
            _require(isinstance(value, dict) and value.get('success') is True
                     and isinstance(value.get('data'), dict)
                     and value['data'].get('visibility') == 'public',
                     'Owned Provider Bot visibility response did not report public')
            stored = self.storage.bot(self._ids['provider_id'], self._ids['provider_bot_uuid'],
                                      self._ids['provider_bot_ref'], self._credentials['bot_runtime'])
            _require(stored['bot']['visibility'] == 'public',
                     'Owned Provider Bot visibility was not persisted')
            evidence = {
                'transport': 'owned-local-http', 'method': 'PUT',
                'path': '/bots/{provider_bot_uuid}/visibility', 'http_status': status,
                'visibility': 'public',
                'storage': {'source': 'local-sqlite', 'bot_uuid': self._ids['provider_bot_uuid'],
                            'visibility': 'public'},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local Provider Bot visibility was not confirmed') from None

    def request_provider_bot_friendship(self) -> AdapterCall:
        row = self._begin('request_provider_bot_friendship')
        try:
            body = {'from_bot': self.ceo_bot_id, 'to_bot': self._ids['provider_bot_uuid']}
            status, _ = self._wire(method='POST', path='/friends/request', body=body,
                                   exact_status=200, accepted_statuses=(200, 201))
            self._acknowledge(row, status)
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST', 'path': '/friends/request',
                'http_status': status, 'from_bot': self.ceo_bot_id,
                'to_bot': self._ids['provider_bot_uuid'], 'authentication': 'local-human',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local CEO-to-Provider trust request failed') from None

    def provider_bot_friendship_list(self) -> AdapterCall:
        row = self._begin('provider_bot_friendship_list')
        try:
            path = '/bots/' + quote(self.ceo_bot_id, safe='') + '/friends'
            status, value = self._wire(method='GET', path=path, body=None, exact_status=200)
            self._acknowledge(row, status)
            data = value.get('data')
            _require(value.get('success') is True and isinstance(data, list) and len(data) <= 100
                     and all(isinstance(item, dict) for item in data),
                     'Owned-local CEO friend-list envelope is invalid')
            ids = [item.get('bot_uuid') for item in data]
            _require(all(isinstance(item, str) for item in ids)
                     and len(ids) == len(set(ids))
                     and self._ids['provider_bot_uuid'] in ids,
                     'Owned-local CEO friend list does not contain the exact Provider Bot')
            evidence = {
                'transport': 'owned-local-http', 'method': 'GET',
                'path': '/bots/{ceo_bot_id}/friends', 'http_status': status,
                'from_bot': self.ceo_bot_id,
                'friend_count': len(ids), 'contains_provider_bot': True,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned-local CEO friend-list verification failed') from None

    def _definition_yaml(self) -> str:
        return (
            'name: HTTP Provider Slow Judge E2E\n'
            'participants:\n'
            '  worker:\n'
            '    required: true\n'
            'runtime:\n'
            '  kind: state_machine\n'
            '  state_machine:\n'
            '    version: 1\n'
            '    graph_mode: acyclic\n'
            '    nodes:\n'
            '      review:\n'
            '        kind: bot_task\n'
            '        display_name: Review\n'
            '        assignee:\n'
            '          type: bot_binding\n'
            '          binding: worker\n'
            '        instruction: Produce a candidate response.\n'
            '        judge:\n'
            '          type: llm\n'
            '          criteria:\n'
            '            - The candidate is suitable for publishing.\n'
            '          outcomes: [approved]\n'
            '        transitions:\n'
            '          approved:\n'
            '            targets: [publish]\n'
            '      publish:\n'
            '        kind: bot_task\n'
            '        display_name: Publish\n'
            '        assignee:\n'
            '          type: bot_binding\n'
            '          binding: worker\n'
            '        instruction: Publish the approved response.\n'
            '        final_output: true\n'
        )

    def create_judged_group(self) -> AdapterCall:
        row = self._begin('create_judged_group')
        try:
            body = {
                'driver_bot': self.ceo_bot_id,
                'label': 'HTTP Provider slow Judge E2E',
                'group_strategy': 'state_machine',
                'participant_bindings': {
                    'worker': {'source': 'manual', 'bot_ids': [self._ids['provider_bot_uuid']]}
                },
                'participants': [
                    {'bot_uuid': self.ceo_bot_id},
                    {'bot_uuid': self._ids['provider_bot_uuid']},
                ],
                'collaboration_definition_yaml': self._definition_yaml(),
            }
            status, value = self._wire(method='POST', path='/groups', body=body, exact_status=200)
            self._acknowledge(row, status)
            group_id = identifier(value.get('id'), 'owned judged Group ID')
            session_id = identifier(value.get('initial_session_id'), 'owned initial Session ID')
            participants = value.get('participants')
            _require(value.get('created') is True
                     and value.get('group_kind') == 'normal'
                     and value.get('driver_bot') == self.ceo_bot_id
                     and isinstance(participants, list)
                     and len(participants) == 2
                     and all(isinstance(item, str) for item in participants)
                     and set(participants) == {self.ceo_bot_id, self._ids['provider_bot_uuid']},
                     'Owned judged Group response does not match the reviewed contract')
            self._ids['group_id'] = group_id
            self._ids['initial_session_id'] = session_id
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST', 'path': '/groups',
                'http_status': status, 'group': {
                    'group_id': group_id, 'driver_bot': self.ceo_bot_id,
                    'initial_session_id': session_id,
                    'participants': [self.ceo_bot_id, self._ids['provider_bot_uuid']],
                    'group_strategy': 'state_machine',
                },
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned judged Group creation was not confirmed') from None

    def bind_explicit_run_receipt_boundary(self) -> AdapterCall:
        row = self._begin('bind_explicit_run_receipt_boundary')
        try:
            deadline = time.monotonic() + 5.0
            while True:
                snapshot = self.sandbox.snapshot()
                count = len(snapshot['provider_receipts'])
                if count > 0:
                    break
                if time.monotonic() >= deadline:
                    raise RunStateError('owned implicit Provider receipt did not arrive')
                time.sleep(0.05)
            self._ids['provider_receipt_count'] = count
            evidence = {
                'boundary': 'immutable receipt cursor',
                'implicit_receipt_count': count,
                'implicit_receipt_id': snapshot['provider_receipts'][0]['delivery_request_id'],
                'explicit_receipt_assignment': 'next-after-cursor',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned explicit receipt boundary was not admitted') from None

    def start_judged_run(self) -> AdapterCall:
        row = self._begin('start_judged_run')
        try:
            group_id = self._ids['group_id']
            path = '/groups/' + quote(group_id, safe='') + '/state-machine-runs'
            status, value = self._wire(
                method='POST', path=path, body={'input': {'question': 'verify asynchronous callback judging'}},
                exact_status=202)
            self._acknowledge(row, status)
            run = value.get('run')
            _require(isinstance(run, dict), 'owned explicit Run response is invalid')
            run_id = identifier(run.get('run_id'), 'owned explicit Run ID')
            session_id = identifier(run.get('session_id'), 'owned explicit Run Session ID')
            _require(run.get('group_id') == group_id
                     and session_id != self._ids['initial_session_id']
                     and isinstance(run.get('status'), str) and run['status'],
                     'Owned explicit Run response differs from its Group binding')
            self._ids['run_id'] = run_id
            self._ids['run_session_id'] = session_id
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST',
                'path': '/groups/{group_id}/state-machine-runs', 'http_status': status,
                'run': {'run_id': run_id, 'group_id': group_id, 'session_id': session_id,
                        'status': run['status']},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned explicit judged Run start was not confirmed') from None

    def deliver_review_request(self) -> AdapterCall:
        row = self._begin('deliver_review_request')
        try:
            run_id = self._ids['run_id']
            expected_request = 'smnode-' + run_id + '-review-0'
            receipt = self.sandbox.wait_provider_request(expected_request, time.monotonic() + 5.0)
            _require(receipt.method == 'chat.send'
                     and receipt.provider_id == self._ids['provider_id']
                     and receipt.provider_bot_ref == self._ids['provider_bot_ref']
                     and receipt.bot_id == self._ids['provider_bot_uuid']
                     and receipt.delivery_request_id == expected_request,
                     'Owned review Provider delivery differs from the exact explicit Run')
            self._ids['review_provider_run_id'] = receipt.delivery_request_id
            evidence = {
                'transport': 'owned-provider-http', 'method': receipt.method,
                'model': 'chat.send', 'status': 'RECEIVED',
                'delivery_request_id': receipt.delivery_request_id,
                'provider_id': receipt.provider_id,
                'provider_bot_ref': receipt.provider_bot_ref,
                'bot_id': receipt.bot_id,
                'session_id': receipt.session_id,
                'bcn_group_id': receipt.bcn_group_id,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned review Provider delivery was not confirmed') from None

    def _graph(self) -> dict:
        path = '/state-machine-runs/' + quote(self._ids['run_id'], safe='') + '/graph'
        status, value = self._wire(method='GET', path=path, body=None, exact_status=200)
        self._last_wire_status = status
        return value

    def await_review_response(self) -> AdapterCall:
        row = self._begin('await_review_response')
        try:
            value = self._graph()
            self._acknowledge(row, self._last_wire_status)
            nodes = value.get('nodes')
            run = value.get('run')
            review = next((item for item in nodes if isinstance(item, dict)
                           and item.get('node_id') == 'review'), None)
            publish = next((item for item in nodes if isinstance(item, dict)
                            and item.get('node_id') == 'publish'), None)
            _require(isinstance(nodes, list) and isinstance(run, dict)
                     and isinstance(review, dict) and isinstance(publish, dict)
                     and run.get('run_id') == self._ids['run_id']
                     and run.get('group_id') == self._ids['group_id']
                     and run.get('session_id') == self._ids['run_session_id']
                     and run.get('status') == 'running'
                     and review.get('status') == 'running'
                     and review.get('sub_status') == 'awaiting_response'
                     and review.get('attempt') == 0
                     and publish.get('status') == 'pending',
                     'Owned review graph does not expose awaiting_response before the callback')
            evidence = {
                'transport': 'owned-local-http', 'method': 'GET',
                'path': '/state-machine-runs/{run_id}/graph',
                'http_status': self._last_wire_status,
                'run': {'run_id': self._ids['run_id'], 'group_id': self._ids['group_id'],
                        'session_id': self._ids['run_session_id'], 'status': 'running'},
                'review': {'status': 'running', 'sub_status': 'awaiting_response', 'attempt': 0},
                'publish': {'status': 'pending'},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned review graph was not confirmed before callback') from None

    def bind_publish_receipt_boundary(self) -> AdapterCall:
        row = self._begin('bind_publish_receipt_boundary')
        try:
            snapshot = self.sandbox.snapshot()
            count = len(snapshot['provider_receipts'])
            _require(count >= 2, 'owned publish receipt boundary has no retained review receipt')
            self._ids['provider_receipt_count'] = count
            review_id = self._ids['review_provider_run_id']
            _require(any(item['delivery_request_id'] == review_id for item in snapshot['provider_receipts']),
                     'owned publish receipt boundary cannot find the review delivery')
            evidence = {
                'boundary': 'immutable receipt cursor',
                'provider_receipt_count': count,
                'review_delivery_id': review_id,
                'publish_receipt_assignment': 'next-after-cursor',
                'implicit_receipt_retained': True,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned publish receipt boundary was not admitted') from None

    def submit_review_callback_before_deadline(self) -> AdapterCall:
        row = self._begin('submit_review_callback_before_deadline')
        try:
            body = {
                'run_id': self._ids['review_provider_run_id'], 'state': 'final',
                'message': {'text': 'candidate awaiting slow judge'},
            }
            started = time.monotonic()
            status, value = self._wire(
                method='POST', path='/bot/events', body=body,
                authorization='Bearer ' + self._credentials['bot_runtime'],
                provider_id=self._ids['provider_id'], exact_status=200)
            elapsed = time.monotonic() - started
            _require(elapsed <= 1.0, 'owned review callback exceeded its one-second deadline')
            self._acknowledge(row, status)
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST', 'path': '/bot/events',
                'http_status': status, 'elapsed_ms': round(elapsed * 1000, 2),
                'deadline_seconds': 1.0, 'provider_id': self._ids['provider_id'],
                'run_id': self._ids['review_provider_run_id'], 'state': 'final',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned review callback was not accepted within its deadline') from None

    def observe_judge_started(self) -> AdapterCall:
        row = self._begin('observe_judge_started')
        try:
            receipt = self.sandbox.wait_judge_started(time.monotonic() + 5.0)
            self._ids['judge_started'] = True
            self._ids['judge_request_sha256'] = receipt.body_sha256
            self._acknowledge(row, 200)
            evidence = {
                'observation': 'owned judge started and remains blocked',
                'judge_started': True, 'judge_released': False,
                'judge_finished': False, 'model': receipt.model,
                'message_count': receipt.message_count,
                'response_format_type': receipt.response_format_type,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned Judge start was not observed') from None

    def await_judging_state(self) -> AdapterCall:
        row = self._begin('await_judging_state')
        try:
            deadline = time.monotonic() + 5.0
            while True:
                value = self._graph()
                nodes = value.get('nodes')
                run = value.get('run')
                review = next((item for item in nodes if isinstance(item, dict)
                               and item.get('node_id') == 'review'), None) if isinstance(nodes, list) else None
                publish = next((item for item in nodes if isinstance(item, dict)
                                and item.get('node_id') == 'publish'), None) if isinstance(nodes, list) else None
                if (isinstance(run, dict) and isinstance(review, dict) and isinstance(publish, dict)
                        and run.get('run_id') == self._ids['run_id']
                        and run.get('group_id') == self._ids['group_id']
                        and run.get('status') == 'running'
                        and review.get('status') == 'running'
                        and review.get('sub_status') == 'judging'
                        and publish.get('status') == 'pending'):
                    break
                if time.monotonic() >= deadline:
                    raise RunStateError('owned review node did not enter judging')
                time.sleep(0.02)
            self._acknowledge(row, self._last_wire_status)
            evidence = {
                'transport': 'owned-local-http', 'method': 'GET',
                'path': '/state-machine-runs/{run_id}/graph',
                'http_status': self._last_wire_status,
                'run': {'run_id': self._ids['run_id'], 'group_id': self._ids['group_id'],
                        'session_id': self._ids['run_session_id'], 'status': 'running'},
                'review': {'status': 'running', 'sub_status': 'judging', 'attempt': 0},
                'publish': {'status': 'pending'},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned judging graph was not confirmed while Judge was blocked') from None

    def read_judging_review_node(self) -> AdapterCall:
        row = self._begin('read_judging_review_node')
        try:
            run_id = self._ids['run_id']
            path = '/state-machine-runs/' + quote(run_id, safe='') + '/nodes/review'
            status, value = self._wire(method='GET', path=path, body=None, exact_status=200)
            self._acknowledge(row, status)
            node = value.get('node')
            _require(value.get('sub_status') == 'judging'
                     and isinstance(node, dict)
                     and node.get('node_id') == 'review'
                     and node.get('run_id') == run_id
                     and node.get('artifact_text') == 'candidate awaiting slow judge',
                     'Owned review node detail does not expose the judging artifact')
            evidence = {
                'transport': 'owned-local-http', 'method': 'GET',
                'path': '/state-machine-runs/{run_id}/nodes/review', 'http_status': status,
                'sub_status': 'judging',
                'node': {'node_id': 'review', 'run_id': run_id,
                         'artifact_text': 'candidate awaiting slow judge'},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned review node detail was not confirmed while Judge was blocked') from None

    def release_owned_judge(self) -> AdapterCall:
        row = self._begin('release_owned_judge')
        try:
            before = self.sandbox.snapshot()
            before_ids = [item['delivery_request_id'] for item in before['provider_receipts']]
            _require(before['state'] == 'LIVE' and before['failed'] is False
                     and before['judge_started'] is True
                     and before['judge_released'] is False
                     and before['judge_finished'] is False,
                     'Owned Judge release precondition was not confirmed')
            self.sandbox.release_judge()
            deadline = time.monotonic() + 5.0
            while True:
                after = self.sandbox.snapshot()
                if after['judge_released'] is True and after['judge_finished'] is True:
                    break
                if time.monotonic() >= deadline:
                    raise RunStateError('owned Judge did not finish after release')
                time.sleep(0.02)
            after_ids = [item['delivery_request_id'] for item in after['provider_receipts']]
            _require(after['state'] == 'LIVE' and after['failed'] is False
                     and after_ids[:len(before_ids)] == before_ids,
                     'Owned Judge release affected foreign receipts or failed its lifecycle')
            self._ids['judge_released'] = True
            evidence = {
                'fixture': 'owned-local-judge-sandbox', 'judge_started': True,
                'judge_released': True, 'judge_finished': True,
                'provider_receipt_count': len(after_ids),
                'existing_provider_receipts_unchanged': True,
                'provider_receipt_count_after_release': len(after_ids),
                'deterministic_outcome': 'approved',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned Judge release did not produce its deterministic outcome') from None

    def deliver_publish_request(self) -> AdapterCall:
        row = self._begin('deliver_publish_request')
        try:
            run_id = self._ids['run_id']
            expected_request = 'smnode-' + run_id + '-publish-0'
            receipt = self.sandbox.wait_provider_request(expected_request, time.monotonic() + 5.0)
            _require(receipt.method == 'chat.send'
                     and receipt.provider_id == self._ids['provider_id']
                     and receipt.provider_bot_ref == self._ids['provider_bot_ref']
                     and receipt.bot_id == self._ids['provider_bot_uuid']
                     and receipt.delivery_request_id == expected_request
                     and receipt.delivery_request_id != self._ids['review_provider_run_id'],
                     'Owned publish Provider delivery is not the exact post-Judge explicit Run')
            self._ids['publish_provider_run_id'] = receipt.delivery_request_id
            evidence = {
                'transport': 'owned-provider-http', 'method': receipt.method,
                'model': 'chat.send', 'status': 'RECEIVED',
                'delivery_request_id': receipt.delivery_request_id,
                'provider_id': receipt.provider_id,
                'provider_bot_ref': receipt.provider_bot_ref,
                'bot_id': receipt.bot_id,
                'session_id': receipt.session_id,
                'bcn_group_id': receipt.bcn_group_id,
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned publish Provider delivery was not confirmed') from None

    def submit_publish_callback(self) -> AdapterCall:
        row = self._begin('submit_publish_callback')
        try:
            body = {
                'run_id': self._ids['publish_provider_run_id'], 'state': 'final',
                'message': {'text': 'published after slow judge'},
            }
            status, _ = self._wire(
                method='POST', path='/bot/events', body=body,
                authorization='Bearer ' + self._credentials['bot_runtime'],
                provider_id=self._ids['provider_id'], exact_status=200)
            self._acknowledge(row, status)
            evidence = {
                'transport': 'owned-local-http', 'method': 'POST', 'path': '/bot/events',
                'http_status': status, 'provider_id': self._ids['provider_id'],
                'run_id': self._ids['publish_provider_run_id'], 'state': 'final',
                'message_text': 'published after slow judge',
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned publish callback was not accepted') from None

    def await_judged_run_completion(self) -> AdapterCall:
        row = self._begin('await_judged_run_completion')
        try:
            run_id = self._ids['run_id']
            path = '/state-machine-runs/' + quote(run_id, safe='')
            deadline = time.monotonic() + 5.0
            while True:
                status, value = self._wire(method='GET', path=path, body=None, exact_status=200)
                run = value.get('run')
                judge_outputs = value.get('judge_outputs')
                decision = (judge_outputs[0].get('decision')
                            if isinstance(judge_outputs, list) and judge_outputs
                            and isinstance(judge_outputs[0], dict) else None)
                outcome = (decision.get('outcome')
                           if isinstance(decision, dict) else None)
                if (isinstance(run, dict) and run.get('run_id') == run_id
                        and run.get('group_id') == self._ids['group_id']
                        and run.get('status') == 'completed'
                        and run.get('output') == 'published after slow judge'
                        and outcome == 'approved'):
                    break
                if time.monotonic() >= deadline:
                    raise RunStateError('owned judged Run did not complete')
                time.sleep(0.02)
            self._acknowledge(row, status)
            evidence = {
                'transport': 'owned-local-http', 'method': 'GET',
                'path': '/state-machine-runs/{run_id}', 'http_status': status,
                'run': {'run_id': run_id, 'group_id': self._ids['group_id'],
                        'session_id': self._ids['run_session_id'], 'status': 'completed',
                        'output': 'published after slow judge'},
                'judge_output': {'outcome': 'approved', 'index': 0},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned judged Run completion was not confirmed') from None

    def delete_judged_group(self) -> AdapterCall:
        row = self._begin('delete_judged_group')
        try:
            group_id = self._ids['group_id']
            path = ('/groups/' + quote(group_id, safe='')
                    + '?bot_id=' + quote(self.ceo_bot_id, safe=''))
            status, value = self._wire(method='DELETE', path=path, body=None, exact_status=200)
            self._acknowledge(row, status)
            _require(value.get('deleted') is True and value.get('id') == group_id,
                     'Owned judged Group deletion was not confirmed')
            evidence = {
                'transport': 'owned-local-http', 'method': 'DELETE',
                'path': '/groups/{group_id}?bot_id={ceo_bot_id}', 'http_status': status,
                'group': {'group_id': group_id, 'caller_actor_id': self.ceo_bot_id,
                          'deleted': True},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned judged Group deletion was not confirmed') from None

    def retire_provider_bot(self) -> AdapterCall:
        row = self._begin('retire_provider_bot')
        try:
            path = ('/providers/' + quote(self._ids['provider_id'], safe='')
                    + '/bots/' + quote(self._ids['provider_bot_ref'], safe=''))
            status, value = self._wire(
                method='DELETE', path=path, body=None,
                authorization='Bearer ' + self._credentials['provider_admin'], exact_status=200)
            self._acknowledge(row, status)
            bot_uuid = value.get('bot_uuid')
            _require(value.get('deleted') is True
                     and value.get('provider_id') == self._ids['provider_id']
                     and value.get('provider_bot_ref') == self._ids['provider_bot_ref']
                     and bot_uuid == self._ids['provider_bot_uuid'],
                     'Owned Provider Bot retirement was not confirmed')
            evidence = {
                'transport': 'owned-local-http', 'method': 'DELETE',
                'path': '/providers/{provider_id}/bots/{provider_bot_ref}', 'http_status': status,
                'provider_bot': {'provider_id': self._ids['provider_id'],
                                 'provider_bot_ref': self._ids['provider_bot_ref'],
                                 'bot_uuid': self._ids['provider_bot_uuid'], 'deleted': True},
            }
            return self._pass(row, evidence)
        except BaseException as error:
            self._blocked(row, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Owned Provider Bot retirement was not confirmed') from None
