"""Owned-local in-process executor for the session owner permissions Story.

The upstream story is a pure HTTP authorization matrix, so this slice runs it
in process against one foreground-owned loopback BCS runtime instead of a
black-box child: the station legacy face still admits per-request
``X-Mock-User-Id`` headers only (ambient local identity is disabled, so every
station request carries an explicit mock identity), and the public openapi/v1
face authenticates per-request minted gateway Principal JWTs signed from the
runtime's per-run principal signing key. The executor bootstraps three Human
principals (manager/owner/outsider), registers and onboards the manager driver
Bot and the owner Bot, then ports the upstream run() sequence obligation by
obligation: the participants mode matrix, the Human message view scope matrix,
Group read isolation for a Session-only Bot, foreign mutation denial with the
exact error codes, actor-scoped collection and history, and membership
revocation isolation. Every validated HTTP response is one frozen assertion
(60 run-phase assertions matching the upstream story marker, plus 4 cleanup
dispositions); structural read-backs are recorded as named checks. The precise
ownership journal, the resource ledger and the cleanup dispositions land in the
run state directory; only ownership ids are durable, never a signing key,
principal token, register token or Bot token. Cleanup deletes only resources
this run created, and any failure short-circuits with the first failure text
while still disposing owned resources. This is an independent migration
slice: its parent stays in the known unmapped backlog.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid

from ..errors import has_cancellation
from ..inventory import (KNOWN_UNMAPPED_LEGACY_IDS, current_manifest, executor_digest,
                          reviewed_release_inventory)
from ..owned_bcs import OwnedBcsProcess, file_sha256
from ..profile import ConfigError, fingerprint, identifier, unique_keys
from ..run_state import RunStateError, read, write
from .session_owner_contract import (CASE_ID, CLEANUP_CHECKS, DOCUMENTED_GAPS, ENVIRONMENT,
                                     MARKER_KEYS, PARENT, REQUIREMENTS, STATUS, STORY_CHECKS,
                                     SUITE, session_owner_source_binding)

BINARY_KIND = 'bcs'
BINDING_NAME = 'session-owner-binding.json'
JOURNAL_NAME = 'session-owner-journal.json'
LEDGER_NAME = 'session-owner-resources.json'
CASE_RECEIPT_NAME = 'session-owner.json'
FLOW_REPORT_NAME = 'session-owner-flow-report.json'
PUBLIC_PREFIX = '/openapi/v1/collaboration'
PRINCIPAL_TENANT = 'bcs-e2e'
PRINCIPAL_ISSUER = 'gateway'
PRINCIPAL_ENV = 'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'
CLEANUP_OBLIGATION = 'SESSION-OWNER.cleanup-disposition'
EXECUTOR_ADMISSION = {'allow_ambient_local_identity': False, 'allow_mock_headers': True,
                      'ambient_mock_user_id': None}
MUTATION_METHODS = ('POST', 'PATCH', 'PUT', 'DELETE')
MAX_ERROR_DETAIL = 800


def _segment(value: str) -> str:
    return quote(value, safe='')


def _json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _bounded_detail(error: BaseException) -> str:
    return str(error)[:MAX_ERROR_DETAIL]


def _gateway_principal_token(*, user_id: str, username: str, tenant: str, signing_key: str,
                             issuer: str) -> str:
    """Verbatim port of the upstream gateway Principal minting helper."""
    now = int(time.time())
    header = {"alg": "HS256", "kid": "bare", "typ": "JWT"}
    claims = {
        "iss": issuer,
        "aud": "bcs",
        "iat": now,
        "exp": now + 60,
        "principals": [
            {
                "type": "user",
                "tenant": tenant,
                "subject": {
                    "id": user_id,
                    "username": username,
                    "display_name": None,
                    "full_name": None,
                    "tenant_id": tenant,
                },
            }
        ],
    }

    def _base64url(payload: bytes) -> str:
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    encoded_header = _base64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    encoded_claims = _base64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(signing_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode('ascii')}.{_base64url(signature)}"


def validate_session_owner_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the owned BCS server binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError('Session owner BCS binary must be a regular frozen file')
    if not os.access(binary, os.X_OK):
        raise RunStateError('Session owner BCS binary must be executable')
    if file_sha256(binary) != binary_sha256:
        raise RunStateError('Session owner BCS binary digest differs from its frozen declaration')
    return binary


class SessionOwnerRunError(RunStateError):
    """First failure short-circuits the story; the journal retains the reason."""


def _admit_runtime(runtime) -> None:
    """Strict owned-local admission; no ambient identity, mock headers per request."""
    if runtime is None:
        raise SessionOwnerRunError('Session owner runtime is missing')
    try:
        ambient = runtime.allow_ambient_local_identity
    except AttributeError:
        raise SessionOwnerRunError('Session owner runtime is incomplete: allow_ambient_local_identity') from None
    if ambient is not False:
        raise SessionOwnerRunError('Session owner requires strict mock-header admission without ambient identity')
    for attribute in ('root', 'run_id', 'binary', 'binary_sha256', 'inputs'):
        if not hasattr(runtime, attribute):
            raise SessionOwnerRunError('Session owner runtime is incomplete: ' + attribute)
    try:
        identifier(runtime.run_id, 'Session owner run ID')
    except ConfigError:
        raise SessionOwnerRunError('Session owner run ID is invalid') from None
    digest = runtime.binary_sha256
    if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise SessionOwnerRunError('Session owner requires an explicit frozen BCS binary digest')
    inputs = runtime.inputs
    origin = getattr(inputs, 'origin', None)
    if type(origin) is not str:
        raise SessionOwnerRunError('Session owner requires an owned loopback origin')
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        port = None
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or port is None
            or not 1024 <= port <= 65535 or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise SessionOwnerRunError('Session owner requires the exact owned loopback origin')
    environment = getattr(inputs, 'environment', None)
    if not isinstance(environment, dict):
        raise SessionOwnerRunError('Session owner requires an owned runtime environment')
    signing_key = environment.get(PRINCIPAL_ENV)
    if (not isinstance(signing_key, str) or not signing_key
            or any(ord(char) <= 32 or ord(char) == 127 for char in signing_key)):
        raise SessionOwnerRunError('Session owner requires the owned principal signing key')
    try:
        config = json.loads((Path(runtime.root) / 'bcs-config.json').read_text(encoding='utf-8'),
                            object_pairs_hook=unique_keys)
    except (OSError, ValueError, TypeError, KeyError, ConfigError):
        raise SessionOwnerRunError('Session owner runtime configuration is unreadable') from None
    if config.get('auth') != {'chain': ['local', 'session'], 'require_authentication': False,
                              'allow_mock_headers': True}:
        raise SessionOwnerRunError('Session owner requires strict per-request mock-header admission')
    principal = config.get('gateway_principal')
    if (not isinstance(principal, dict)
            or principal.get('signing_key_env') != PRINCIPAL_ENV):
        raise SessionOwnerRunError('Session owner requires the owned gateway principal signing key')

CHECK_IDS = (
    'group-participants-hide-session-bot',
    'bot-mode-write-echo-muted', 'bot-mode-readback-muted',
    'bot-mode-write-echo-auto', 'bot-mode-readback-auto',
    'denied-patch-no-partial-write',
    'human-join-echo',
    'human-scope-echo-participant', 'human-scope-readback-participant',
    'human-scope-echo-full', 'human-scope-readback-full',
    'manager-scope-write', 'manager-mode-write-readback',
)
REQUIRED_ASSERTION_COUNT = STORY_CHECKS + CLEANUP_CHECKS + len(CHECK_IDS) + 1
REQUIRED_ASSERTION_INVENTORY = {'run_http': STORY_CHECKS, 'cleanup_http': CLEANUP_CHECKS,
                                'structural_python': len(CHECK_IDS), 'shell': 1,
                                'total': REQUIRED_ASSERTION_COUNT}
RESOURCE_DISPOSITION_PASS = 'SESSION_GROUP_AND_OWNED_BOTS_DELETED'
CREDENTIAL_PERSISTENCE_KEYS = ('gateway_principal', 'register_token', 'bot_token',
                               'principal_signing_key', 'mock_admission_headers')
BINDING_KEYS = {'schema_version', 'contract', 'suite', 'case_id', 'run_id', 'executor_digest',
                'binary_kind', 'bcs_binary_sha256', 'auto_build', 'source_binding',
                'selected_requirement_ids', 'legacy_inventory', 'runtime_dir', 'environment',
                'executor_admission'}


class OwnedLocalSessionOwner:
    """In-process owned-local executor for the frozen session owner Story.

    One foreground-owned loopback BCS runtime serves both faces: the station
    legacy face with strict per-request ``X-Mock-User-Id`` admission (ambient
    local identity stays disabled, so nobody is ever borrowed from the
    environment) and the public openapi/v1 face with per-request minted
    gateway Principal JWTs signed from the runtime's own per-run signing key.
    The nine upstream obligations run in their frozen order; each validated
    HTTP response is one frozen assertion (60 run-phase, 4 cleanup), the
    thirteen structural read-backs are named checks, and every mutation intent
    is durable in the journal before dispatch.
    """

    def __init__(self, runtime):
        _admit_runtime(runtime)
        self.runtime = runtime
        self.root = Path(runtime.root)
        self.run_id = runtime.run_id
        self.origin = runtime.inputs.origin
        self._signing_key = runtime.inputs.environment[PRINCIPAL_ENV]
        self.executor_digest = executor_digest()
        self.source_binding = session_owner_source_binding()
        self.binding = self._validated_binding()
        self.binding_digest = fingerprint(self.binding)
        suffix = uuid.uuid4().hex
        self._manager = 'sessionmanager' + suffix
        self._owner = 'sessionowner' + suffix
        self._outsider = 'sessionoutsider' + suffix
        self._driver = ''
        self._owned = ''
        self._group = ''
        self._session = ''
        self._register_tokens: dict[str, str] = {}
        self._bot_tokens: dict[str, str] = {}
        self._opener = build_opener(ProxyHandler({}))
        self._journal_digest = ''
        self._ledger_digest = ''
        self._op_counters = {name: 0 for name in REQUIREMENTS}
        self._story_http = 0
        self._cleanup_http = 0
        self._ledger_rows: list[dict] = []
        self._invalidated = False
        self.state: dict | None = None

    # --- frozen evidence paths and durable fences -------------------------

    def _validated_binding(self) -> dict:
        path = self.root / BINDING_NAME
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError
            binding = read(path)
            if (set(binding) != BINDING_KEYS
                    or binding['schema_version'] != 1 or binding['contract'] != SUITE
                    or binding['suite'] != SUITE or binding['case_id'] != CASE_ID
                    or binding['run_id'] != self.runtime.run_id
                    or binding['executor_digest'] != executor_digest()
                    or binding['binary_kind'] != BINARY_KIND
                    or binding['bcs_binary_sha256'] != self.runtime.binary_sha256
                    or binding['auto_build'] is not False
                    or binding['source_binding'] != self.source_binding
                    or binding['selected_requirement_ids'] != list(REQUIREMENTS)
                    or binding['legacy_inventory'] != current_manifest()['legacy_inventory']
                    or binding['runtime_dir'] != str(self.root.resolve())
                    or binding['environment'] != ENVIRONMENT
                    or binding['executor_admission'] != EXECUTOR_ADMISSION):
                raise ValueError
            return binding
        except (KeyError, OSError, TypeError, ValueError, RunStateError):
            raise SessionOwnerRunError('Session owner frozen execution binding is missing or changed') from None

    def _exclusive(self, path: Path, value: dict) -> None:
        # O_EXCL creation with a schema-valid first write: an empty evidence
        # file would make every later _active() fence read fail as unreadable.
        payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise SessionOwnerRunError('Session owner requires fresh journal and resource ledger paths') from None
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def _check_file(self, path: Path, digest: str, label: str) -> None:
        if not digest:
            return
        try:
            matches = fingerprint(read(path)) == digest
        except Exception:
            matches = False
        if not matches:
            self._invalidated = True
            raise SessionOwnerRunError(f'{label} changed; preserve the original artifact')

    def _save(self) -> None:
        self._check_file(self.root / JOURNAL_NAME, self._journal_digest, 'Session owner journal')
        self._check_file(self.root / LEDGER_NAME, self._ledger_digest, 'Session owner resource ledger')
        if self.state is not None:
            self.state['resources'] = [dict(row) for row in self._ledger_rows]
        serialized = json.dumps(self.state, sort_keys=True, separators=(',', ':'), allow_nan=False)
        sensitive = [self._signing_key, *self._register_tokens.values(), *self._bot_tokens.values()]
        if any(secret and secret in serialized for secret in sensitive):
            self._invalidated = True
            raise SessionOwnerRunError('Session owner credential leaked into durable evidence')
        write(self.root / JOURNAL_NAME, self.state)
        ledger = {'schema_version': 1, 'run_id': self.run_id, 'contract': SUITE,
                  'case_id': CASE_ID, 'humans_bootstrap_retained': True,
                  'resources': [dict(row) for row in self._ledger_rows]}
        write(self.root / LEDGER_NAME, ledger)
        self._journal_digest = fingerprint(read(self.root / JOURNAL_NAME))
        self._ledger_digest = fingerprint(read(self.root / LEDGER_NAME))

    def _active(self) -> None:
        if self._invalidated:
            raise SessionOwnerRunError('Session owner mutation authority is invalidated')
        if (executor_digest() != self.executor_digest
                or fingerprint(session_owner_source_binding()) != fingerprint(self.source_binding)
                or fingerprint(read(self.root / BINDING_NAME)) != self.binding_digest
                or fingerprint(read(self.root / LEDGER_NAME)) != self._ledger_digest):
            raise SessionOwnerRunError('Session owner source, binding, or resource ledger changed')
        self._check_file(self.root / JOURNAL_NAME, self._journal_digest, 'Session owner journal')

    def _ledger_add(self, kind: str, owner: str, resource_id: str) -> None:
        if any(row['kind'] == kind and row['id'] == resource_id for row in self._ledger_rows):
            raise SessionOwnerRunError('Session owner resource ledger duplicate: ' + kind)
        self._ledger_rows.append({'kind': kind, 'owner': owner, 'id': resource_id, 'state': 'created'})

    def _ledger_delete(self, kind: str, resource_id: str) -> None:
        for row in self._ledger_rows:
            if row['kind'] == kind and row['id'] == resource_id:
                if row['state'] != 'created':
                    raise SessionOwnerRunError('Session owner resource is already disposed: ' + resource_id)
                row['state'] = 'deleted'
                return
        raise SessionOwnerRunError('Session owner resource ledger lost an owned resource: ' + resource_id)

    # --- strict per-request dispatch ---------------------------------------

    def _request(self, obligation: str, user: str, method: str, path: str, body=None, *,
                 status: int = 200, error: str = '', public: bool = True,
                 headers=None):
        self._active()
        route = (PUBLIC_PREFIX if public else '') + path.split('?', 1)[0]
        if public:
            principal = _gateway_principal_token(user_id=user, username=user, tenant=PRINCIPAL_TENANT,
                                                 signing_key=self._signing_key, issuer=PRINCIPAL_ISSUER)
            auth = {'X-Avernet-Principal': principal}
        else:
            auth = {'X-Mock-User-Id': user, 'X-Mock-Nick-Name': user}
        request_headers = {'Content-Type': 'application/json', **auth, **(headers or {})}
        payload = None if body is None else json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        self._op_counters[obligation] += 1
        event = {'operation': obligation, 'index': self._op_counters[obligation],
                 'method': method, 'route': route, 'public': public,
                 'status': 'INTENDED', 'kind': 'MUTATION' if method in MUTATION_METHODS else 'CHECK',
                 'transitions': [{'status': 'INTENDED'}]}
        self.state['operations'].append(event)
        self._save()
        try:
            code, raw = _dispatch_http(self._opener, method,
                                       self.origin + (PUBLIC_PREFIX if public else '') + path,
                                       request_headers, payload)
            result = json.loads(raw.decode('utf-8')) if raw else {}
            if not isinstance(result, dict):
                raise SessionOwnerRunError(f'{method} {route}: response is not an object')
            detail = result.get('message', '') if public else ''
            data = result.get('data', {}) if public else result
            if code != status:
                raise SessionOwnerRunError(f'{method} {route}: expected {status}, got {code}: {detail}')
            if error and data.get('error_code') != error:
                raise SessionOwnerRunError(
                    f'{method} {route}: expected error {error}, got {data.get("error_code")}')
        except SessionOwnerRunError as failure:
            event.update(status='FAILED', error_type='SessionOwnerRunError',
                         error_detail=_bounded_detail(failure))
            event['transitions'].append({'status': 'FAILED', 'error_type': 'SessionOwnerRunError'})
            self._save()
            raise
        except BaseException as failure:
            if has_cancellation(failure):
                event.update(status='UNKNOWN', error_type='KeyboardInterrupt')
                event['transitions'].append({'status': 'UNKNOWN', 'error_type': 'KeyboardInterrupt'})
                try:
                    self._save()
                except BaseException:
                    pass
                raise KeyboardInterrupt() from None
            event.update(status='UNKNOWN', error_type=type(failure).__name__)
            event['transitions'].append({'status': 'UNKNOWN', 'error_type': type(failure).__name__})
            try:
                self._save()
            except BaseException:
                pass
            raise SessionOwnerRunError(
                'Session owner request failed without a trusted response; no retry, no adoption') from None
        event.update(status='ACKNOWLEDGED', http_status=code)
        event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': code})
        if obligation == CLEANUP_OBLIGATION:
            self._cleanup_http += 1
        else:
            self._story_http += 1
        self.state['observed_assertion_count'] += 1
        self._save()
        return data

    # --- upstream role helpers ---------------------------------------------

    def _register(self, user: str, bot_name: str, obligation: str) -> str:
        # Station ensure-human, one-time register token, register, onboard.
        self._request(obligation, user, 'POST', '/me/ensure-human', {}, public=False)
        self._ledger_add('human', user, user)
        token = self._request(obligation, user, 'GET', '/register/token', public=False)['token']
        self._register_tokens[user] = token
        registered = self._request(
            obligation, user, 'POST',
            f'/register?token={_segment(token)}&bot-name={_segment(bot_name)}', {}, public=False)
        bot = registered['bot_uuid']
        self._bot_tokens[bot] = registered['bot_token']
        self._ledger_add('bot', user, bot)
        self._request(obligation, user, 'POST', '/bots/onboard', {
            'name': bot_name, 'summary': 'Session authorization E2E Bot',
            'domains': ['testing'], 'skills': ['review'], 'scopes': ['local'],
        }, public=False, headers={'X-BCS-Bot-Token': registered['bot_token']})
        return bot

    def _participant(self, session: str, actor: str, obligation: str) -> dict:
        detail = self._request(obligation, self._manager, 'GET', f'/sessions/{session}')
        return next(item for item in detail['participants'] if item['actor_id'] == actor)

    def _patch(self, user: str, session: str, actor: str, body: dict, obligation: str, **kwargs):
        return self._request(obligation, user, 'PATCH',
                             f'/sessions/{session}/participants/{actor}', body, **kwargs)

    def _require(self, check_id: str, condition: bool, evidence: dict, message: str) -> None:
        if any(row['id'] == check_id for row in self.state['checks']):
            raise SessionOwnerRunError('Session owner check ID is duplicated: ' + check_id)
        row = {'id': check_id, 'status': 'PASS' if condition else 'FAIL',
               'evidence': {key: value for key, value in evidence.items()}}
        self.state['checks'].append(row)
        self._save()
        if not condition:
            raise SessionOwnerRunError(f'Session owner check {check_id} failed: {message}')

    def _pass_requirement(self, obligation: str) -> None:
        for row in self.state['requirements']:
            if row['id'] == obligation:
                row['status'] = 'PASS'
                break
        self._save()

    # --- the nine frozen obligations, in upstream order --------------------

    def _obligation_bootstrap_ownership(self) -> None:
        obligation = REQUIREMENTS[0]
        self._driver = self._register(self._manager, 'Session story driver', obligation)
        self._owned = self._register(self._owner, 'Session story owned Bot', obligation)
        self._request(obligation, self._outsider, 'POST', '/me/ensure-human', {}, public=False)
        self._ledger_add('human', self._outsider, self._outsider)
        # A manager can invite a public Bot, but does not own it.
        self._request(obligation, self._owner, 'PUT', f'/bots/{self._owned}/visibility',
                      {'visibility': 'public'}, public=False)
        self.state['ownership'] = {
            'humans': {'manager': self._manager, 'owner': self._owner, 'outsider': self._outsider},
            'human_actors': {'owner': 'human_' + self._owner, 'outsider': 'human_' + self._outsider},
            'bots': [{'bot_uuid': self._driver, 'owner': 'manager', 'role': 'driver'},
                     {'bot_uuid': self._owned, 'owner': 'owner', 'role': 'owned'}],
            'group': self._group, 'session': self._session,
        }
        self._pass_requirement(obligation)

    def _obligation_group_session_creation(self) -> None:
        obligation = REQUIREMENTS[1]
        group = self._request(obligation, self._manager, 'POST', '/groups', {
            'group_kind': 'normal', 'name': 'Session owner authorization E2E',
            'driver_bot_uuid': self._driver,
            'participants': [{'actor_id': self._driver, 'role': 'driver'}],
            'collaboration': {'strategy': 'chat'},
        }, status=201)['group_id']
        self._group = group
        self._ledger_add('group', self._manager, group)
        self._request(obligation, self._manager, 'PATCH', f'/groups/{group}', {'visibility': 'private'})
        session = self._request(obligation, self._manager, 'POST', f'/groups/{group}/sessions',
                                {'title': 'Session-only participation'}, status=201)['session_id']
        self._session = session
        self._ledger_add('session', self._manager, session)
        self.state['ownership']['group'] = self._group
        self.state['ownership']['session'] = self._session
        self._pass_requirement(obligation)

    def _obligation_group_read_boundary(self) -> None:
        obligation = REQUIREMENTS[2]
        self._request(obligation, self._owner, 'GET', f'/groups/{self._group}',
                      status=403, error='forbidden')
        self._request(obligation, self._manager, 'POST', f'/sessions/{self._session}/participants',
                      {'bot_uuid': self._owned})
        detail = self._request(obligation, self._owner, 'GET', f'/groups/{self._group}')
        self._require('group-participants-hide-session-bot',
                      all(p.get('actor_id') != self._owned for p in detail.get('participants', [])),
                      {'http_status': 200, 'group_id': self._group, 'session_only_bot_hidden': True},
                      'Session-only Bot leaked into the Group participant projection')
        self._request(obligation, self._outsider, 'GET', f'/groups/{self._group}',
                      status=403, error='forbidden')
        self._pass_requirement(obligation)

    def _obligation_bot_mode_ownership(self) -> None:
        obligation = REQUIREMENTS[3]
        for mode in ('muted', 'auto'):
            updated = self._patch(self._owner, self._session, self._owned, {'mode': mode}, obligation)
            self._require(f'bot-mode-write-echo-{mode}', updated.get('mode') == mode,
                          {'http_status': 200, 'actor_id': self._owned, 'mode': mode},
                          'Owned Bot mode patch did not echo the selected mode')
            stored = self._participant(self._session, self._owned, obligation)
            self._require(f'bot-mode-readback-{mode}', stored.get('mode') == mode,
                          {'http_status': 200, 'actor_id': self._owned, 'mode': stored.get('mode')},
                          'Owned Bot mode patch did not persist')
        self._patch(self._owner, self._session, self._driver, {'mode': 'muted'}, obligation,
                    status=403, error='forbidden')
        self._patch(self._owner, self._session, self._owned, {'mode': 'present'}, obligation,
                    status=400, error='invalid_participant_mode')
        self._patch(self._owner, self._session, self._owned,
                    {'mode': 'muted', 'message_view_scope': 'full'}, obligation,
                    status=403, error='message_view_scope_forbidden')
        stored = self._participant(self._session, self._owned, obligation)
        self._require('denied-patch-no-partial-write', stored.get('mode') == 'auto',
                      {'http_status': 200, 'actor_id': self._owned, 'mode': stored.get('mode')},
                      'Denied patch partially wrote mode')
        self._patch(self._owner, self._session, self._owned, {'message_view_scope': 'participant'},
                    obligation, status=400, error='invalid_message_view_scope')
        self._pass_requirement(obligation)

    def _obligation_foreign_mutation_denied(self) -> None:
        obligation = REQUIREMENTS[4]
        self._request(obligation, self._owner, 'PATCH', f'/sessions/{self._session}',
                      {'title': 'denied'}, status=403, error='forbidden')
        self._request(obligation, self._owner, 'DELETE', f'/sessions/{self._session}',
                      status=403, error='forbidden')
        self._request(obligation, self._owner, 'PATCH', f'/groups/{self._group}',
                      {'name': 'denied'}, status=403, error='forbidden')
        self._request(obligation, self._owner, 'DELETE', f'/groups/{self._group}',
                      status=403, error='forbidden')
        self._request(obligation, self._owner, 'POST', f'/sessions/{self._session}/participants',
                      {'bot_uuid': self._driver}, status=403, error='forbidden')
        self._patch(self._manager, self._session, 'missing-bot', {'mode': 'auto'}, obligation,
                    status=404, error='participant_not_found')
        self._patch(self._owner, self._session, 'missing-bot', {'mode': 'auto'}, obligation,
                    status=403, error='forbidden')
        self._pass_requirement(obligation)

    def _obligation_human_self_service_scope(self) -> None:
        obligation = REQUIREMENTS[5]
        human = 'human_' + self._owner
        stranger = 'human_' + self._outsider
        self._patch(self._outsider, self._session, stranger, {'mode': 'present'}, obligation,
                    status=403, error='forbidden')
        updated = self._patch(self._owner, self._session, human, {'mode': 'present'}, obligation)
        self._require('human-join-echo',
                      updated.get('mode') == 'present' and updated.get('message_view_scope') == 'full',
                      {'http_status': 200, 'actor_id': human, 'mode': updated.get('mode'),
                       'message_view_scope': updated.get('message_view_scope')},
                      'Human self-join did not echo present with full view scope')
        self._patch(self._owner, self._session, human, {'mode': 'muted'}, obligation,
                    status=400, error='invalid_participant_mode')
        self._patch(self._manager, self._session, human, {'mode': 'absent'}, obligation,
                    status=403, error='forbidden')
        self._patch(self._outsider, self._session, human, {'message_view_scope': 'full'}, obligation,
                    status=403, error='message_view_scope_forbidden')
        for scope, mode in (('participant', 'absent'), ('full', 'present')):
            updated = self._patch(self._owner, self._session, human,
                                  {'mode': mode, 'message_view_scope': scope}, obligation)
            self._require(f'human-scope-echo-{scope}',
                          updated.get('mode') == mode and updated.get('message_view_scope') == scope,
                          {'http_status': 200, 'actor_id': human, 'mode': mode,
                           'message_view_scope': scope},
                          'Human scope loopback patch did not echo the selected pair')
            stored = self._participant(self._session, human, obligation)
            self._require(f'human-scope-readback-{scope}',
                          stored.get('mode') == mode and stored.get('message_view_scope') == scope,
                          {'http_status': 200, 'actor_id': human,
                           'stored': [stored.get('mode'), stored.get('message_view_scope')]},
                          'Human scope loopback patch did not persist')
        self._patch(self._manager, self._session, human, {'message_view_scope': 'participant'}, obligation)
        stored = self._participant(self._session, human, obligation)
        self._require('manager-scope-write', stored.get('message_view_scope') == 'participant',
                      {'http_status': 200, 'actor_id': human,
                       'message_view_scope': stored.get('message_view_scope')},
                      'Manager scope narrowing was not persisted for the Human relation')
        self._patch(self._owner, self._session, human, {'message_view_scope': 'full'}, obligation)
        self._patch(self._manager, self._session, self._owned,
                    {'mode': 'muted', 'message_view_scope': 'full'}, obligation)
        stored = self._participant(self._session, self._owned, obligation)
        self._require('manager-mode-write-readback', stored.get('mode') == 'muted',
                      {'http_status': 200, 'actor_id': self._owned, 'mode': stored.get('mode')},
                      'Manager mode write for the owned Bot was not persisted')
        self._pass_requirement(obligation)

    def _obligation_collection_history_actor_scope(self) -> None:
        obligation = REQUIREMENTS[6]
        self._request(obligation, self._owner, 'GET',
                      f'/groups/{self._group}/sessions?view_bot_id={self._owned}&limit=20')
        self._request(obligation, self._owner, 'GET',
                      f'/groups/{self._group}/sessions?view_bot_id={self._driver}&limit=20',
                      status=403, error='forbidden')
        self._request(obligation, self._owner, 'GET',
                      f'/sessions/{self._session}/messages?limit=20')
        self._request(obligation, self._owner, 'POST', f'/sessions/{self._session}/collect',
                      {'participant': self._owned})
        self._request(obligation, self._owner, 'DELETE',
                      f'/sessions/{self._session}/collect?participant={self._owned}')
        self._request(obligation, self._outsider, 'POST', f'/sessions/{self._session}/collect',
                      {'participant': self._owned}, status=403, error='forbidden')
        self._pass_requirement(obligation)

    def _obligation_membership_revocation_isolation(self) -> None:
        obligation = REQUIREMENTS[7]
        self._request(obligation, self._manager, 'DELETE',
                      f'/sessions/{self._session}/participants/{self._owned}')
        self._request(obligation, self._owner, 'GET', f'/groups/{self._group}')
        self._patch(self._owner, self._session, self._owned, {'mode': 'auto'}, obligation,
                    status=403, error='forbidden')
        self._request(obligation, self._manager, 'DELETE',
                      f'/sessions/{self._session}/participants/human_' + self._owner)
        self._request(obligation, self._owner, 'GET', f'/groups/{self._group}',
                      status=403, error='forbidden')
        self._request(obligation, self._owner, 'GET', f'/sessions/{self._session}',
                      status=403, error='forbidden')
        self._request(obligation, self._manager, 'GET', f'/groups/{self._group}')
        self._pass_requirement(obligation)

    def _cleanup(self) -> None:
        failures: list[str] = []

        def dispose(user: str, path: str, *, public: bool, kind: str, resource_id: str) -> None:
            try:
                self._request(CLEANUP_OBLIGATION, user, 'DELETE', path, public=public)
                self._ledger_delete(kind, resource_id)
            except SessionOwnerRunError as failure:
                failures.append(f'{path}: {_bounded_detail(failure)}')

        for session_id in (self._session,):
            if session_id:
                dispose(self._manager, f'/sessions/{session_id}', public=True,
                        kind='session', resource_id=session_id)
        for group_id in (self._group,):
            if group_id:
                dispose(self._manager, f'/groups/{group_id}', public=True,
                        kind='group', resource_id=group_id)
        for user, bot_uuid in ((self._manager, self._driver), (self._owner, self._owned)):
            if bot_uuid:
                dispose(user, f'/bots/{bot_uuid}', public=False, kind='bot', resource_id=bot_uuid)
        if failures:
            raise SessionOwnerRunError('Story cleanup failed: ' + repr(failures)[:MAX_ERROR_DETAIL])

    # --- frozen Run lifecycle ----------------------------------------------

    def _initial_state(self) -> dict:
        return {
            'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
            'parent': PARENT, 'run_id': self.run_id, 'status': 'RUNNING',
            'executor_digest': self.executor_digest, 'binding_digest': self.binding_digest,
            'source_binding_digest': fingerprint(self.source_binding),
            'bcs_binary_sha256': self.runtime.binary_sha256, 'binary_kind': BINARY_KIND,
            'auto_build': False,
            'ownership': {'humans': {}, 'human_actors': {}, 'bots': [], 'group': '', 'session': ''},
            'requirements': [{'id': name, 'status': 'NOT_RUN'} for name in REQUIREMENTS],
            'operations': [], 'checks': [], 'resources': [],
            'story_markers': {'observed': False, 'http_checks': 0},
            'story_http_checks': 0, 'cleanup_http_checks': 0,
            'observed_assertion_count': 0,
            'required_assertion_count': REQUIRED_ASSERTION_COUNT,
            'required_assertion_inventory': dict(REQUIRED_ASSERTION_INVENTORY),
            'cleanup': {'status': 'NOT_RUN'},
            'resource_disposition': 'UNVERIFIED',
            'credential_persistence': dict.fromkeys(CREDENTIAL_PERSISTENCE_KEYS, False),
            'execution_scope': {'runtime': 'OWNED_LOCAL_FOREGROUND',
                                'strict_mock_header_admission': True,
                                'ambient_local_identity': False,
                                'remote_capabilities': 'NONE'},
            'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
        }

    def run(self) -> dict:
        journal_path = self.root / JOURNAL_NAME
        ledger_path = self.root / LEDGER_NAME
        if (journal_path.exists() or journal_path.is_symlink()
                or ledger_path.exists() or ledger_path.is_symlink()):
            raise SessionOwnerRunError('Session owner requires fresh journal and resource ledger paths')
        self.state = self._initial_state()
        self._exclusive(journal_path, self.state)
        self._exclusive(ledger_path, {'schema_version': 1, 'run_id': self.run_id,
                                      'contract': SUITE, 'case_id': CASE_ID,
                                      'humans_bootstrap_retained': True, 'resources': []})
        self._save()
        pending: BaseException | None = None
        try:
            try:
                self._obligation_bootstrap_ownership()
                self._obligation_group_session_creation()
                self._obligation_group_read_boundary()
                self._obligation_bot_mode_ownership()
                self._obligation_foreign_mutation_denied()
                self._obligation_human_self_service_scope()
                self._obligation_collection_history_actor_scope()
                self._obligation_membership_revocation_isolation()
                if self._story_http != STORY_CHECKS:
                    raise SessionOwnerRunError(
                        f'Session owner story marker drift: expected {STORY_CHECKS} HTTP checks, '
                        f'observed {self._story_http}')
                self.state['story_markers'] = {'observed': True, 'http_checks': self._story_http}
                self.state['story_http_checks'] = self._story_http
                self.state['status'] = STATUS
                self._save()
            except BaseException as error:
                pending = error
            cleanup_error: BaseException | None = None
            try:
                self._cleanup()
                self.state['cleanup'] = {'status': 'PASS', 'http_checks': self._cleanup_http}
                self.state['cleanup_http_checks'] = self._cleanup_http
                self.state['resource_disposition'] = RESOURCE_DISPOSITION_PASS
                self._pass_requirement(REQUIREMENTS[8])
            except BaseException as error:
                cleanup_error = error
                self.state['cleanup_http_checks'] = self._cleanup_http
                self.state['cleanup'] = {'status': 'FAIL', 'http_checks': self._cleanup_http,
                                         'error_type': type(error).__name__,
                                         'error_detail': _bounded_detail(error)}
            primary = pending if pending is not None else cleanup_error
            if primary is not None:
                self.state['status'] = 'CANCELLED' if has_cancellation(primary) else 'BLOCKED'
                self.state['error_type'] = type(primary).__name__
                self.state['error_detail'] = _bounded_detail(primary)
            self._save()
        except BaseException as error:
            try:
                if self.state is not None:
                    self.state['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
                    self.state['error_type'] = type(error).__name__
                    self.state['error_detail'] = _bounded_detail(error)
                    self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            if isinstance(error, SessionOwnerRunError):
                raise
            raise SessionOwnerRunError('Session owner story failed; inspect the retained journal') from None
        if pending is not None:
            if isinstance(pending, (SessionOwnerRunError, KeyboardInterrupt)):
                raise pending
            raise SessionOwnerRunError('Session owner story failed; inspect the retained journal') from pending
        if cleanup_error is not None:
            if isinstance(cleanup_error, (SessionOwnerRunError, KeyboardInterrupt)):
                raise cleanup_error
            raise SessionOwnerRunError('Session owner cleanup failed; inspect the retained journal') from cleanup_error
        return _json_copy(self.state)


def _dispatch_http(opener, method: str, url: str, headers: dict, payload) -> tuple[int, bytes]:
    """Single HTTP seam; tests replace it with the FakeBCS state machine."""
    request = Request(url, method=method, data=payload, headers=headers)
    try:
        response = opener.open(request, timeout=20)
    except HTTPError as failure:
        response = failure
    with response:
        return response.status, response.read()


def local_session_owner_run(*, binary, binary_sha256: str, state_dir: Path, run_id: str,
                            confirm_run_id: str, allow_local_processes: bool,
                            allow_fixture_write: bool, allow_write: bool) -> dict:
    """Owned-local wrapper: strict admission, frozen binding, PASS gates, publication."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise SessionOwnerRunError('Session owner requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Session owner run ID')
    state_dir = Path(state_dir)
    if state_dir.exists() or state_dir.is_symlink():
        raise SessionOwnerRunError('Session owner requires a fresh state directory; existing evidence is retained')
    if state_dir.parent.is_symlink() or (state_dir.parent.exists() and not state_dir.parent.is_dir()):
        raise SessionOwnerRunError('Session owner requires a non-symlink directory parent for its state')
    binary = validate_session_owner_binary(Path(binary), binary_sha256)
    source = executor_digest()
    original = session_owner_source_binding()
    legacy = current_manifest()['legacy_inventory']
    reviewed = {row['id'] for row in reviewed_release_inventory()}
    if PARENT not in KNOWN_UNMAPPED_LEGACY_IDS or PARENT in reviewed:
        raise SessionOwnerRunError('Session owner slice must stay an independent unmapped slice')
    if CASE_ID in {row['id'] for row in current_manifest()['cases']}:
        raise SessionOwnerRunError('Session owner case id must not join the reviewed manifest before promotion')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
               'run_id': run_id, 'executor_digest': source, 'binary_kind': BINARY_KIND,
               'bcs_binary_sha256': binary_sha256, 'auto_build': False,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'runtime_dir': str(state_dir.resolve()),
               'environment': ENVIRONMENT, 'executor_admission': EXECUTOR_ADMISSION}
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    error: BaseException | None = None
    runtime = None
    journal: dict | None = None
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256, root=state_dir,
                                 run_id=run_id, allow_ambient_local_identity=False) as owned_runtime:
                runtime = owned_runtime
                write(runtime.root / BINDING_NAME, binding)
                journal = OwnedLocalSessionOwner(runtime).run()
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise SessionOwnerRunError('Session owner owned runtime was not created')
        journal_value = journal if journal is not None else (
            read(runtime.root / JOURNAL_NAME) if (runtime.root / JOURNAL_NAME).exists() else {})
        owned_record = read(runtime.root / 'owned-bcs.json') if (runtime.root / 'owned-bcs.json').exists() else {}
        case: dict = {}
        pass_gate = (
            error is None and journal is not None
            and journal_value.get('status') == STATUS
            and journal_value.get('story_http_checks') == STORY_CHECKS
            and journal_value.get('cleanup_http_checks') == CLEANUP_CHECKS
            and journal_value.get('observed_assertion_count') == STORY_CHECKS + CLEANUP_CHECKS
            and journal_value.get('story_markers') == {'observed': True, 'http_checks': STORY_CHECKS}
            and len(journal_value.get('operations', [])) == STORY_CHECKS + CLEANUP_CHECKS
            and all(row.get('status') == 'ACKNOWLEDGED' for row in journal_value.get('operations', []))
            and [row.get('id') for row in journal_value.get('requirements', [])] == list(REQUIREMENTS)
            and all(row.get('status') == 'PASS' for row in journal_value.get('requirements', []))
            and [row.get('id') for row in journal_value.get('checks', [])] == list(CHECK_IDS)
            and all(row.get('status') == 'PASS' for row in journal_value.get('checks', []))
            and journal_value.get('resource_disposition') == RESOURCE_DISPOSITION_PASS
            and journal_value.get('cleanup', {}).get('status') == 'PASS'
            and journal_value.get('credential_persistence') == {
                key: False for key in CREDENTIAL_PERSISTENCE_KEYS}
            and journal_value.get('remote_validation') == 'NOT_RUN'
            and executor_digest() == source
            and read(runtime.root / BINDING_NAME) == binding
            and file_sha256(binary) == binary_sha256
            and fingerprint(session_owner_source_binding()) == fingerprint(original)
            and owned_record.get('status') == 'STOPPED' and owned_record.get('returncode') == 0)
        if pass_gate:
            case = {
                'schema_version': 1, 'status': STATUS, 'run_id': run_id, 'case_id': CASE_ID,
                'suite': SUITE, 'parent': PARENT, 'executor_digest': source,
                'binary_kind': BINARY_KIND, 'bcs_binary_sha256': binary_sha256,
                'auto_build': False, 'environment': ENVIRONMENT,
                'executor_admission': EXECUTOR_ADMISSION,
                'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
                'story_lines': {row['id']: list(row['story_lines'])
                                for row in original['requirements']},
                'story_markers': {'observed': True, 'http_checks': STORY_CHECKS},
                'checks': [dict(row) for row in journal_value.get('checks', [])],
                'story_http_checks': STORY_CHECKS, 'cleanup_http_checks': CLEANUP_CHECKS,
                'observed_assertion_count': STORY_CHECKS + CLEANUP_CHECKS,
                'required_assertion_count': REQUIRED_ASSERTION_COUNT,
                'required_assertion_inventory': dict(REQUIRED_ASSERTION_INVENTORY),
                'ownership': dict(journal_value.get('ownership', {})),
                'resource_disposition': RESOURCE_DISPOSITION_PASS,
                'cleanup': {'status': 'PASS', 'http_checks': CLEANUP_CHECKS},
                'credential_persistence': {key: False for key in CREDENTIAL_PERSISTENCE_KEYS},
                'execution_scope': dict(journal_value.get('execution_scope', {})),
                'documented_gaps': DOCUMENTED_GAPS,
                'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            }
            write(runtime.root / CASE_RECEIPT_NAME, case)
        result = {'schema_version': 1, 'status': 'BLOCKED', 'run_id': run_id, 'case_id': CASE_ID,
                  'suite': SUITE, 'contract': SUITE, 'parent': PARENT,
                  'executor_digest': source, 'binary_kind': BINARY_KIND,
                  'bcs_binary_sha256': binary_sha256, 'auto_build': False,
                  'journal': journal_value, 'case': case,
                  'observed_assertion_count': journal_value.get('observed_assertion_count', 0),
                  'required_assertion_count': REQUIRED_ASSERTION_COUNT,
                  'required_assertion_inventory': dict(REQUIRED_ASSERTION_INVENTORY),
                  'documented_gaps': DOCUMENTED_GAPS,
                  'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
                  'ready_to_execute': False, 'grants_release_credit': False,
                  'environment': ENVIRONMENT, 'state_dir': str(state_dir.resolve())}
        if error is not None:
            result['error_type'] = type(error).__name__
            result['error_detail'] = _bounded_detail(error)
            result['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
        else:
            result['status'] = STATUS if pass_gate else 'BLOCKED'
        if result['status'] == STATUS and (
                executor_digest() != source
                or read(runtime.root / BINDING_NAME) != binding
                or file_sha256(binary) != binary_sha256
                or fingerprint(session_owner_source_binding()) != fingerprint(original)):
            result['status'] = 'BLOCKED'
        write(runtime.root / FLOW_REPORT_NAME, result)
        from .local_session_owner_reporting import publish_session_owner_report
        canonical = publish_session_owner_report(runtime.root, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
