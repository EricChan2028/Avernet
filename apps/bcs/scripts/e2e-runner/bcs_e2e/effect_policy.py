"""Finite effect declarations approved for the current shared pilot.

This is admission/review policy, not deletion authority or proof of execution.
Exact ownership, API contracts and cleanup handlers remain independently gated.
Unsupported retained resources cannot be admitted by inventing a cleanup label.
"""
from __future__ import annotations

from .run_state import RunStateError
from .retained_contract import FILE_SUITE, retained_cases, retained_suites


# Dependencies include cleanup declarations for every currently creatable kind.
# Group creation also creates an initial Session; that is not optional metadata.
_REQUIREMENTS = {
    'create:group': {'create:initial_session', 'delete:owned_group'},
    'create:initial_session': {'create:group', 'delete:owned_initial_session'},
    'create:session': {'create:group', 'delete:owned_session'},
    'upload:file': {'create:session', 'delete:owned_file'},
    'update:owned_group': {'create:group'},
    'update:owned_session': {'create:session'},
    'mint:session_token': {'create:session', 'delete:owned_session'},
    'share:owned_file': {'upload:file', 'delete:owned_file'},
    'delete:owned_file': {'upload:file'},
    'delete:owned_session': {'create:session'},
    'delete:owned_initial_session': {'create:initial_session'},
    'delete:owned_group': {'create:group'},
}


# Separate finite resource-retention declarations. These do not grant deletion,
# execution-completion or remote admission authority.
_RETAINED_REQUIREMENTS = {
    'create:group': {'create:initial_session', 'retain:owned_group'},
    'create:initial_session': {'create:group', 'retain:owned_session'},
    'create:session': {'create:group', 'retain:owned_session'},
    'update:owned_group': {'create:group'},
    'update:owned_session': {'create:session'},
    'mint:session_token': {'create:session', 'retain:owned_session'},
    'retain:owned_group': {'create:group'},
    'retain:owned_session': {'create:initial_session'},
}

_FILE_RETAINED_REQUIREMENTS = {
    **_RETAINED_REQUIREMENTS,
    'upload:file': {'create:session', 'retain:owned_file'},
    'share:owned_file': {'upload:file', 'retain:owned_file'},
    'retain:owned_file': {'upload:file'},
}


def validate_effect_policy(manifest: dict) -> None:
    """Validate an already schema-checked manifest for new execution only."""
    if manifest.get('suite') == 'owned-local-trusted-team-v1':
        from .trust_registration import validate_registration
        validate_registration(manifest)
        return
    retained = manifest.get('suite') in retained_suites()
    if retained and tuple(case['id'] for case in manifest['cases']) != retained_cases(manifest['suite']):
        raise RunStateError('retained inventory differs from its separately versioned suite')
    requirements = (_FILE_RETAINED_REQUIREMENTS if manifest.get('suite') == FILE_SUITE
                    else _RETAINED_REQUIREMENTS if retained else _REQUIREMENTS)
    for case in manifest['cases']:
        if retained and case.get('finalization') != 'retain':
            raise RunStateError('retained Case must declare resource retention')
        effects = set(case['effects'])
        if not effects <= requirements.keys():
            raise RunStateError('Case declares an unreviewed resource effect; extend the governed handler contract first')
        if any(not requirements[effect] <= effects for effect in effects):
            raise RunStateError('Case effect declarations omit owned creation, parent or cleanup dependencies')


def policy_summary() -> dict:
    return {'version': 1, 'status': 'DECLARATION_CHECK_ONLY',
            'approved_effects': sorted(_REQUIREMENTS),
            'cleanup_resource_kinds': ['group', 'session', 'file'],
            'requires': ['exact Run ownership', 'authorized API operations', 'child-before-parent cleanup',
                         'retention disposition for associated history'],
            'grants_delete_authority': False, 'proves_physical_erasure': False}
