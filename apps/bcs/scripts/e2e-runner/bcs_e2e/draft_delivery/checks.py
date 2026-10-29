"""Semantic checks over non-credential Draft delivery observations.

The black-box child emits only statuses, fixed revision metadata and hashes.
These functions independently reject any observation that violates the eight
frozen obligations; they never inspect or retain an authorization credential.
"""
from __future__ import annotations

import re

from ..run_state import RunStateError
from .contract import REDACTION_TOKENS

OBLIGATION_CLIENT_ID = 'DRAFT-DELIVERY.client-selected-idempotent-create'
OBLIGATION_APPEND = 'DRAFT-DELIVERY.immutable-revision-append'
OBLIGATION_STALE = 'DRAFT-DELIVERY.stale-update-conflict'
OBLIGATION_READ_TOKEN = 'DRAFT-DELIVERY.read-token-single-revision'
OBLIGATION_UNAVAILABLE = 'DRAFT-DELIVERY.revoked-or-deleted-unavailable'
OBLIGATION_READ_ONLY = 'DRAFT-DELIVERY.read-token-read-only'
OBLIGATION_NO_STORE = 'DRAFT-DELIVERY.no-store-every-response'
OBLIGATION_REDACTION = 'DRAFT-DELIVERY.token-redaction'
REDACTION_STREAMS = ('story.stdout', 'story.stderr', 'owned.console')
REDACTION_ARTIFACTS = ('child.receipt', 'flow.report', 'canonical.report')
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


class DraftDeliveryViolation(RunStateError):
    """A live observation contradicted a frozen Draft delivery obligation."""


def _fail(obligation: str, detail: str) -> None:
    raise DraftDeliveryViolation(obligation + ': ' + detail)


def _mapping(value: object, field: str, obligation: str = OBLIGATION_CLIENT_ID) -> dict:
    if not isinstance(value, dict):
        _fail(obligation, field + ' observation is missing')
    return value


def _integer(value: object, field: str, obligation: str = OBLIGATION_CLIENT_ID) -> int:
    if type(value) is not int:
        _fail(obligation, field + ' must be an integer')
    return value


def check_client_selected_idempotent_create(evidence: dict) -> None:
    """Client selects one canonical UUID; create replay is idempotent."""
    create = _mapping(evidence.get('create'), 'create')
    draft_id = create.get('draft_id')
    if not isinstance(draft_id, str) or _UUID.fullmatch(draft_id) is None:
        _fail(OBLIGATION_CLIENT_ID, 'draft ID is not a canonical lowercase UUIDv4')
    attempts = create.get('attempts')
    if not isinstance(attempts, list) or len(attempts) != 2:
        _fail(OBLIGATION_CLIENT_ID, 'create must be attempted exactly twice')
    first = _mapping(attempts[0], 'first create')
    replay = _mapping(attempts[1], 'create replay')
    if _integer(first.get('status'), 'first create status') != 201:
        _fail(OBLIGATION_CLIENT_ID, 'first create must return 201')
    if _integer(replay.get('status'), 'replay status') != 200:
        _fail(OBLIGATION_CLIENT_ID, 'exact create replay must return 200')
    for label, observation in (('first create', first), ('create replay', replay)):
        if observation.get('draft_id') != draft_id:
            _fail(OBLIGATION_CLIENT_ID, label + ' used a different draft ID')
        if _integer(observation.get('revision'), label + ' revision') != 1:
            _fail(OBLIGATION_CLIENT_ID, label + ' must bind revision 1')
        if _integer(observation.get('size_bytes'), label + ' size') != 2174:
            _fail(OBLIGATION_CLIENT_ID, label + ' changed the fixture byte count')
        if observation.get('content_hash') != create.get('content_hash'):
            _fail(OBLIGATION_CLIENT_ID, label + ' changed the immutable content hash')
        if observation.get('valid') is not True:
            _fail(OBLIGATION_CLIENT_ID, label + ' source did not validate')
    conflict = _mapping(create.get('different_content'), 'different-content create')
    if _integer(conflict.get('status'), 'different-content status') != 409:
        _fail(OBLIGATION_CLIENT_ID, 'different content for the selected ID must conflict')


