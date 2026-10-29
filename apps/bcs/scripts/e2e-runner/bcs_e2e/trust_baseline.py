"""Read-only preconditions for the complete trusted-team Story, not its result."""
from __future__ import annotations

from .profile import identifier

CONTRACT = 'owned-local-trusted-team-baseline-v1'
ROLES = ('CEO', 'ENG', 'PM', 'QA', 'CS')
# Preserve the original public auto-accept, protected accept/reject and CLI pair.
PAIRS = (('CEO', 'ENG', 'public-auto-accept'), ('QA', 'PM', 'protected-accept'),
         ('ENG', 'CS', 'protected-reject'), ('CEO', 'PM', 'cli-accept'))
DESIRED_VISIBILITY = {'ENG': 'public', 'PM': 'protected', 'CS': 'protected'}


class TrustBaselineError(RuntimeError):
    pass


def visibility(payload: dict, bot_id: str) -> str:
    value = _data(payload)
    if (not isinstance(value, dict) or value.get('bot_uuid') != bot_id
            or value.get('visibility') not in ('public', 'protected', 'private')):
        raise TrustBaselineError('baseline visibility does not match the bound Bot')
    return value['visibility']


def _data(payload: dict):
    if (not isinstance(payload, dict) or payload.get('success') is not True
            or type(payload.get('truncated', False)) is not bool or payload.get('truncated', False)):
        raise TrustBaselineError('baseline response failed or is incomplete')
    return payload.get('data')


def require_empty(payload: dict, *, operation: str, bot_id: str) -> None:
    if operation not in ('friends', 'pending-sent', 'pending-received'):
        raise TrustBaselineError('unsupported baseline list')
    rows = _data(payload)
    if not isinstance(rows, list) or len(rows) >= 100:
        raise TrustBaselineError('baseline list has invalid or incomplete cardinality')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TrustBaselineError('baseline list row is invalid')
        if operation == 'friends':
            key = identifier(row.get('bot_uuid'), 'baseline friend ID')
        elif operation in ('pending-sent', 'pending-received'):
            key = identifier(row.get('id'), 'baseline pending request ID')
            for field in ('from_bot', 'to_bot'):
                identifier(row.get(field), 'baseline request actor')
            side = 'from_bot' if operation == 'pending-sent' else 'to_bot'
            if row.get(side) != bot_id or row.get('status') != 'pending':
                raise TrustBaselineError('pending request differs from the bound direction or state')
        else:
            raise TrustBaselineError('unsupported baseline list')
        if key in seen:
            raise TrustBaselineError('baseline list contains duplicate identities')
        seen.add(key)
    if rows:
        # These are fresh isolated local fixtures. Never remove a relationship
        # or decide a request to manufacture the expected empty baseline.
        raise TrustBaselineError('existing relationship or pending request conflicts with the fresh fixture baseline')


def result(bindings: dict, original_visibility: dict) -> dict:
    if set(bindings) != set(ROLES) or set(original_visibility) != set(ROLES):
        raise TrustBaselineError('trusted-team baseline requires every declared role')
    ids = [identifier(bindings[role], 'baseline Bot ID') for role in ROLES]
    if len(set(ids)) != len(ids) or any(v not in ('public', 'protected', 'private') for v in original_visibility.values()):
        raise TrustBaselineError('baseline roles or visibility are invalid')
    return {'contract': CONTRACT, 'status': 'API_BASELINE_OBSERVED', 'ready_to_execute': False,
            'business_writes': 0, 'friendship_mutations': 0, 'visibility_mutations': 0,
            'roles': [{'role': role, 'bot_id': bindings[role], 'original_visibility': original_visibility[role],
                       'friends_returned': 0, 'pending_sent_returned': 0, 'pending_received_returned': 0}
                      for role in ROLES],
            'required_absent_pairs': [{'from_role': sender, 'to_role': receiver, 'scenario': scenario,
                                      'status': 'ABSENT_IN_API_BASELINE'} for sender, receiver, scenario in PAIRS],
            'visibility_changes_required': [{'role': role, 'before': original_visibility[role], 'required': target}
                                            for role, target in DESIRED_VISIBILITY.items()
                                            if original_visibility[role] != target],
            'consistency': 'NON_ATOMIC_API_READS', 'storage_completeness': 'NOT_VERIFIED',
            'story_admission': 'NOT_EVALUATED', 'full_story_status': 'NOT_MIGRATED',
            'note': 'Fresh owned-local API baseline only; no remote preflight or proof of database absence.'}
