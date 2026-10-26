"""Offline selectors from an immutable retained observation; grants no log reads."""
from __future__ import annotations

from pathlib import Path
import re

from .delivery_observation import DeliveryReadPlan, project_rows
from .evidence import EvidenceError
from .ledger import Ledger
from .observation_budget import BUDGETED_WAIT_CONTRACT
from .profile import ConfigError, fingerprint
from .recheck_review import CASES, CONTRACT as REVIEW_CONTRACT, PLAN_CONTRACT, source_snapshot
from .run_state import read

CONTRACT = 'retained-transport-plan-v1'
MAX_SENDS = 8
BINDING_FIELDS = ('delivery_id', 'run_id', 'request_id', 'downstream_run_id', 'target_bot_id',
                  'group_id', 'session_id', 'attempt_no', 'state_version', 'status', 'terminal_at_ms')


def transport_plan(profile: dict, state_dir: Path, run_id: str, case_id: str,
                   observation_id: str, start_epoch: int, end_epoch: int) -> dict:
    if (not isinstance(observation_id, str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', observation_id) is None):
        raise ConfigError('transport source observation must be one safe directory component')
    if (type(start_epoch) is not int or type(end_epoch) is not int
            or start_epoch < 0 or not 0 < end_epoch-start_epoch <= 900):
        raise ConfigError('transport window needs integer epochs and at most 900 seconds')
    before = source_snapshot(profile, state_dir, run_id, case_id)
    directory = state_dir/'observations'/observation_id
    for path in (state_dir/'observations', directory):
        if path.is_symlink() or not path.is_dir():
            raise ConfigError('transport source must be an existing Run-local observation directory')
    report_path = directory/'report.json'
    report = read(report_path)
    if (report.get('kind') != 'RETAINED_EXECUTION_READ_ONLY'
            or report.get('run_id') != run_id or report.get('case_id') != case_id
            or report.get('profile_digest') != fingerprint(profile)
            or report.get('observation_id') != observation_id or report.get('source') != before
            or report.get('persistence') != 'SAVED' or report.get('errors') != []
            or report.get('status') != 'BLOCKED' or report.get('execution_status') != 'UNCONFIRMED'
            or report.get('pool_disposition') != 'QUARANTINED'
            or report.get('pool_release_authorized') is not False):
        raise ConfigError('transport source differs from the saved retained Run observation')
    planned = 'read_plan_digest' in report
    if planned and 'recheck_review_digest' in report:
        raise ConfigError('transport source cannot mix historical review contracts')
    receipt_path = directory/('read-plan.json' if planned else 'recheck-review.json')
    receipt = read(receipt_path)
    digest_key = 'read_plan_digest' if planned else 'recheck_review_digest'
    if (receipt.get('contract') != (PLAN_CONTRACT if planned else REVIEW_CONTRACT)
            or fingerprint(receipt) != report.get(digest_key) or receipt.get('source') != before
            or receipt.get('run_id') != run_id or receipt.get('case_id') != case_id
            or receipt.get('profile_digest') != fingerprint(profile)
            or receipt.get('observation_id') != observation_id):
        raise ConfigError('transport source receipt differs from the recorded observation')
    # Receipt expiry is historical: this command never re-enables its authority.
    observation = report.get('execution_observation')
    if (not isinstance(observation, dict) or observation.get('status') != 'OBSERVED'
            or observation.get('contract') != BUDGETED_WAIT_CONTRACT
            or receipt.get('execution_observation') != observation.get('contract')
            or observation.get('errors') != []
            or observation.get('stop_reason') not in ('KNOWN_DELIVERIES_SETTLED', 'OBSERVATION_LIMIT')
            or observation.get('scope_status') != 'UNCONFIRMED'):
        raise ConfigError('transport plan needs a complete successful delivery snapshot, not partial history')
    with Ledger(state_dir/'run.sqlite', run_id, fingerprint(profile), read_only=True) as ledger:
        resources = ledger.resource_snapshot()
    group = next(r['resource_id'] for r in resources if r['kind'] == 'group')
    owned_sessions = {r['resource_id'] for r in resources if r['kind'] == 'session'}
    sessions = observation.get('sessions')
    if not isinstance(sessions, list) or len(sessions) != CASES[case_id]:
        raise ConfigError('transport source must cover every owned Session')
    bots = tuple(sorted(a['bot_id'] for a in profile['actors'].values()))
    environment = profile['environment']['expected_bcs_env']
    seen_sessions, bindings = set(), []
    for session in sessions:
        if (not isinstance(session, dict) or not isinstance(session.get('session_id'), str)
                or session['session_id'] not in owned_sessions
                or session['session_id'] in seen_sessions or session.get('group_id') != group
                or not isinstance(session.get('rows'), list)
                or type(session.get('row_count')) is not int or session['row_count'] != len(session['rows'])):
            raise ConfigError('transport source Session inventory is incomplete or mismatched')
        seen_sessions.add(session['session_id'])
        if any(not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                   for key in ('kind', 'status', 'target_bot_id')) for row in session['rows']):
            raise ConfigError('transport source delivery kind, status and recipient must be scalar identifiers')
        query = DeliveryReadPlan(profile['observer']['database'], environment, group, session['session_id'], bots)
        try:
            rows = project_rows(query, {'success': True, 'data': session['rows']}, frozenset(bots))
        except (EvidenceError, ConfigError):
            raise ConfigError('transport source has an invalid exact-scope delivery projection') from None
        for row in rows:
            if row['kind'] != 'send':
                continue
            if (row['status'] != 'completed' or not row['run_id'] or not row['request_id']
                    or row['terminal_at_ms'] is None or not row['may_have_been_sent']
                    or row['attempt_no'] < 1 or row['state_version'] < 1):
                raise ConfigError('this transport plan requires every observed Send to have a completed attempt binding')
            bindings.append({key: row[key] for key in BINDING_FIELDS})
    if not 1 <= len(bindings) <= MAX_SENDS:
        raise ConfigError('transport plan exceeds its complete known-Send inventory budget')
    for key in ('delivery_id', 'run_id', 'request_id'):
        if len({row[key] for row in bindings}) != len(bindings):
            raise ConfigError('transport source has ambiguous delivery or transport identities')
    result = {'schema_version': 1, 'contract': CONTRACT, 'status': 'PLANNED_OFFLINE',
              'run_id': run_id, 'case_id': case_id, 'profile_digest': fingerprint(profile),
              'observation_id': observation_id, 'observation_digest': fingerprint(report),
              'source': before, 'environment': environment,
              'window': {'start_epoch': start_epoch, 'end_epoch': end_epoch, 'end_exclusive': True},
              'deliveries': sorted(bindings, key=lambda row: row['delivery_id']),
              'query_selectors': {'ws_attempt_ids': sorted(row['request_id'] for row in bindings),
                                  'http_canonical_ids': sorted(row['run_id'] for row in bindings)},
              'maximum_projected_rows': 30, 'query_permission': 'NONE', 'network_operations': 0,
              'source_verification': 'LOCAL_ARTIFACT_BINDING_ONLY',
              'window_coverage': 'OPERATOR_SELECTED_NOT_FULL_EXECUTION_PROOF',
              'execution_status': 'UNCONFIRMED', 'pool_release_authorized': False,
              'ready_to_execute': False}
    if (source_snapshot(profile, state_dir, run_id, case_id) != before
            or fingerprint(read(report_path)) != fingerprint(report)
            or fingerprint(read(receipt_path)) != fingerprint(receipt)):
        raise ConfigError('transport source changed while planning; no mixed snapshot accepted')
    result['plan_digest'] = fingerprint(result)
    return result
