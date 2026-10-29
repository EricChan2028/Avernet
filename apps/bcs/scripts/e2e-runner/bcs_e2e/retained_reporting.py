"""Read-only retained inventory/handoff projection, not closure or reuse authority."""
from __future__ import annotations

from pathlib import Path
import copy
import sqlite3

from .ledger import Ledger, LedgerError
from .profile import ConfigError, fingerprint
from .retained_contract import resource_budget
from .retention_quota import CONTRACT as QUOTA_CONTRACT, COSTS
from .retention_quota_v2 import (CONTRACT as QUOTA_V2_CONTRACT, COSTS as V2_COSTS,
                                 DIMENSIONS as V2_DIMENSIONS, MAXIMUMS as V2_MAXIMUMS,
                                 validate_journal as validate_v2_journal, validate_quota as validate_v2_quota,
                                 validate_source_binding)
from .run_state import RunStateError, read

FIELDS = ('handle', 'case_id', 'attempt', 'kind', 'parent', 'resource_id', 'origin', 'state', 'request_id')
ACTIONS = ['STOP_NEW_TEST_TRIGGERS', 'KEEP_POOL_QUARANTINED', 'REVIEW_EXACT_RESOURCE_INVENTORY',
           'ESTABLISH_INPUT_CUTOFF_AND_INFLIGHT_COVERAGE', 'OBTAIN_FRESH_COMPLETE_SCOPE_EVIDENCE',
           'REVIEW_RETENTION_QUOTA_BEFORE_REUSE']


def _require(condition, message):
    if not condition:
        raise ConfigError(message)


def _quota_v1(quota: dict, state: dict, profile: dict, case_id: str) -> dict:
    _require(case_id in COSTS and quota.get('contract') == QUOTA_CONTRACT,
             'retention quota is unsupported for this retained suite')
    cost = quota.get('cost')
    _require(isinstance(cost, dict) and set(cost) == {'groups', 'sessions'}
             and all(type(v) is int for v in cost.values()), 'invalid retention quota cost')
    entry = quota.get('reservation')
    _require(isinstance(entry, dict) and entry.get('run_id') == state['run_id']
             and entry.get('case_id') == case_id and entry.get('profile_digest') == fingerprint(profile)
             and isinstance(entry.get('cost'), dict) and fingerprint(cost) == fingerprint(entry['cost'])
             and cost == COSTS[case_id], 'retention quota reservation mismatch')
    totals, limits = quota.get('accounted_after'), quota.get('limits')
    _require(isinstance(totals, dict) and isinstance(limits, dict)
             and set(totals) == set(limits) == {'groups', 'sessions'}
             and all(type(totals[k]) is int and type(limits[k]) is int
                     and COSTS[case_id][k] <= totals[k] <= limits[k] <= 10000 for k in totals),
             'invalid retention quota accounting')
    return {'contract': QUOTA_CONTRACT, 'status': 'RESERVED_MAXIMUM',
            'accounted_at_reservation': totals, 'limits_at_reservation': limits,
            'automatic_refund': False, 'current_remaining_capacity': 'NOT_VERIFIED'}


