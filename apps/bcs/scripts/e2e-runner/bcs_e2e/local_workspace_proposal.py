"""Owned-local proposal create/preview/confirm slice for the Workspace Story.

This module deliberately stops before Group deletion.  It never grants the
``WORKSPACE.proposal`` obligation: the parent executor must later perform and
independently verify exact deletion through a separately reviewed cleanup
boundary.  Confirmation is a local token-capability operation, not remote auth
proof.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request
import uuid

from .correlation import record_response_id
from .errors import has_cancellation
from .inventory import executor_digest
from .ledger import Ledger
from .local_role_identity import OwnedLocalRoleIdentity
from .local_sqlite import OwnedSqliteDatabase
from .local_workspace_incident import (
    CASE_ID as INCIDENT_CASE,
    OBLIGATIONS as INCIDENT_OBLIGATIONS,
    OwnedLocalWorkspaceIncident,
)
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

CASE_ID = 'LOCAL-WORKSPACE-PROPOSAL-01'
TOPIC = 'Review release readiness'
ROLES = ('CEO', 'PM', 'QA')
OPERATIONS = ('proposal-create', 'proposal-preview', 'proposal-confirm', 'proposal-group-get')
TOKEN_PATH = re.compile(
    r'^/groups/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/confirm$'
)
REDACTED_CONFIRM_PATH = '/groups/<redacted-capability>/confirm'
_ALLOWED_TAGS = {
    'html', 'head', 'title', 'meta', 'body', 'h1', 'p', 'strong', 'h3', 'pre', 'form', 'button'
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def validate_confirmation_url(origin: str, confirm_url: object) -> tuple[str, str, str]:
    """Return the in-memory path, token and a report-safe digest.

    The returned path/token are capabilities and must never be written to a
    journal.  Callers clear their references in ``finally`` blocks.
    """
    if (not isinstance(confirm_url, str) or not 1 <= len(confirm_url) <= 2048
            or not confirm_url.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in confirm_url)):
        raise RunStateError('Workspace proposal confirmation URL is invalid')
    expected = urlsplit(origin)
    candidate = urlsplit(confirm_url)
    try:
        expected_port, candidate_port = expected.port, candidate.port
    except ValueError:
        raise RunStateError('Workspace proposal confirmation URL has an invalid port') from None
    if (expected.scheme != 'http' or expected.hostname != '127.0.0.1'
            or expected.username is not None or expected.password is not None
            or expected.query or expected.fragment or expected.path not in ('', '/')
            or candidate.scheme != expected.scheme
            or candidate.hostname != expected.hostname
            or candidate_port != expected_port
            or candidate.netloc != expected.netloc
            or candidate.username is not None or candidate.password is not None
            or candidate.query or candidate.fragment):
        raise RunStateError('Workspace proposal confirmation URL escaped the owned origin')
    match = TOKEN_PATH.fullmatch(candidate.path)
    if match is None or '%' in candidate.path or '\\' in candidate.path:
        raise RunStateError('Workspace proposal confirmation path is invalid')
    token = match.group(1)
    return candidate.path, token, _sha256_text(confirm_url)


class _ConfirmationPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, str]] = []
        self.text: list[str] = []
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            self.invalid = True
        values = dict(attrs)
        if len(values) != len(attrs):
            self.invalid = True
        if tag == 'form':
            self.forms.append(values)
        if tag in {'script', 'iframe', 'object', 'embed', 'base'}:
            self.invalid = True
        if any(name.lower().startswith('on') for name, _value in attrs):
            self.invalid = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        self.text.append(data)


def validate_confirmation_html(html: object, *, expected_path: str, expected_text: tuple[str, ...]) -> dict:
    if not isinstance(html, str) or not 1 <= len(html.encode('utf-8')) <= 65_536:
        raise RunStateError('Workspace proposal confirmation HTML is invalid')
    parser = _ConfirmationPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        raise RunStateError('Workspace proposal confirmation HTML is malformed') from None
    if parser.invalid or len(parser.forms) != 1:
        raise RunStateError('Workspace proposal confirmation HTML structure is unsafe')
    form = parser.forms[0]
    action = form.get('action')
    method = form.get('method', '').lower()
    if action != expected_path or method != 'post' or urlsplit(action).scheme or urlsplit(action).netloc:
        raise RunStateError('Workspace proposal confirmation form action is mismatched')
    visible = ' '.join(' '.join(parser.text).split())
    if any(not isinstance(value, str) or value not in visible for value in expected_text):
        raise RunStateError('Workspace proposal confirmation page omitted expected text')
    return {'status': 'PASS', 'form_count': 1, 'method': 'POST',
            'action': REDACTED_CONFIRM_PATH, 'expected_text_count': len(expected_text)}


class OwnedLocalWorkspaceProposal:
    """Create and confirm one proposal, retaining its Group for parent cleanup."""

    def __init__(self, identities):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ('CEO', 'PM', 'ENG', 'QA')
                or set(identities._verified) != {'CEO', 'PM', 'ENG', 'QA'}):
            raise RunStateError('Workspace proposal requires verified owned CEO/PM/ENG/QA identities')
        identities._require_roles(('CEO', 'PM', 'ENG', 'QA'))
        self.scope = OwnedLocalWorkspaceIncident(identities)
        self.identities, self.runtime = identities, identities.runtime
        self.scope.state = read(self.scope.path)
        if (self.scope.state.get('case_id') != INCIDENT_CASE
                or self.scope.state.get('status') != 'LOCAL_WORKSPACE_INCIDENT_CONFIG_PASS'
                or self.scope.state.get('run_id') != self.runtime.run_id
                or self.scope.state.get('binding_digest') != self.scope.binding_digest
                or self.scope.state.get('assertions') != [
                    {'id': item, 'status': 'PASS'} for item in INCIDENT_OBLIGATIONS]):
            raise RunStateError('Workspace proposal requires the exact successful incident scope')
        # Intentionally do not require the incident Group ledger/resource to be
        # live: parent convergence may delete it before this proposal stage.
        self.scope._journal_digest = fingerprint(self.scope.state)
        self.executor_digest = executor_digest()
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.incident_digest = self.scope._journal_digest
        self.binding_digest = fingerprint({
            'case_id': CASE_ID, 'run_id': self.runtime.run_id,
            'executor_digest': self.executor_digest,
            'fixture_digest': self.fixture_digest,
            'incident_digest': self.incident_digest,
            'incident_source_binding': self.scope.source_binding,
        })
        self.path = self.runtime.root / 'local-workspace-proposal.json'
        self.ledger_path = self.runtime.root / 'workspace-proposal-resources.sqlite'
        self.state = None
        self.ledger = None
        self._journal_digest = ''
        self._invalidated = False
        self._capability_path: str | None = None
        self._capability_token: str | None = None
        self._completed = False

    def _active(self):
        if self._invalidated:
            raise RunStateError('Workspace proposal mutation authority is invalidated')
        self.scope._active()
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(read(self.scope.path)) != self.incident_digest):
            raise RunStateError('Workspace proposal source, fixture, or prior journal binding changed')
        if self._journal_digest and fingerprint(read(self.path)) != self._journal_digest:
            self._invalidated = True
            raise RunStateError('Workspace proposal journal changed; preserve the original artifact')

    def _save(self):
        if self._journal_digest and fingerprint(read(self.path)) != self._journal_digest:
            self._invalidated = True
            raise RunStateError('Workspace proposal journal changed; preserve the original artifact')
        serialized = json.dumps(self.state, sort_keys=True, separators=(',', ':'), allow_nan=False)
        for secret in (self._capability_path, self._capability_token):
            if secret and secret in serialized:
                raise RunStateError('Workspace proposal capability leaked into its journal')
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _id(self, role: str) -> str:
        self._active()
        return self.identities._entries[role][0]

    def _admit_public_roles(self):
        evidence = []
        receipt = self.identities.fixtures.check()
        for role in ROLES:
            self._active()
            bot_id, token = self.identities._entries[role]
            binding = receipt['roles'].get(role)
            if (binding != {'bot_id': bot_id, 'owner_id': self.identities.human,
                            'dedicated': True,
                            'name': f'e2e-{role}-{self.identities.human}', 'origin': 'created'}):
                raise RunStateError('Workspace proposal role is not an exact owned fixture')
            observed = []
            for operation in ('visibility', 'friends', 'visibility'):
                value = self.identities._request(operation, bot_id, token)
                data = value.get('data')
                if operation == 'visibility':
                    if data != {'bot_uuid': bot_id, 'visibility': 'public'}:
                        raise RunStateError('Workspace proposal requires public CEO/PM/QA fixtures')
                    observed.append('public')
                elif not isinstance(data, list) or len(data) > 100:
                    raise RunStateError('Workspace proposal role identity read is invalid')
            if self.identities._entries[role] != (bot_id, token) or observed != ['public', 'public']:
                raise RunStateError('Workspace proposal role credential changed during admission')
            evidence.append({'role': role, 'bot_id': bot_id, 'visibility': 'public',
                             'ownership': 'OWNED_LOCAL_CREATED'})
        self.state['role_admission'] = {'status': 'PASS', 'actors': evidence}
        self._save()

    def _record_intent(self, operation: str, method: str, path: str, *, role: str,
                       request_id: str, handles: tuple[str, str] | None = None):
        index = len(self.state['operations'])
        if index >= len(OPERATIONS) or OPERATIONS[index] != operation:
            raise RunStateError('Workspace proposal operation is outside its finite ordered plan')
        event = {'operation': operation, 'method': method, 'path': path, 'role': role,
                 'submitted_request_id': request_id, 'status': 'INTENDED'}
        if handles:
            event['resource_handles'] = {'group': handles[0], 'initial_session': handles[1]}
            event['resource_binding'] = 'UNKNOWN'
        self.state['operations'].append(event)
        self._save()
        return event

    def _validate_request(self, operation, method, path, body, role):
        if operation == 'proposal-create':
            expected = ('POST', '/groups/request', {
                'topic': TOPIC, 'suggested_participants': [self._id('PM'), self._id('QA')],
                'suggested_driver': self._id('CEO')}, 'CEO')
        elif operation == 'proposal-confirm' and self._capability_path:
            expected = ('POST', self._capability_path, None, 'TOKEN_CAPABILITY')
        elif operation == 'proposal-group-get':
            resource = self.state['resources'].get('proposal_group')
            if not resource or self.ledger is None:
                raise RunStateError('Proposal Group read requires its owned resource')
            group = self.ledger._get(resource['handle'])
            session = self.ledger._get(resource['initial_session']['handle'])
            if (group['kind'] != 'group' or session['kind'] != 'session'
                    or group['resource_id'] != resource['id']
                    or session['resource_id'] != resource['initial_session']['id']
                    or session['parent'] != group['handle'] or group['parent'] is not None
                    or any(row['case_id'] != CASE_ID or row['attempt'] != 1 or row['origin'] != 'created'
                           or row['state'] != 'live' for row in (group, session))):
                raise RunStateError('Proposal Group read ledger scope changed')
            expected = ('GET', '/groups/'+quote(resource['id'],safe=''), None, 'LOCAL_NO_AUTH')
        else:
            raise RunStateError('Proposal request is outside its fixed operations')
        if (method, path, body, role) != expected:
            raise RunStateError('Proposal request differs from its exact owned plan')

    def _json_request(self, *, operation: str, method: str, path: str, body: dict | None,
                      role: str, expected_status: int = 200, handles=None,
                      request_id: str | None = None) -> dict:
        self._active()
        self._validate_request(operation, method, path, body, role)
        request_id = request_id or 'e2e-workspace-proposal-' + uuid.uuid4().hex
        report_path = REDACTED_CONFIRM_PATH if operation in {'proposal-preview', 'proposal-confirm'} else path
        event = self._record_intent(operation, method, report_path, role=role,
                                    request_id=request_id, handles=handles)
        headers = {'Accept': 'application/json', 'X-Request-ID': request_id}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        if role in {'TOKEN_CAPABILITY', 'LOCAL_NO_AUTH'}:
            token = None
        else:
            token = self.identities._entries[role][1]
            headers['Authorization'] = 'Bearer ' + token
        request = Request(self.identities.origin + path, method=method, data=data, headers=headers)
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=((token,) if token else ()))
                raw = response.read(65_537)
            self._active()
            if response.status != expected_status or len(raw) > 65_536:
                raise RunStateError('Workspace proposal operation was not exactly confirmed')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(value, dict):
                raise RunStateError('Workspace proposal response is not an object')
            event['status'] = 'ACKNOWLEDGED'
            self._save()
            return value
        except BaseException as error:
            self._unknown(event, error, handles)

    def _html_request(self, path: str, expected_text: tuple[str, ...]) -> dict:
        self._active()
        if not self._capability_path or path != self._capability_path:
            raise RunStateError('Proposal preview requires its exact in-memory capability')
        request_id = 'e2e-workspace-proposal-' + uuid.uuid4().hex
        event = self._record_intent('proposal-preview', 'GET', REDACTED_CONFIRM_PATH,
                                    role='TOKEN_CAPABILITY', request_id=request_id)
        request = Request(self.identities.origin + path, method='GET',
                          headers={'Accept': 'text/html', 'X-Request-ID': request_id})
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                event['http_status'] = response.status
                raw = response.read(65_537)
            self._active()
            if response.status != 200 or len(raw) > 65_536:
                raise RunStateError('Workspace proposal preview was not confirmed')
            evidence = validate_confirmation_html(raw.decode('utf-8'), expected_path=path,
                                                  expected_text=expected_text)
            event['status'] = 'ACKNOWLEDGED'
            self.state['html_validation'] = evidence
            self._save()
            return evidence
        except BaseException as error:
            self._unknown(event, error, None)

    def _unknown(self, event, error, handles):
        self._invalidated = True
        event['status'] = 'UNKNOWN'
        event['error_type'] = type(error).__name__
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
        raise RunStateError('Workspace proposal operation failed; retain without replay') from None

    def _verify_group_and_storage(self, resource: dict, group_result: dict):
        self._active()
        group_id = resource['id']
        session_id = resource['initial_session']['id']
        participants = [self._id('PM'), self._id('QA'), self._id('CEO')]
        participant_rows = group_result.get('participants')
        if (not isinstance(participant_rows, list) or len(participant_rows) != 3
                or any(not isinstance(row, dict) for row in participant_rows)):
            raise RunStateError('Workspace proposal Group participant read-back is invalid')
        projected = [(row.get('bot_uuid'), row.get('role')) for row in participant_rows]
        if (group_result.get('id') != group_id or group_result.get('driver_bot') != self._id('CEO')
                or group_result.get('label') != 'Group: ' + TOPIC
                or group_result.get('status') != 'active'
                or group_result.get('latest_running_session_id') != session_id
                or set(projected) != {(participants[0], 'consultant'),
                                      (participants[1], 'consultant'),
                                      (participants[2], 'driver')}):
            raise RunStateError('Workspace proposal Group read-back is not exact')
        with OwnedSqliteDatabase(self.runtime)._connection() as database:
            database.execute('BEGIN')
            groups = [tuple(row) for row in database.execute(
                "SELECT group_id,label,status,driver_bot FROM bcs_groups "
                "WHERE env='local' AND group_id=? LIMIT 2", (group_id,)).fetchall()]
            members = [tuple(row) for row in database.execute(
                "SELECT bot_uuid,role FROM bcs_group_participants "
                "WHERE env='local' AND group_id=? ORDER BY id LIMIT 4", (group_id,)).fetchall()]
            sessions = [tuple(row) for row in database.execute(
                "SELECT session_id,group_id,status,session_kind FROM bcs_group_sessions "
                "WHERE env='local' AND session_id=? LIMIT 2", (session_id,)).fetchall()]
            messages = [tuple(row) for row in database.execute(
                "SELECT message_id,session_id,message_type,status FROM bcs_messages "
                "WHERE env='local' AND group_id=? ORDER BY created_at LIMIT 21", (group_id,)).fetchall()]
        if (groups != [(group_id, 'Group: '+TOPIC, 'active', self._id('CEO'))]
                or len(members) != 3 or set(members) != {(self._id('PM'), 'consultant'), (self._id('QA'), 'consultant'),
                                                       (self._id('CEO'), 'driver')}
                or sessions != [(session_id, group_id, 'running', 'chat')]
                or len(messages) > 20 or any(row[1] != session_id for row in messages)):
            raise RunStateError('Workspace proposal SQLite state is not exact')
        rows = self.ledger.resource_snapshot()
        if (len(rows) != 2 or rows[0]['resource_id'] != group_id or rows[1]['resource_id'] != session_id
                or rows[0]['state'] != 'live' or rows[1]['state'] != 'live'
                or rows[1]['parent'] != rows[0]['handle']):
            raise RunStateError('Workspace proposal resource ledger is incomplete')
        self.state['storage_verification'] = {
            'status': 'PASS', 'group_rows': 1, 'participant_rows': 3,
            'initial_session_rows': 1, 'ledger_resources': 2,
        }
        self.state['retained_messages'] = {
            'status': 'OBSERVED_SEPARATELY', 'row_count': len(messages),
            'message_ids': [identifier(row[0], 'proposal retained message ID') for row in messages],
            'session_id': session_id, 'group_delete_contract': 'RETAINED_NOT_ERASED',
        }
        self._save()

    def run_create_confirm(self) -> dict:
        """Run through confirmation and return owned handles for parent cleanup."""
        self._active()
        if (self.path.exists() or self.path.is_symlink()
                or self.ledger_path.exists() or self.ledger_path.is_symlink()):
            raise RunStateError('Workspace proposal requires fresh journal and ledger paths')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.state = {
            'schema_version': 1, 'contract': 'owned-local-workspace-proposal-create-confirm-v1',
            'case_id': CASE_ID, 'run_id': self.runtime.run_id, 'status': 'RUNNING',
            'executor_digest': self.executor_digest, 'fixture_digest': self.fixture_digest,
            'incident_digest': self.incident_digest, 'binding_digest': self.binding_digest,
            'topic': TOPIC, 'operations': [], 'resources': {},
            'role_admission': {'status': 'NOT_RUN'}, 'url_validation': {'status': 'NOT_RUN'},
            'html_validation': {'status': 'NOT_RUN'}, 'storage_verification': {'status': 'NOT_RUN'},
            'retained_messages': {'status': 'NOT_RUN'},
            'capability': {'storage': 'DIGEST_ONLY', 'memory': 'HELD_DURING_OPERATION'},
            'cleanup': {'status': 'NOT_RUN', 'required_for_obligation': True},
            'assertions': [{'id': 'WORKSPACE.proposal', 'status': 'NOT_RUN'}],
            'full_story_status': 'NOT_MIGRATED', 'remote_authorization_proof': 'NOT_CLAIMED',
            'confirmation_auth': 'LOCAL_TOKEN_CAPABILITY_NO_AUTH',
            'legacy_delete_auth': 'LOCAL_QUERY_ACTOR_NOT_REMOTE_PROOF',
            'execution_scope': {
                'receiver_mode': 'NOT_OPENED', 'active_bot_connections': 'NOT_REQUIRED',
                'engine_child': 'NOT_STARTED_BY_ADAPTER',
                'context_bootstrap': 'ATTEMPT_COUNT_ONLY_NOT_EXECUTION_PROOF',
                'session_or_run_terminal': 'NOT_CLAIMED',
            },
            'ready_to_execute': False,
        }
        self._save()
        try:
            with Ledger(self.ledger_path, self.runtime.run_id, self.binding_digest, create=True) as ledger:
                self.ledger = ledger
                self._admit_public_roles()
                expected_participants = [self._id('PM'), self._id('QA'), self._id('CEO')]
                created = self._json_request(
                    operation='proposal-create', method='POST', path='/groups/request', role='CEO',
                    body={'topic': TOPIC, 'suggested_participants': expected_participants[:2],
                          'suggested_driver': self._id('CEO')})
                if (created.get('proposal_created') is not True
                        or created.get('driver_bot') != self._id('CEO')
                        or created.get('participants') != expected_participants
                        or created.get('expires_in_seconds') != 600
                        or not isinstance(created.get('member_intros'), str)
                        or not isinstance(created.get('message'), str)):
                    raise RunStateError('Workspace proposal create response is not exact')
                path, token, digest = validate_confirmation_url(
                    self.identities.origin, created.get('confirm_url'))
                self._capability_path, self._capability_token = path, token
                self.state['url_validation'] = {
                    'status': 'PASS', 'origin': 'OWNED_LOOPBACK_EXACT',
                    'path': REDACTED_CONFIRM_PATH, 'confirm_url_sha256': digest,
                    'query': False, 'fragment': False, 'userinfo': False,
                }
                self._save()
                fixture = read(self.identities.fixtures.path)['roles']
                self._html_request(path, (TOPIC, self._id('CEO'),
                                          fixture['PM']['name'], fixture['QA']['name']))
                request_id = 'e2e-workspace-proposal-' + uuid.uuid4().hex
                handles = ledger.group_creation_intent(CASE_ID, 1, request_id)
                # The durable Group/Session intent exists before token confirmation.
                confirmed = self._json_request(
                    operation='proposal-confirm', method='POST', path=path, body=None,
                    role='TOKEN_CAPABILITY', handles=handles, request_id=request_id)
                group_id = identifier(confirmed.get('group_id'), 'proposal Group ID')
                session_id = identifier(confirmed.get('session_id'), 'proposal initial Session ID')
                if (confirmed.get('created') is not True
                        or confirmed.get('driver_bot') != self._id('CEO')
                        or confirmed.get('participants') != expected_participants
                        or not session_id.startswith(group_id + ':')
                        or type(confirmed.get('context_injected')) is not int
                        or confirmed['context_injected'] < 0):
                    raise RunStateError('Workspace proposal confirmation response is not exact')
                ledger.observed(handles[0], group_id, origin='created')
                ledger.observed(handles[1], session_id, origin='created')
                resource = {'id': group_id, 'handle': handles[0],
                            'initial_session': {'id': session_id, 'handle': handles[1]}}
                self.state['resources']['proposal_group'] = resource
                self.state['operations'][-1]['resource_binding'] = 'BOUND'
                self.state['operations'][-1]['candidate_ids'] = {
                    'group': group_id, 'initial_session': session_id}
                self._save()
                group = self._json_request(
                    operation='proposal-group-get', method='GET',
                    path='/groups/' + quote(group_id, safe=''), body=None,
                    role='LOCAL_NO_AUTH')
                self._verify_group_and_storage(resource, group)
                self.state['status'] = 'LOCAL_WORKSPACE_PROPOSAL_CONFIRMED_AWAITING_DELETE'
                self.state['cleanup'] = {
                    'status': 'REQUIRED_PARENT_CALLBACK_NOT_RUN',
                    'group_handle': handles[0], 'initial_session_handle': handles[1],
                    'group_id': group_id, 'initial_session_id': session_id,
                    'required_for_obligation': True,
                }
                self._capability_path = None
                self._capability_token = None
                self.state['capability']['memory'] = 'CLEARED_REFERENCE_ONLY'
                self._save()
                self._completed = True
                return json.loads(json.dumps(self.state, allow_nan=False))
        except BaseException as error:
            self._invalidated = True
            self.state['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
            self.state['error_type'] = type(error).__name__
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('Workspace proposal create/confirm failed; inspect retained artifacts') from None
        finally:
            self._capability_path = None
            self._capability_token = None
            if self.state is not None:
                self.state['capability']['memory'] = 'CLEARED_REFERENCE_ONLY'
                try:
                    # This update contains no capability and is safe after both
                    # acknowledged and unknown outcomes.
                    self._save()
                except BaseException:
                    pass
