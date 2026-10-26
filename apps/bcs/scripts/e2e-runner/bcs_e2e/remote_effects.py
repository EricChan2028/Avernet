"""Fail closed for unmodelled normal-group bootstrap on persistent targets."""
from __future__ import annotations

from .profile import ConfigError, object_keys, origin, identifier


class RemoteBootstrapAdmissionError(RuntimeError):
    pass


REASON = (
    'remote normal-group creation can bootstrap Bot execution and public-Bot relation edges; '
    'the pilot has no reviewed suppression or termination evidence for these effects'
)


def validate_remote_effect_review(value: object, profile: dict) -> None:
    if (profile.get('environment', {}).get('tier') != 'preprod'
            or profile.get('entrypoint', {}).get('kind') not in {'gateway', 'bcs-direct'}):
        raise ConfigError('remote effect review is only admitted for a preprod remote profile')
    if profile.get('policy', {}).get('exclusive_use_confirmed') is not True:
        raise ConfigError('remote effect review requires confirmed exclusive fixture use')
    review = object_keys(value, {'isolated_test_runtime', 'no_operational_channels',
                                  'implicit_bot_execution_allowed'}, 'policy.remote_effect_review')
    for key in review:
        if review[key] is not True:
            raise ConfigError(f'policy.remote_effect_review.{key} must be explicitly verified')
    identifier(profile.get('human', {}).get('user_id', ''), 'human.user_id')
    origin(profile['entrypoint']['origin'], profile['environment']['tier'])


def remote_effect_admission(profile: dict) -> dict:
    remote = profile['entrypoint']['kind'] in {'gateway', 'bcs-direct'}
    if remote and 'remote_effect_review' in profile.get('policy', {}):
        validate_remote_effect_review(profile['policy']['remote_effect_review'], profile)
        return {'status': 'ADMITTED',
                'reason': 'operator-reviewed isolated preprod full pilot with no operational channels',
                'grants_write_authority': True}
    return {'status': 'BLOCKED' if remote else 'OWNED_LOCAL_SCOPE',
            'reason': REASON if remote else 'local runtime owns its fixture/process lifecycle',
            'grants_write_authority': False}


def require_group_effect_admission(profile: dict) -> None:
    if remote_effect_admission(profile)['status'] == 'BLOCKED':
        raise RemoteBootstrapAdmissionError(REASON)
