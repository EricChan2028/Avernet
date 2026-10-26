"""Versioned retained quota including bounded file effects.

The v2 journal occupies the same pool-local path as v1. An upgrade is explicit,
validates the complete predecessor, and embeds its exact value without rewriting
historical reservation rows or Run artifacts.
"""
from __future__ import annotations

import copy
import re
import time

from .pool import PoolLease, pool_path, pool_scope
from .profile import ConfigError, fingerprint, identifier, object_keys
from .run_state import read, write
from . import retention_quota as v1

CONTRACT = 'retained-resource-quota-v2'
SOURCE_BINDING_CONTRACT = 'retained-resource-quota-source-binding-v1'
GROUP_LIMIT_CHANGE_CONTRACT = 'retained-group-limit-increase-v1'
GROUP_LIMIT_CHANGE_KEYS = {'contract', 'run_id', 'profile_digest', 'supervisor',
                           'reviewed_at', 'expires_at', 'reference', 'evidence_sha256',
                           'previous_max_groups', 'new_max_groups'}
DIMENSIONS = ('groups', 'sessions', 'files', 'uploaded_bytes', 'share_mints')
COSTS = {
    'RETAIN-GROUP-01': {'groups': 1, 'sessions': 1, 'files': 0, 'uploaded_bytes': 0, 'share_mints': 0},
    'RETAIN-SESSION-01': {'groups': 1, 'sessions': 2, 'files': 0, 'uploaded_bytes': 0, 'share_mints': 0},
    'RETAIN-FILE-01': {'groups': 1, 'sessions': 2, 'files': 1, 'uploaded_bytes': 4096, 'share_mints': 2},
}
MAXIMUMS = {'groups': 10000, 'sessions': 10000, 'files': 10000,
            'uploaded_bytes': 1_000_000_000, 'share_mints': 10000}


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ConfigError(label + ' requires an exact sha256 digest')
    return value


def _validate_group_limit_change(value: object, profile: dict | None = None, *,
                                 run_id: str | None = None, live: bool) -> dict:
    receipt = object_keys(value, GROUP_LIMIT_CHANGE_KEYS, 'retained group limit increase')
    identifier(receipt['run_id'], 'group limit increase Run ID')
    _digest(receipt['profile_digest'], 'group limit increase profile')
    identifier(receipt['supervisor'], 'group limit increase supervisor')
    identifier(receipt['reference'], 'group limit increase evidence reference')
    _digest(receipt['evidence_sha256'], 'group limit increase evidence')
    previous, new = receipt['previous_max_groups'], receipt['new_max_groups']
    if (receipt['contract'] != GROUP_LIMIT_CHANGE_CONTRACT
            or type(previous) is not int or type(new) is not int
            or not 1 <= previous < MAXIMUMS['groups'] or new != previous + 1):
        raise ConfigError('retained group limit increase permits exactly one additional group')
    reviewed, expires = receipt['reviewed_at'], receipt['expires_at']
    if (type(reviewed) is not int or type(expires) is not int or not 0 < reviewed < expires
            or expires - reviewed > 3600):
        raise ConfigError('retained group limit increase validity is invalid')
    if profile is not None and (receipt['profile_digest'] != fingerprint(profile)
            or receipt['supervisor'] != profile['human']['user_id']):
        raise ConfigError('retained group limit increase differs from the permitted target/Human')
    if run_id is not None and receipt['run_id'] != run_id:
        raise ConfigError('retained group limit increase differs from the permitted Run')
    checked_at = int(time.time()) if live else reviewed
    if not reviewed <= checked_at < expires:
        raise ConfigError('retained group limit increase is expired or not yet valid')
    return copy.deepcopy(receipt)


def validate_group_limit_authorization(quota: dict, profile: dict, run_id: str) -> None:
    """Recheck live amendment authority before preflight and again under the pool guard."""
    if quota.get('mode') == 'raise-group-limit':
        _validate_group_limit_change(quota.get('group_limit_change'), profile,
                                     run_id=run_id, live=True)


