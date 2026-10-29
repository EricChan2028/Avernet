"""Semantic checks over non-credential Draft delivery observations.

The black-box child emits only statuses, fixed revision metadata and hashes.
These functions independently reject any observation that violates the eight
frozen obligations; they never inspect or retain an authorization credential.
"""
from __future__ import annotations

import re

from ..run_state import RunStateError

OBLIGATION_CLIENT_ID = 'DRAFT-DELIVERY.client-selected-idempotent-create'
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


class DraftDeliveryViolation(RunStateError):
    """A live observation contradicted a frozen Draft delivery obligation."""


def _fail(obligation: str, detail: str) -> None:
    raise DraftDeliveryViolation(obligation + ': ' + detail)


def _mapping(value: object, field: str) -> dict:
    if not isinstance(value, dict):
        _fail(OBLIGATION_CLIENT_ID, field + ' observation is missing')
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        _fail(OBLIGATION_CLIENT_ID, field + ' must be an integer')
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