def check_immutable_revision_append(evidence: dict) -> None:
    """An update appends exactly head+1 and never mutates or renumbers history."""
    append = _mapping(evidence.get('append'), 'append', OBLIGATION_APPEND)
    if _integer(append.get('head_before'), 'head before append', OBLIGATION_APPEND) != 1:
        _fail(OBLIGATION_APPEND, 'append did not start from revision 1')
    if _integer(append.get('expected_revision'), 'expected revision', OBLIGATION_APPEND) != 1:
        _fail(OBLIGATION_APPEND, 'append expected revision differs from the immutable head')
    if _integer(append.get('status'), 'append status', OBLIGATION_APPEND) != 200:
        _fail(OBLIGATION_APPEND, 'append must return 200')
    if _integer(append.get('revision'), 'append result revision', OBLIGATION_APPEND) != 2:
        _fail(OBLIGATION_APPEND, 'append must create exactly revision 2')
    if _integer(append.get('head_after'), 'head after append', OBLIGATION_APPEND) != 2:
        _fail(OBLIGATION_APPEND, 'append must advance the head exactly once')
    original = _mapping(append.get('original_after'), 'original after append', OBLIGATION_APPEND)
    if _integer(original.get('status'), 'original status', OBLIGATION_APPEND) != 200:
        _fail(OBLIGATION_APPEND, 'historical revision 1 became unreadable')
    if _integer(original.get('revision'), 'original revision', OBLIGATION_APPEND) != 1:
        _fail(OBLIGATION_APPEND, 'historical revision was renumbered')
    if _integer(original.get('size_bytes'), 'original size', OBLIGATION_APPEND) != 2174:
        _fail(OBLIGATION_APPEND, 'historical revision byte count changed')
    if original.get('content_hash') != evidence.get('fixture', {}).get('content_hash'):
        _fail(OBLIGATION_APPEND, 'historical revision content hash changed')
    updated = _mapping(append.get('updated_after'), 'updated after append', OBLIGATION_APPEND)
    new_hash = append.get('new_content_hash')
    if not isinstance(new_hash, str) or len(new_hash) != 64:
        _fail(OBLIGATION_APPEND, 'appended revision hash is invalid')
    if new_hash == original.get('content_hash'):
        _fail(OBLIGATION_APPEND, 'appended revision did not change content')
    if (_integer(updated.get('status'), 'updated status', OBLIGATION_APPEND) != 200
            or _integer(updated.get('revision'), 'updated revision', OBLIGATION_APPEND) != 2
            or updated.get('content_hash') != new_hash):
        _fail(OBLIGATION_APPEND, 'revision 2 does not bind the appended content')


def check_stale_update_conflict(evidence: dict) -> None:
    """A stale expected_revision returns 409 without advancing or replacing head."""
    stale = _mapping(evidence.get('stale'), 'stale update', OBLIGATION_STALE)
    if _integer(stale.get('status'), 'stale status', OBLIGATION_STALE) != 409:
        _fail(OBLIGATION_STALE, 'stale update must return HTTP 409')
    if stale.get('expected_revision') != 1:
        _fail(OBLIGATION_STALE, 'stale observation did not use revision 1 as expected head')
    if _integer(stale.get('head_after'), 'head after stale update', OBLIGATION_STALE) != 2:
        _fail(OBLIGATION_STALE, 'stale update advanced the head')
    latest = _mapping(stale.get('latest_after'), 'latest revision after stale update', OBLIGATION_STALE)
    if _integer(latest.get('status'), 'latest status', OBLIGATION_STALE) != 200:
        _fail(OBLIGATION_STALE, 'newer revision became unreadable after stale update')
    if _integer(latest.get('revision'), 'latest revision', OBLIGATION_STALE) != 2:
        _fail(OBLIGATION_STALE, 'stale update renumbered the newer revision')
    if latest.get('content_hash') != stale.get('new_content_hash'):
        _fail(OBLIGATION_STALE, 'stale update replaced the newer revision')
    if latest.get('content_hash') == stale.get('original_content_hash'):
        _fail(OBLIGATION_STALE, 'stale observation cannot distinguish original and newer content')


