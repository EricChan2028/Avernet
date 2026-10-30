"""Independent semantic checks for the Provider subbots owned-local slice.

The child emits only status codes, non-secret identifiers, references and
derived booleans.  These validators independently reject any observation that
contradicts one of the fifteen frozen obligations; they never inspect or
retain a runtime token, Provider admin token, signed principal or browser state.
"""
from __future__ import annotations

import re
from ..run_state import RunStateError
from .contract import REDACTION_TOKENS

OBLIGATION_PROVIDER = 'PROVIDER-SUBBOTS.provider-registration-discovery'
OBLIGATION_ADMIN_REFS = 'PROVIDER-SUBBOTS.admin-complete-ref-validation'
OBLIGATION_ADMIN_REGISTRATION = 'PROVIDER-SUBBOTS.admin-sibling-registration'
OBLIGATION_SCOPED_REFS = 'PROVIDER-SUBBOTS.scoped-complete-ref-validation'
OBLIGATION_SCOPED_IDENTITY = 'PROVIDER-SUBBOTS.scoped-sibling-distinct-identity'
OBLIGATION_DUPLICATES = 'PROVIDER-SUBBOTS.duplicate-reference-conflicts'
OBLIGATION_SELF_LOOKUP = 'PROVIDER-SUBBOTS.self-lookup-actor-binding'
OBLIGATION_LEGACY_API = 'PROVIDER-SUBBOTS.legacy-api-actor-binding'
OBLIGATION_METADATA = 'PROVIDER-SUBBOTS.metadata-update-parent-immutability'
OBLIGATION_ONBOARD = 'PROVIDER-SUBBOTS.onboard-parent-immutability'
OBLIGATION_PARENT_DISCOVERY = 'PROVIDER-SUBBOTS.parent-discovery-fail-closed'
OBLIGATION_CALLBACK = 'PROVIDER-SUBBOTS.callback-actor-binding'
OBLIGATION_SIGNED = 'PROVIDER-SUBBOTS.signed-principal-live-state'
OBLIGATION_DISABLE_ENABLE = 'PROVIDER-SUBBOTS.disable-enable-state'
OBLIGATION_DELETION = 'PROVIDER-SUBBOTS.deletion-tombstone-retirement'
REDACTION_GATE = 'PROVIDER-SUBBOTS.redaction-gate'
BOT_ID_RE = re.compile(r'^bot_[0-9a-f]{8}$')
PARENT_RE = re.compile(r'^poolab:e2e:[0-9a-f]{12}$')
REDACTION_STREAMS = ('story.stdout', 'story.stderr', 'owned.console', 'child.evidence')
REDACTION_ARTIFACTS = ('child.receipt', 'flow.report', 'canonical.report')


class ProviderSubbotsViolation(RunStateError):
    """A live observation contradicted a frozen Provider subbots obligation."""


def _fail(obligation: str, detail: str) -> None:
    raise ProviderSubbotsViolation(obligation + ': ' + detail)


def _mapping(value: object, field: str, obligation: str) -> dict:
    if not isinstance(value, dict):
        _fail(obligation, field + ' observation is missing')
    return value


