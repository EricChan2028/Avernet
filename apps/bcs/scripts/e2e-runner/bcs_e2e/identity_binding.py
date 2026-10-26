"""Finite identity discovery contracts; never infer a route from a hostname.

The native BCS /auth/user endpoint authenticates via the service's configured
chain and returns a plain UserInfoResponse. It is not a gateway V1 envelope.
Selecting it grants no authority to rewrite business routes or forge principals.
"""
from __future__ import annotations


NATIVE = {'method': 'GET', 'path': '/auth/user', 'response': 'plain-user-info'}
HUMAN_VIEW = {
    'method': 'GET',
    'path': '/openapi/v1/collaboration/bots/{human_actor_id}/candidates?offset=0&limit=1',
    'response': 'authorized-human-view',
}


def validate_identity_binding(value: object, entrypoint: dict) -> dict:
    from .profile import ConfigError
    if isinstance(value, dict):
        if entrypoint['kind'] == 'bcs-direct' and value == NATIVE:
            return dict(NATIVE)
        if entrypoint['kind'] == 'gateway' and value == HUMAN_VIEW:
            return dict(HUMAN_VIEW)
    raise ConfigError('identity_binding: unsupported entrypoint, method, path or response contract')


def resolve_identity_binding(profile: dict, *, owned_local: bool = False) -> dict:
    if 'identity_binding' in profile:
        return validate_identity_binding(profile['identity_binding'], profile['entrypoint'])
    # Preserve existing profiles and frozen fingerprints. Native direct discovery
    # must be explicitly selected rather than changing historical behavior.
    if owned_local:
        return dict(NATIVE)
    return {'method': 'GET', 'path': '/openapi/v1/auth/user', 'response': 'public-envelope'}


# The current pilot uses gateway-principal V1 business contracts. Native Human
# discovery is not a compatible business adapter. Keep this finite opt-in
# blocked until a separately tested native operation adapter is implemented.
NATIVE_PILOT_BLOCKER = (
    'native direct identity is discovery-only: the current V1 pilot requires '
    'gateway-authenticated business routes; native business/WS adapters are not implemented'
)


def pilot_execution_blocker(profile: dict) -> str:
    if 'identity_binding' in profile:
        binding = validate_identity_binding(profile['identity_binding'], profile['entrypoint'])
        if binding == NATIVE:
            return NATIVE_PILOT_BLOCKER
    return ''


def require_pilot_execution_binding(profile: dict) -> None:
    from .profile import ConfigError
    reason = pilot_execution_blocker(profile)
    if reason:
        raise ConfigError(reason)


def identity_request(profile: dict, *, owned_local: bool = False) -> dict:
    """Resolve the finite self-view target; no caller-supplied path or identity header."""
    from urllib.parse import quote
    from .profile import identifier
    binding = resolve_identity_binding(profile, owned_local=owned_local)
    if binding == HUMAN_VIEW:
        user_id = identifier(profile['human']['user_id'], 'human.user_id')
        binding['path'] = binding['path'].replace('{human_actor_id}', quote('human_' + user_id, safe=''))
    return binding


def project_authorized_human_view(profile: dict, page: dict) -> dict:
    """Proof comes from the server's exclusive Human-perspective authorization.

    The endpoint requires an authenticated User and admits a Human perspective
    only when its ID equals human_{caller.id}. It reads an EXISTING Human record,
    unlike bots/mine which may initialize it. The page contains candidates, NOT
    the caller's identity. Do not echo candidate records or claim user_id was
    returned by the service; this is an explicitly derived authorization proof.
    """
    from .transport import ObservationError
    items = page.get('items')
    total, offset, limit = (page.get(field) for field in ('total', 'offset', 'limit'))
    if (not isinstance(items, list) or len(items) > 1
            or any(not isinstance(item, dict) for item in items)
            or type(total) is not int or total < len(items)
            or type(offset) is not int or offset != 0
            or type(limit) is not int or limit != 1):
        raise ObservationError('authorized Human view requires the exact bounded candidate-page contract')
    return {'user_id': profile['human']['user_id'], 'identity_basis': 'server-authorized-human-view'}


def human_actor_path(profile: dict) -> str:
    from urllib.parse import quote
    from .profile import identifier
    user_id = identifier(profile['human']['user_id'], 'human.user_id')
    return '/openapi/v1/collaboration/bots/' + quote('human_' + user_id, safe='')


def require_human_actor(profile: dict, actor: dict) -> None:
    from .transport import ObservationError
    expected = {'bot_id': 'human_' + profile['human']['user_id'], 'kind': 'human',
                'created_by': profile['human']['user_id'],
                'env': profile['environment']['expected_bcs_env']}
    if any(actor.get(key) != value for key, value in expected.items()):
        raise ObservationError('identity viewpoint is not the exact expected Human/owner/environment')