def check_read_token_single_revision(evidence: dict) -> None:
    """A historical card stays pinned to one revision and its token is inert."""
    cards = _mapping(evidence.get('cards'), 'card observations', OBLIGATION_READ_TOKEN)
    if _integer(cards.get('head_revision'), 'card head revision', OBLIGATION_READ_TOKEN) != 2:
        _fail(OBLIGATION_READ_TOKEN, 'historical card was not reissued after head advanced to 2')
    historical = _mapping(cards.get('historical'), 'historical card', OBLIGATION_READ_TOKEN)
    if historical.get('draft_id') != evidence.get('create', {}).get('draft_id'):
        _fail(OBLIGATION_READ_TOKEN, 'historical card bound a different draft ID')
    if _integer(historical.get('revision'), 'historical card revision', OBLIGATION_READ_TOKEN) != 1:
        _fail(OBLIGATION_READ_TOKEN, 'historical card followed the live head instead of staying on revision 1')
    if historical.get('read_token_present') is not True:
        _fail(OBLIGATION_READ_TOKEN, 'compatibility card is missing the deprecated readToken field')
    if _integer(historical.get('read_token_revision'), 'read token revision',
                OBLIGATION_READ_TOKEN) != 1:
        _fail(OBLIGATION_READ_TOKEN, 'readToken is not pinned to the card revision')
    if historical.get('read_with_token_as_header') is not False:
        _fail(OBLIGATION_READ_TOKEN, 'readToken was used as an authorization header')
    read = _mapping(historical.get('read_without_token'), 'read without token',
                    OBLIGATION_READ_TOKEN)
    if _integer(read.get('status'), 'read without token status', OBLIGATION_READ_TOKEN) != 200:
        _fail(OBLIGATION_READ_TOKEN, 'ID plus revision did not authorize the fixed read')
    if _integer(read.get('revision'), 'read revision', OBLIGATION_READ_TOKEN) != 1:
        _fail(OBLIGATION_READ_TOKEN, 'read returned a different revision')
    if _integer(read.get('size_bytes'), 'read size', OBLIGATION_READ_TOKEN) != 2174:
        _fail(OBLIGATION_READ_TOKEN, 'read returned different source bytes')
    if read.get('content_hash') != historical.get('content_hash'):
        _fail(OBLIGATION_READ_TOKEN, 'read content differs from the card-bound revision')
    other = _mapping(cards.get('other_revision'), 'other revision card', OBLIGATION_READ_TOKEN)
    if _integer(other.get('revision'), 'other card revision', OBLIGATION_READ_TOKEN) != 2:
        _fail(OBLIGATION_READ_TOKEN, 'a second card was not bound to revision 2')
    if other.get('read_token_revision') != 2:
        _fail(OBLIGATION_READ_TOKEN, 'readToken was reused across revisions')


def check_revoked_or_deleted_unavailable(evidence: dict) -> None:
    """Revoked and deleted fixed reads are uniformly unavailable without leaks."""
    availability = _mapping(evidence.get('availability'), 'availability observations',
                            OBLIGATION_UNAVAILABLE)
    revoked = _mapping(availability.get('revoked'), 'revoked revision', OBLIGATION_UNAVAILABLE)
    if _integer(revoked.get('status'), 'revoked status', OBLIGATION_UNAVAILABLE) != 404:
        _fail(OBLIGATION_UNAVAILABLE, 'revoked revision must be unavailable with 404')
    if revoked.get('error') != 'DRAFT_REFERENCE_UNAVAILABLE':
        _fail(OBLIGATION_UNAVAILABLE, 'revoked read leaked a different error')
    sibling = _mapping(availability.get('historical_sibling'), 'historical sibling',
                       OBLIGATION_UNAVAILABLE)
    if _integer(sibling.get('status'), 'historical sibling status',
                OBLIGATION_UNAVAILABLE) != 200:
        _fail(OBLIGATION_UNAVAILABLE, 'revoking one revision made a sibling unavailable')
    if sibling.get('content_hash') != evidence.get('fixture', {}).get('content_hash'):
        _fail(OBLIGATION_UNAVAILABLE, 'historical sibling changed content')
    deleted = _mapping(availability.get('deleted'), 'deleted draft', OBLIGATION_UNAVAILABLE)
    if _integer(deleted.get('status'), 'deleted status', OBLIGATION_UNAVAILABLE) != 404:
        _fail(OBLIGATION_UNAVAILABLE, 'deleted Draft revision must be unavailable with 404')
    if deleted.get('error') != 'DRAFT_REFERENCE_UNAVAILABLE':
        _fail(OBLIGATION_UNAVAILABLE, 'deleted read leaked a different error')
    late = _mapping(availability.get('late_create'), 'late create', OBLIGATION_UNAVAILABLE)
    if late.get('method') != 'PUT' or _integer(late.get('status'), 'late create status',
                                               OBLIGATION_UNAVAILABLE) != 404:
        _fail(OBLIGATION_UNAVAILABLE, 'late create revived a deleted Draft')


