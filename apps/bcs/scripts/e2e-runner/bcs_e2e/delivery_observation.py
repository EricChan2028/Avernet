"""Exact Session-scoped delivery observations, not a proof of global quiescence.

No policy/transport JSON, prompt, token or arbitrary SQL is read. Optional
downstream reads require a separate reviewed exact-correlation contract. A full 100-row page is incomplete, never evidence that remaining work
is absent. Terminal known Sends do not certify all producers or descendants.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from .errors import has_cancellation, primary_error
from .evidence import EvidenceError
from .profile import identifier
from .ledger import Ledger
from .pool import PoolLease, bound_pool_path
from .run_state import read
from .observation_budget import BUDGETED_WAIT_CONTRACT, reviewed_budget

CONTRACT = 'bcs-session-deliveries-v1'
WAIT_CONTRACT = 'bcs-session-deliveries-wait-v1'
CONTRACTS = {CONTRACT, WAIT_CONTRACT, BUDGETED_WAIT_CONTRACT}
TABLE = 'bcs_message_deliveries'
FIELDS = ('delivery_id', 'env', 'group_id', 'session_id', 'source_message_id', 'target_bot_id',
          'kind', 'status', 'state_version', 'attempt_no', 'request_id', 'run_id',
          'downstream_run_id', 'bound_to_delivery_id', 'may_have_been_sent', 'terminal_at_ms')
STATUSES = {'queued', 'dispatching', 'running', 'unknown', 'cancelling', 'cancel_unknown',
            'completed', 'failed', 'cancelled', 'expired', 'rejected_capacity',
            'pending_context', 'bound', 'consumed', 'discarded_context'}


@dataclass(frozen=True)
class DeliveryReadPlan:
    database: str
    environment: str
    group_id: str
    session_id: str
    target_bot_ids: tuple[str, str]


class DeliveryReadScope:
    """Query-boundary proof from the actual Run ledger and already-held lease."""
    def __init__(self, ledger: Ledger, lease: PoolLease, case_id: str):
        if not isinstance(ledger, Ledger) or not isinstance(lease, PoolLease):
            raise EvidenceError('delivery query needs a real owned ledger and held lease')
        self.ledger, self.lease, self.case_id = ledger, lease, case_id

    def validate(self, plan: DeliveryReadPlan, review) -> None:
        self.lease.require_held()
        expected_bots = tuple(sorted(a['bot_id'] for a in review.profile['actors'].values()))
        if plan.target_bot_ids != expected_bots:
            raise EvidenceError('delivery query recipients differ from the bound test actors')
        if (review.state_dir is None or self.ledger.run_id != review.run_id
                or self.lease.run_id != review.run_id or self.case_id != review.case_id):
            raise EvidenceError('delivery query ownership differs from the reviewed Run/Case')
        state = read(review.state_dir / 'run.json')
        from .recheck_review import RetainedRecheckReview
        recheck = type(review) is RetainedRecheckReview
        if recheck:
            review.validate()  # Fresh read-only grant pins the original quarantined artifacts.
        expected_status = 'QUARANTINED' if recheck else 'RUNNING'
        if (state.get('run_id') != review.run_id or state.get('active_case') != self.case_id
                or state.get('status') != expected_status or state.get('profile_digest') != self.lease.profile_digest
                or self.lease.path != bound_pool_path(state)):
            raise EvidenceError('delivery query requires the active bound Run and held pool')
        self.ledger._check_binding(review.run_id, self.lease.profile_digest)
        database_path = self.ledger.db.execute('PRAGMA database_list').fetchone()[2]
        if Path(database_path).resolve() != Path(state['ledger_path']).resolve():
            raise EvidenceError('delivery query ledger differs from the prepared Run')
        rows = self.ledger.resource_snapshot()
        sessions = [r for r in rows if r['kind'] == 'session']
        if len(rows) > 3 or not 1 <= len(sessions) <= 2:
            raise EvidenceError('delivery query exceeds the retained resource scope')
        group = next((r for r in rows if r['kind'] == 'group' and r['resource_id'] == plan.group_id), None)
        session = next((r for r in sessions if r['resource_id'] == plan.session_id), None)
        if (group is None or session is None or session['parent'] != group['handle'] or group['parent'] is not None
                or any(r['case_id'] != self.case_id or r['attempt'] != 1 or r['origin'] != 'created'
                       or r['state'] != 'live' for r in (group, session))):
            raise EvidenceError('delivery query targets foreign, borrowed or unresolved resources')


def query_arguments(plan: DeliveryReadPlan) -> dict:
    if type(plan) is not DeliveryReadPlan:
        raise EvidenceError('execution observation requires the fixed delivery plan')
    for field in ('database', 'environment', 'group_id', 'session_id'):
        identifier(getattr(plan, field), 'delivery.' + field)
    if (type(plan.target_bot_ids) is not tuple or len(plan.target_bot_ids) != 2
            or any(not isinstance(bot, str) for bot in plan.target_bot_ids)
            or tuple(sorted(set(plan.target_bot_ids))) != plan.target_bot_ids):
        raise EvidenceError('delivery query needs two distinct sorted bound Bot IDs')
    for bot in plan.target_bot_ids:
        identifier(bot, 'delivery.target_bot_id')
    targets = ','.join("'" + bot + "'" for bot in plan.target_bot_ids)
    return {'db': plan.database, 'table': TABLE, 'select': ','.join(FIELDS),
            'where': (f"env = '{plan.environment}' AND group_id = '{plan.group_id}' AND session_id = '{plan.session_id}'"
                      f" AND target_bot_id IN ({targets})"),
            'limit': '100', 'orderby': 'delivery_id asc'}


def project_rows(plan: DeliveryReadPlan, payload: dict, allowed_bots: frozenset[str]) -> list[dict]:
    query_arguments(plan)
    if (not isinstance(payload, dict) or payload.get('success') is not True
            or type(payload.get('truncated', False)) is not bool or payload.get('truncated', False)):
        raise EvidenceError('delivery observation failed or was truncated')
    values = payload.get('data')
    if not isinstance(values, list) or len(values) >= 100:
        raise EvidenceError('delivery observation is incomplete or exceeds row budget')
    result, seen = [], set()
    for row in values:
        if not isinstance(row, dict) or any(field not in row for field in FIELDS):
            raise EvidenceError('delivery projection is incomplete')
        if (row['env'] != plan.environment or row['group_id'] != plan.group_id
                or row['session_id'] != plan.session_id or row['target_bot_id'] not in allowed_bots
                or row['target_bot_id'] not in plan.target_bot_ids):
            raise EvidenceError('delivery row is outside the exact owned scope')
        for field in ('delivery_id', 'source_message_id', 'target_bot_id'):
            identifier(row[field], 'delivery.' + field)
        for field in ('request_id', 'run_id', 'downstream_run_id', 'bound_to_delivery_id'):
            if row[field] is not None:
                identifier(row[field], 'delivery.' + field)
        if row['delivery_id'] in seen:
            raise EvidenceError('duplicate delivery identity in observation')
        seen.add(row['delivery_id'])
        if row['kind'] not in {'send', 'inject'} or row['status'] not in STATUSES:
            raise EvidenceError('unknown delivery kind or state')
        if any(type(row[field]) is not int or row[field] < 0 for field in ('state_version', 'attempt_no')):
            raise EvidenceError('delivery version or attempt has invalid type')
        if type(row['may_have_been_sent']) not in (bool, int) or row['may_have_been_sent'] not in (0, 1):
            raise EvidenceError('delivery send certainty has invalid type')
        if row['terminal_at_ms'] is not None and (type(row['terminal_at_ms']) is not int or row['terminal_at_ms'] < 0):
            raise EvidenceError('delivery terminal timestamp is invalid')
        result.append({field: row[field] for field in FIELDS})
    return result


def summarize(rows: list[dict]) -> dict:
    completed, active, unresolved, context, unsuccessful = [], [], [], [], []
    for row in rows:
        key, status = row['delivery_id'], row['status']
        if row['kind'] == 'inject':
            context.append({'delivery_id': key, 'status': status,
                            'carrier_delivery_id': row['bound_to_delivery_id']})
        elif status == 'completed' and row['run_id'] and row['terminal_at_ms'] is not None:
            completed.append(key)
        elif status in {'queued', 'dispatching', 'running', 'cancelling'}:
            active.append(key)
        elif status in {'failed', 'cancelled', 'rejected_capacity'}:
            unsuccessful.append(key)
        else:
            unresolved.append(key)
    return {'known_completed_send_ids': completed, 'active_send_ids': active,
            'unresolved_send_ids': unresolved, 'unsuccessful_send_ids': unsuccessful,
            'context_dispositions': context,
            'snapshot_activity': snapshot_activity(rows),
            'scope_status': 'UNCONFIRMED',
            'coverage_gaps': ['effective delivery policy and non-managed work',
                              'other recipients and complete successor/producer inventory', 'downstream and callback completion']}


def _known_deliveries_settled(sessions: list[dict]) -> bool:
    """Only a polling stop condition, never a scope/PASS/reuse assertion."""
    return bool(sessions) and all(
        s['row_count'] and s['known_completed_send_ids'] and not s['active_send_ids']
        and not s['unresolved_send_ids'] and not s['unsuccessful_send_ids']
        # The classifier accepts terminal Inject only with may_have_been_sent=false.
        # Pending/bound or malformed context must not inherit Send terminal semantics.
        and s['snapshot_activity']['terminal_contexts'] == len(s['context_dispositions'])
        for s in sessions)


class DeliveryObserver:
    def __init__(self, *, client, database: str, environment: str, allowed_bots: frozenset[str],
                 contract: str = CONTRACT, downstream_config: dict | None = None,
                 observation_budget: dict | None = None, scope_contract: str | None = None,
                 pagination: dict | None = None):
        if not isinstance(contract, str) or contract not in CONTRACTS:
            raise EvidenceError('unreviewed execution observation contract')
        self.contract = contract
        self.budget = reviewed_budget(contract, observation_budget)
        self.pagination = None
        if pagination is not None:
            from .delivery_pagination import validate_limits
            if self.budget is None:
                raise EvidenceError('delivery pagination requires explicit wait-v2 observation')
            self.pagination = validate_limits(pagination)
        self.max_rounds = self.budget.max_rounds if self.budget is not None else 3 if contract == WAIT_CONTRACT else 1
        self.poll_interval = self.budget.poll_interval_seconds if self.budget is not None else 1
        self.client = client
        self.database, self.environment, self.allowed_bots = database, environment, allowed_bots
        self.downstream_config = dict(downstream_config) if downstream_config is not None else None
        self.scope_contract = scope_contract
        if scope_contract is not None:
            from .scope_observation import CONTRACTS as SCOPE_CONTRACTS
            if scope_contract not in SCOPE_CONTRACTS or self.budget is None:
                raise EvidenceError('scope facts require explicit wait-v2 observation')
        self._admitted = False

    def admit(self) -> None:
        schema = self.client.table_describe(self.database, TABLE)
        if (not isinstance(schema, dict) or schema.get('success') is not True
                or type(schema.get('truncated', False)) is not bool or schema.get('truncated', False)
                or not isinstance(schema.get('columns'), list)
                or not (set(FIELDS) | ({'id'} if self.pagination is not None else set())) <= set(schema['columns'])):
            raise EvidenceError('execution delivery schema is unavailable or incompatible')
        if self.downstream_config is not None:
            from .downstream_observation import QUEUE_TABLE, QUEUE_FIELDS, RESULT_TABLE, RESULT_FIELDS
            for table, fields in ((QUEUE_TABLE, QUEUE_FIELDS), (RESULT_TABLE, RESULT_FIELDS)):
                schema = self.client.table_describe(self.database, table)
                if (not isinstance(schema, dict) or schema.get('success') is not True
                        or type(schema.get('truncated', False)) is not bool or schema.get('truncated', False)
                        or not isinstance(schema.get('columns'), list) or not set(fields) <= set(schema['columns'])):
                    raise EvidenceError('correlated downstream schema is unavailable or incompatible')
        if self.scope_contract is not None:
            from .scope_observation import GROUP_TABLE, group_fields, SESSION_TABLE, SESSION_FIELDS
            from .producer_inventory import CONTRACT as PRODUCER_CONTRACT, TABLE as PRODUCER_TABLE, FIELDS as PRODUCER_FIELDS
            projections = [(GROUP_TABLE, group_fields(self.scope_contract)), (SESSION_TABLE, SESSION_FIELDS)]
            if self.scope_contract == PRODUCER_CONTRACT:
                projections.append((PRODUCER_TABLE, PRODUCER_FIELDS))
            for table, fields in projections:
                schema = self.client.table_describe(self.database, table)
                if (not isinstance(schema, dict) or schema.get('success') is not True
                        or type(schema.get('truncated', False)) is not bool or schema.get('truncated', False)
                        or not isinstance(schema.get('columns'), list) or not set(fields) <= set(schema['columns'])):
                    raise EvidenceError('scope observation schema is unavailable or incompatible')
        self._admitted = True

    def observe(self, ledger, scope, require_active, *, lease: PoolLease, case_id: str, timeout_seconds: int) -> dict:
        if not self._admitted:
            raise EvidenceError('delivery observation was not admitted before execution')
        # timeout_seconds is the historical transport-derived window only for v1.
        # v2 uses its separately reviewed schedule without changing client timeouts.
        if self.budget is not None:
            timeout_seconds = self.budget.timeout_seconds
        started = time.monotonic()
        report = {'status': 'INCOMPLETE', 'contract': self.contract, 'scope_status': 'UNCONFIRMED',
                  'captured_at': int(time.time()), 'queries': 0, 'sessions': [], 'errors': []}
        if self.scope_contract is not None:
            report['scope_observation'] = {'status': 'NOT_RUN', 'contract': self.scope_contract,
                'queries': 0, 'group': None, 'sessions': [], 'scope_status': 'UNCONFIRMED',
                'producer_coverage': 'NOT_VERIFIED', 'pool_reuse_authorized': False}
        if self.budget is not None:
            report['observation_budget'] = self.budget.public()
        if self.pagination is not None:
            report['delivery_pagination'] = {'limits': self.pagination, 'traversals': []}
        if self.downstream_config is not None:
            report['downstream_observation'] = {'status': 'NOT_RUN', 'scope_status': 'UNCONFIRMED',
                                               'runs': [], 'missing_run_binding_delivery_ids': []}
        try:
            require_active()
            ownership = DeliveryReadScope(ledger, lease, case_id)
            resources = ledger.resource_snapshot()
            sessions = [r for r in resources if r['case_id'] == case_id and r['kind'] == 'session']
            if not 1 <= len(sessions) <= 2 or len(resources) > 3:
                raise EvidenceError('delivery observation requires the bounded owned Session inventory')
            if self.scope_contract is not None:
                from .scope_observation import observe_facts
                observe_facts(self, sessions, scope, ownership, require_active, report, started + timeout_seconds)
            history = {}
            latest_by_session = {}
            if self.max_rounds > 1:
                report.update(rounds=[], maximum_rounds=self.max_rounds, stop_reason='OBSERVING')
            for round_number in range(1, self.max_rounds + 1):
                if round_number > 1:
                    require_active()
                    if time.monotonic() - started >= timeout_seconds:
                        raise TimeoutError('execution observation budget expired')
                current_sessions = []
                if self.max_rounds == 1:
                    report['sessions'] = []
                else:
                    report['attempted_round'] = round_number
                for session in sessions:
                    if time.monotonic() - started >= timeout_seconds:
                        raise TimeoutError('execution observation budget expired')
                    before = scope('session.persisted', session['resource_id'])
                    if session['attempt'] != 1 or len(before) != 2:
                        raise EvidenceError('delivery Session ownership or parent is invalid')
                    plan = DeliveryReadPlan(self.database, self.environment, before[1]['resource_id'], session['resource_id'],
                                            tuple(sorted(self.allowed_bots)))
                    require_active()
                    if self.pagination is None:
                        report['queries'] += 1
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("execution observation budget expired")
                    deadline = ({'deadline_monotonic': started + timeout_seconds}
                                if self.budget is not None else {})
                    if self.pagination is None:
                        raw = self.client.delivery_query(plan, scope=ownership, timeout_seconds=remaining, **deadline)
                    else:
                        progress = {'session_id': plan.session_id, 'round': round_number}
                        report['delivery_pagination']['traversals'].append(progress)
                        try:
                            rows = self.client.delivery_scan(plan, scope=ownership,
                                deadline_monotonic=started + timeout_seconds, progress=progress)
                        finally:
                            report['queries'] += progress.get('queries', 0)
                    require_active()
                    if scope('session.persisted', session['resource_id']) != before:
                        raise EvidenceError('Session ownership changed during delivery observation')
                    if time.monotonic() - started >= timeout_seconds:
                        raise TimeoutError('late execution observation is not accepted')
                    if self.pagination is None:
                        rows = project_rows(plan, raw, self.allowed_bots)
                    observation = {'group_id': plan.group_id, 'session_id': plan.session_id,
                                   'row_count': len(rows), 'rows': rows, **summarize(rows)}
                    current_sessions.append(observation)
                    if self.max_rounds == 1:
                        report['sessions'].append(observation)
                    else:
                        observation['observed_round'] = round_number
                        latest_by_session[plan.session_id] = observation
                        report['sessions'] = [latest_by_session[r['resource_id']] for r in sessions
                                              if r['resource_id'] in latest_by_session]
                report['status'] = 'OBSERVED' if all(s['row_count'] for s in current_sessions) else 'INCOMPLETE'
                if self.max_rounds == 1:
                    break
                current = {session['session_id']: {r['delivery_id']: r for r in session['rows']}
                           for session in current_sessions}
                drift = []
                for session_id, rows in history.items():
                    latest = current.get(session_id, {})
                    for delivery_id, previous in rows.items():
                        now = latest.get(delivery_id)
                        if (now is None or now['state_version'] < previous['state_version']
                                or now['attempt_no'] < previous['attempt_no']
                                or now['source_message_id'] != previous['source_message_id']
                                or now['target_bot_id'] != previous['target_bot_id']
                                or now['kind'] != previous['kind']
                                or previous['run_id'] is not None and now['run_id'] != previous['run_id']
                                or previous['status'] in {'completed', 'failed', 'cancelled', 'expired',
                                                          'rejected_capacity', 'consumed', 'discarded_context'}
                                    and now['status'] != previous['status']
                                or now['state_version'] == previous['state_version'] and now != previous):
                            drift.append(delivery_id)
                # Only projected counters/identities and status/version history;
                # keep full rows for the latest round, not an unbounded archive.
                report['rounds'].append({'round': round_number, 'status': report['status'],
                    'sessions': [{**{k: v for k, v in session.items() if k != 'rows'},
                                  'delivery_states': [{k: r[k] for k in ('delivery_id', 'status', 'state_version', 'attempt_no')}
                                                      for r in session['rows']]}
                                 for session in current_sessions], 'inconsistent_delivery_ids': sorted(set(drift))})
                if drift:
                    report.update(status='INCOMPLETE', stop_reason='INCONSISTENT_HISTORY')
                    break
                history = current
                if _known_deliveries_settled(current_sessions):
                    report['stop_reason'] = 'KNOWN_DELIVERIES_SETTLED'
                    break
                if round_number == self.max_rounds:
                    report['stop_reason'] = 'OBSERVATION_LIMIT'
                    break
                require_active()
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError('execution observation budget expired')
                time.sleep(min(float(self.poll_interval), remaining))

            if self.downstream_config is not None and report['status'] == 'OBSERVED':
                self._observe_downstream(report, ownership, require_active, started, timeout_seconds)
        except (Exception, KeyboardInterrupt) as error:
            if report.get('scope_observation', {}).get('status') == 'OBSERVING':
                report['scope_observation']['status'] = 'CANCELLED' if has_cancellation(error) else 'INCOMPLETE'
            if 'downstream_observation' in report and report['downstream_observation']['status'] == 'OBSERVING':
                report['downstream_observation']['status'] = 'CANCELLED' if has_cancellation(error) else 'INCOMPLETE'
            cause = primary_error(error)
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                report['child_shutdown_status'] = 'UNCONFIRMED'
            if has_cancellation(error):
                report['status'] = 'CANCELLED'
                if self.max_rounds > 1:
                    report['stop_reason'] = 'CANCELLED'
                cancelled = KeyboardInterrupt()
                cancelled.execution_observation = report
                if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                    cancelled.child_shutdown_status = 'UNCONFIRMED'
                raise cancelled from None
            report['status'] = 'TIMED_OUT' if isinstance(cause, TimeoutError) else 'BLOCKED'
            if self.max_rounds > 1:
                report['stop_reason'] = report['status']
            report['errors'].append({'error_type': type(cause).__name__})
        return report


    def _observe_downstream(self, report, ownership, require_active, started, timeout_seconds):
        from .downstream_observation import DownstreamReadPlan, MAX_RUNS
        observation = report['downstream_observation']
        observation['status'] = 'OBSERVING'
        observation['run_id_source'] = self.downstream_config['run_id_source']
        candidates = []
        for session in report['sessions']:
            parent = DeliveryReadPlan(self.database, self.environment, session['group_id'], session['session_id'],
                                      tuple(sorted(self.allowed_bots)))
            for row in session['rows']:
                if row['kind'] != 'send':
                    continue
                run_key = 'downstream_run_id' if self.downstream_config['run_id_source'] == 'accepted-alias' else 'run_id'
                if row[run_key] is None:
                    observation['missing_run_binding_delivery_ids'].append(row['delivery_id'])
                else:
                    candidates.append(DownstreamReadPlan(parent, row['delivery_id'], row['target_bot_id'],
                        row[run_key], self.downstream_config['bot_bindings'][row['target_bot_id']]))
        # Do not choose an arbitrary prefix of a larger task chain.
        if len(candidates) > MAX_RUNS:
            observation.update(status='INCOMPLETE', reason='downstream run budget exceeded')
            return
        for plan in candidates:
            require_active()
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError('shared delivery/downstream observation deadline expired')
            result = None
            try:
                result = self.client.downstream_observe(
                    plan, scope=ownership, deadline_monotonic=started + timeout_seconds)
                require_active()
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError('late downstream evidence is not accepted')
            except (Exception, KeyboardInterrupt) as error:
                partial = getattr(error, 'downstream_observation', None)
                if result is not None:
                    # Preserve the verified queue, but do not credit a final
                    # result rejected at the outer authority/deadline boundary.
                    partial = {**result, 'result_status': 'UNOBSERVED',
                               'known_run_result_completed': False}
                if isinstance(partial, dict):
                    observation['runs'].append(partial)
                raise
            observation['runs'].append(result)
        observation['status'] = ('OBSERVED' if candidates and not observation['missing_run_binding_delivery_ids']
                                 and all(r['queue_status'] != 'UNOBSERVED' and r['result_status'] != 'UNOBSERVED'
                                         for r in observation['runs']) else 'INCOMPLETE')


def snapshot_activity(rows: list[dict]) -> dict:
    counts = {'active_sends': 0, 'uncertain_sends': 0, 'terminal_sends': 0,
              'pending_contexts': 0, 'bound_contexts': 0, 'terminal_contexts': 0,
              'unclassified_rows': 0}
    for row in rows:
        kind, status = row['kind'], row['status']
        if kind == 'send':
            if status in {'queued', 'dispatching', 'running', 'cancelling'}:
                counts['active_sends'] += 1
            elif status in {'unknown', 'cancel_unknown', 'expired'}:
                # Expired can preserve possible-send evidence after quarantine.
                counts['uncertain_sends'] += 1
            elif status in {'completed', 'failed', 'cancelled', 'rejected_capacity'}:
                counts['terminal_sends'] += 1
            else:
                counts['unclassified_rows'] += 1
        elif kind == 'inject' and not row['may_have_been_sent']:
            if status == 'pending_context' and row['bound_to_delivery_id'] is None:
                counts['pending_contexts'] += 1
            elif status == 'bound' and row['bound_to_delivery_id']:
                counts['bound_contexts'] += 1
            elif status in {'consumed', 'discarded_context', 'cancelled', 'expired', 'failed'}:
                counts['terminal_contexts'] += 1
            else:
                counts['unclassified_rows'] += 1
        else:
            counts['unclassified_rows'] += 1
    if counts['active_sends']:
        status = 'ACTIVE_SEND_OBSERVED'
    elif counts['uncertain_sends'] or counts['bound_contexts'] or counts['unclassified_rows']:
        status = 'UNRESOLVED_ACTIVITY'
    elif not rows:
        status = 'NO_EVIDENCE'
    else:
        status = 'NO_ACTIVE_SEND_IN_SNAPSHOT'
    return {'schema_version': 1, 'status': status, **counts,
            'scope_status': 'UNCONFIRMED', 'pool_reuse_authorized': False}
