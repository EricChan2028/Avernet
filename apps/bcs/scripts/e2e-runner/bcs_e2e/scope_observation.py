"""Exact owned Group/Session shape facts; not a producer-completion certificate."""
from __future__ import annotations

import time

from .delivery_observation import DeliveryReadPlan, query_arguments as delivery_arguments
from .evidence import EvidenceError

from .producer_inventory import CONTRACT as PRODUCER_CONTRACT

CONTRACT = 'retained-group-session-facts-v1'
CONTRACTS = {CONTRACT, PRODUCER_CONTRACT}
GROUP_TABLE = 'bcs_groups'
GROUP_FIELDS = ('group_id', 'env', 'group_kind', 'group_strategy', 'status', 'originator', 'driver_bot')
SESSION_TABLE = 'bcs_group_sessions'
SESSION_FIELDS = ('session_id', 'group_id', 'env', 'session_kind', 'status',
                  'activation_count', 'callback_status', 'completed_at')


def group_fields(contract: str) -> tuple[str, ...]:
    if not isinstance(contract, str) or contract not in CONTRACTS:
        raise EvidenceError('unreviewed scope projection contract')
    return GROUP_FIELDS + (('service_mode',) if contract == PRODUCER_CONTRACT else ())


def query_arguments(plan: DeliveryReadPlan, *, group: bool, contract: str = CONTRACT) -> dict:
    delivery_arguments(plan)
    group_fields(contract)
    if type(group) is not bool:
        raise EvidenceError('scope observation requires an exact projection selector')
    where = f"env = '{plan.environment}' AND group_id = '{plan.group_id}'"
    if not group:
        where += f" AND session_id = '{plan.session_id}'"
    return {'db': plan.database, 'table': GROUP_TABLE if group else SESSION_TABLE,
            'select': ','.join(group_fields(contract) if group else SESSION_FIELDS), 'where': where, 'limit': '2'}


def project_row(plan: DeliveryReadPlan, payload: dict, *, group: bool, profile: dict, contract: str = CONTRACT) -> dict:
    query_arguments(plan, group=group, contract=contract)
    if (not isinstance(payload, dict) or payload.get('success') is not True
            or type(payload.get('truncated', False)) is not bool or payload.get('truncated', False)
            or not isinstance(payload.get('data'), list) or len(payload['data']) != 1):
        raise EvidenceError('scope facts require one complete unambiguous owned row')
    row = payload['data'][0]
    fields = group_fields(contract) if group else SESSION_FIELDS
    if (not isinstance(row, dict) or not set(fields) <= set(row)
            or row['env'] != plan.environment or row['group_id'] != plan.group_id):
        raise EvidenceError('scope facts differ from the exact owned environment/Group')
    if group:
        if contract == PRODUCER_CONTRACT and row['service_mode'] is not None and (
                not isinstance(row['service_mode'], str) or not row['service_mode'] or len(row['service_mode']) > 64):
            raise EvidenceError('Group service mode is invalid')
        if (row['originator'] != 'human_' + profile['human']['user_id']
                or row['driver_bot'] != profile['actors']['driver']['bot_id']
                or any(not isinstance(row[k], str) or not row[k] or len(row[k]) > 64
                       for k in ('group_kind', 'group_strategy', 'status'))):
            raise EvidenceError('Group shape or owner differs from the reviewed fixture')
    elif (row['session_id'] != plan.session_id
            or row['session_kind'] not in ('chat', 'service_invocation')
            or row['status'] not in ('running', 'completed')
            or type(row['activation_count']) is not int or row['activation_count'] < 1
            or row['callback_status'] not in (None, 'pending', 'succeeded', 'partial_failed', 'failed', 'not_applicable')
            or row['completed_at'] is not None and (type(row['completed_at']) is not int or row['completed_at'] < 0)):
        raise EvidenceError('Session shape or activation/callback state is invalid')
    return {k:row[k] for k in fields}


def observe_facts(observer, sessions, scope, ownership, require_active, report, deadline):
    facts = report['scope_observation']
    facts['status'] = 'OBSERVING'
    extended = getattr(observer, 'scope_contract', None) == PRODUCER_CONTRACT
    if extended:
        facts['state_machine_inventories'] = []
    for index, session in enumerate(sessions):
        chain = scope('session.persisted', session['resource_id'])
        plan = DeliveryReadPlan(observer.database, observer.environment, chain[1]['resource_id'],
                                session['resource_id'], tuple(sorted(observer.allowed_bots)))
        for group in ([True, False] if index == 0 else [False]):
            require_active()
            facts['queries'] += 1
            row = observer.client.scope_query(plan, group=group, scope=ownership, deadline_monotonic=deadline)
            require_active()
            if time.monotonic() >= deadline:
                raise TimeoutError('late scope facts after authority validation are not accepted')
            if group:
                facts['group'] = row
            else:
                facts['sessions'].append(row)
        if extended:
            require_active()
            facts['queries'] += 1
            rows = observer.client.producer_query(plan, scope=ownership, deadline_monotonic=deadline)
            require_active()
            if time.monotonic() >= deadline:
                raise TimeoutError('late producer inventory after authority validation is not accepted')
            facts['state_machine_inventories'].append({
                'group_id': plan.group_id, 'session_id': plan.session_id, 'rows': rows,
                'status': 'RUNS_REQUIRE_REVIEW' if rows else 'NO_RECORDED_RUNS_IN_SNAPSHOT',
                'whole_scope_completion': 'NOT_PROVEN'})
    facts['status'] = 'OBSERVED'
