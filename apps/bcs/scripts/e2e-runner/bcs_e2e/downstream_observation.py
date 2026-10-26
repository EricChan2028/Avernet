"""Finite BaaS queue/result reads from already-observed BCS delivery aliases.

The queue's exact env/run/Bot binding is required before reading the result table
which has no env column. No payload, metadata JSON, credential or Bot-wide scan.
These snapshots cannot establish callback delivery or whole-scope completion.
"""
from __future__ import annotations

from dataclasses import dataclass

from .delivery_observation import DeliveryReadPlan, query_arguments as delivery_arguments
from .evidence import EvidenceError
from .profile import identifier

CONTRACT = 'baas-correlated-run-v1'
QUEUE_TABLE = 'baas_bot_run_queue'
QUEUE_FIELDS = ('run_id', 'bot_id', 'session_id', 'env', 'status')
RESULT_TABLE = 'baas_bot_run'
RESULT_FIELDS = ('run_id', 'bot_id', 'status')
MAX_RUNS = 4


@dataclass(frozen=True)
class DownstreamReadPlan:
    parent: DeliveryReadPlan
    delivery_id: str
    target_bot_id: str
    downstream_run_id: str
    baas_bot_id: str


def query_arguments(plan: DownstreamReadPlan, *, result: bool = False) -> dict:
    if type(plan) is not DownstreamReadPlan:
        raise EvidenceError('downstream read requires the fixed correlated-run plan')
    delivery_arguments(plan.parent)
    for field in ('delivery_id', 'target_bot_id', 'downstream_run_id', 'baas_bot_id'):
        identifier(getattr(plan, field), 'downstream.' + field)
    if plan.target_bot_id not in plan.parent.target_bot_ids:
        raise EvidenceError('downstream target is outside the declared test Bots')
    return {'db': plan.parent.database, 'table': RESULT_TABLE if result else QUEUE_TABLE,
            'select': ','.join(RESULT_FIELDS if result else QUEUE_FIELDS),
            'where': ('' if result else f"env = '{plan.parent.environment}' AND ")
                     + f"run_id = '{plan.downstream_run_id}' AND bot_id = '{plan.baas_bot_id}'",
            'limit': '2', 'orderby': 'run_id asc'}


def project_row(plan: DownstreamReadPlan, payload: dict, *, result: bool = False) -> dict | None:
    query_arguments(plan, result=result)
    if (not isinstance(payload, dict) or payload.get('success') is not True
            or type(payload.get('truncated', False)) is not bool or payload.get('truncated', False)):
        raise EvidenceError('downstream observation failed or was truncated')
    rows = payload.get('data')
    if not isinstance(rows, list) or len(rows) > 1:
        raise EvidenceError('downstream run identity is ambiguous or unreadable')
    if not rows:
        return None  # Queue TTL/removal is not proof of completion or absence.
    row = rows[0]
    fields = RESULT_FIELDS if result else QUEUE_FIELDS
    states = {'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'TIME_OUT', 'ABORTED'} if result else {'PENDING', 'RUNNING', 'DONE'}
    if (not isinstance(row, dict) or any(field not in row for field in fields)
            or row['run_id'] != plan.downstream_run_id or row['bot_id'] != plan.baas_bot_id
            or not result and (row['env'] != plan.parent.environment or row['session_id'] != plan.parent.session_id)
            or not isinstance(row['status'], str) or row['status'] not in states):
        raise EvidenceError('downstream row does not match its exact scope/state contract')
    return {field: row[field] for field in fields}


def correlate(plan: DownstreamReadPlan, queue: dict | None, result: dict | None) -> dict:
    return {'delivery_id': plan.delivery_id, 'target_bot_id': plan.target_bot_id,
            'downstream_run_id': plan.downstream_run_id, 'baas_bot_id': plan.baas_bot_id,
            'queue_status': queue['status'] if queue is not None else 'UNOBSERVED',
            'result_status': result['status'] if result is not None else 'UNOBSERVED',
            'known_run_result_completed': queue is not None and result is not None
                and queue['status'] == 'DONE' and result['status'] == 'COMPLETED',
            'callback_status': 'UNCONFIRMED', 'scope_status': 'UNCONFIRMED'}