def _quota_v2(quota: dict, state: dict, profile: dict, case_id: str) -> dict:
    required = {'schema_version', 'contract', 'status', 'registry_id', 'previous_journal_digest',
                'journal_digest', 'journal_snapshot', 'reservation', 'cost', 'accounted_before', 'accounted_after',
                'limits', 'source_binding', 'basis', 'automatic_refund', 'physical_storage_usage'}
    _require(set(quota) == required and type(quota['schema_version']) is int
             and quota['schema_version'] == 1 and case_id in V2_COSTS,
             'invalid retention quota v2 receipt schema')
    cost, entry = quota['cost'], quota['reservation']
    _require(isinstance(cost, dict) and set(cost) == set(V2_DIMENSIONS)
             and all(type(cost[k]) is int for k in V2_DIMENSIONS) and cost == V2_COSTS[case_id]
             and isinstance(entry, dict) and set(entry) == {'run_id', 'case_id', 'profile_digest', 'review_digest', 'cost'}
             and entry['run_id'] == state['run_id'] and entry['case_id'] == case_id
             and entry['profile_digest'] == fingerprint(profile) and entry['cost'] == cost,
             'retention quota v2 reservation mismatch')
    for key in ('profile_digest', 'review_digest'):
        _require(isinstance(entry[key], str) and len(entry[key]) == 64
                 and all(c in '0123456789abcdef' for c in entry[key]),
                 'retention quota v2 reservation digest is invalid')
    before, after, limits = quota['accounted_before'], quota['accounted_after'], quota['limits']
    _require(isinstance(before, dict) and isinstance(after, dict) and isinstance(limits, dict)
             and set(before) == set(after) == set(limits) == set(V2_DIMENSIONS)
             and all(type(before[k]) is int and type(after[k]) is int and type(limits[k]) is int
                     and 0 <= before[k] and after[k] == before[k] + cost[k]
                     and after[k] <= limits[k] <= V2_MAXIMUMS[k] for k in V2_DIMENSIONS),
             'invalid retention quota v2 accounting')
    _require(isinstance(quota['registry_id'], str) and quota['registry_id'],
             'invalid retention quota v2 registry')
    _require(isinstance(quota['journal_digest'], str) and len(quota['journal_digest']) == 64
             and all(c in '0123456789abcdef' for c in quota['journal_digest'])
             and (quota['previous_journal_digest'] == 'absent'
                  or isinstance(quota['previous_journal_digest'], str)
                  and len(quota['previous_journal_digest']) == 64
                  and all(c in '0123456789abcdef' for c in quota['previous_journal_digest'])),
             'invalid retention quota v2 journal binding')
    binding = validate_source_binding(quota['source_binding'], profile, entry, limits)
    _require(binding['policy']['registry_id'] == quota['registry_id'],
             'retention quota v2 registry differs from source binding')
    source_quota = validate_v2_quota({**binding['policy'], 'mode': 'initialize',
                                      'expected_digest': 'absent'})
    snapshot = quota['journal_snapshot']
    source_totals, _ = validate_v2_journal(snapshot, profile, source_quota)
    rows = snapshot['reservations']
    prior = copy.deepcopy(snapshot)
    prior['reservations'] = prior['reservations'][:-1]
    if len(rows) == 1 and 'previous_v2' in snapshot:
        expected_previous = fingerprint(snapshot['previous_v2'])
    elif len(rows) == 1 and 'legacy_v1' not in snapshot:
        expected_previous = 'absent'
    elif len(rows) == 1:
        expected_previous = fingerprint(snapshot['legacy_v1'])
    else:
        expected_previous = fingerprint(prior)
    _require(fingerprint(snapshot) == quota['journal_digest']
             and fingerprint(rows) == binding['reservations_digest']
             and rows and rows[-1] == entry
             and quota['previous_journal_digest'] == expected_previous
             and source_totals == after
             and {key: source_totals[key] - cost[key] for key in V2_DIMENSIONS} == before
             and ('legacy_v1' in snapshot) == ('legacy_v1_digest' in binding)
             and ('legacy_v1' not in snapshot
                  or fingerprint(snapshot['legacy_v1']) == binding['legacy_v1_digest'])
             and ('previous_v2' in snapshot) == ('previous_v2_digest' in binding)
             and ('group_limit_change' in snapshot) == ('group_limit_change_digest' in binding)
             and ('previous_v2' not in snapshot
                  or (fingerprint(snapshot['previous_v2']) == binding['previous_v2_digest']
                      and fingerprint(snapshot['group_limit_change']) == binding['group_limit_change_digest'])),
             'retention quota v2 totals differ from the bound journal snapshot')
    return {'contract': QUOTA_V2_CONTRACT, 'status': 'RESERVED_MAXIMUM',
            'accounted_at_reservation': after, 'limits_at_reservation': limits,
            'automatic_refund': False, 'current_remaining_capacity': 'NOT_VERIFIED'}