def _string(value: object, field: str, obligation: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(obligation, field + ' must be a non-empty string')
    return value


def _integer(value: object, field: str, obligation: str) -> int:
    if type(value) is not int:
        _fail(obligation, field + ' must be an integer')
    return value


def _boolean(value: object, field: str, obligation: str, expected: bool = True) -> None:
    if value is not expected:
        _fail(obligation, field + ' must be ' + str(expected).lower())


def _statuses(value: object, field: str, obligation: str, expected: list[int]) -> None:
    if not isinstance(value, list) or value != expected:
        _fail(obligation, field + ' status matrix differs from ' + str(expected))


def _bot_id(value: object, field: str, obligation: str) -> str:
    if not isinstance(value, str) or BOT_ID_RE.fullmatch(value) is None:
        _fail(obligation, field + ' is not a canonical BCS Bot ID')
    return value


def _ref(value: object, parent: str, slug: str, field: str,
         obligation: str) -> str:
    expected = parent + ':' + slug
    if value != expected:
        _fail(obligation, field + ' is not the exact ' + expected + ' reference')
    return expected


def check_provider_registration_discovery(evidence: dict) -> None:
    """A subbots Provider is registered and publicly discoverable by slug."""
    provider = _mapping(evidence.get('provider'), 'provider', OBLIGATION_PROVIDER)
    if _integer(provider.get('registration_status'), 'registration status',
                OBLIGATION_PROVIDER) != 200:
        _fail(OBLIGATION_PROVIDER, 'Provider registration must return HTTP 200')
    _boolean(provider.get('provider_id_present'), 'Provider ID presence', OBLIGATION_PROVIDER)
    _boolean(provider.get('admin_credential_present'), 'admin credential presence',
              OBLIGATION_PROVIDER)
    slug = _string(provider.get('provider_slug'), 'Provider slug', OBLIGATION_PROVIDER)
    if not slug.startswith('e2e-subbots-'):
        _fail(OBLIGATION_PROVIDER, 'Provider slug is outside the story namespace')
    if _integer(provider.get('discovery_status'), 'discovery status',
                OBLIGATION_PROVIDER) != 200:
        _fail(OBLIGATION_PROVIDER, 'public discovery must return HTTP 200')
    if provider.get('auth_mode') != 'agentpass-subbots':
        _fail(OBLIGATION_PROVIDER, 'public discovery did not report agentpass-subbots')


def check_admin_complete_ref_validation(evidence: dict) -> None:
    """The Provider-admin registration path rejects six malformed complete refs."""
    value = _mapping(evidence.get('admin_ref_validation'), 'admin ref validation',
                     OBLIGATION_ADMIN_REFS)
    if _integer(value.get('malformed_count'), 'malformed ref count',
                OBLIGATION_ADMIN_REFS) != 6:
        _fail(OBLIGATION_ADMIN_REFS, 'story must probe exactly six malformed refs')
    _statuses(value.get('statuses'), 'admin malformed ref', OBLIGATION_ADMIN_REFS,
              [400] * 6)


def check_admin_sibling_registration(evidence: dict) -> None:
    """Admin registration preserves the client-composed parent/slug identity."""
    value = _mapping(evidence.get('admin_registration'), 'admin registration',
                     OBLIGATION_ADMIN_REGISTRATION)
    if _integer(value.get('status'), 'admin registration status',
                OBLIGATION_ADMIN_REGISTRATION) != 200:
        _fail(OBLIGATION_ADMIN_REGISTRATION, 'admin registration must return HTTP 200')
    parent = _string(value.get('agent_code'), 'admin registration parent',
                     OBLIGATION_ADMIN_REGISTRATION)
    if PARENT_RE.fullmatch(parent) is None:
        _fail(OBLIGATION_ADMIN_REGISTRATION, 'admin registration parent namespace is invalid')
    if value.get('bot_slug') != 'reviewer':
        _fail(OBLIGATION_ADMIN_REGISTRATION, 'admin registration slug is not reviewer')
    if value.get('auth_mode') != 'agentpass-subbots':
        _fail(OBLIGATION_ADMIN_REGISTRATION, 'admin registration auth mode changed')
    _ref(value.get('provider_bot_ref'), parent, 'reviewer',
         'admin registration ref', OBLIGATION_ADMIN_REGISTRATION)
    _bot_id(value.get('bot_id'), 'admin registration Bot ID', OBLIGATION_ADMIN_REGISTRATION)
    _boolean(value.get('token_present'), 'admin runtime token presence',
             OBLIGATION_ADMIN_REGISTRATION)


def check_scoped_complete_ref_validation(evidence: dict) -> None:
    """The scoped path rejects four malformed refs and a removed bot_slug cannot repair one."""
    value = _mapping(evidence.get('scoped_ref_validation'), 'scoped ref validation',
                     OBLIGATION_SCOPED_REFS)
    if _integer(value.get('removed_slug_status'), 'removed bot_slug status',
                OBLIGATION_SCOPED_REFS) != 400:
        _fail(OBLIGATION_SCOPED_REFS, 'a removed bot_slug must not repair an incomplete ref')
    _statuses(value.get('malformed_statuses'), 'scoped malformed ref',
              OBLIGATION_SCOPED_REFS, [400] * 4)


def check_scoped_sibling_distinct_identity(evidence: dict) -> None:
    """The scoped path registers the second sibling with a distinct UUID and token."""
    admin = _mapping(evidence.get('admin_registration'), 'admin registration',
                     OBLIGATION_SCOPED_IDENTITY)
    value = _mapping(evidence.get('scoped_registration'), 'scoped registration',
                     OBLIGATION_SCOPED_IDENTITY)
    if _integer(value.get('status'), 'scoped registration status',
                OBLIGATION_SCOPED_IDENTITY) != 200:
        _fail(OBLIGATION_SCOPED_IDENTITY, 'scoped registration must return HTTP 200')
    parent = _string(value.get('agent_code'), 'scoped registration parent',
                     OBLIGATION_SCOPED_IDENTITY)
    if PARENT_RE.fullmatch(parent) is None or parent != admin.get('agent_code'):
        _fail(OBLIGATION_SCOPED_IDENTITY, 'both registration paths must share one parent')
    if value.get('bot_slug') != 'coder':
        _fail(OBLIGATION_SCOPED_IDENTITY, 'scoped registration slug is not coder')
    if value.get('auth_mode') != 'agentpass-subbots':
        _fail(OBLIGATION_SCOPED_IDENTITY, 'scoped registration auth mode changed')
    _ref(value.get('provider_bot_ref'), parent, 'coder', 'scoped registration ref',
         OBLIGATION_SCOPED_IDENTITY)
    _bot_id(value.get('bot_id'), 'scoped registration Bot ID', OBLIGATION_SCOPED_IDENTITY)
    _boolean(value.get('token_present'), 'scoped runtime token presence',
             OBLIGATION_SCOPED_IDENTITY)
    _boolean(value.get('distinct_from_admin_uuid'), 'sibling UUID distinctness',
             OBLIGATION_SCOPED_IDENTITY)
    _boolean(value.get('distinct_from_admin_token'), 'sibling token distinctness',
             OBLIGATION_SCOPED_IDENTITY)


def check_duplicate_reference_conflicts(evidence: dict) -> None:
    """Exact duplicate references conflict on both registration paths."""
    value = _mapping(evidence.get('duplicates'), 'duplicate observations',
                     OBLIGATION_DUPLICATES)
    if _integer(value.get('admin_status'), 'admin duplicate status',
                OBLIGATION_DUPLICATES) != 409:
        _fail(OBLIGATION_DUPLICATES, 'admin duplicate must return HTTP 409')
    if _integer(value.get('scoped_status'), 'scoped duplicate status',
                OBLIGATION_DUPLICATES) != 409:
        _fail(OBLIGATION_DUPLICATES, 'scoped duplicate must return HTTP 409')


def _validate_success_view(row: dict, index: int) -> None:
    if _integer(row.get('status'), 'self view status', OBLIGATION_SELF_LOOKUP) != 200:
        _fail(OBLIGATION_SELF_LOOKUP, 'successful self view ' + str(index) + ' is not 200')
    if row.get('cache_control') != 'no-store':
        _fail(OBLIGATION_SELF_LOOKUP, 'successful self view is cacheable')
    for field in ('registered', 'parent_matches', 'bot_id_matches', 'ref_matches',
                  'token_absent'):
        _boolean(row.get(field), 'self view ' + field, OBLIGATION_SELF_LOOKUP)


def check_self_lookup_actor_binding(evidence: dict) -> None:
    """Bot self lookup selects the credential's exact Bot and rejects Actor confusion."""
    value = _mapping(evidence.get('self_lookup'), 'self lookup',
                     OBLIGATION_SELF_LOOKUP)
    successes = value.get('successes')
    rejections = value.get('rejections')
    if not isinstance(successes, list) or len(successes) != 9:
        _fail(OBLIGATION_SELF_LOOKUP, 'expected exactly nine successful self views')
    if not isinstance(rejections, list) or len(rejections) != 6:
        _fail(OBLIGATION_SELF_LOOKUP, 'expected exactly six rejected self views')
    for index, row in enumerate(successes):
        _validate_success_view(_mapping(row, 'successful self view', OBLIGATION_SELF_LOOKUP),
                               index)
    for index, row in enumerate(rejections):
        row = _mapping(row, 'rejected self view', OBLIGATION_SELF_LOOKUP)
        if _integer(row.get('status'), 'rejected self view status',
                    OBLIGATION_SELF_LOOKUP) != 401:
            _fail(OBLIGATION_SELF_LOOKUP, 'rejected self view ' + str(index) + ' is not 401')
        if row.get('cache_control') != 'no-store':
            _fail(OBLIGATION_SELF_LOOKUP, 'rejected self view is cacheable')
    if _integer(value.get('matching_actor_successes'), 'matching Actor successes',
                OBLIGATION_SELF_LOOKUP) != 2:
        _fail(OBLIGATION_SELF_LOOKUP, 'matching Actor must be exercised for both siblings')
    if _integer(value.get('actor_only_status'), 'Actor-only status',
                OBLIGATION_SELF_LOOKUP) != 401:
        _fail(OBLIGATION_SELF_LOOKUP, 'Actor alone fell back to the mock Human')
    if _integer(value.get('malformed_actor_status'), 'malformed Actor status',
                OBLIGATION_SELF_LOOKUP) != 401:
        _fail(OBLIGATION_SELF_LOOKUP, 'malformed Actor was accepted')


def check_legacy_api_actor_binding(evidence: dict) -> None:
    """Legacy API Actor selection cannot override the token's exact Bot."""
    value = _mapping(evidence.get('legacy_api'), 'legacy API', OBLIGATION_LEGACY_API)
    _statuses(value.get('matching_statuses'), 'legacy matching Actor',
              OBLIGATION_LEGACY_API, [200, 200])
    _statuses(value.get('sibling_statuses'), 'legacy sibling Actor',
              OBLIGATION_LEGACY_API, [401, 401])


def check_metadata_update_parent_immutability(evidence: dict) -> None:
    """Metadata update retains the Bot UUID and parent/ref read-back."""
    value = _mapping(evidence.get('metadata_update'), 'metadata update',
                     OBLIGATION_METADATA)
    if _integer(value.get('status'), 'metadata update status',
                OBLIGATION_METADATA) != 200:
        _fail(OBLIGATION_METADATA, 'metadata update must return HTTP 200')
    requested = _bot_id(value.get('requested_bot_id'), 'requested Bot ID',
                        OBLIGATION_METADATA)
    returned = _bot_id(value.get('returned_bot_id'), 'returned Bot ID',
                       OBLIGATION_METADATA)
    if returned != requested:
        _fail(OBLIGATION_METADATA, 'metadata update changed the Bot UUID')
    if value.get('name') != 'E2E Senior Reviewer':
        _fail(OBLIGATION_METADATA, 'metadata update did not apply the requested name')
    if value.get('authorization_scopes') != []:
        _fail(OBLIGATION_METADATA, 'metadata update changed the empty scope set')
    _boolean(value.get('readback_parent_matches'), 'metadata parent read-back',
             OBLIGATION_METADATA)
    _boolean(value.get('readback_ref_matches'), 'metadata ref read-back',
             OBLIGATION_METADATA)


def check_onboard_parent_immutability(evidence: dict) -> None:
    """Onboarding preserves parent, ref and registered status."""
    value = _mapping(evidence.get('onboarding'), 'onboarding', OBLIGATION_ONBOARD)
    if _integer(value.get('status'), 'onboarding status', OBLIGATION_ONBOARD) != 200:
        _fail(OBLIGATION_ONBOARD, 'onboarding must return HTTP 200')
    if _integer(value.get('readback_status'), 'onboarding read-back status',
                OBLIGATION_ONBOARD) != 200:
        _fail(OBLIGATION_ONBOARD, 'self lookup after onboarding must return HTTP 200')
    _boolean(value.get('readback_parent_matches'), 'onboarding parent read-back',
             OBLIGATION_ONBOARD)
    _boolean(value.get('readback_ref_matches'), 'onboarding ref read-back',
             OBLIGATION_ONBOARD)


def check_parent_discovery_fail_closed(evidence: dict) -> None:
    """Parent discovery requires auth, validates paging and fails closed without a verifier."""
    value = _mapping(evidence.get('parent_discovery'), 'parent discovery',
                     OBLIGATION_PARENT_DISCOVERY)
    if _integer(value.get('unauthenticated_status'), 'unauthenticated parent discovery',
                OBLIGATION_PARENT_DISCOVERY) != 401:
        _fail(OBLIGATION_PARENT_DISCOVERY, 'parent discovery must require credentials')
    _statuses(value.get('invalid_query_statuses'), 'invalid parent query',
              OBLIGATION_PARENT_DISCOVERY, [400] * 4)
    if _integer(value.get('unavailable_status'), 'verifier-unavailable status',
                OBLIGATION_PARENT_DISCOVERY) != 502:
        _fail(OBLIGATION_PARENT_DISCOVERY, 'missing verifier must fail closed with 502')
    if value.get('error_code') != 'agent_identity_unavailable':
        _fail(OBLIGATION_PARENT_DISCOVERY, 'missing verifier leaked a different error')


def check_callback_actor_binding(evidence: dict) -> None:
    """Provider callbacks bind token, Provider and Actor consistently."""
    value = _mapping(evidence.get('callback'), 'callback', OBLIGATION_CALLBACK)
    if _integer(value.get('authenticated_status'), 'authenticated callback status',
                OBLIGATION_CALLBACK) != 404:
        _fail(OBLIGATION_CALLBACK, 'authenticated callback must reach run lookup with 404')
    if value.get('authenticated_error') != 'run_not_found':
        _fail(OBLIGATION_CALLBACK, 'callback did not reach exact run lookup')
    if _integer(value.get('sibling_status'), 'sibling callback status',
                OBLIGATION_CALLBACK) != 401:
        _fail(OBLIGATION_CALLBACK, 'callback accepted a sibling Actor')
    if _integer(value.get('token_only_status'), 'token-only callback status',
                OBLIGATION_CALLBACK) != 404:
        _fail(OBLIGATION_CALLBACK, 'callback without Actor did not reach run lookup')


def check_signed_principal_live_state(evidence: dict) -> None:
    """Signed Gateway principals are checked against live Provider/Bot state."""
    value = _mapping(evidence.get('signed_principal'), 'signed principal',
                     OBLIGATION_SIGNED)
    if _integer(value.get('accepted_status'), 'signed principal status',
                OBLIGATION_SIGNED) != 200:
        _fail(OBLIGATION_SIGNED, 'signed Bot principal was not accepted')
    _boolean(value.get('conflicting_actor_sent'), 'conflicting unsigned Actor',
             OBLIGATION_SIGNED)
    if _integer(value.get('disabled_status'), 'disabled signed status',
                OBLIGATION_SIGNED) != 401:
        _fail(OBLIGATION_SIGNED, 'disabled Provider did not invalidate signed principal')
    if _integer(value.get('enabled_status'), 'enabled signed status',
                OBLIGATION_SIGNED) != 200:
        _fail(OBLIGATION_SIGNED, 'enabled Provider did not restore signed principal')
    if _integer(value.get('deleted_status'), 'deleted signed status',
                OBLIGATION_SIGNED) != 401:
        _fail(OBLIGATION_SIGNED, 'deleted Bot principal remained valid')


def check_disable_enable_state(evidence: dict) -> None:
    """Disable invalidates and enable restores both sibling identities and callbacks."""
    value = _mapping(evidence.get('disable_enable'), 'disable/enable',
                     OBLIGATION_DISABLE_ENABLE)
    if _integer(value.get('disable_status'), 'disable status',
                OBLIGATION_DISABLE_ENABLE) != 200:
        _fail(OBLIGATION_DISABLE_ENABLE, 'disable must return HTTP 200')
    _statuses(value.get('disabled_view_statuses'), 'disabled self views',
              OBLIGATION_DISABLE_ENABLE, [401, 401])
    if _integer(value.get('disabled_callback_status'), 'disabled callback status',
                OBLIGATION_DISABLE_ENABLE) != 401:
        _fail(OBLIGATION_DISABLE_ENABLE, 'disabled Provider accepted callbacks')
    if _integer(value.get('disabled_signed_status'), 'disabled signed status',
                OBLIGATION_DISABLE_ENABLE) != 401:
        _fail(OBLIGATION_DISABLE_ENABLE, 'disabled Provider accepted signed principal')
    if _integer(value.get('enable_status'), 'enable status',
                OBLIGATION_DISABLE_ENABLE) != 200:
        _fail(OBLIGATION_DISABLE_ENABLE, 'enable must return HTTP 200')
    _statuses(value.get('enabled_view_statuses'), 'enabled self views',
              OBLIGATION_DISABLE_ENABLE, [200, 200])
    if _integer(value.get('enabled_signed_status'), 'enabled signed status',
                OBLIGATION_DISABLE_ENABLE) != 200:
        _fail(OBLIGATION_DISABLE_ENABLE, 'enabled Provider did not restore signed principal')


def check_deletion_tombstone_retirement(evidence: dict) -> None:
    """Deletion tombstones exact refs without deleting the sibling, then retires Provider."""
    value = _mapping(evidence.get('deletion'), 'deletion', OBLIGATION_DELETION)
    expected = (
        ('delete_reviewer_status', 200, 'delete reviewer'),
        ('reviewer_view_status', 401, 'deleted reviewer token'),
        ('sibling_view_status', 200, 'live sibling token'),
        ('deleted_signed_status', 401, 'deleted signed principal'),
        ('admin_reclaim_status', 409, 'admin tombstone reclaim'),
        ('scoped_reclaim_status', 409, 'scoped tombstone reclaim'),
        ('delete_coder_status', 200, 'delete coder'),
        ('coder_view_status', 401, 'deleted coder token'),
        ('retire_status', 200, 'Provider retirement'),
    )
    for field, status, label in expected:
        if _integer(value.get(field), field, OBLIGATION_DELETION) != status:
            _fail(OBLIGATION_DELETION, label + ' status differs from ' + str(status))


def verify_all_obligations(evidence: dict) -> dict:
    """Run all fifteen independent checks and return a non-secret receipt."""
    if not isinstance(evidence, dict):
        raise ProviderSubbotsViolation('Provider subbots evidence must be an object')
    checks = (
        (OBLIGATION_PROVIDER, check_provider_registration_discovery),
        (OBLIGATION_ADMIN_REFS, check_admin_complete_ref_validation),
        (OBLIGATION_ADMIN_REGISTRATION, check_admin_sibling_registration),
        (OBLIGATION_SCOPED_REFS, check_scoped_complete_ref_validation),
        (OBLIGATION_SCOPED_IDENTITY, check_scoped_sibling_distinct_identity),
        (OBLIGATION_DUPLICATES, check_duplicate_reference_conflicts),
        (OBLIGATION_SELF_LOOKUP, check_self_lookup_actor_binding),
        (OBLIGATION_LEGACY_API, check_legacy_api_actor_binding),
        (OBLIGATION_METADATA, check_metadata_update_parent_immutability),
        (OBLIGATION_ONBOARD, check_onboard_parent_immutability),
        (OBLIGATION_PARENT_DISCOVERY, check_parent_discovery_fail_closed),
        (OBLIGATION_CALLBACK, check_callback_actor_binding),
        (OBLIGATION_SIGNED, check_signed_principal_live_state),
        (OBLIGATION_DISABLE_ENABLE, check_disable_enable_state),
        (OBLIGATION_DELETION, check_deletion_tombstone_retirement),
    )
    for obligation, check in checks:
        try:
            check(evidence)
        except ProviderSubbotsViolation:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderSubbotsViolation(obligation + ': malformed observation') from None
    return {'status': 'PASS', 'checked': [name for name, _ in checks]}


def scan_redaction(name: str, payload: bytes, *,
                   tokens: tuple[str, ...] = REDACTION_TOKENS) -> dict:
    """Return a non-secret observation of whether a byte stream leaks a canary."""
    if not isinstance(name, str) or not name:
        _fail(REDACTION_GATE, 'redaction scan name is invalid')
    if not isinstance(payload, bytes):
        _fail(REDACTION_GATE, 'redaction scan payload must be bytes')
    return {'name': name, 'clean': all(token.encode('utf-8') not in payload
                                       for token in tokens),
            'bytes': len(payload)}
