"""Conservative pool-local admission accounting, never a remote storage census.

Approved baseline + pre-dispatch maximum reservations consume quota permanently
until a separately implemented/authorized reconciliation exists. No automatic
refund from API errors, terminal execution, elapsed time or resource deletion.
"""
from __future__ import annotations

import copy
import re

from .pool import PoolLease, pool_path, pool_scope
from .profile import ConfigError, fingerprint, identifier, object_keys
from .run_state import read, write

CONTRACT = 'retained-resource-quota-v1'
COSTS = {'RETAIN-GROUP-01': {'groups': 1, 'sessions': 1},
         'RETAIN-SESSION-01': {'groups': 1, 'sessions': 2}}
MAX_ENTRIES = 1000


def _validate_v1_quota(value: object) -> dict:
    quota = object_keys(value, {'contract', 'registry_id', 'mode', 'expected_digest',
        'max_groups', 'max_sessions', 'baseline_groups', 'baseline_sessions',
        'inventory_reference', 'inventory_sha256'}, 'retention quota')
    if quota['contract'] != CONTRACT or quota['mode'] not in ('initialize', 'existing'):
        raise ConfigError('retention quota requires explicit initialize/existing reviewed accounting')
    identifier(quota['registry_id'], 'quota registry ID')
    identifier(quota['inventory_reference'], 'reviewed retention inventory reference')
    for key in ('inventory_sha256',):
        if not isinstance(quota[key], str) or re.fullmatch('[0-9a-f]{64}', quota[key]) is None:
            raise ConfigError('retention inventory requires its reviewed evidence digest')
    expected = quota['expected_digest']
    if (quota['mode'] == 'initialize' and expected != 'absent'
            or quota['mode'] == 'existing' and (not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None)):
        raise ConfigError('retention quota requires the exact current journal digest or explicit absent initialization')
    for resource in ('groups', 'sessions'):
        maximum, baseline = quota['max_'+resource], quota['baseline_'+resource]
        if (type(maximum) is not int or not 1 <= maximum <= 10000
                or type(baseline) is not int or not 0 <= baseline <= maximum):
            raise ConfigError('retention baseline and limits must be bounded nonnegative integers')
    return copy.deepcopy(quota)


def validate_quota(value: object) -> dict:
    """Dispatch the explicitly versioned quota configuration without migration."""
    if isinstance(value, dict) and value.get('contract') == 'retained-resource-quota-v2':
        from .retention_quota_v2 import validate_quota as validate_v2
        return validate_v2(value)
    return _validate_v1_quota(value)


def target_binding(profile: dict) -> dict:
    return {'pool': pool_scope(profile), 'origin': profile['entrypoint']['origin'],
            'environment': profile['environment']['expected_bcs_env'], 'human': profile['human'],
            'actors': profile['actors'], 'observer': profile['observer']}


def _policy(quota: dict) -> dict:
    return {key:value for key,value in quota.items() if key not in {'mode', 'expected_digest'}}


def _validate_journal(value: dict, profile: dict, quota: dict) -> tuple[dict, set[str]]:
    object_keys(value, {'schema_version', 'contract', 'target', 'policy', 'reservations'}, 'quota journal')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT or value['target'] != target_binding(profile)
            or value['policy'] != _policy(quota)):
        raise ConfigError('retention quota target/baseline/limits differ; implicit rebasing is forbidden')
    rows = value['reservations']
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise ConfigError('retention quota journal is invalid or at its finite capacity')
    totals = {'groups': quota['baseline_groups'], 'sessions': quota['baseline_sessions']}
    seen: set[str] = set()
    for row in rows:
        object_keys(row, {'run_id', 'case_id', 'profile_digest', 'review_digest', 'cost'}, 'quota reservation')
        identifier(row['run_id'], 'reserved Run ID')
        if (row['run_id'] in seen or not isinstance(row['case_id'], str) or row['case_id'] not in COSTS
                or not isinstance(row['cost'], dict) or set(row['cost']) != {'groups', 'sessions'}
                or any(type(n) is not int for n in row['cost'].values())
                or row['cost'] != COSTS[row['case_id']]
                or any(not isinstance(row[k],str) or re.fullmatch('[0-9a-f]{64}',row[k]) is None
                       for k in ('profile_digest','review_digest'))):
            raise ConfigError('retention quota journal contains inconsistent reservations')
        seen.add(row['run_id'])
        for key in totals:
            totals[key] += row['cost'][key]
    if any(totals[key] > quota['max_'+key] for key in totals):
        raise ConfigError('retention quota journal exceeds its reviewed limits')
    return totals, seen