def check_read_token_read_only(evidence: dict) -> None:
    """No draft token can authorize delete or revision management."""
    management = _mapping(evidence.get('management'), 'management observations',
                          OBLIGATION_READ_ONLY)
    for field, label in (('public_delete', 'public delete'),
                         ('token_delete', 'readToken delete'),
                         ('token_update', 'readToken update')):
        observation = _mapping(management.get(field), label, OBLIGATION_READ_ONLY)
        status = _integer(observation.get('status'), label + ' status', OBLIGATION_READ_ONLY)
        if status not in (401, 403):
            _fail(OBLIGATION_READ_ONLY, label + ' was not rejected as unauthorized')
    if _integer(management.get('head_after'), 'head after management rejection',
                OBLIGATION_READ_ONLY) != 2:
        _fail(OBLIGATION_READ_ONLY, 'rejected management operation changed the head')
    if management.get('deleted_after') is not False:
        _fail(OBLIGATION_READ_ONLY, 'rejected management operation deleted the Draft')


def check_no_store_every_response(evidence: dict) -> None:
    """Every observed success and error response carries Cache-Control: no-store."""
    responses = evidence.get('responses')
    if not isinstance(responses, list) or not responses:
        _fail(OBLIGATION_NO_STORE, 'no Draft HTTP responses were observed')
    observed_statuses = set()
    for index, value in enumerate(responses):
        response = _mapping(value, 'response ' + str(index), OBLIGATION_NO_STORE)
        method, path = response.get('method'), response.get('path')
        if not isinstance(method, str) or method not in {'GET', 'PUT', 'POST', 'DELETE'}:
            _fail(OBLIGATION_NO_STORE, 'response method is invalid')
        if not isinstance(path, str) or not path.startswith('/collaboration/drafts/'):
            _fail(OBLIGATION_NO_STORE, 'response path is outside the Draft surface')
        status = _integer(response.get('status'), 'response status', OBLIGATION_NO_STORE)
        observed_statuses.add(status)
        if response.get('cache_control') != 'no-store':
            _fail(OBLIGATION_NO_STORE,
                  method + ' ' + path + ' response did not carry no-store')
    required = {200, 201, 204, 401, 404, 409}
    if not required.issubset(observed_statuses):
        _fail(OBLIGATION_NO_STORE, 'response matrix omitted success or error status coverage')


def scan_redaction(name: str, payload: bytes, *,
                   tokens: tuple[str, ...] = REDACTION_TOKENS) -> dict:
    """Return a non-secret observation of whether a byte stream leaks a canary."""
    if not isinstance(name, str) or not name:
        _fail(OBLIGATION_REDACTION, 'redaction scan name is invalid')
    if not isinstance(payload, bytes):
        _fail(OBLIGATION_REDACTION, 'redaction scan payload must be bytes')
    return {'name': name, 'clean': all(token.encode('utf-8') not in payload
                                       for token in tokens),
            'bytes': len(payload)}


def check_token_redaction(evidence: dict) -> None:
    """All child streams and persisted artifacts are free of draft-token canaries."""
    redaction = _mapping(evidence.get('redaction'), 'redaction observations',
                         OBLIGATION_REDACTION)
    if redaction.get('token_material_captured') is not False:
        _fail(OBLIGATION_REDACTION, 'the child captured token material')
    for group, required in (('streams', REDACTION_STREAMS),
                            ('artifacts', REDACTION_ARTIFACTS)):
        entries = redaction.get(group)
        if not isinstance(entries, list):
            _fail(OBLIGATION_REDACTION, group + ' redaction observations are missing')
        by_name = {}
        for index, value in enumerate(entries):
            entry = _mapping(value, group + ' redaction entry ' + str(index),
                             OBLIGATION_REDACTION)
            name = entry.get('name')
            if not isinstance(name, str) or not name or name in by_name:
                _fail(OBLIGATION_REDACTION, group + ' redaction names are invalid')
            by_name[name] = entry
            if entry.get('clean') is not True:
                _fail(OBLIGATION_REDACTION, name + ' leaked a draft-token canary')
        if not set(required).issubset(by_name):
            _fail(OBLIGATION_REDACTION, group + ' redaction coverage is incomplete')
