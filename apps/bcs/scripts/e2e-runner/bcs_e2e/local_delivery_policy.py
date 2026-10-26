"""Finite human delivery-policy CAS Story against one owned runtime.

The parent story proves administrative delivery policy is
human-only and optimistically versioned: Bot tokens are denied
read and write, the human replaces the policy with the matching
expected version, a stale expect conflicts without mutating
state, and the original policy is restored exactly. The owned
adapter never retries a business step, never adopts another
environment's policy record, and never extracts credentials into
the frozen journal.
"""
from __future__ import annotations

import copy
from http.client import HTTPConnection
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit

from .inventory import current_manifest, executor_digest
from .local_delivery_policy_contract import (
    CASE_ID,
    REQUIREMENTS,
    SUITE,
    delivery_policy_source_binding,
)
from .local_network_reporting import _exclusive
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO',)
POLICY_PATH = '/admin/message-delivery/policy'
# Replayed actor per obligation index: indices 1 and 2 drive the
# Bot denial probes with the driver Bot's session token.
BOT_ACTOR_INDEXES = (1, 2)
PUT_REQUIREMENTS = ('DELIVERY.bot-write-denied', 'DELIVERY.cas-update',
                    'DELIVERY.stale-rejected', 'DELIVERY.restore')
CONFLICT_ERROR = 'delivery_policy_version_conflict'


def require(value, message='Delivery policy finite contract differs'):
    if not value:
        raise RunStateError(message)