def validate_quota(value: object) -> dict:
    optional = {'group_limit_change'} & set(value) if isinstance(value, dict) else set()
    quota = object_keys(value, {'contract', 'registry_id', 'mode', 'expected_digest',
        *(f'max_{name}' for name in DIMENSIONS), *(f'baseline_{name}' for name in DIMENSIONS),
        'inventory_reference', 'inventory_sha256', 'file_inventory_reference',
        'file_inventory_sha256'} | optional, 'retention quota v2')
    modes = ('initialize', 'existing', 'upgrade-v1', 'raise-group-limit')
    if quota['contract'] != CONTRACT or quota['mode'] not in modes:
        raise ConfigError('retention quota v2 requires an explicit supported accounting transition')
    if (quota['mode'] == 'raise-group-limit') != ('group_limit_change' in quota):
        raise ConfigError('retention quota v2 group limit receipt is allowed only for its explicit mode')
    identifier(quota['registry_id'], 'quota registry ID')
    identifier(quota['inventory_reference'], 'reviewed retention inventory reference')
    identifier(quota['file_inventory_reference'], 'reviewed file inventory reference')
    _digest(quota['inventory_sha256'], 'retention inventory')
    _digest(quota['file_inventory_sha256'], 'file inventory')
    expected = quota['expected_digest']
    if quota['mode'] == 'initialize':
        if expected != 'absent':
            raise ConfigError('retention quota v2 initialization requires explicit absent accounting')
    else:
        _digest(expected, 'retention quota predecessor')
    for resource in DIMENSIONS:
        maximum, baseline = quota['max_'+resource], quota['baseline_'+resource]
        if (type(maximum) is not int or not 1 <= maximum <= MAXIMUMS[resource]
                or type(baseline) is not int or not 0 <= baseline <= maximum):
            raise ConfigError('retention quota v2 baseline and limits must be bounded nonnegative integers')
    if 'group_limit_change' in quota:
        receipt = _validate_group_limit_change(quota['group_limit_change'], live=False)
        if (receipt['previous_max_groups'] + 1 != quota['max_groups']
                or receipt['new_max_groups'] != quota['max_groups']):
            raise ConfigError('retained group limit increase does not match the requested maximum')
    return copy.deepcopy(quota)


def _policy(quota: dict) -> dict:
    return {key: value for key, value in quota.items()
            if key not in {'mode', 'expected_digest', 'group_limit_change'}}


def _v1_quota(quota: dict) -> dict:
    return {'contract': v1.CONTRACT, 'registry_id': quota['registry_id'], 'mode': 'existing',
            'expected_digest': quota['expected_digest'],
            'max_groups': quota['max_groups'], 'max_sessions': quota['max_sessions'],
            'baseline_groups': quota['baseline_groups'], 'baseline_sessions': quota['baseline_sessions'],
            'inventory_reference': quota['inventory_reference'],
            'inventory_sha256': quota['inventory_sha256']}


def _new_journal(profile: dict, quota: dict, *, legacy_v1: dict | None = None,
                 previous_v2: dict | None = None) -> dict:
    if legacy_v1 is not None and previous_v2 is not None:
        raise ConfigError('retention quota v2 predecessor formats cannot be mixed')
    value = {'schema_version': 1, 'contract': CONTRACT, 'target': v1.target_binding(profile),
             'policy': _policy(quota), 'reservations': []}
    if legacy_v1 is not None:
        value['predecessor'] = {'contract': v1.CONTRACT, 'digest': fingerprint(legacy_v1)}
        value['legacy_v1'] = copy.deepcopy(legacy_v1)
    if previous_v2 is not None:
        value['predecessor'] = {'contract': CONTRACT, 'digest': fingerprint(previous_v2)}
        value['previous_v2'] = copy.deepcopy(previous_v2)
        value['group_limit_change'] = copy.deepcopy(quota['group_limit_change'])
    return value

