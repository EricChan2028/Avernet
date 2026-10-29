"""Explicit, bounded delivery traversal; never an atomic snapshot or drain proof."""
from __future__ import annotations

import time

from .evidence import EvidenceError
from .profile import ConfigError, object_keys

CONTRACT = 'bcs-session-deliveries-keyset-v1'


def validate_limits(value: object) -> dict:
    value = object_keys(value, {'contract', 'page_size', 'max_pages', 'max_rows'}, 'delivery pagination')
    if value['contract'] != CONTRACT:
        raise ConfigError('unsupported delivery pagination contract')
    for key, maximum in (('page_size', 100), ('max_pages', 8), ('max_rows', 500)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ConfigError('delivery pagination requires finite integer bounds')
    return dict(value)


def query_arguments(plan, *, after_id: int, limit: int) -> dict:
    from .delivery_observation import query_arguments as legacy_arguments
    args = legacy_arguments(plan)
    if (type(after_id) is not int or not 0 <= after_id <= (1 << 64) - 1
            or type(limit) is not int or not 1 <= limit <= 100):
        raise EvidenceError('delivery page cursor or limit is invalid')
    # MySQL migration 021 has an unsigned integer primary key. Do not rely on
    # textual delivery UUID collation or expose arbitrary SQL/order selectors.
    return {**args, 'select': 'id,' + args['select'],
            'where': args['where'] + f' AND id > {after_id}',
            'orderby': 'id asc', 'limit': str(limit)}


def collect(plan, limits: dict, fetch, *, deadline: float, progress: dict) -> list[dict]:
    """The caller authorizes and debits every fetch, including an empty probe.

    Only a fully validated traversal returns rows. Partial rows are not supplied
    to downstream alias authorization; counts/reason survive in ``progress``.
    """
    from .delivery_observation import project_rows
    limits = validate_limits(limits)
    cursor, rows, seen = 0, [], set()
    progress.update(contract=CONTRACT, pages=0, queries=0, rows=0, status='INCOMPLETE',
                    page_count_basis='ATTEMPTS',
                    reason='NOT_FINISHED', consistency='NON_ATOMIC_TRAVERSAL',
                    scope_status='UNCONFIRMED')
    try:
        while progress['pages'] < limits['max_pages']:
            if time.monotonic() >= deadline:
                raise TimeoutError('delivery traversal deadline expired')
            # At the aggregate cap a one-row probe can establish end-of-scan,
            # but one extra row must fail rather than silently truncate.
            limit = min(limits['page_size'], limits['max_rows'] - len(rows) + 1)
            progress['pages'] += 1
            payload = fetch(cursor, limit)
            if time.monotonic() >= deadline:
                raise TimeoutError('late delivery page is not accepted')
            if (not isinstance(payload, dict) or payload.get('success') is not True
                    or type(payload.get('truncated', False)) is not bool or payload.get('truncated', False)
                    or not isinstance(payload.get('data'), list) or len(payload['data']) > limit):
                raise EvidenceError('delivery page failed or has invalid completeness')
            page = []
            for value in payload['data']:
                if (not isinstance(value, dict) or type(value.get('id')) is not int
                        or not cursor < value['id'] <= (1 << 64) - 1):
                    raise EvidenceError('delivery keyset cursor did not strictly advance')
                projected = project_rows(plan, {'success': True, 'data': [value]}, frozenset(plan.target_bot_ids))[0]
                if projected['delivery_id'] in seen:
                    raise EvidenceError('delivery identity repeated across pages')
                cursor = value['id']; seen.add(projected['delivery_id']); page.append(projected)
            if len(rows) + len(page) > limits['max_rows']:
                progress['reason'] = 'ROW_LIMIT'
                raise EvidenceError('delivery traversal exceeds aggregate row budget')
            rows.extend(page); progress['rows'] = len(rows)
            if len(page) < limit:
                progress.update(status='COMPLETE', reason='SHORT_PAGE')
                return rows
        progress['reason'] = 'PAGE_LIMIT'
        raise EvidenceError('full delivery page at page budget remains incomplete')
    except BaseException:
        if progress['reason'] == 'NOT_FINISHED':
            progress['reason'] = 'QUERY_OR_VALIDATION_FAILED'
        raise