class OwnedLocalDeliveryPolicy:
    """Ten ordered HTTP obligations with strict journal transitions."""

    def __init__(self, identities):
        require(type(identities) is OwnedLocalRoleIdentity
                and identities.fixture_set == 'workspace'
                and identities.roles == ROLES and set(identities._verified) == set(ROLES),
                'Delivery policy requires freshly verified owned workspace identities')
        identities._require_roles(ROLES)
        self.identities, self.runtime = identities, identities.runtime
        require(type(self.runtime) is OwnedBcsProcess and self.runtime.provider_sink is None)
        self.root, self.run_id = self.runtime.root, self.runtime.run_id
        self.path = self.root/'local-delivery-policy.json'
        self.binding_path = self.root/'delivery-policy-binding.json'
        self.source = executor_digest()
        self.source_binding = delivery_policy_source_binding()
        self.binding = read(self.binding_path)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.config_digest = file_sha256(self.runtime.inputs.config_path)
        config = json.loads(self.runtime.inputs.config_path.read_text())
        require(config.get('store_messages') is False,
                'Delivery policy requires a message-store-disabled owned runtime')
        require(config.get('auth', {}).get('mock_user_id') == identities.human
                and config.get('auth', {}).get('allow_mock_headers') is False,
                'Delivery policy requires the ambient human identity binding')
        self.human = identities.human
        self.actor = 'human_'+identities.human
        target = urlsplit(identities.origin)
        require(target.scheme == 'http' and target.hostname == '127.0.0.1'
                and target.port and target.netloc == '127.0.0.1:'+str(target.port)
                and not target.path and not target.query and not target.fragment,
                'Delivery policy requires an owned local origin')
        self.port = target.port
        self.state = None
        self._digest = ''
        self._attempted = False
        self._active()
        require(not self.path.exists() and not self.path.is_symlink(),
                'Delivery policy journal already exists')

    def _active(self):
        self.identities._require_roles(ROLES)
        require(self.runtime.root == self.root and self.runtime.run_id == self.run_id
                and executor_digest() == self.source
                and delivery_policy_source_binding() == self.source_binding
                and file_sha256(self.runtime.inputs.config_path) == self.config_digest
                and fingerprint(read(self.identities.fixtures.path)) == self.fixture_digest)
        expected = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
                    'case_id': CASE_ID, 'run_id': self.run_id,
                    'executor_digest': self.source,
                    'bcs_binary_sha256': self.runtime.binary_sha256,
                    'source_binding': self.source_binding,
                    'selected_requirement_ids': list(REQUIREMENTS),
                    'legacy_inventory': current_manifest()['legacy_inventory'],
                    'runtime_dir': str(self.root.resolve()), 'store_messages': False}
        require(self.binding == expected and read(self.binding_path) == expected
                and not self.binding_path.is_symlink(),
                'Delivery policy frozen binding differs')
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest,
                    'Delivery policy evidence changed')

    def _save(self):
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest,
                    'Delivery policy evidence changed')
        raw = json.dumps(self.state)
        require(not any(token in raw for _, token in self.identities._entries.values()),
                'credential in delivery policy evidence')
        write(self.path, self.state)
        self._digest = fingerprint(self.state)

    def _spec(self, index):
        """Method and path for obligation index, before the row exists."""
        method = 'PUT' if REQUIREMENTS[index] in PUT_REQUIREMENTS else 'GET'
        return method, POLICY_PATH

    def _wire(self, index, row, body):
        self._active()
        require(self.state is not None and len(self.state['operations']) == index+1
                and self.state['operations'][-1] is row
                and row['status'] == 'INTENDED'
                and row['submitted_request_id'] not in self._previous_claim_ids(index),
                'Delivery policy operation row is not the intended next step')
        method, path = self._spec(index)
        require(row['operation'] == REQUIREMENTS[index] and row['method'] == method
                and row['path'] == path and row['actor'] == self._actor(index),
                'Delivery policy step contract differs')
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            deadline = time.monotonic()+5
            payload = json.dumps(body).encode() if body is not None else None
            headers = {'Content-Type': 'application/json',
                       'X-Request-ID': row['submitted_request_id']}
            if index in BOT_ACTOR_INDEXES:
                headers['X-BCS-Bot-Token'] = self.identities._entries['CEO'][1]
            connection.request(method, path, body=payload, headers=headers)
            sock = connection.sock
            response = connection.getresponse()
            raw = bytearray()
            with response:
                while True:
                    remaining = deadline-time.monotonic()
                    require(remaining > 0, 'Delivery policy response deadline exceeded')
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(4096, 65537-len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    require(len(raw) <= 65536, 'Delivery policy response exceeds budget')
                require(response.length in (None, 0), 'Delivery policy response truncated')
                code = response.status
            value = json.loads(raw.decode(), object_pairs_hook=unique_keys) if raw else None
            return code, value
        finally:
            connection.close()

    def _actor(self, index):
        return 'bot:'+self.identities.actor_id('CEO') if index in BOT_ACTOR_INDEXES else self.actor

    def _previous_claim_ids(self, index):
        return {row['submitted_request_id'] for row in self.state['operations'][:index]}

    def _step(self, index, body):
        self._active()
        method, path = self._spec(index)
        require(self.state['assertions'] == [{'id': x, 'status': 'PASS'} for x in REQUIREMENTS[:index]],
                'Delivery policy step order differs')
        row = {'operation': REQUIREMENTS[index], 'method': method, 'path': path,
               'actor': self._actor(index),
               'submitted_request_id': 'e2e-delivery-'+uuid.uuid4().hex,
               'status': 'INTENDED', 'transitions': ['INTENDED']}
        if body is not None:
            row['body_digest'] = fingerprint(body)
        self.state['operations'].append(row)
        self._save()
        try:
            code, value = self._wire(index, row, body)
            row['http_status'] = code
            observed = self._assert_step(index, code, value)
            row.update(status='VERIFIED', observed=observed)
            row['transitions'].append('VERIFIED')
            self.state['assertions'].append({'id': REQUIREMENTS[index], 'status': 'PASS'})
            self._save()
            return value
        except BaseException:
            row['status'] = 'UNKNOWN'
            row['transitions'].append('UNKNOWN')
            self._save()
            raise

    def _assert_step(self, index, code, value):
        requirement = REQUIREMENTS[index]
        if requirement == 'DELIVERY.human-read':
            require(code == 200 and isinstance(value, dict), 'human policy read contract differs')
            version = value.get('version')
            require(isinstance(version, int) and not isinstance(version, bool) and version >= 0,
                    'delivery policy version is not a non-negative integer')
            require(isinstance(value.get('policy'), dict) and isinstance(value.get('updated_by'), str)
                    and isinstance(value.get('updated_at_ms'), int),
                    'delivery policy record is incomplete')
            return {'version': version, 'policy': value['policy']}
        if requirement in ('DELIVERY.bot-read-denied', 'DELIVERY.bot-write-denied'):
            require(code == 403, 'Bot credentials are not denied delivery-policy administration')
            require(value is None, 'denied Bot administration must not expose a policy payload')
            return {'http_status': code}
        if requirement == 'DELIVERY.denied-preserves':
            require(code == 200 and value.get('version') == self.state['policy_version']
                    and value.get('policy') == self.state['original_policy'],
                    'denied Bot writes must preserve the effective policy')
            return {'version': value.get('version'), 'policy': value.get('policy')}
        if requirement == 'DELIVERY.cas-update':
            require(code == 200 and value.get('version') == self.state['policy_version']+1
                    and value.get('updated_by') == self.actor
                    and value.get('policy') == self.state['updated_policy'],
                    'human CAS policy replacement differs')
            return {'version': value.get('version'), 'updated_by': value.get('updated_by'),
                    'policy': value.get('policy')}
        if requirement == 'DELIVERY.readback':
            require(code == 200 and value.get('version') == self.state['policy_version']+1
                    and value.get('policy') == self.state['updated_policy'],
                    'policy read-back after replacement differs')
            return {'version': value.get('version'), 'policy': value.get('policy')}
        if requirement == 'DELIVERY.stale-rejected':
            require(code == 409 and value == {'error': CONFLICT_ERROR},
                    'stale delivery-policy replacement is not rejected with the conflict contract')
            return {'http_status': code, 'error': CONFLICT_ERROR}
        if requirement == 'DELIVERY.conflict-preserves':
            require(code == 200 and value.get('version') == self.state['policy_version']+1
                    and value.get('policy') == self.state['updated_policy'],
                    'stale-write conflict must preserve the effective policy')
            return {'version': value.get('version'), 'policy': value.get('policy')}
        if requirement == 'DELIVERY.restore':
            require(code == 200 and value.get('version') == self.state['policy_version']+2
                    and value.get('updated_by') == self.actor
                    and value.get('policy') == self.state['original_policy'],
                    'policy restoration differs')
            return {'version': value.get('version'), 'updated_by': value.get('updated_by'),
                    'policy': value.get('policy')}
        if requirement == 'DELIVERY.restored':
            require(code == 200 and value.get('version') == self.state['policy_version']+2
                    and value.get('policy') == self.state['original_policy'],
                    'restored policy read-back differs')
            return {'version': value.get('version'), 'policy': value.get('policy')}
        raise RunStateError('unknown delivery policy obligation')

    def run(self):
        require(not self._attempted, 'Delivery policy execution cannot repeat')
        self._attempted = True
        self._active()
        self.state = {'schema_version': 1, 'run_id': self.run_id, 'case_id': CASE_ID,
                      'status': 'RUNNING', 'executor_digest': self.source,
                      'binding_digest': fingerprint(self.binding),
                      'source_binding_digest': fingerprint(self.source_binding),
                      'human_actor': self.actor,
                      'driver_bot_id': self.identities.actor_id('CEO'),
                      'conflict_error': CONFLICT_ERROR, 'operations': [], 'assertions': [],
                      'policy_version': None, 'original_policy': None, 'updated_policy': None,
                      'policy_change': None,
                      'resources': {'created': [], 'deleted': [], 'unknown': []},
                      'resource_disposition': 'UNVERIFIED'}
        _exclusive(self.path, self.state)
        self._digest = fingerprint(self.state)
        try:
            self._step(0, None)
            self.state['policy_version'] = self.state['operations'][0]['observed']['version']
            self.state['original_policy'] = copy.deepcopy(
                self.state['operations'][0]['observed']['policy'])
            self._save()
            self._step(1, None)
            self._step(2, self._replace_body(self.state['policy_version'],
                                             self.state['original_policy']))
            self._step(3, None)
            updated = copy.deepcopy(self.state['original_policy'])
            target = 23 if updated.get('max_context_messages') != 23 else 24
            updated['max_context_messages'] = target
            self.state['updated_policy'] = updated
            self.state['policy_change'] = {
                'field': 'max_context_messages',
                'from': self.state['original_policy'].get('max_context_messages'),
                'to': target}
            self._save()
            self._step(4, self._replace_body(self.state['policy_version'], updated))
            self._step(5, None)
            self._step(6, self._replace_body(self.state['policy_version'], updated))
            self._step(7, None)
            self._step(8, self._replace_body(self.state['policy_version']+1,
                                             self.state['original_policy']))
            self._step(9, None)
            self.state.update(status='LOCAL_DELIVERY_POLICY_PASS',
                              resource_disposition='NO_BOT_CREATED_NO_GROUP_DELETED_POLICY_RESTORED')
        except BaseException as error:
            self.state.update(status='BLOCKED', error_type=type(error).__name__)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Delivery policy Story blocked; no retry or cleanup') from None
        finally:
            self._save()
        return copy.deepcopy(self.state)

    def _replace_body(self, expected_version, policy):
        return {'expected_version': expected_version, 'policy': copy.deepcopy(policy)}