def _validate_row(row: object) -> dict:
    entry = object_keys(row, {'run_id', 'case_id', 'profile_digest', 'review_digest', 'cost'},
                        'quota v2 reservation')
    identifier(entry['run_id'], 'reserved Run ID')
    if (not isinstance(entry['case_id'], str) or entry['case_id'] not in COSTS
            or not isinstance(entry['cost'], dict) or set(entry['cost']) != set(DIMENSIONS)
            or any(type(entry['cost'][key]) is not int for key in DIMENSIONS)
            or entry['cost'] != COSTS[entry['case_id']]
            or any(not isinstance(entry[key], str) or re.fullmatch('[0-9a-f]{64}', entry[key]) is None
                   for key in ('profile_digest', 'review_digest'))):
        raise ConfigError('retention quota v2 journal contains inconsistent reservations')
    return entry


def _quota_from_policy(policy: object, expected_digest: str) -> dict:
    if not isinstance(policy, dict):
        raise ConfigError('retention quota v2 predecessor policy is invalid')
    return validate_quota({**copy.deepcopy(policy), 'mode': 'existing',
                           'expected_digest': expected_digest})


def _validate_policy_increase(old_quota: dict, quota: dict, receipt: dict) -> None:
    old_policy, new_policy = _policy(old_quota), _policy(quota)
    old_groups, new_groups = old_policy.pop('max_groups'), new_policy.pop('max_groups')
    if (old_policy != new_policy or new_groups != old_groups + 1
            or receipt['previous_max_groups'] != old_groups
            or receipt['new_max_groups'] != new_groups):
        raise ConfigError('retained group limit increase changed non-group policy or more than one group')


def validate_journal(value: object, profile: dict, quota: dict) -> tuple[dict, set[str]]:
    lineage = {'predecessor', 'legacy_v1', 'previous_v2', 'group_limit_change'}
    optional = lineage & set(value) if isinstance(value, dict) else set()
    journal = object_keys(value, {'schema_version', 'contract', 'target', 'policy', 'reservations'} | optional,
                          'quota v2 journal')
    if (type(journal['schema_version']) is not int or journal['schema_version'] != 1
            or journal['contract'] != CONTRACT or journal['target'] != v1.target_binding(profile)
            or journal['policy'] != _policy(quota)):
        raise ConfigError('retention quota v2 target/baseline/limits differ; implicit rebasing is forbidden')
    legacy = {'predecessor', 'legacy_v1'}
    increased = {'predecessor', 'previous_v2', 'group_limit_change'}
    if optional and optional not in (legacy, increased):
        raise ConfigError('retention quota v2 predecessor is incomplete or mixes lineage formats')
    totals = {key: quota['baseline_'+key] for key in DIMENSIONS}
    seen: set[str] = set()
    if optional == legacy:
        predecessor = object_keys(journal['predecessor'], {'contract', 'digest'}, 'quota v2 predecessor')
        if (predecessor['contract'] != v1.CONTRACT
                or _digest(predecessor['digest'], 'quota v2 predecessor') != fingerprint(journal['legacy_v1'])):
            raise ConfigError('retention quota v2 predecessor binding is invalid')
        legacy_quota = _v1_quota(quota)
        legacy_quota['expected_digest'] = predecessor['digest']
        legacy_totals, legacy_seen = v1._validate_journal(journal['legacy_v1'], profile, legacy_quota)
        seen.update(legacy_seen)
        for key in ('groups', 'sessions'):
            totals[key] = legacy_totals[key]
    elif optional == increased:
        predecessor = object_keys(journal['predecessor'], {'contract', 'digest'}, 'quota v2 predecessor')
        previous = journal['previous_v2']
        if (predecessor['contract'] != CONTRACT
                or _digest(predecessor['digest'], 'quota v2 predecessor') != fingerprint(previous)):
            raise ConfigError('retention quota v2 predecessor binding is invalid')
        if not isinstance(previous, dict):
            raise ConfigError('retention quota v2 predecessor is invalid')
        old_quota = _quota_from_policy(previous.get('policy'), predecessor['digest'])
        old_totals, old_seen = validate_journal(previous, profile, old_quota)
        receipt = _validate_group_limit_change(journal['group_limit_change'], profile, live=False)
        _validate_policy_increase(old_quota, quota, receipt)
        totals, seen = old_totals, set(old_seen)
    rows = journal['reservations']
    if not isinstance(rows, list) or len(rows) > v1.MAX_ENTRIES:
        raise ConfigError('retention quota v2 journal is invalid or at its finite capacity')
    for index, raw in enumerate(rows):
        row = _validate_row(raw)
        if (optional == increased and index == 0
                and row['run_id'] != journal['group_limit_change']['run_id']):
            raise ConfigError('retained group limit increase must be consumed by its first new reservation')
        if row['run_id'] in seen:
            raise ConfigError('retention quota v2 journal contains duplicate Run reservations')
        seen.add(row['run_id'])
        for key in DIMENSIONS:
            totals[key] += row['cost'][key]
    if any(totals[key] > quota['max_'+key] for key in DIMENSIONS):
        raise ConfigError('retention quota v2 journal exceeds its reviewed limits')
    return totals, seen


