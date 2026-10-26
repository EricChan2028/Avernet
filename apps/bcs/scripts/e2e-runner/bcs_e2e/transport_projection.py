"""Match an offline supplied log projection; never certify its live provenance."""
from __future__ import annotations

from .profile import ConfigError, fingerprint, identifier
from .transport_plan import CONTRACT as PLAN_CONTRACT

CONTRACT = 'retained-transport-log-projection-v1'
HTTP_ACCEPTED = {'HTTP_ACK', 'HTTP_CALLBACK_ACK', 'HTTP_SSE_ACCEPTED'}
EVENTS = HTTP_ACCEPTED | {'WS_ACK', 'HTTP_START'}
ERRORS = {'PERMISSION_DENIED', 'AUTHENTICATION_REQUIRED', 'TIMEOUT', 'QUERY_ERROR', 'SCHEMA_ERROR'}


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ConfigError(label + ' has invalid fields')


def match_transport(plan: dict, projection: dict) -> dict:
    if (not isinstance(plan, dict) or plan.get('contract') != PLAN_CONTRACT
            or fingerprint({k: v for k, v in plan.items() if k != 'plan_digest'}) != plan.get('plan_digest')):
        raise ConfigError('transport plan is missing or changed')
    _keys(projection, {'schema_version', 'contract', 'plan_digest', 'source_reference', 'environment',
                      'status', 'error_code', 'truncated', 'records'}, 'log projection')
    if (type(projection['schema_version']) is not int or projection['schema_version'] != 1
            or projection['contract'] != CONTRACT or projection['plan_digest'] != plan['plan_digest']
            or projection['environment'] != plan['environment']
            or projection['status'] not in ('OBSERVED', 'ERROR')
            or type(projection['truncated']) is not bool or not isinstance(projection['records'], list)):
        raise ConfigError('log projection differs from its contract, plan or environment')
    identifier(projection['source_reference'], 'log projection source reference')
    if len(projection['records']) > plan['maximum_projected_rows']:
        raise ConfigError('log projection exceeds the complete-page row bound; do not trim or deduplicate to fit')
    result = {'schema_version': 1, 'kind': 'OFFLINE_TRANSPORT_CORRELATION',
              'run_id': plan['run_id'], 'case_id': plan['case_id'], 'plan_digest': plan['plan_digest'],
              'projection_digest': fingerprint(projection), 'evidence_origin': 'OFFLINE_SUPPLIED',
              'source_verification': 'NOT_VERIFIED', 'environment_verification': 'NOT_VERIFIED',
              'historical_policy_coverage': 'NOT_VERIFIED', 'scope_status': 'UNCONFIRMED',
              'execution_status': 'UNCONFIRMED', 'pool_release_authorized': False,
              'ready_to_execute': False, 'network_operations': 0,
              'known_send_count': len(plan['deliveries']), 'record_count': len(projection['records']),
              'truncated': projection['truncated'], 'duplicate_count': 0, 'deliveries': []}
    if projection['status'] == 'ERROR':
        if (not isinstance(projection['error_code'], str) or projection['error_code'] not in ERRORS
                or projection['records']):
            raise ConfigError('failed log query requires an allowed error code and no credited records')
        return {**result, 'matching_result': 'UNAVAILABLE', 'error_code': projection['error_code']}
    if projection['error_code'] is not None:
        raise ConfigError('observed log projection cannot conceal a query error')
    by_attempt = {d['request_id']: d for d in plan['deliveries']}
    by_run = {d['run_id']: d for d in plan['deliveries']}
    grouped = {d['delivery_id']: [] for d in plan['deliveries']}
    seen = set()
    for record in projection['records']:
        event = record.get('event') if isinstance(record, dict) else None
        if not isinstance(event, str) or event not in EVENTS:
            raise ConfigError('log projection has an unsupported event')
        fields = {'event', 'timestamp_ms', 'run_id', 'bot_id'}
        if event == 'WS_ACK':
            fields |= {'group_id', 'session_id', 'accepted_run_id'}
        else:
            fields.add('provider_id')
            if event != 'HTTP_SSE_ACCEPTED':
                fields.add('method')
            if event == 'HTTP_START':
                fields.add('provider_bot_ref')
        _keys(record, fields, 'projected log record')
        if (type(record['timestamp_ms']) is not int
                or not plan['window']['start_epoch']*1000 <= record['timestamp_ms'] < plan['window']['end_epoch']*1000):
            raise ConfigError('projected log timestamp is outside the frozen window')
        for key in fields - {'event', 'timestamp_ms', 'method', 'accepted_run_id'}:
            identifier(record[key], 'projected log identifier')
        if 'method' in record and record['method'] != 'chat.send':
            raise ConfigError('transport projection must describe chat.send, not another method')
        delivery = (by_attempt if event == 'WS_ACK' else by_run).get(record['run_id'])
        if delivery is None or record['bot_id'] != delivery['target_bot_id']:
            raise ConfigError('log identity does not match one exact delivery transport tuple')
        if event == 'WS_ACK':
            if record['group_id'] != delivery['group_id'] or record['session_id'] != delivery['session_id']:
                raise ConfigError('WS acknowledgement differs from its exact owned Group/Session')
            if record['accepted_run_id'] not in (None, ''):
                identifier(record['accepted_run_id'], 'accepted Run alias')
        digest = fingerprint(record)
        if digest in seen:
            result['duplicate_count'] += 1
        seen.add(digest)
        grouped[delivery['delivery_id']].append(record)
    for delivery in plan['deliveries']:
        rows = grouped[delivery['delivery_id']]
        events = {r['event'] for r in rows}
        ws = 'WS_ACK' in events
        http = events & HTTP_ACCEPTED
        providers = {r['provider_id'] for r in rows if r['event'] != 'WS_ACK'}
        reasons = []
        aliases = {r['accepted_run_id'] for r in rows if r['event'] == 'WS_ACK' and r['accepted_run_id']}
        alias_conflict = len(aliases) > 1 or (delivery['downstream_run_id'] is not None
                            and any(alias != delivery['downstream_run_id'] for alias in aliases))
        refs = {r['provider_bot_ref'] for r in rows if r['event'] == 'HTTP_START'}
        if alias_conflict:
            reasons.append('ACCEPTED_ALIAS_CONFLICT')
        if len(providers) > 1 or len(refs) > 1 or len(http) > 1:
            reasons.append('HTTP_TRANSPORT_CONFLICT')
        if ws and http and delivery['attempt_no'] == 1:
            reasons.append('WS_HTTP_CONFLICT')
        if reasons:
            outcome = 'CONFLICT'
        elif any(event.startswith('HTTP_') for event in events) and delivery['attempt_no'] > 1:
            outcome = 'UNRESOLVED'
            reasons.append('HTTP_CANONICAL_ID_DOES_NOT_IDENTIFY_FINAL_ATTEMPT')
        elif 'HTTP_SSE_ACCEPTED' in http and 'HTTP_START' not in events:
            outcome = 'UNRESOLVED'
            reasons.append('SSE_REQUIRES_SAME_RUN_BOT_PROVIDER_CHAT_SEND_START')
        elif ws or http:
            outcome = 'MATCHED'
        else:
            outcome = 'UNRESOLVED'
            reasons.append('NO_ACCEPTANCE_RECORD')
        result['deliveries'].append({'delivery_id': delivery['delivery_id'],
            'matching_result': outcome, 'attempt_no': delivery['attempt_no'],
            'attempt_evidence': (['WS_ACK'] if ws else []) + (sorted(http) if outcome == 'MATCHED' else []),
            'run_level_http_evidence': sorted(http), 'record_count': len(rows), 'reasons': reasons,
            'engine_implementation': 'NOT_VERIFIED', 'execution_completion': 'NOT_PROVEN_BY_ACK'})
    outcomes = {d['matching_result'] for d in result['deliveries']}
    result['matching_result'] = ('CONFLICT' if 'CONFLICT' in outcomes else 'INCOMPLETE'
        if projection['truncated'] or result['duplicate_count'] or outcomes != {'MATCHED'} else 'MATCHED')
    return result
