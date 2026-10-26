"""Explicit same-origin BCN policy discovery; no policy writes or Run admission."""
from __future__ import annotations

import copy
from pathlib import Path
import re
import time

from .identity_binding import NATIVE
from .profile import ConfigError, fingerprint, identifier, object_keys, require_resolved, validate
from .run_state import read
from .transport import ObservationError, ReadClient

CONTRACT = 'bcs-effective-delivery-policy-read-v1'
PATH = '/admin/message-delivery/policy'


def require_profile(profile: dict) -> None:
    validate(copy.deepcopy(profile))
    if (profile['environment']['tier'] != 'preprod' or profile['entrypoint']['kind'] != 'bcs-direct'
            or profile.get('identity_binding') != NATIVE
            or profile['entrypoint']['manifest_face'] != 'legacy'
            or set(profile['auth'].get('header_env', {})) != {'Cookie'}):
        raise ConfigError('policy check requires explicit preprod native BCN discovery and Cookie references; no gateway fallback')
    require_resolved({k: profile[k] for k in ('entrypoint', 'identity_binding', 'environment', 'human', 'actors', 'auth')})


class PolicyReadReview:
    """Operator attestation of the exact endpoint/version, never generated authority."""
    def __init__(self, profile: dict, path: Path):
        require_profile(profile)
        self.profile, self.path = copy.deepcopy(profile), path
        self.value = self._read()
        self.digest = fingerprint(self.value)

    def _read(self) -> dict:
        value = read(self.path)
        object_keys(value, {'schema_version', 'contract', 'profile_digest', 'reviewer', 'reviewed_at',
                           'expires_at', 'path', 'source_review'}, 'policy read review')
        if (value['contract'] != CONTRACT or value['path'] != PATH
                or value['profile_digest'] != fingerprint(self.profile)
                or value['reviewer'] != self.profile['human']['user_id']):
            raise ConfigError('policy read review differs from the exact native target/Profile/Human')
        start, end = value['reviewed_at'], value['expires_at']
        if (type(start) is not int or type(end) is not int or not 0 < end-start <= 3600
                or not start <= int(time.time()) < end):
            raise ConfigError('policy read review is expired, not yet valid or exceeds one hour')
        source = object_keys(value['source_review'], {'reference', 'sha256', 'deployment_commit'}, 'policy source review')
        identifier(source['reference'], 'policy source review reference')
        for key, size in (('sha256', 64), ('deployment_commit', 40)):
            if not isinstance(source[key], str) or re.fullmatch('[0-9a-f]{'+str(size)+'}', source[key]) is None:
                raise ConfigError('policy read needs reviewed endpoint/deployment evidence')
        return value

    def validate(self) -> dict:
        value = self._read()
        if fingerprint(value) != self.digest:
            raise ConfigError('policy read authority changed; no automatic renewal')
        return value


def policy_check(profile: dict, *, review_path: Path, allow_read: bool) -> dict:
    if allow_read is not True:
        raise ConfigError('policy-check requires explicit --allow-read')
    review = PolicyReadReview(profile, review_path)  # Before credential resolution or HTTP.
    report = {'schema_version': 1, 'status': 'BLOCKED', 'ready_to_execute': False,
              'kind': 'EFFECTIVE_POLICY_READ_ONLY', 'profile_digest': fingerprint(profile),
              'review_digest': review.digest, 'verification_mode': 'OPERATOR_ATTESTED',
              'declared_deployment_commit': review.value['source_review']['deployment_commit'],
              'live_deployment_verification': 'NOT_PERFORMED', 'policy_write_operations': 0,
              'pool_release_authorized': False, 'execution_status': 'UNCONFIRMED',
              'requests': [], 'policy': None, 'failure': None}
    phase = 'transport-setup'
    try:
        client = ReadClient(profile)
        report['requests'] = client.evidence
        for operation, expected_field, expected in (
                ('environment.read', 'env', profile['environment']['expected_bcs_env']),
                ('identity.read', 'user_id', profile['human']['user_id'])):
            phase = operation
            review.validate()
            observed = client.read(operation)
            review.validate()
            if observed.data.get(expected_field) != expected:
                raise ObservationError('policy preflight environment or Human mismatch')
            if operation == 'identity.read':
                provider = observed.data.get('provider')
                if not isinstance(provider, str) or not provider.strip() or provider.casefold() == 'local':
                    raise ObservationError('preprod policy check requires a non-local native identity provider')
        phase = 'delivery.policy.read'
        review.validate()
        observed = client.read(phase, policy_authority=review)
        review.validate()
        report.update(status='POLICY_OBSERVED', policy=observed.data)
    except (Exception, KeyboardInterrupt) as error:
        report['status'] = 'CANCELLED' if isinstance(error, KeyboardInterrupt) else 'BLOCKED'
        report['failure'] = {'phase': phase, 'error_type': type(error).__name__}
    return report