def _load(profile: dict, quota: dict, path) -> tuple[dict, str]:
    exists = path.exists() or path.is_symlink()
    if quota['mode'] == 'initialize':
        if exists:
            raise ConfigError('retention quota registry already exists; initialization cannot reset usage')
        return _new_journal(profile, quota), 'absent'
    if not exists:
        raise ConfigError('retention quota registry is missing; never recreate previous accounting')
    predecessor = read(path)
    if fingerprint(predecessor) != quota['expected_digest']:
        raise ConfigError('retention quota snapshot changed; obtain a fresh accounting review')
    if quota['mode'] == 'upgrade-v1':
        legacy_quota = _v1_quota(quota)
        v1._validate_journal(predecessor, profile, legacy_quota)
        return _new_journal(profile, quota, legacy_v1=predecessor), quota['expected_digest']
    if not isinstance(predecessor, dict) or predecessor.get('contract') != CONTRACT:
        raise ConfigError('retention quota v2 existing mode cannot migrate or downgrade another journal')
    if quota['mode'] == 'raise-group-limit':
        old_quota = _quota_from_policy(predecessor.get('policy'), quota['expected_digest'])
        validate_journal(predecessor, profile, old_quota)
        receipt = _validate_group_limit_change(quota['group_limit_change'], profile, live=False)
        _validate_policy_increase(old_quota, quota, receipt)
        return _new_journal(profile, quota, previous_v2=predecessor), quota['expected_digest']
    return predecessor, quota['expected_digest']

def _source_binding(journal: dict, entry: dict) -> dict:
    result = {'schema_version': 1, 'contract': SOURCE_BINDING_CONTRACT,
              'journal_contract': CONTRACT, 'target': copy.deepcopy(journal['target']),
              'policy': copy.deepcopy(journal['policy']),
              'reservation_digest': fingerprint(entry),
              'reservations_digest': fingerprint(journal['reservations'])}
    if 'legacy_v1' in journal:
        result['legacy_v1_digest'] = fingerprint(journal['legacy_v1'])
    if 'previous_v2' in journal:
        result['previous_v2_digest'] = fingerprint(journal['previous_v2'])
        result['group_limit_change_digest'] = fingerprint(journal['group_limit_change'])
    return result