def _load(profile: dict, quota: dict, path) -> dict:
    exists = path.exists() or path.is_symlink()
    if quota['mode'] == 'initialize':
        if exists:
            raise ConfigError('retention quota registry already exists; initialization cannot reset usage')
        return {'schema_version': 1, 'contract': CONTRACT, 'target': target_binding(profile),
                'policy': _policy(quota), 'reservations': []}
    if not exists:
        raise ConfigError('retention quota registry is missing; never recreate previous accounting')
    value = read(path)
    if fingerprint(value) != quota['expected_digest']:
        raise ConfigError('retention quota snapshot changed; obtain a fresh accounting review')
    return value


def _reserve_v1(profile: dict, permission: dict, review, lease: PoolLease, case_id: str,
                quota: dict) -> dict:
    if case_id not in COSTS:
        raise ConfigError('quota reservation requires a known retained Case and real held pool')
    expected_path = pool_path(pool_scope(profile))
    if (lease.path != expected_path or lease.run_id != permission['run_id']
            or lease.profile_digest != fingerprint(profile) or permission['case_id'] != case_id):
        raise ConfigError('quota reservation differs from the held Run/Case/Profile')
    path = expected_path.with_suffix('.retention-quota.json')
    value = _load(profile, quota, path)
    previous_digest = 'absent' if quota['mode'] == 'initialize' else fingerprint(value)
    totals, seen = _validate_journal(value, profile, quota)
    if len(value['reservations']) >= MAX_ENTRIES:
        raise ConfigError('retention quota journal is invalid or at its finite capacity')
    if permission['run_id'] in seen:
        raise ConfigError('retained Run already consumed a quota reservation; no implicit retry')
    cost = COSTS[case_id]
    if any(totals[key] + cost[key] > quota['max_'+key] for key in totals):
        raise ConfigError('retained resource quota exhausted; no remote creation is admitted')
    entry = {'run_id': permission['run_id'], 'case_id': case_id, 'profile_digest': fingerprint(profile),
             'review_digest': fingerprint(permission), 'cost': dict(cost)}
    value['reservations'].append(entry)
    lease.require_held()
    if review.validate() != permission:
        raise ConfigError('quota authorization changed before reservation')
    # Caller holds the same non-recreated pool command guard as every writer.
    write(path, value)
    lease.require_held()
    if read(path) != value:
        raise ConfigError('quota reservation persistence differs from intended accounting')
    return {'contract': CONTRACT, 'status': 'RESERVED_MAXIMUM', 'registry_id': quota['registry_id'],
            'previous_journal_digest': previous_digest, 'journal_digest': fingerprint(value),
            'reservation': entry, 'cost': dict(cost),
            'accounted_after': {k: totals[k] + cost[k] for k in totals},
            'limits': {k: quota['max_'+k] for k in totals},
            'basis': 'OPERATOR_BASELINE_PLUS_LOCAL_MAXIMUM_RESERVATIONS',
            'automatic_refund': False, 'physical_storage_usage': 'NOT_MEASURED'}


def reserve(profile: dict, review, lease: PoolLease, case_id: str) -> dict:
    """Called once under the existing pool guard, before any remote effect."""
    if not isinstance(lease, PoolLease):
        raise ConfigError('quota reservation requires a known retained Case and real held pool')
    lease.require_held()
    permission = review.validate()
    quota = validate_quota(permission['retention_quota'])
    if quota['contract'] == CONTRACT:
        return _reserve_v1(profile, permission, review, lease, case_id, quota)
    from .retention_quota_v2 import reserve as reserve_v2
    return reserve_v2(profile, permission, review, lease, case_id, quota)
