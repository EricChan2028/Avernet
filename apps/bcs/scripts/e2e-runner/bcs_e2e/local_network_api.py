"""Complete owned-local execution of the legacy Agent-network parent Story.

The caller owns the live ``OwnedBcsProcess``, the workspace fixtures, and the
one-lifecycle role credentials.  This executor contacts only that loopback
runtime, never follows redirects or proxies, and never retries an ambiguous
mutation.  The temporary Bot has its own durable JSON ledger because the common
resource ledger intentionally has no Bot kind.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .local_network_contract import CASE_ID, REQUIREMENTS, SUITE, network_source_binding
from .local_panel_probe import verify_owned_panel
from .local_role_identity import OwnedLocalRoleIdentity
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO', 'PM', 'ENG', 'QA')
CONTRACT = 'owned-local-network-v1'
LEDGER_CONTRACT = 'owned-local-network-ledger-v1'
JOURNAL_NAME = 'local-network-api.json'
LEDGER_NAME = 'local-network-ledger.json'
BINDING_NAME = 'network-binding.json'
MAX_RESPONSE_BYTES = 65_536


class LocalNetworkApiError(RunStateError):
    pass


def _credential(value: object, label: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise LocalNetworkApiError(f'{label} is missing or invalid')
    return value


class OwnedLocalNetworkApi:
    """Execute all eight ordered Agent-network requirements in one owned runtime."""

    def __init__(self, identities: OwnedLocalRoleIdentity):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise LocalNetworkApiError(
                'network API requires freshly verified CEO/PM/ENG/QA owned-local identities')
        identities._require_roles(ROLES)
        self.identities = identities
        self.runtime = identities.runtime
        self.root = self.runtime.root
        self.path = self.root / JOURNAL_NAME
        self.ledger_path = self.root / LEDGER_NAME
        self.binding_path = self.root / BINDING_NAME
        self.executor_digest = executor_digest()
        self.source_binding = network_source_binding()
        self.binding = self._validated_binding()
        self.binding_digest = fingerprint(self.binding)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.state: dict | None = None
        self.ledger: dict | None = None
        self._journal_digest = ''
        self._ledger_digest = ''
        self._invalidated = False
        self._register_token = ''
        self._bot_token = ''
        self._temporary_id = ''
        self._current_requirement = ''

    def _validated_binding(self) -> dict:
        try:
            if self.binding_path.is_symlink() or not self.binding_path.is_file():
                raise ValueError
            binding = read(self.binding_path)
            panel_sha = self.runtime.panel_asset.sha256 if self.runtime.panel_asset is not None else None
            expected_legacy = current_manifest()['legacy_inventory']
            if (set(binding) != {
                    'schema_version', 'contract', 'suite', 'case_id', 'run_id', 'executor_digest',
                    'bcs_binary_sha256', 'panel_sha256', 'source_binding',
                    'selected_requirement_ids', 'legacy_inventory', 'runtime_dir'}
                    or binding['schema_version'] != 1
                    or binding['contract'] != CONTRACT or binding['suite'] != SUITE
                    or binding['case_id'] != CASE_ID
                    or binding['run_id'] != self.runtime.run_id
                    or binding['executor_digest'] != self.executor_digest
                    or binding['bcs_binary_sha256'] != self.runtime.binary_sha256
                    or panel_sha is None or binding['panel_sha256'] != panel_sha
                    or binding['source_binding'] != self.source_binding
                    or binding['selected_requirement_ids'] != list(REQUIREMENTS)
                    or binding['legacy_inventory'] != expected_legacy
                    or binding['runtime_dir'] != str(self.root)):
                raise ValueError
            return binding
        except (KeyError, OSError, TypeError, ValueError, RunStateError):
            raise LocalNetworkApiError('network execution binding is missing or changed') from None

    def _check_file(self, path: Path, expected_digest: str, label: str):
        if not expected_digest:
            return
        try:
            matches = fingerprint(read(path)) == expected_digest
        except Exception:
            matches = False
        if not matches:
            self._invalidated = True
            raise LocalNetworkApiError(f'{label} changed; preserve the original artifact')

    def _save(self):
        self._check_file(self.path, self._journal_digest, 'network journal')
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _save_ledger(self):
        self._check_file(self.ledger_path, self._ledger_digest, 'network Bot ledger')
        write(self.ledger_path, self.ledger)
        self._ledger_digest = fingerprint(self.ledger)

    def _active(self):
        if self._invalidated:
            raise LocalNetworkApiError('network mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(network_source_binding()) != fingerprint(self.source_binding)
                or fingerprint(self._validated_binding()) != self.binding_digest):
            raise LocalNetworkApiError('network executor, fixtures, source, or binding changed')
        self._check_file(self.path, self._journal_digest, 'network journal')
        self._check_file(self.ledger_path, self._ledger_digest, 'network Bot ledger')

    @staticmethod
    def _exclusive(path: Path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise LocalNetworkApiError('network executor requires fresh journal and ledger paths') from None
        os.close(fd)

    def _role_id(self, role: str) -> str:
        self._active()
        return self.identities._entries[role][0]

    def _human_projection(self) -> dict:
        value, _ = self._request('platform-me', 'GET', '/me')
        expected_actor = 'human_' + self.identities.human
        if (value.get('user_id') != self.identities.human
                or value.get('staff_no') != self.identities.human
                or value.get('actor_uuid') != expected_actor
                or value.get('nick_name') != 'E2E local test human'):
            raise LocalNetworkApiError('network local Human identity is not exact')
        return {'user_id': self.identities.human, 'actor_uuid': expected_actor}

    def _admit_ceo(self) -> tuple[str, str]:
        self._active()
        entry = self.identities._entries['CEO']
        bot_id, token = entry
        for operation in ('visibility', 'friends', 'visibility'):
            value = self.identities._request(operation, bot_id, token)
            self._active()
            data = value.get('data')
            if operation == 'visibility':
                if (not isinstance(data, dict) or data.get('bot_uuid') != bot_id
                        or data.get('visibility') not in ('public', 'protected', 'private')):
                    raise LocalNetworkApiError('CEO self-read is invalid')
            elif not isinstance(data, list) or len(data) > 100:
                raise LocalNetworkApiError('CEO friend self-read is invalid')
        if self.identities._entries['CEO'] != entry:
            raise LocalNetworkApiError('CEO credential changed during mutation admission')
        return entry

    def _append_operation(self, name: str, method: str, safe_path: str, *, mutation: bool,
                          request_id: str = '') -> dict:
        self._active()
        if any(row['name'] == name for row in self.state['operations']):
            raise LocalNetworkApiError('network operation cannot be replayed')
        event = {
            'name': name,
            'requirement_id': self._current_requirement,
            'method': method,
            'path': safe_path,
            'kind': 'MUTATION' if mutation else 'READ',
            'submitted_request_id': request_id or 'e2e-network-' + uuid.uuid4().hex,
            'status': 'INTENDED',
            'transitions': [{'status': 'INTENDED'}],
        }
        self.state['operations'].append(event)
        self._save()
        return event

    def _require_request_scope(self, name: str, method: str, path: str, body: dict | None,
                               token: str, expected_status: int, mutation: bool):
        """Admit one exact reviewed helper request before journal or transport."""
        requirement = self._current_requirement
        ceo = self.identities._entries['CEO']
        temporary = self.ledger['resource'] if self.ledger is not None else {}
        temporary_id = temporary.get('resource_id')
        registration_name = temporary.get('registration_name')
        status_path = '/actors/' + quote(ceo[0], safe='') + '/status'
        temporary_path = ('/bots/' + quote(temporary_id, safe='')
                          if isinstance(temporary_id, str) and temporary_id else '')
        mutation_plan = {
            'actor-status-hidden': ('NETWORK.actor-status', 'PUT', status_path,
                                    {'status': 'hidden'}, ceo[1], 200),
            'actor-status-online': ('NETWORK.actor-status', 'PUT', status_path,
                                    {'status': 'online'}, ceo[1], 200),
            'onboard-owned-bot': ('NETWORK.register-onboard', 'POST', '/bots/onboard',
                {'name': registration_name, 'summary': 'Story-owned support agent',
                 'domains': ['support'], 'skills': ['triage'], 'scopes': ['local']},
                self._bot_token, 200),
            'admin-reonboard-owned-bot': ('NETWORK.register-onboard', 'POST',
                '/admin/bots/onboard',
                {'bot_id': temporary_id, 'name': (registration_name + '-admin'
                    if isinstance(registration_name, str) else None),
                 'summary': 'Admin-reviewed agent', 'domains': ['support'],
                 'skills': ['triage'], 'scopes': ['local']}, '', 200),
            'delete-owned-bot': ('NETWORK.register-onboard', 'DELETE', temporary_path,
                                 None, '', 200),
        }
        if mutation:
            expected = mutation_plan.get(name)
            if expected is None or (requirement, method, path, body, token, expected_status) != expected:
                raise LocalNetworkApiError('network mutation is outside the finite owned request plan')
            if name.startswith('actor-status-'):
                if ceo[0] != self._role_id('CEO') or self.identities._entries['CEO'] != ceo:
                    raise LocalNetworkApiError('network CEO status target is not the owned fixture')
            else:
                # Every temporary-Bot mutation requires an immediately preceding
                # exact Human-owner proof. The proof method itself is read-only.
                proof = {
                    'onboard-owned-bot': 'onboard-owner-proof',
                    'admin-reonboard-owned-bot': 'admin-owner-proof',
                    'delete-owned-bot': 'delete-owner-proof',
                }[name]
                if (not self.state['operations'] or self.state['operations'][-1]['name'] != proof
                        or self.state['operations'][-1]['status'] != 'ACKNOWLEDGED'
                        or temporary.get('owner_id') != self.identities.human
                        or temporary_id != self._temporary_id):
                    raise LocalNetworkApiError('network temporary-Bot mutation lacks exact owner proof')
            return

        # A mutation route can never be smuggled through the read path. POST
        # /bots/query is the sole reviewed read-semantic POST and has its own
        # bounded array implementation.
        if (method not in ('GET',)
                or path == '/bots/onboard' or path == '/admin/bots/onboard'
                or path.startswith('/actors/') and path.endswith('/status')
                or method == 'DELETE'):
            raise LocalNetworkApiError('network read request cannot target a mutation endpoint')
        exact_reads = {
            'platform-health': ('NETWORK.platform', '/health', 200),
            'platform-me': ('NETWORK.platform', '/me', 200),
            'platform-repair-info': ('NETWORK.platform', '/me/repair-info', 200),
            'platform-missing-secret': ('NETWORK.platform', '/admin/secret/e2e-missing-secret', 404),
            'actor-list': ('NETWORK.actor-list', '/actors/list?' + urlencode({
                'current_bot_uuid': ceo[0], 'cooperatable_only': 'false',
                'limit': 20}), 200),
            'actor-search': ('NETWORK.actor-search', '/actors/search?' + urlencode({
                'q': '产品', 'current_bot_uuid': ceo[0], 'cooperatable_only': 'false'}), 200),
            'actor-status-before': ('NETWORK.actor-status', '/bots/' + quote(ceo[0], safe=''), 200),
            'actor-status-hidden-readback': ('NETWORK.actor-status', '/bots/' + quote(ceo[0], safe=''), 200),
            'actor-status-online-readback': ('NETWORK.actor-status', '/bots/' + quote(ceo[0], safe=''), 200),
            'actor-status-final': ('NETWORK.actor-status', '/bots/' + quote(ceo[0], safe=''), 200),
            'bots-my': ('NETWORK.bots-my', '/bots/my?offset=0&limit=20', 200),
            'bots-paged': ('NETWORK.bots-paged', '/bots/paged?limit=5&offset=0', 200),
            'register-token': ('NETWORK.register-onboard', '/register/token', 200),
            'registered-owner-proof': ('NETWORK.register-onboard', temporary_path, 200),
            'onboard-owner-proof': ('NETWORK.register-onboard', temporary_path, 200),
            'browser-url-owner-proof': ('NETWORK.register-onboard', temporary_path, 200),
            'browser-onboard-url': ('NETWORK.register-onboard', '/onboard/url?' + urlencode({
                'token': self._bot_token, 'name': registration_name,
                'summary': 'Story-owned support agent'}), 200),
            'admin-owner-proof': ('NETWORK.register-onboard', temporary_path, 200),
            'admin-detail-readback': ('NETWORK.register-onboard', temporary_path, 200),
            'delete-owner-proof': ('NETWORK.register-onboard', temporary_path, 200),
            'deleted-bot-404': ('NETWORK.register-onboard', temporary_path, 404),
        }
        expected = exact_reads.get(name)
        if name.startswith('initial-fixture-') or name.startswith('final-fixture-'):
            phase, role = name.split('-fixture-', 1)
            role = role.upper()
            expected = ('NETWORK.platform' if phase == 'initial' else 'NETWORK.register-onboard',
                        '/bots/' + quote(self._role_id(role), safe=''), 200)
        elif name == 'initial-bots-my-snapshot':
            expected = ('NETWORK.platform', '/bots/my?offset=0&limit=20', 200)
        elif name == 'final-bots-my-snapshot':
            expected = ('NETWORK.register-onboard', '/bots/my?offset=0&limit=20', 200)
        if (expected is None or (requirement, path, expected_status) != expected
                or body is not None or token):
            raise LocalNetworkApiError('network read is outside the finite reviewed request plan')

    def _request(self, name: str, method: str, path: str, *, body: dict | None = None,
                 token: str = '', expected_status: int = 200, safe_path: str | None = None,
                 mutation: bool = False) -> tuple[dict, int]:
        if not path.startswith('/') or '\n' in path or '\r' in path:
            raise LocalNetworkApiError('network request path is invalid')
        self._require_request_scope(name, method, path, body, token, expected_status, mutation)
        event = self._append_operation(name, method, safe_path or path, mutation=mutation)
        headers = {'Accept': 'application/json', 'X-Request-ID': event['submitted_request_id']}
        payload = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            payload = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(self.identities.origin + path, method=method, data=payload, headers=headers)
        try:
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
                raise LocalNetworkApiError('network response status or byte budget differs')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(value, dict):
                raise LocalNetworkApiError('network response is not an object')
            event.update(status='ACKNOWLEDGED', http_status=status)
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': status})
            self._save()
            return value, status
        except BaseException as error:
            self._invalidated = True
            event['status'] = 'UNKNOWN' if mutation else 'FAILED'
            event['error_type'] = type(error).__name__
            event['transitions'].append({'status': event['status'], 'error_type': event['error_type']})
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise LocalNetworkApiError('network request failed; retain evidence without retry') from None

    def _read_bot(self, bot_id: str, *, operation: str) -> dict:
        identifier(bot_id, 'network Bot ID')
        value, _ = self._request(operation, 'GET', '/bots/' + quote(bot_id, safe=''))
        if value.get('bot_uuid') != bot_id:
            raise LocalNetworkApiError('network Bot detail identity mismatched')
        return value

    @staticmethod
    def _bot_projection(value: dict) -> dict:
        capabilities = value.get('capabilities')
        if not isinstance(capabilities, dict):
            raise LocalNetworkApiError('network Bot capabilities are invalid')
        return {
            'bot_uuid': value.get('bot_uuid'),
            'created_by': value.get('created_by'),
            'actor_kind': value.get('actor_kind'),
            'env': value.get('env'),
            'status': value.get('status'),
            'capabilities': capabilities,
        }

    def _fixture_snapshot(self, phase: str) -> dict:
        roles = {}
        for role in ROLES:
            bot_id = self._role_id(role)
            detail = self._read_bot(bot_id, operation=f'{phase}-fixture-{role.lower()}')
            projection = self._bot_projection(detail)
            if (projection['created_by'] != self.identities.human
                    or projection['actor_kind'] != 'bot' or projection['env'] != 'local'):
                raise LocalNetworkApiError('network fixture ownership projection is invalid')
            roles[role] = projection
        mine, _ = self._request(f'{phase}-bots-my-snapshot', 'GET', '/bots/my?offset=0&limit=20')
        items = mine.get('items')
        if (not isinstance(items, list) or mine.get('offset') != 0 or mine.get('limit') != 20
                or mine.get('total') != len(items) or len(items) > 20):
            raise LocalNetworkApiError('network owned-Bot snapshot is invalid or truncated')
        ids = [row.get('bot_uuid') for row in items if isinstance(row, dict)]
        if len(ids) != len(items) or len(set(ids)) != len(ids):
            raise LocalNetworkApiError('network owned-Bot snapshot contains invalid identities')
        return {'roles': roles, 'owned_bot_ids': sorted(ids), 'owned_bot_total': len(ids)}

    def _assert_fixtures_unchanged(self):
        final = self._fixture_snapshot('final')
        if final != self.state['fixture_snapshot']:
            raise LocalNetworkApiError('non-target fixture or owned-Bot inventory changed')
        self.state['non_target_after'] = final['roles']
        self.state['owned_bot_inventory_after'] = final['owned_bot_ids']
        self.state['fixture_preservation'] = {'status': 'PASS'}
        self._save()

    def _platform_entrypoints(self):
        health, _ = self._request('platform-health', 'GET', '/health')
        if health.get('status') != 'ok' or health.get('service') != 'bcs':
            raise LocalNetworkApiError('platform health contract differs')
        panel = verify_owned_panel(self.runtime)
        if panel.get('status') != 'PASS' or panel.get('sha256') != self.runtime.panel_asset.sha256:
            raise LocalNetworkApiError('platform manifest asset was not verified')
        human = self._human_projection()
        repair, _ = self._request('platform-repair-info', 'GET', '/me/repair-info')
        if repair.get('ok') is not True or repair.get('actor_uuid') != human['actor_uuid']:
            raise LocalNetworkApiError('platform Human repair inspection differs')
        missing, _ = self._request('platform-missing-secret', 'GET',
                                   '/admin/secret/e2e-missing-secret', expected_status=404)
        if missing.get('error') != 'not_found':
            raise LocalNetworkApiError('platform missing-secret contract differs')
        self.state['platform'] = {'panel': panel, 'human': human, 'missing_secret': 'CONFIRMED_404'}
        self._save()

    def _actor_list(self):
        ceo = self._role_id('CEO')
        path = '/actors/list?' + urlencode({
            'current_bot_uuid': ceo, 'cooperatable_only': 'false', 'limit': 20})
        value, _ = self._request('actor-list', 'GET', path)
        bots, total = value.get('bots'), value.get('total')
        if (not isinstance(bots, list) or not isinstance(total, int) or total < len(bots)
                or len(bots) > 20 or any(not isinstance(row, dict) for row in bots)):
            raise LocalNetworkApiError('actor list response shape differs')

    def _actor_search(self):
        path = '/actors/search?' + urlencode({
            'q': '产品', 'current_bot_uuid': self._role_id('CEO'), 'cooperatable_only': 'false'})
        value, _ = self._request('actor-search', 'GET', path)
        if (not isinstance(value.get('bots'), list) or not isinstance(value.get('context'), dict)
                or len(value['bots']) > 20 or any(not isinstance(row, dict) for row in value['bots'])):
            raise LocalNetworkApiError('actor search response shape differs')

    def _actor_status(self):
        ceo, token = self._admit_ceo()
        before = self._bot_projection(self._read_bot(ceo, operation='actor-status-before'))
        if before['status'] != 'online':
            raise LocalNetworkApiError('owned CEO did not start online')
        for desired in ('hidden', 'online'):
            self._active()
            if self.identities._entries['CEO'] != (ceo, token):
                raise LocalNetworkApiError('CEO credential changed before status mutation')
            result, _ = self._request(
                'actor-status-' + desired, 'PUT', '/actors/' + quote(ceo, safe='') + '/status',
                body={'status': desired}, token=token, mutation=True)
            if (result.get('success') is not True
                    or result.get('data') != {'actor_id': ceo, 'status': desired}):
                raise LocalNetworkApiError('CEO status mutation response differs')
            detail = self._read_bot(ceo, operation='actor-status-' + desired + '-readback')
            if detail.get('status') != desired:
                raise LocalNetworkApiError('CEO status readback differs')
        after = self._bot_projection(self._read_bot(ceo, operation='actor-status-final'))
        if after != before:
            raise LocalNetworkApiError('owned CEO status restoration changed another field')

    def _bots_my(self):
        value, _ = self._request('bots-my', 'GET', '/bots/my?offset=0&limit=20')
        items = value.get('items')
        expected = {self._role_id(role) for role in ROLES}
        ids = [row.get('bot_uuid') for row in items if isinstance(row, dict)] if isinstance(items, list) else []
        if (not isinstance(items, list) or len(items) > 20 or value.get('offset') != 0
                or value.get('limit') != 20 or value.get('total') != len(items)
                or len(ids) != len(items) or len(set(ids)) != len(ids) or not expected <= set(ids)
                or any(row.get('created_by') != self.identities.human for row in items)):
            raise LocalNetworkApiError('owned Bot listing differs')

    def _bots_paged(self):
        value, _ = self._request('bots-paged', 'GET', '/bots/paged?limit=5&offset=0')
        items = value.get('items')
        if (not isinstance(items, list) or not 1 <= len(items) <= 5
                or value.get('offset') != 0 or value.get('limit') != 5
                or not isinstance(value.get('total'), int) or value['total'] < len(items)
                or any(not isinstance(row, dict) or not isinstance(row.get('bot_uuid'), str)
                       for row in items)):
            raise LocalNetworkApiError('paged Bot listing differs')

    def _bots_query(self):
        expected = [self._role_id('CEO'), self._role_id('PM')]
        if (self._current_requirement != 'NETWORK.bots-query'
                or len(expected) != 2 or len(set(expected)) != 2):
            raise LocalNetworkApiError('bulk Bot query is outside the exact reviewed plan')
        # This legacy route returns an array. Use the bounded array helper rather
        # than widening the general object-only request surface.
        event = self._append_operation('bots-query', 'POST', '/bots/query', mutation=False)
        request = Request(
            self.identities.origin + '/bots/query', method='POST',
            data=json.dumps({'bot_uuids': expected}, separators=(',', ':')).encode(),
            headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                     'X-Request-ID': event['submitted_request_id']},
        )
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
            self._active()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            ids = [row.get('bot_uuid') for row in value if isinstance(row, dict)] if isinstance(value, list) else []
            if (status != 200 or len(raw) > MAX_RESPONSE_BYTES or len(value) != 2
                    or ids != expected or len(set(ids)) != 2
                    or any(row.get('actor_kind') != 'bot' or row.get('status') != 'online'
                           for row in value)):
                raise LocalNetworkApiError('bulk Bot query differs')
            event.update(status='ACKNOWLEDGED', http_status=200)
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': 200})
            self._save()
        except BaseException as error:
            self._invalidated = True
            event.update(status='FAILED', error_type=type(error).__name__)
            event['transitions'].append({'status': 'FAILED', 'error_type': event['error_type']})
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise LocalNetworkApiError('bulk Bot query failed; retain evidence') from None

    def _ledger_intent(self, name: str, request_id: str):
        if self.ledger['resource']['state'] != 'NONE':
            raise LocalNetworkApiError('temporary Bot registration cannot be retried')
        self.ledger['resource'] = {
            'handle': 'temporary-network-bot', 'kind': 'bot', 'owner_id': self.identities.human,
            'ownership_basis': 'HUMAN_REGISTER_TOKEN_AND_EXACT_DETAIL_READBACK',
            'registration_name': name, 'request_id': request_id, 'state': 'INTENDED',
            'resource_id': None, 'token_persisted': False,
        }
        self.ledger['status'] = 'REGISTRATION_INTENDED'
        self.ledger['transitions'].append({'status': 'REGISTRATION_INTENDED',
                                           'resource_state': 'INTENDED', 'request_id': request_id})
        self._save_ledger()

    def _ledger_unknown(self):
        self.ledger['resource']['state'] = 'UNKNOWN'
        self.ledger['status'] = 'UNKNOWN_OUTCOME_NO_CLEANUP_OR_ADOPTION'
        self.ledger['transitions'].append({'status': self.ledger['status'],
                                           'resource_state': 'UNKNOWN'})
        self._save_ledger()

    def _owned_temporary(self, operation: str) -> dict:
        self._active()
        resource = self.ledger['resource']
        if (resource.get('state') not in ('LIVE', 'ONBOARDED', 'ADMIN_REVIEWED')
                or resource.get('resource_id') != self._temporary_id
                or resource.get('owner_id') != self.identities.human
                or not self._temporary_id or not self._bot_token):
            raise LocalNetworkApiError('temporary Bot is not an exact live owned resource')
        detail = self._read_bot(self._temporary_id, operation=operation)
        if (detail.get('created_by') != self.identities.human
                or detail.get('actor_kind') != 'bot' or detail.get('env') != 'local'):
            raise LocalNetworkApiError('temporary Bot Human owner proof failed')
        return detail

    def _register_and_onboard(self):
        token_result, _ = self._request('register-token', 'GET', '/register/token')
        self._register_token = _credential(token_result.get('token'), 'registration token')
        expires = token_result.get('expires_at')
        if not isinstance(expires, int) or expires <= int(time.time() * 1000):
            raise LocalNetworkApiError('registration token expiry is invalid')
        name = 'story-agent-' + hashlib.sha256(self.runtime.run_id.encode()).hexdigest()[:16]
        request_id = 'e2e-network-' + uuid.uuid4().hex
        self._ledger_intent(name, request_id)
        if (self._current_requirement != 'NETWORK.register-onboard'
                or self.ledger['resource'].get('request_id') != request_id
                or self.ledger['resource'].get('registration_name') != name
                or not self._register_token):
            raise LocalNetworkApiError('registration dispatch is outside its durable exact intent')
        event = self._append_operation('register-owned-bot', 'POST', '/register',
                                       mutation=True, request_id=request_id)
        path = '/register?' + urlencode({'token': self._register_token, 'bot-name': name})
        request = Request(self.identities.origin + path, method='POST', data=b'{}', headers={
            'Accept': 'application/json', 'Content-Type': 'application/json', 'X-Request-ID': request_id})
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
            self._active()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            bot_id = identifier(value.get('bot_uuid'), 'registered temporary Bot ID')
            bot_token = _credential(value.get('bot_token'), 'registered temporary Bot token')
            if (status != 200 or len(raw) > MAX_RESPONSE_BYTES or value.get('bot_name') != name
                    or bot_id in {self._role_id(role) for role in ROLES}):
                raise LocalNetworkApiError('temporary Bot registration response differs')
            self._temporary_id, self._bot_token = bot_id, bot_token
            event.update(status='ACKNOWLEDGED', http_status=200, resource_binding='BOUND')
            event['transitions'].append({'status': 'ACKNOWLEDGED', 'http_status': 200,
                                         'resource_binding': 'BOUND'})
            self.ledger['resource'].update(state='LIVE', resource_id=bot_id)
            self.ledger['status'] = 'LIVE_OWNED_RESOURCE'
            self.ledger['transitions'].append({'status': 'LIVE_OWNED_RESOURCE',
                                               'resource_state': 'LIVE', 'request_id': request_id})
            self._save_ledger()
            self._save()
        except BaseException as error:
            self._invalidated = True
            event.update(status='UNKNOWN', error_type=type(error).__name__, resource_binding='UNKNOWN')
            event['transitions'].append({'status': 'UNKNOWN', 'error_type': event['error_type'],
                                         'resource_binding': 'UNKNOWN'})
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            try:
                self._ledger_unknown()
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise LocalNetworkApiError(
                'temporary Bot registration failed; no retry, cleanup, or adoption is allowed') from None

        detail = self._owned_temporary('registered-owner-proof')
        if detail.get('capabilities', {}).get('name') != name:
            raise LocalNetworkApiError('registered temporary Bot name differs')
        onboard_body = {'name': name, 'summary': 'Story-owned support agent',
                        'domains': ['support'], 'skills': ['triage'], 'scopes': ['local']}
        self._owned_temporary('onboard-owner-proof')
        onboard, _ = self._request('onboard-owned-bot', 'POST', '/bots/onboard', body=onboard_body,
                                   token=self._bot_token, mutation=True)
        if (onboard.get('bot_uuid') != self._temporary_id or onboard.get('onboarded') is not True
                or onboard.get('name') != name):
            raise LocalNetworkApiError('temporary Bot onboarding response differs')
        self.ledger['resource']['state'] = 'ONBOARDED'
        self.ledger['status'] = 'ONBOARDED_OWNED_RESOURCE'
        self.ledger['transitions'].append({'status': 'ONBOARDED_OWNED_RESOURCE',
                                           'resource_state': 'ONBOARDED'})
        self._save_ledger()

        self._owned_temporary('browser-url-owner-proof')
        query = urlencode({'token': self._bot_token, 'name': name,
                           'summary': 'Story-owned support agent'})
        browser, _ = self._request('browser-onboard-url', 'GET', '/onboard/url?' + query,
                                   safe_path='/onboard/url')
        onboard_url = browser.get('onboard_url')
        if not isinstance(onboard_url, str) or len(onboard_url) > 8192:
            raise LocalNetworkApiError('browser onboarding URL is invalid')
        parsed = urlsplit(onboard_url)
        fields = parse_qs(parsed.query, strict_parsing=True)
        if (parsed.scheme != 'http' or parsed.netloc != urlsplit(self.identities.origin).netloc
                or parsed.path != '/bcn/register' or parsed.fragment
                or fields.get('token') != [self._bot_token] or fields.get('name') != [name]
                or fields.get('summary') != ['Story-owned support agent']):
            raise LocalNetworkApiError('browser onboarding URL contract differs')

        admin_name = name + '-admin'
        self._owned_temporary('admin-owner-proof')
        admin_body = {'bot_id': self._temporary_id, 'name': admin_name,
                      'summary': 'Admin-reviewed agent', 'domains': ['support'],
                      'skills': ['triage'], 'scopes': ['local']}
        admin, _ = self._request('admin-reonboard-owned-bot', 'POST', '/admin/bots/onboard',
                                 body=admin_body, mutation=True)
        if (admin.get('bot_uuid') != self._temporary_id or admin.get('onboarded') is not True
                or admin.get('name') != admin_name):
            raise LocalNetworkApiError('temporary Bot administrative review differs')
        self.ledger['resource']['state'] = 'ADMIN_REVIEWED'
        self.ledger['status'] = 'ADMIN_REVIEWED_OWNED_RESOURCE'
        self.ledger['transitions'].append({'status': 'ADMIN_REVIEWED_OWNED_RESOURCE',
                                           'resource_state': 'ADMIN_REVIEWED'})
        self._save_ledger()
        reviewed = self._owned_temporary('admin-detail-readback')
        capabilities = reviewed.get('capabilities')
        if (not isinstance(capabilities, dict) or capabilities.get('name') != admin_name
                or capabilities.get('summary') != 'Admin-reviewed agent'):
            raise LocalNetworkApiError('temporary Bot reviewed detail differs')

        self._owned_temporary('delete-owner-proof')
        deleted, _ = self._request('delete-owned-bot', 'DELETE',
                                   '/bots/' + quote(self._temporary_id, safe=''), mutation=True)
        if deleted != {'left': True, 'bot_uuid': self._temporary_id}:
            raise LocalNetworkApiError('temporary Bot deletion response differs')
        missing, status = self._request('deleted-bot-404', 'GET',
                                        '/bots/' + quote(self._temporary_id, safe=''), expected_status=404)
        if status != 404 or not isinstance(missing, dict):
            raise LocalNetworkApiError('deleted temporary Bot was not independently absent')
        self.ledger['resource']['state'] = 'DELETED_CONFIRMED_404'
        self.ledger['status'] = 'RELEASED_EXACT_RESOURCE'
        self.ledger['resource']['token_reference'] = 'CLEARED_AFTER_USE'
        self.ledger['transitions'].append({'status': 'RELEASED_EXACT_RESOURCE',
                                           'resource_state': 'DELETED_CONFIRMED_404'})
        self._save_ledger()
        self._bot_token = ''
        self._register_token = ''
        self._assert_fixtures_unchanged()

    def _run_requirement(self, requirement: str, action):
        self._current_requirement = requirement
        try:
            action()
            row = next(item for item in self.state['assertions'] if item['id'] == requirement)
            if row['status'] != 'NOT_RUN':
                raise LocalNetworkApiError('network requirement outcome was already assigned')
            row['status'] = 'PASS'
            self._save()
        except BaseException:
            row = next(item for item in self.state['assertions'] if item['id'] == requirement)
            if row['status'] == 'NOT_RUN':
                row['status'] = 'FAIL'
                try:
                    self._save()
                except BaseException:
                    pass
            raise
        finally:
            self._current_requirement = ''

    def _execute(self):
        actions = (
            self._platform_entrypoints,
            self._actor_list,
            self._actor_search,
            self._actor_status,
            self._bots_my,
            self._bots_paged,
            self._bots_query,
            self._register_and_onboard,
        )
        for requirement, action in zip(REQUIREMENTS, actions, strict=True):
            self._run_requirement(requirement, action)

    def run(self) -> dict:
        self._active()
        self._exclusive(self.path)
        self._exclusive(self.ledger_path)
        self.state = {
            'schema_version': 1,
            'contract': CONTRACT,
            'case_id': CASE_ID,
            'run_id': self.runtime.run_id,
            'executor_digest': self.executor_digest,
            'status': 'RUNNING',
            'binding_digest': self.binding_digest,
            'binding_path': BINDING_NAME,
            'fixture_digest': self.fixture_digest,
            'assertions': [{'id': requirement, 'status': 'NOT_RUN'} for requirement in REQUIREMENTS],
            'operations': [],
            'resource_disposition': 'NO_TEMPORARY_RESOURCE_YET',
            'credentials': {'new_bot_memory': 'NOT_CAPTURED'},
            'ledger': {'path': LEDGER_NAME, 'status': 'NOT_INITIALIZED'},
            'non_target_before': {},
            'non_target_after': {},
            'owned_bot_inventory_before': [],
            'owned_bot_inventory_after': [],
            'fixture_preservation': {'status': 'NOT_RUN'},
            'full_release_status': 'NOT_CLAIMED',
            'remote_execution': 'NOT_RUN',
            'shell_harness': 'NOT_USED',
            'ready_to_release': False,
        }
        self.ledger = {
            'schema_version': 1,
            'contract': LEDGER_CONTRACT,
            'case_id': CASE_ID,
            'run_id': self.runtime.run_id,
            'executor_digest': self.executor_digest,
            'binding_digest': self.binding_digest,
            'status': 'OPEN',
            'transitions': [{'status': 'OPEN', 'resource_state': 'NONE'}],
            'resource': {'handle': 'temporary-network-bot', 'kind': 'bot', 'state': 'NONE',
                         'resource_id': None, 'token_persisted': False},
            'retry_policy': 'NO_REGISTRATION_RETRY',
            'unknown_outcome_policy': 'NO_CLEANUP_OR_ADOPTION',
            'broad_discovery_or_delete': False,
        }
        self._save()
        self._save_ledger()
        try:
            self._current_requirement = REQUIREMENTS[0]
            initial = self._fixture_snapshot('initial')
            self._current_requirement = ''
            self.state['fixture_snapshot'] = initial
            self.state['non_target_before'] = initial['roles']
            self.state['owned_bot_inventory_before'] = initial['owned_bot_ids']
            self._save()
            self._execute()
            expected = [{'id': requirement, 'status': 'PASS'} for requirement in REQUIREMENTS]
            if (self.state['assertions'] != expected
                    or self.ledger['status'] != 'RELEASED_EXACT_RESOURCE'
                    or self.ledger['resource']['state'] != 'DELETED_CONFIRMED_404'
                    or any(row['status'] != 'ACKNOWLEDGED' for row in self.state['operations'])
                    or self.state['fixture_preservation'].get('status') != 'PASS'):
                raise LocalNetworkApiError('network evidence is incomplete or out of order')
            self.state.update(
                status='LOCAL_NETWORK_PASS',
                resource_disposition='TEMPORARY_BOT_DELETED_FIXTURES_RETAINED',
                credentials={'new_bot_memory': 'CLEARED_REFERENCE_ONLY'},
                ledger={'path': LEDGER_NAME, 'status': self.ledger['status'],
                        'resource_state': self.ledger['resource']['state']},
            )
            self._save()
            return json.loads(json.dumps(self.state, allow_nan=False))
        except BaseException as error:
            cancelled = has_cancellation(error)
            self._invalidated = True
            resource_state = self.ledger['resource'].get('state', 'UNKNOWN')
            if self.ledger['status'] not in ('RELEASED_EXACT_RESOURCE',
                                             'UNKNOWN_OUTCOME_NO_CLEANUP_OR_ADOPTION'):
                self.ledger['status'] = 'BLOCKED_RETAINED_NO_CLEANUP'
                try:
                    self._invalidated = False
                    self._save_ledger()
                except BaseException:
                    pass
                finally:
                    self._invalidated = True
            self.state.update(
                status='CANCELLED' if cancelled else 'FAIL',
                resource_disposition=(
                    'UNKNOWN_NO_CLEANUP_OR_ADOPTION' if resource_state == 'UNKNOWN'
                    else 'RETAINED_FAILURE_EVIDENCE' if resource_state not in ('NONE', 'DELETED_CONFIRMED_404')
                    else 'NO_LIVE_TEMPORARY_RESOURCE_CONFIRMED'),
                credentials={'new_bot_memory': 'CLEARED_REFERENCE_ONLY'},
                ledger={'path': LEDGER_NAME, 'status': self.ledger['status'],
                        'resource_state': resource_state},
            )
            try:
                self._invalidated = False
                self._save()
            except BaseException:
                pass
            finally:
                self._invalidated = True
                self._register_token = ''
                self._bot_token = ''
            if cancelled:
                raise KeyboardInterrupt() from None
            raise LocalNetworkApiError(
                'owned-local Agent-network execution failed; inspect retained artifacts') from None
        finally:
            self._register_token = ''
            self._bot_token = ''
