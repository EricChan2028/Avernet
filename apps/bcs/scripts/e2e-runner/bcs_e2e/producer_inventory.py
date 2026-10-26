"""Bounded exact-Session state-machine inventory, not a quiescence certificate."""
from __future__ import annotations

from .delivery_observation import query_arguments as delivery_arguments
from .evidence import EvidenceError
from .profile import identifier

CONTRACT = 'retained-producer-inventory-v1'
TABLE = 'bcs_state_machine_runs'
FIELDS = ('id', 'env', 'group_id', 'session_id', 'run_id', 'record_status', 'status',
          'session_activation_count', 'root_run_id', 'rerun_of', 'updated_at_ms', 'completed_at_ms')
MAX_RUNS = 20


def query_arguments(plan) -> dict:
    delivery_arguments(plan)
    # Do not hide chat one-shots (NULL activation), inactive records or older
    # runs: their absence from a product's default listing is not termination.
    return {'db': plan.database, 'table': TABLE, 'select': ','.join(FIELDS),
            'where': f"env = '{plan.environment}' AND group_id = '{plan.group_id}' AND session_id = '{plan.session_id}'",
            'orderby': 'id asc', 'limit': str(MAX_RUNS + 1)}


def project_rows(plan, payload: dict) -> list[dict]:
    query_arguments(plan)
    if (not isinstance(payload, dict) or payload.get('success') is not True
            or type(payload.get('truncated', False)) is not bool or payload.get('truncated', False)
            or not isinstance(payload.get('data'), list) or len(payload['data']) > MAX_RUNS):
        raise EvidenceError('state-machine inventory failed or exceeds its complete-page bound')
    result, seen, previous = [], set(), 0
    for row in payload['data']:
        if (not isinstance(row, dict) or not set(FIELDS) <= set(row)
                or row['env'] != plan.environment or row['group_id'] != plan.group_id
                or row['session_id'] != plan.session_id
                or type(row['id']) is not int or row['id'] <= previous
                or not isinstance(row['record_status'], str) or not row['record_status']
                or len(row['record_status']) > 64
                or row['status'] not in ('pending', 'running', 'completed', 'failed', 'aborted')):
            raise EvidenceError('state-machine row differs from its exact scope or ordered schema')
        identifier(row['run_id'], 'state-machine Run ID')
        for key in ('root_run_id', 'rerun_of'):
            if row[key] is not None:
                identifier(row[key], 'state-machine lineage ID')
        if row['run_id'] in seen:
            raise EvidenceError('duplicate state-machine Run identity')
        if (type(row['updated_at_ms']) is not int or row['updated_at_ms'] < 0
                or row['completed_at_ms'] is not None and
                   (type(row['completed_at_ms']) is not int or row['completed_at_ms'] < 0)
                or row['session_activation_count'] is not None and
                   (type(row['session_activation_count']) is not int or row['session_activation_count'] < 1)):
            raise EvidenceError('state-machine activation or timestamps have invalid types')
        seen.add(row['run_id']); previous = row['id']
        result.append({key: row[key] for key in FIELDS})
    return result