def _quota(value: dict, state: dict, report: dict, profile: dict, case_id: str) -> dict:
    quota = value.get('retention_quota')
    if quota is None:
        _require('retention_quota' not in state and 'retention_quota' not in report,
                 'retention quota receipt is missing')
        return {'status': 'NOT_RECORDED', 'current_remaining_capacity': 'NOT_VERIFIED',
                'automatic_refund': False}
    _require(isinstance(quota, dict) and quota == state.get('retention_quota') == report.get('retention_quota')
             and quota.get('status') == 'RESERVED_MAXIMUM' and quota.get('automatic_refund') is False,
             'retention quota receipt differs from its Run/Case')
    if quota.get('contract') == QUOTA_CONTRACT:
        return _quota_v1(quota, state, profile, case_id)
    if quota.get('contract') == QUOTA_V2_CONTRACT:
        return _quota_v2(quota, state, profile, case_id)
    raise ConfigError('retention quota receipt contract is unsupported')


def retained_summary(profile: dict, state: dict, state_dir: Path, manifest: dict,
                     reports: dict, resources: list[dict] | None, *, now_epoch: int) -> tuple[dict, list[str]]:
    declared = {c['id']: c for c in manifest.get('cases', []) if c.get('finalization') == 'retain'}
    if not declared:
        return {}, []
    base = {'status': 'BLOCKED', 'policy': 'retain', 'inventory_status': 'UNVERIFIED',
            'resources': [], 'automatic_expiry': False, 'automatic_delete_authorized': False,
            'pool_release_authorized': False, 'handoff': {'status': 'REQUIRED',
                'owner': 'UNKNOWN', 'owner_status': 'UNVERIFIED', 'review_state': 'UNKNOWN', 'actions': list(ACTIONS)},
            'quota': {'status': 'UNVERIFIED', 'current_remaining_capacity': 'NOT_VERIFIED', 'automatic_refund': False}}
    path = state_dir/'retention.json'
    try:
        _require(state.get('profile_digest') == fingerprint(profile), 'retained Run/profile mismatch')
        base['handoff'].update(owner=profile['human']['user_id'], owner_status='RUN_PROFILE_BOUND')
        if (not path.exists() and not path.is_symlink() and not reports and state.get('status') == 'PREPARED'
                and 'retention_quota' not in state and state.get('attempted_cases', []) == [] and resources == []):
            _require(read(state_dir/'run.json') == state, 'prepared Run changed during reporting')
            return {**base, 'status': 'NOT_RUN', 'handoff': {**base['handoff'], 'status': 'NOT_REQUIRED',
                     'review_state': 'NOT_SCHEDULED', 'actions': []}}, []
        if isinstance(state.get('retention_quota'), dict) and state['retention_quota'].get('status') == 'UNCONFIRMED':
            base['quota']['status'] = 'UNCONFIRMED'
        value = read(path)
        case_id = value.get('case_id')
        _require(isinstance(case_id, str) and case_id in declared and set(reports) == {case_id},
                 'retention Case/report inventory mismatch')
        budget = resource_budget(case_id)
        actual = reports[case_id]
        serial = None
        if 'admission_policy' in state:
            from .retained_serial import policy_view
            serial = policy_view(profile, state, state_dir, require_finalized=True)
            _require(actual.get('admission_policy') == state['admission_policy']
                     and actual.get('serial_admission_digest') == state['serial_admission_digest'],
                     'serial Case admission differs from its Run')
            base.update(serial_admission=serial)
            base['handoff']['actions'] = ['RETAIN_EXACT_RESOURCE_INVENTORY',
                'ACQUIRE_SERIAL_TRIGGER_AUTHORITY', 'RECHECK_DEPENDENCIES_AND_QUOTA_BEFORE_NEXT_RUN']
        _require(value.get('run_id') == state['run_id'] and value.get('profile_digest') == fingerprint(profile)
                 and value.get('owner') == profile['human']['user_id'] and value.get('policy') == 'retain'
                 and value.get('automatic_expiry') is False, 'retention ownership or policy mismatch')
        # Version 1 has no admitted complete-scope proof consumer yet. An edited
        # report cannot add one, even if every known delivery has completed.
        _require(actual.get('execution_status') == 'UNCONFIRMED' and actual.get('quiescence') == 'UNKNOWN'
                 and actual.get('resource_disposition') == 'RETAINED_UNRESOLVED'
                 and actual.get('cleanup') == 'RETAINED' and actual.get('pool_disposition') == ('RETAINED_SERIAL' if serial else 'QUARANTINED')
                 and value.get('disposition') == 'RETAINED_UNRESOLVED', 'unsupported retained closure or disposition claim')
        _require(isinstance(resources, list) and len(resources) <= sum(budget.values()),
                 'retained ledger is missing or exceeds the Case budget')
        projected = [{key: row[key] for key in FIELDS} for row in resources]
        _require(isinstance(value.get('resources'), list)
                 and fingerprint({'resources': value['resources']}) == fingerprint({'resources': projected}),
                 'retention inventory differs from the exact ledger')
        handles = {r['handle']: r for r in projected}
        _require(len(handles) == len(projected), 'duplicate retained resource handles')
        counts = {kind: 0 for kind in budget}
        known_ids = set()
        for row in projected:
            _require(row['case_id'] == case_id and type(row['attempt']) is int and row['attempt'] == 1
                     and row['kind'] in counts and row['origin'] in {'created', 'unknown'}, 'retained resource scope mismatch')
            counts[row['kind']] += 1
            if row['resource_id'] is not None:
                _require(row['resource_id'] not in known_ids, 'duplicate retained resource IDs')
                known_ids.add(row['resource_id'])
            if row['kind'] == 'group':
                _require(row['parent'] is None, 'retained Group has an unexpected parent')
            elif row['kind'] == 'session':
                parent = handles.get(row['parent'])
                _require(parent is not None and parent['kind'] == 'group', 'retained Session parent mismatch')
            else:
                parent = handles.get(row['parent'])
                _require(parent is not None and parent['kind'] == 'session'
                         and parent['request_id'] == 'e2e-pre03-session-' + state['run_id'],
                         'retained File parent is not the explicit Session')
        _require(all(counts[kind] <= limit for kind, limit in budget.items()),
                 'retained resource kinds exceed the Case budget')
        if actual.get('functional') == 'PASS':
            _require(counts == budget,
                     'successful retained Case is missing its declared resource inventory')
        inventory = ('UNKNOWN_RESOURCES' if any(r['state'] != 'live' or r['origin'] != 'created' or not r['resource_id']
                                              for r in projected) else 'COMPLETE')
        _require(value.get('inventory_status') == inventory, 'retention inventory status contradicts resources')
        deadline = value.get('review_after_epoch')
        _require(type(deadline) is int and 0 < deadline < 2**63, 'retention review deadline is invalid')
        quota = _quota(value, state, actual, profile, case_id)
        # Compare again around the independent read-only ledger snapshot. This
        # detects observed drift, not hostile writes by the local file owner.
        with Ledger(Path(state['ledger_path']), state['run_id'], fingerprint(profile), read_only=True) as ledger:
            _require(ledger.resource_snapshot() == resources, 'retained ledger changed during reporting')
        _require(read(path) == value and read(state_dir/'run.json') == state
                 and read(state_dir/f'report-{case_id}.json') == actual,
                 'retained source changed during reporting')
        return {**base, 'status': 'VERIFIED_LOCAL_INVENTORY', 'case_id': case_id,
                'inventory_status': inventory, 'resources': projected, 'quota': quota,
                'source_digest': fingerprint(value), 'resource_count': len(projected),
                'disposition': 'RETAINED_UNRESOLVED', 'verification_scope': 'LOCAL_ARTIFACT_BINDING_ONLY',
                'remote_existence': 'NOT_RECHECKED', 'handoff': {**base['handoff'],
                    'review_after_epoch': deadline, 'review_state': 'DUE' if now_epoch >= deadline else 'PENDING',
                    'review_deadline_is_ttl': False}}, []
    except (ConfigError, RunStateError, LedgerError, OSError, sqlite3.Error, KeyError, TypeError, ValueError):
        # Do not render unbound ownership, foreign IDs or free-form file content.
        return base, ['retention inventory, disposition, quota or source binding is invalid']


