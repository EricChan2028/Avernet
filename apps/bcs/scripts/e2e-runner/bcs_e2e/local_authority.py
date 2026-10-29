"""Finite owned-local Bot-authority user journey against one owned runtime.

The upstream shell story drives the trusted registration surface and the
unified Bot-authority surface end to end: the owner human registers its
disposable fixture Bot, the transfer target human registers its own Bot
(which seeds the target ``human_`` actor row), the owner reads ownership,
exercises the manager single-item surface with the ``owner_not_manager``
polarity, proves the mine-ensured Human (created only by the public
``bots/mine`` read) is a valid strict-edit target while a never-seen user
stays unknown, walks the ownership-transfer chain, and reads both transfer
inboxes. The owned adapter replays the frozen 16-obligation/24-call matrix
once against a strict owned runtime, never retries a business step, never
adopts another environment's record, and never extracts a credential into
the frozen journal.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
from http.client import HTTPConnection
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from .inventory import current_manifest, executor_digest
from .local_authority_contract import (
    BINDING,
    CALLS,
    CASE_ID,
    EXPECTED_HTTP_CALLS,
    JOURNAL,
    PRIMARY,
    REQUIREMENTS,
    STAFF_B,
    STAFF_MINE,
    STATUS,
    SUITE,
    authority_source_binding,
)
from .local_network_reporting import _exclusive
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, unique_keys
from .run_state import RunStateError, read, write

OWNER_BOT_NAME = 'authority-story-owner-'
TARGET_BOT_NAME = 'authority-story-target-'
TARGET_OWNER_ACTOR_ID = 'human_' + STAFF_B
STRICT_AUTH = {'chain': ['local', 'session'], 'require_authentication': False,
               'allow_mock_headers': True}
FORBIDDEN_JOURNAL_SUBSTRINGS = ('bot_token', 'X-Avernet-Principal', 'register?token')
RENDERED_TOKENS = ('{bot_id}', '{owner_user_id}', '{target_user_id}',
                   '{mine_user_id}', '{never_user_id}', '{target_owner_actor_id}')


def require(value: object, message: str = 'Bot-authority finite contract differs') -> None:
    if not value:
        raise RunStateError(message)


class OwnedLocalAuthority:
    """Sixteen ordered obligations with strict journal transitions."""

    def __init__(self, runtime):
        require(type(runtime) is OwnedBcsProcess and runtime.provider_sink is None,
                'authority requires the bare owned BCS runtime')
        require(runtime.store_messages is False,
                'authority requires a message-store-disabled owned runtime')
        config = json.loads(runtime.inputs.config_path.read_text())
        require(config.get('store_messages') is False,
                'authority requires a message-store-disabled owned runtime configuration')
        require(config.get('auth') == STRICT_AUTH,
                'authority requires the strict mock-header admission configuration')
        target = urlsplit(runtime.inputs.origin)
        require(target.scheme == 'http' and target.hostname == '127.0.0.1'
                and target.port is not None
                and target.netloc == '127.0.0.1:' + str(target.port)
                and not target.path and not target.query and not target.fragment,
                'authority requires an owned loopback origin')
        self.runtime = runtime
        self.root, self.run_id, self.port = runtime.root, runtime.run_id, target.port
        self.path = self.root / JOURNAL
        self.binding_path = self.root / BINDING
        self.source = executor_digest()
        self.source_binding = authority_source_binding()
        self.binding = read(self.binding_path)
        self.config_digest = file_sha256(runtime.inputs.config_path)
        expected = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
                    'case_id': CASE_ID, 'run_id': self.run_id,
                    'executor_digest': self.source, 'binary_kind': 'bcs',
                    'bcs_binary_sha256': runtime.binary_sha256, 'auto_build': False,
                    'store_messages': False, 'source_binding': self.source_binding,
                    'selected_requirement_ids': list(REQUIREMENTS),
                    'legacy_inventory': current_manifest()['legacy_inventory'],
                    'environment': self.source_binding['environment'],
                    'runtime_dir': str(self.root.resolve())}
        require(self.binding == expected and not self.binding_path.is_symlink(),
                'authority frozen binding differs')
        self.owner_human_id = runtime.inputs.human_id
        self.owner_actor_id = 'human_' + self.owner_human_id
        self.target_user_id = STAFF_B
        self.target_actor_id = TARGET_OWNER_ACTOR_ID
        self.mine_user_id = STAFF_MINE
        self.mine_actor_id = 'human_' + STAFF_MINE
        self.never_user_id = 'never-seen-' + uuid4().hex[:12]
        self.owner_bot_uuid: str | None = None
        self.target_bot_uuid: str | None = None
        self.http_call_count = 0
        self.state = None
        self._digest = ''
        self._attempted = False
        self._active()
        require(not self.path.exists() and not self.path.is_symlink(),
                'authority journal already exists')

    # --- drift and integrity fences -------------------------------------
    def _active(self) -> None:
        runtime = self.runtime
        require(type(runtime) is OwnedBcsProcess and runtime.provider_sink is None
                and runtime.root == self.root and runtime.run_id == self.run_id
                and executor_digest() == self.source
                and authority_source_binding() == self.source_binding
                and file_sha256(runtime.inputs.config_path) == self.config_digest
                and read(self.binding_path) == self.binding,
                'authority runtime or frozen binding drifted')
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest,
                    'authority evidence changed')

    def _save(self) -> None:
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest,
                    'authority evidence changed')
        raw = json.dumps(self.state)
        for forbidden in FORBIDDEN_JOURNAL_SUBSTRINGS:
            require(forbidden not in raw, 'credential in authority evidence')
        write(self.path, self.state)
        self._digest = fingerprint(self.state)

    def _begin_op(self, requirement: str) -> dict:
        self._active()
        method, template, actor = PRIMARY[requirement]
        # The frozen journal records the path actually replayed for the
        # obligation, so identity tokens are resolved before the INTENDED
        # row is published (the registration path is token-free and every
        # later obligation begins with both fixture Bots already created).
        path = self._render(template) if template is not None else None
        row = {'operation': requirement, 'method': method, 'path': path, 'actor': actor,
               'status': 'INTENDED', 'transitions': ['INTENDED'], 'calls': []}
        self.state['operations'].append(row)
        self._save()
        return row

    def _finish_op(self, requirement: str) -> None:
        row = self.state['operations'][-1]
        require(row['operation'] == requirement and row['status'] == 'INTENDED',
                'authority obligation row is not the live INTENDED step')
        row['status'] = 'VERIFIED'
        row['transitions'].append('VERIFIED')
        self.state['assertions'].append({'id': requirement, 'status': 'PASS'})
        self._save()

    # --- frozen call-matrix replay --------------------------------------
    def _request_headers(self, label: str) -> dict[str, str]:
        if label in ('register_owner', 'register_target'):
            staff = self.owner_human_id if label == 'register_owner' else STAFF_B
            return {'X-Mock-User-Id': staff, 'X-Mock-Nick-Name': staff}
        if label in ('owner', 'target', 'mine'):
            user_id = {'owner': self.owner_human_id, 'target': self.target_user_id,
                       'mine': self.mine_user_id}[label]
            return {'X-Avernet-Principal': self._principal(user_id),
                    'Content-Type': 'application/json'}
        raise RunStateError('unknown authority actor label')

    def _principal(self, user_id: str) -> str:
        key = self.runtime.inputs.environment['AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE']
        require(isinstance(key, str) and key,
                'authority owned runtime lost its principal signing-key reference')
        now = int(time.time())
        header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
        claims = {'iss': 'gateway', 'aud': 'bcs', 'iat': now, 'exp': now + 60,
                  'principals': [{'type': 'user', 'tenant': 'bcs-e2e',
                                  'subject': {'id': user_id, 'username': user_id,
                                              'display_name': None, 'full_name': None,
                                              'tenant_id': 'bcs-e2e'}}]}

        def encode(value: dict) -> str:
            return base64.urlsafe_b64encode(
                json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
            ).rstrip(b'=').decode('ascii')

        signing_input = (encode(header) + '.' + encode(claims)).encode('ascii')
        digest = hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
        return signing_input.decode('ascii') + '.' + (
            base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii'))

    def _render(self, template: str) -> str:
        value = template
        for token in RENDERED_TOKENS:
            if token not in value:
                continue
            actual = {'{bot_id}': self.owner_bot_uuid,
                      '{owner_user_id}': self.owner_human_id,
                      '{target_user_id}': self.target_user_id,
                      '{mine_user_id}': self.mine_user_id,
                      '{never_user_id}': self.never_user_id,
                      '{target_owner_actor_id}': self.target_actor_id}[token]
            require(isinstance(actual, str) and actual,
                    'authority path template used an unbound identity')
            value = value.replace(token, actual)
        require('{' not in value and '}' not in value,
                'authority path template left an unbound placeholder')
        return value

    def _exchange(self, method: str, url_path: str, headers: dict[str, str]) -> tuple[int, object, bytes]:
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            deadline = time.monotonic() + 5
            connection.request(method, url_path, body=None, headers=headers)
            sock = connection.sock
            response = connection.getresponse()
            raw = bytearray()
            with response:
                while True:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'authority response deadline exceeded')
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(4096, 65537 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    require(len(raw) <= 65536, 'authority response exceeds budget')
                require(response.length in (None, 0), 'authority response truncated')
                code = response.status
            value = json.loads(raw.decode(), object_pairs_hook=unique_keys) if raw else None
            return code, value, bytes(raw)
        finally:
            connection.close()

    def _call(self, requirement: str, index: int, *, project,
              url_query: dict[str, str] | None = None) -> tuple[int, object, bytes]:
        self._active()
        operation = self.state['operations'][-1]
        require(operation['operation'] == requirement and len(operation['calls']) == index,
                'authority frozen obligation order differs')
        method, template, expect_status, label, query = CALLS[requirement][index]
        rendered = self._render(template)
        row = {'method': method, 'path': rendered, 'expect_status': expect_status,
               'actor': label, 'query': query,
               'submitted_request_id': 'e2e-authority-' + uuid4().hex,
               'status': 'INTENDED', 'transitions': ['INTENDED']}
        operation['calls'].append(row)
        self._save()
        self.http_call_count += 1
        headers = self._request_headers(label)
        headers['X-Request-ID'] = 'e2e-authority-' + uuid4().hex
        wire_query = url_query if url_query is not None else query
        target = rendered + ('?' + urlencode(wire_query) if wire_query else '')
        code, value, raw = self._exchange(method, target, headers)
        require(code == expect_status, 'authority frozen HTTP expectation differs')
        observed = project(code, value, raw)
        require(isinstance(observed, dict), 'authority call projection must be a bounded object')
        row['http_status'] = code
        row['observed'] = observed
        row['status'] = 'VERIFIED'
        row['transitions'].append('VERIFIED')
        self.state['http_call_count'] = self.http_call_count
        self._save()
        return code, value, raw

    # --- projection helpers ---------------------------------------------
    def _envelope_data(self, value: object) -> dict:
        require(isinstance(value, dict) and isinstance(value.get('data'), dict),
                'authority business response envelope differs')
        return value['data']

    def _project_mint(self, code: int, value: object, raw: bytes) -> dict:
        require(isinstance(value, dict) and isinstance(value.get('token'), str)
                and bool(value['token']),
                'authority register token minting differs')
        return {'token_minted': True}

    def _project_register(self, code: int, value: object, raw: bytes) -> dict:
        require(isinstance(value, dict) and isinstance(value.get('bot_uuid'), str)
                and bool(value['bot_uuid'])
                and isinstance(value.get('bot_token'), str) and bool(value['bot_token']),
                'authority trusted registration commits a disposable Bot')
        return {'bot_registered': True, 'bot_uuid': value['bot_uuid']}

    def _project_ownership(self, code: int, value: object, raw: bytes) -> dict:
        data = self._envelope_data(value)
        require(data.get('bot_id') == self.owner_bot_uuid
                and data.get('owner_user_id') == self.owner_human_id,
                'authority ownership view names the registered Bot and owner')
        return {'bot_id': data['bot_id'], 'owner_user_id': data['owner_user_id']}

    def _project_transferred_ownership(self, code: int, value: object, raw: bytes) -> dict:
        data = self._envelope_data(value)
        require(data.get('bot_id') == self.owner_bot_uuid
                and data.get('owner_user_id') == self.target_user_id,
                'ownership transfer moved the owner')
        return {'bot_id': data['bot_id'], 'owner_user_id': data['owner_user_id']}

    # --- the sixteen frozen obligations ---------------------------------
    def _obligation_trusted_registration(self) -> None:
        requirement = REQUIREMENTS[0]
        operation = self._begin_op(requirement)
        bots: list[str] = []
        for index, (staff, prefix) in enumerate(
                ((self.owner_human_id, OWNER_BOT_NAME), (STAFF_B, TARGET_BOT_NAME))):
            name = prefix + self.run_id
            require(2 <= len(name) <= 64 and name.isascii()
                    and all(33 <= ord(character) < 127 for character in name),
                    'authority Bot name is not a bounded ASCII identifier')
            _, minted, _ = self._call(requirement, 2 * index, project=self._project_mint)
            require(isinstance(minted, dict)
                    and isinstance(minted.get('token'), str) and bool(minted['token']),
                    'authority register token minting differs')
            _, registered, _ = self._call(
                requirement, 2 * index + 1,
                url_query={'token': minted['token'], 'bot-name': name},
                project=self._project_register)
            bots.append(registered['bot_uuid'])
        require(len(bots) == 2 and bots[0] and bots[1] and bots[0] != bots[1],
                'authority registration must commit two distinct disposable Bots')
        self.owner_bot_uuid, self.target_bot_uuid = bots[0], bots[1]
        operation['observed'] = {'bots': list(bots)}
        self.state['resources']['created'] = list(bots)
        self._finish_op(requirement)

    def _obligation_ownership_read(self) -> None:
        requirement = REQUIREMENTS[1]
        operation = self._begin_op(requirement)
        _, value, _ = self._call(requirement, 0, project=self._project_ownership)
        operation['observed'] = {'bot_id': value['data']['bot_id'],
                                 'owner_user_id': value['data']['owner_user_id']}
        self._finish_op(requirement)

    def _obligation_empty_manager_page(self) -> None:
        requirement = REQUIREMENTS[2]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('total') == 0, 'authority manager page starts empty')
            return {'total': data['total']}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'total': value['data']['total']}
        self._finish_op(requirement)

    def _obligation_owner_not_manager_polarity(self) -> None:
        requirement = REQUIREMENTS[3]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('error_code') == 'owner_not_manager',
                    'authority owner-as-manager add keeps the stable polarity')
            return {'error_code': data['error_code']}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'error_code': value['data']['error_code']}
        self._finish_op(requirement)

    def _obligation_manager_add_list_remove_idempotent(self) -> None:
        requirement = REQUIREMENTS[4]
        operation = self._begin_op(requirement)

        def project_add(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('added') == 1, 'authority manager add reports one added')
            return {'added': data['added']}

        def project_list(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            items = data.get('items')
            require(data.get('total') == 1 and isinstance(items, list)
                    and self.target_user_id in items,
                    'authority manager page exposes the added manager')
            return {'total': data['total'], 'items': list(items)}

        def project_remove(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('removed') == 1, 'authority manager removal reports one removed')
            return {'removed': data['removed']}

        def project_absent(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('removed') == 0,
                    'authority absent manager removal reports no change')
            return {'removed': data['removed']}

        for index, project in enumerate((project_add, project_list, project_remove, project_absent)):
            self._call(requirement, index, project=project)
        operation['observed'] = {'added': 1, 'total': 1, 'removed': 1, 'removed_absent': 0}
        self._finish_op(requirement)

    def _obligation_manager_restore(self) -> None:
        requirement = REQUIREMENTS[5]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('added') == 1, 'authority manager restore reports one added')
            return {'added': data['added']}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'added': value['data']['added']}
        self._finish_op(requirement)

    def _obligation_mine_ensured_human_read(self) -> None:
        requirement = REQUIREMENTS[6]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            require(code == 200, 'authority mine-ensured human reads its own mine')
            return {'mine_user_id': self.mine_user_id}

        self._call(requirement, 0, project=project)
        operation['observed'] = {'mine_user_id': self.mine_user_id}
        self._finish_op(requirement)

    def _obligation_mine_ensured_manager_add(self) -> None:
        requirement = REQUIREMENTS[7]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('added') == 1,
                    'authority mine-ensured human is a valid manager add target')
            return {'added': data['added']}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'added': value['data']['added']}
        self._finish_op(requirement)

    def _obligation_never_seen_manager_unknown(self) -> None:
        requirement = REQUIREMENTS[8]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('error_code') == 'bot_authority_human_unknown',
                    'authority never-seen human stays unknown')
            return {'error_code': data['error_code']}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'error_code': value['data']['error_code']}
        self._finish_op(requirement)

    def _obligation_mine_ensured_manager_remove(self) -> None:
        requirement = REQUIREMENTS[9]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('removed') == 1,
                    'authority mine-ensured manager removal reports one removed')
            return {'removed': data['removed']}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'removed': value['data']['removed']}
        self._finish_op(requirement)

    def _obligation_transfer_initiate_refresh(self) -> None:
        requirement = REQUIREMENTS[10]
        operation = self._begin_op(requirement)
        window: list[int] = []

        def project_initiate(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            expires = data.get('expires_at')
            require(data.get('status') == 'pending'
                    and data.get('target_owner_user_id') == self.target_user_id
                    and type(expires) is int,
                    'authority initiated transfer is pending and names the target')
            window.append(expires)
            return {'status': 'pending', 'target_owner_user_id': self.target_user_id,
                    'expires_at': expires}

        def project_refresh(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            expires = data.get('expires_at')
            require(type(expires) is int and window and expires >= window[0]
                    and expires - window[0] < 300000,
                    'authority transfer refresh renews without shrinking the window')
            window.append(expires)
            return {'expires_at': expires}

        self._call(requirement, 0, project=project_initiate)
        self._call(requirement, 1, project=project_refresh)
        require(len(window) == 2, 'authority transfer window evidence is incomplete')
        operation['observed'] = {'status': 'pending',
                                 'target_owner_user_id': self.target_user_id,
                                 'initiated_expires_at': window[0],
                                 'refreshed_expires_at': window[1]}
        self._finish_op(requirement)

    def _obligation_transfer_read_accept(self) -> None:
        requirement = REQUIREMENTS[11]
        operation = self._begin_op(requirement)

        def project_read(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('status') == 'pending',
                    'authority target human reads the pending transfer')
            return {'status': data['status']}

        def project_accept(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            require(data.get('status') == 'accepted',
                    'authority accept records the accepted status')
            return {'status': data['status']}

        self._call(requirement, 0, project=project_read)
        self._call(requirement, 1, project=project_accept)
        operation['observed'] = {'read_status': 'pending', 'accept_status': 'accepted'}
        self._finish_op(requirement)

    def _obligation_transferred_ownership(self) -> None:
        requirement = REQUIREMENTS[12]
        operation = self._begin_op(requirement)
        _, value, _ = self._call(requirement, 0, project=self._project_transferred_ownership)
        operation['observed'] = {'bot_id': value['data']['bot_id'],
                                 'owner_user_id': value['data']['owner_user_id']}
        self._finish_op(requirement)

    def _obligation_transferred_manager_ledger(self) -> None:
        requirement = REQUIREMENTS[13]
        operation = self._begin_op(requirement)

        def project(code: int, value: object, raw: bytes) -> dict:
            data = self._envelope_data(value)
            owner = data.get('owner')
            require(isinstance(owner, dict) and owner.get('owner_user_id') == self.target_user_id,
                    'authority transferred ledger names the new owner')
            items = data.get('items')
            require(isinstance(items, list) and self.owner_human_id in items,
                    'authority transferred ledger keeps the original owner as a plain manager')
            return {'owner_user_id': owner['owner_user_id'], 'manager_items': list(items)}

        _, value, _ = self._call(requirement, 0, project=project)
        operation['observed'] = {'owner_user_id': value['data']['owner']['owner_user_id'],
                                 'manager_items': value['data']['items']}
        self._finish_op(requirement)

    def _obligation_transfer_inboxes(self) -> None:
        requirement = REQUIREMENTS[14]
        operation = self._begin_op(requirement)

        def project_contains(code: int, value: object, raw: bytes) -> dict:
            require(self.owner_bot_uuid.encode() in raw,
                    'authority transfer inbox must contain the accepted Bot')
            return {'contains_bot': True}

        self._call(requirement, 0, project=project_contains)
        self._call(requirement, 1, project=project_contains)
        operation['observed'] = {'received_contains_bot': True, 'sent_contains_bot': True}
        self._finish_op(requirement)

    def _obligation_story_scoped_disposition(self) -> None:
        requirement = REQUIREMENTS[15]
        operation = self._begin_op(requirement)
        require(self.http_call_count == EXPECTED_HTTP_CALLS,
                'authority replay must consume the frozen call matrix exactly once')
        require(self.state['resources']['created'] == [self.owner_bot_uuid, self.target_bot_uuid],
                'authority retained resource disposition differs from the two registered Bots')
        operation['observed'] = {'http_call_count': self.http_call_count,
                                 'bots_retained': len(self.state['resources']['created'])}
        self._finish_op(requirement)

    OBLIGATIONS = (
        _obligation_trusted_registration,
        _obligation_ownership_read,
        _obligation_empty_manager_page,
        _obligation_owner_not_manager_polarity,
        _obligation_manager_add_list_remove_idempotent,
        _obligation_manager_restore,
        _obligation_mine_ensured_human_read,
        _obligation_mine_ensured_manager_add,
        _obligation_never_seen_manager_unknown,
        _obligation_mine_ensured_manager_remove,
        _obligation_transfer_initiate_refresh,
        _obligation_transfer_read_accept,
        _obligation_transferred_ownership,
        _obligation_transferred_manager_ledger,
        _obligation_transfer_inboxes,
        _obligation_story_scoped_disposition,
    )

    # --- lifecycle --------------------------------------------------------
    def run(self) -> dict:
        require(not self._attempted, 'authority execution cannot repeat')
        self._attempted = True
        self._active()
        self.state = {'schema_version': 1, 'run_id': self.run_id, 'case_id': CASE_ID,
                      'status': 'RUNNING', 'executor_digest': self.source,
                      'binding_digest': fingerprint(self.binding),
                      'source_binding_digest': fingerprint(self.source_binding),
                      'owner_human_id': self.owner_human_id,
                      'owner_actor_id': self.owner_actor_id,
                      'target_user_id': self.target_user_id,
                      'target_actor_id': self.target_actor_id,
                      'mine_user_id': self.mine_user_id,
                      'mine_actor_id': self.mine_actor_id,
                      'never_user_id': self.never_user_id, 'http_call_count': 0,
                      'resources': {'created': [], 'deleted': [], 'unknown': []},
                      'resource_disposition': 'UNVERIFIED', 'operations': [],
                      'assertions': []}
        _exclusive(self.path, self.state)
        self._digest = fingerprint(self.state)
        try:
            for obligation in self.OBLIGATIONS:
                obligation(self)
            require(self.http_call_count == EXPECTED_HTTP_CALLS
                    and len(self.state['operations']) == len(REQUIREMENTS)
                    and len(self.state['assertions']) == len(REQUIREMENTS),
                    'authority frozen obligation coverage differs')
            self.state['status'] = STATUS
            self.state['resource_disposition'] = 'TWO_BOTS_CREATED_AND_RETAINED'
        except BaseException as error:
            self.state.update(status='BLOCKED', error_type=type(error).__name__)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Bot-authority Story blocked; no retry or cleanup') from None
        finally:
            self._save()
        return copy.deepcopy(self.state)
