"""Versioned retained suite and exact local resource budgets.

These declarations govern executor/report inventory only. They do not grant
remote effect admission, cleanup authority, pool release or retention quota.
"""
from __future__ import annotations

from .profile import ConfigError

CONTROL_SUITE = 'preprod-retained-v1'
FILE_SUITE = 'preprod-retained-file-v1'

_CASES_BY_SUITE = {
    CONTROL_SUITE: ('RETAIN-GROUP-01', 'RETAIN-SESSION-01'),
    FILE_SUITE: ('RETAIN-FILE-01',),
}

_RESOURCE_BUDGETS = {
    'RETAIN-GROUP-01': {'group': 1, 'session': 1},
    'RETAIN-SESSION-01': {'group': 1, 'session': 2},
    'RETAIN-FILE-01': {'group': 1, 'session': 2, 'file': 1},
}


def retained_suites() -> frozenset[str]:
    return frozenset(_CASES_BY_SUITE)


def retained_cases(suite: str) -> tuple[str, ...]:
    try:
        return _CASES_BY_SUITE[suite]
    except KeyError:
        raise ConfigError('unsupported retained suite') from None


def suite_for_case(case_id: str) -> str:
    for suite, cases in _CASES_BY_SUITE.items():
        if case_id in cases:
            return suite
    raise ConfigError('unknown retained Case')


def resource_budget(case_id: str) -> dict[str, int]:
    try:
        return dict(_RESOURCE_BUDGETS[case_id])
    except KeyError:
        raise ConfigError('unknown retained Case resource budget') from None


def validate_resource_snapshot(case_id: str, run_id: str, rows: list[dict], *,
                               require_complete: bool) -> dict[str, int]:
    """Validate executor-owned counts and, for PASS, the exact parent topology."""
    budget = resource_budget(case_id)
    counts = {kind: 0 for kind in budget}
    for row in rows:
        if (not isinstance(row, dict) or row.get('case_id') != case_id or row.get('attempt') != 1
                or row.get('kind') not in budget):
            raise ConfigError('retained inventory is outside the declared Case resource budget')
        counts[row['kind']] += 1
    if any(counts[kind] > maximum for kind, maximum in budget.items()):
        raise ConfigError('retained inventory exceeds a per-kind Case resource budget')
    if not require_complete:
        return counts
    if counts != budget:
        raise ConfigError('successful retained Case is missing its exact resource inventory')
    handles = {row.get('handle'): row for row in rows}
    if None in handles or len(handles) != len(rows):
        raise ConfigError('successful retained Case has invalid resource handles')
    for row in rows:
        if row['kind'] == 'group':
            valid = row.get('parent') is None
        elif row['kind'] == 'session':
            parent = handles.get(row.get('parent'))
            valid = parent is not None and parent.get('kind') == 'group'
        else:
            parent = handles.get(row.get('parent'))
            valid = (parent is not None and parent.get('kind') == 'session'
                     and parent.get('request_id') == 'e2e-pre03-session-' + run_id)
        if not valid:
            raise ConfigError('successful retained Case resource parent topology is invalid')
    return counts
