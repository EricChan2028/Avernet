"""Semantic checks over non-credential Draft delivery observations.

The black-box child emits only statuses, fixed revision metadata and hashes.
These functions independently reject any observation that violates the eight
frozen obligations; they never inspect or retain an authorization credential.
"""
from __future__ import annotations

import re

from ..run_state import RunStateError

OBLIGATION_CLIENT_ID = 'DRAFT-DELIVERY.client-selected-idempotent-create'
OBLIGATION_APPEND = 'DRAFT-DELIVERY.immutable-revision-append'
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
