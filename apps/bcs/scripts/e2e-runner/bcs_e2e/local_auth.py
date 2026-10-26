"""Test-only Principal signing tied to one live loopback runtime, never remote."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from .local_fixtures import OwnedLocalFixtures
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, validate
from .run_state import read


class LocalAuthError(RuntimeError):
    pass


class OwnedLocalAuth:
    def __init__(self, runtime: OwnedBcsProcess, profile: dict):
        self.profile = validate(profile)
        if (self.profile['auth'] != {'kind': 'owned-local'}
                or self.profile['entrypoint']['kind'] != 'singlebox'
                or self.profile['environment']['tier'] != 'local'
                or self.profile['environment']['expected_bcs_env'] != 'local'):
            raise LocalAuthError('owned-local signing cannot be used with remote or mock profiles')
        self.runtime = runtime
        self.fixtures = OwnedLocalFixtures(runtime)
        verified = self.fixtures.check()
        if (self.profile['entrypoint']['origin'] != runtime.inputs.origin
                or self.profile['human']['user_id'] != runtime.inputs.human_id
                or self.profile['actors'] != {role: {key: binding[key] for key in ('bot_id', 'owner_id', 'dedicated')}
                                             for role, binding in verified['roles'].items()}):
            raise LocalAuthError('local profile differs from the verified runtime/fixture binding')
        self.run_id = runtime.run_id
        self.origin = runtime.inputs.origin
        self.human = runtime.inputs.human_id
        self.digest = fingerprint(self.profile)
        self.fixture_digest = fingerprint(read(self.fixtures.path))
        self._key = runtime.inputs.environment['AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE']

    def require_bound(self, profile: dict) -> None:
        if (fingerprint(profile) != self.digest or self.runtime.run_id != self.run_id
                or self.runtime.inputs.origin != self.origin or self.runtime.inputs.human_id != self.human
                or self.runtime.inputs.environment.get('SERVER_ENV') != 'local'
                or fingerprint(read(self.fixtures.path)) != self.fixture_digest):
            raise LocalAuthError('owned-local request binding has changed')
        self.fixtures._require_owned()

    def headers(self, profile: dict) -> dict[str, str]:
        self.require_bound(profile)
        now = int(time.time())
        header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
        claims = {'iss': 'gateway', 'aud': 'bcs', 'iat': now, 'exp': now + 60,
                  'principals': [{'type': 'user', 'tenant': 'e2e-local', 'subject': {
                      'id': self.human, 'username': 'E2E local test human', 'display_name': None,
                      'full_name': None, 'tenant_id': 'e2e-local'}}]}
        def encode(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')
        parts = [encode(json.dumps(value, separators=(',', ':'), sort_keys=True).encode())
                 for value in (header, claims)]
        signing_input = '.'.join(parts).encode('ascii')
        signature = encode(hmac.new(self._key.encode(), signing_input, hashlib.sha256).digest())
        return {'X-Avernet-Principal': signing_input.decode('ascii') + '.' + signature}


def require_local_auth(profile: dict, local_auth) -> None:
    """Clients cannot acquire the local signing capability from a profile string."""
    if not isinstance(local_auth, OwnedLocalAuth):
        raise LocalAuthError('owned-local requests require a live runtime signing capability')
    local_auth.require_bound(profile)


def minted_principal_headers(
    profile: dict, local_auth=None
) -> dict[str, str]:
    """Exact Gateway Principal authorization for the caller profile.

    The owned-local runtime mints from its live, Run-bound signing
    capability. A gateway profile mints from an operator-provisioned
    deployment signing key referenced by ``principal_signing_key_env``;
    the key material is read at request time and never stored in the
    profile or exposed in evidence.
    """
    if local_auth is not None:
        require_local_auth(profile, local_auth)
        return local_auth.headers(profile)
    from .profile import ConfigError
    auth = profile.get('auth')
    key_ref = auth.get('principal_signing_key_env') if isinstance(auth, dict) else None
    if not isinstance(key_ref, str) or not key_ref:
        raise ConfigError('principal requests require an operator-provisioned principal signing key')
    signing_key = os.environ.get(key_ref, '')
    if (not signing_key or not signing_key.isascii()
            or any(ord(char) < 32 or ord(char) == 127 for char in signing_key)):
        raise ConfigError('principal signing key reference is not provisioned')
    user_id = profile['human']['user_id']
    now = int(time.time())
    header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
    claims = {
        'iss': 'gateway',
        'aud': 'bcs',
        'iat': now,
        'exp': now + 60,
        'principals': [{
            'type': 'user', 'tenant': 'bcs-e2e',
            'subject': {'id': user_id, 'username': user_id,
                        'display_name': None, 'full_name': None,
                        'tenant_id': 'bcs-e2e'},
        }],
    }
    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')
    parts = [encode(json.dumps(value, separators=(',', ':'), sort_keys=True).encode())
             for value in (header, claims)]
    signing_input = '.'.join(parts).encode('ascii')
    signature = encode(hmac.new(signing_key.encode(), signing_input, hashlib.sha256).digest())
    return {'X-Avernet-Principal': signing_input.decode('ascii') + '.' + signature}