def validate_source_binding(value: object, profile: dict, entry: dict, limits: dict) -> dict:
    required = {'schema_version', 'contract', 'journal_contract', 'target', 'policy',
                'reservation_digest', 'reservations_digest'}
    lineage = {'legacy_v1_digest', 'previous_v2_digest', 'group_limit_change_digest'}
    optional = lineage & set(value) if isinstance(value, dict) else set()
    binding = object_keys(value, required | optional, 'quota v2 source binding')
    if optional not in (set(), {'legacy_v1_digest'},
                         {'previous_v2_digest', 'group_limit_change_digest'}):
        raise ConfigError('quota v2 source binding mixes or incompletely binds predecessor formats')
    if (type(binding['schema_version']) is not int or binding['schema_version'] != 1
            or binding['contract'] != SOURCE_BINDING_CONTRACT or binding['journal_contract'] != CONTRACT
            or binding['target'] != v1.target_binding(profile)
            or binding['reservation_digest'] != fingerprint(entry)):
        raise ConfigError('retention quota v2 source binding differs from its Run/Profile')
    _digest(binding['reservation_digest'], 'quota v2 reservation binding')
    _digest(binding['reservations_digest'], 'quota v2 journal rows binding')
    if 'legacy_v1_digest' in binding:
        _digest(binding['legacy_v1_digest'], 'quota v2 legacy binding')
    if 'previous_v2_digest' in binding:
        _digest(binding['previous_v2_digest'], 'quota v2 previous journal binding')
        _digest(binding['group_limit_change_digest'], 'quota v2 group limit change binding')
    policy = validate_quota({**binding['policy'], 'mode': 'initialize', 'expected_digest': 'absent'})
    if {key: policy['max_'+key] for key in DIMENSIONS} != limits:
        raise ConfigError('retention quota v2 limits differ from their reviewed source binding')
    return copy.deepcopy(binding)


def reserve(profile: dict, permission: dict, review, lease: PoolLease, case_id: str,
            quota: dict | None = None) -> dict:
    if not isinstance(lease, PoolLease) or case_id not in COSTS:
        raise ConfigError('quota v2 reservation requires a known retained Case and real held pool')
    lease.require_held()
    quota = validate_quota(permission['retention_quota']) if quota is None else validate_quota(quota)
    validate_group_limit_authorization(quota, profile, permission['run_id'])
    expected_path = pool_path(pool_scope(profile))
    if (lease.path != expected_path or lease.run_id != permission['run_id']
            or lease.profile_digest != fingerprint(profile) or permission['case_id'] != case_id):
        raise ConfigError('quota v2 reservation differs from the held Run/Case/Profile')
    path = expected_path.with_suffix('.retention-quota.json')
    journal, previous_digest = _load(profile, quota, path)
    totals, seen = validate_journal(journal, profile, quota)
    if len(journal['reservations']) >= v1.MAX_ENTRIES:
        raise ConfigError('retention quota v2 journal is invalid or at its finite capacity')
    if permission['run_id'] in seen:
        raise ConfigError('retained Run already consumed a quota reservation; no implicit retry')
    cost = COSTS[case_id]
    if any(totals[key] + cost[key] > quota['max_'+key] for key in DIMENSIONS):
        raise ConfigError('retained resource quota exhausted; no remote creation is admitted')
    entry = {'run_id': permission['run_id'], 'case_id': case_id,
             'profile_digest': fingerprint(profile), 'review_digest': fingerprint(permission),
             'cost': dict(cost)}
    accounted_after = {key: totals[key] + cost[key] for key in DIMENSIONS}
    journal['reservations'].append(entry)
    source_binding = _source_binding(journal, entry)
    lease.require_held()
    if review.validate() != permission:
        raise ConfigError('quota authorization changed before reservation')
    write(path, journal)
    lease.require_held()
    if read(path) != journal:
        raise ConfigError('quota reservation persistence differs from intended accounting')
    return {'schema_version': 1, 'contract': CONTRACT, 'status': 'RESERVED_MAXIMUM',
            'registry_id': quota['registry_id'], 'previous_journal_digest': previous_digest,
            'journal_digest': fingerprint(journal), 'journal_snapshot': copy.deepcopy(journal),
            'reservation': entry, 'cost': dict(cost),
            'accounted_before': totals, 'accounted_after': accounted_after,
            'limits': {key: quota['max_'+key] for key in DIMENSIONS},
            'source_binding': source_binding,
            'basis': 'OPERATOR_BASELINE_PLUS_LOCAL_MAXIMUM_RESERVATIONS',
            'automatic_refund': False, 'physical_storage_usage': 'NOT_MEASURED'}