def retained_markdown(summary: dict) -> list[str]:
    retention = summary.get('retention')
    if not retention:
        return []
    def cell(value):
        return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('`', '&#96;').replace('\n', ' ').replace('\r', ' ')
    handoff = retention['handoff']
    lines = ['', '## Retained resources and manual handoff', '',
        'Functional success does not confirm execution completion. Review deadlines are not deletion TTLs.', '',
        '| Case | Functional | Execution | Resource disposition | Pool |', '|---|---|---|---|---|']
    for case in summary['cases']:
        if 'resource_disposition' in case:
            lines.append('| '+' | '.join(cell(case[k]) for k in ('id','functional','execution_status','resource_disposition','pool_disposition'))+' |')
    lines += ['', f"- Inventory verification: {retention['status']}; remote existence is not rechecked.",
              f"- Owner: {cell(handoff['owner'])}; handoff: {handoff['status']}; review: {handoff['review_state']}.",
              f"- Review-after epoch: {handoff.get('review_after_epoch', 'UNKNOWN')} (not an automatic expiry).",
              '- Automatic deletion and pool release: not authorized.',
              f"- Quota: {retention['quota']['status']}; current remaining capacity is NOT_VERIFIED."]
    if 'accounted_at_reservation' in retention['quota']:
        quota=retention['quota']
        for kind in quota['accounted_at_reservation']:
            lines.append(f"- {kind} at reservation: {quota['accounted_at_reservation'][kind]}/{quota['limits_at_reservation'][kind]}; no automatic refund.")
    if retention.get('serial_admission'):
        lines += ['- Serial admission: preflight passed at admission; the next Run requires fresh checks.',
                  '- Complete remote execution closure is diagnostic under this policy; it is not confirmed.']
    if retention['resources']:
        lines += ['', '| Kind | Resource ID | Parent handle | Origin | State |', '|---|---|---|---|---|']
        for resource in retention['resources']:
            lines.append('| '+' | '.join(cell(resource[k]) for k in ('kind','resource_id','parent','origin','state'))+' |')
    lines += ['', *['- '+action for action in handoff['actions']], '']
    return lines


def retained_axes(case_id: str, actual: dict | None, state: dict, retention: dict) -> dict:
    attempts = state.get('attempted_cases', [])
    attempted = actual is not None or not isinstance(attempts, list) or case_id in attempts
    if not attempted:
        return {'execution_status': 'NOT_RUN', 'resource_disposition': 'NOT_RUN',
                'retention_inventory': 'NOT_RUN', 'pool_disposition': 'NOT_RUN'}
    verified = retention.get('status') == 'VERIFIED_LOCAL_INVENTORY' and retention.get('case_id') == case_id
    return {'execution_status': 'UNCONFIRMED',
            'resource_disposition': 'RETAINED_UNRESOLVED' if verified else 'UNVERIFIED',
            'retention_inventory': retention.get('inventory_status', 'UNVERIFIED') if verified else 'UNVERIFIED',
            'pool_disposition': 'RETAINED_SERIAL' if verified and retention.get('serial_admission') else 'QUARANTINED',
            **({'next_run_admission': 'FRESH_CHECK_REQUIRED'} if verified and retention.get('serial_admission') else {})}


def retained_properties(retention: dict) -> dict:
    """Scalar properties shared by suite and applicable Case JUnit nodes."""
    if not retention:
        return {}
    result = {'retention.status': retention['status'],
              'retention.automatic_delete_authorized': False, 'retention.pool_release_authorized': False}
    if retention.get('serial_admission'):
        for name, value in retention['serial_admission'].items():
            result['retention.serial.'+name] = value
    for name, value in retention['handoff'].items():
        if name != 'actions':
            result['retention.'+('handoff_status' if name == 'status' else name)] = value
    for name, value in retention['quota'].items():
        if isinstance(value, dict):
            for kind, count in value.items():
                result['retention.quota.'+name+'.'+kind] = count
        else:
            result['retention.quota.'+name] = value
    for index, resource in enumerate(retention['resources']):
        for name in ('kind', 'resource_id', 'parent', 'origin', 'state'):
            result[f'retention.resource.{index}.{name}'] = resource[name]
    return result
