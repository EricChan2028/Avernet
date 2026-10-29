"""Scalar-only legacy cron-list evidence, never a complete input-boundary proof.

The V1 draft-only routine Page loses failed_targets, so this parser deliberately
accepts only the independently reviewed legacy CronListApiResponse shape.
"""
from __future__ import annotations

from .profile import ConfigError, identifier

MAX_ROWS = 100
STAGES = {'draft', 'verify', 'online'}


def project_crons(payload: dict, *, bot_id: str, owner_id: str) -> dict:
    identifier(bot_id, 'fixture backend Bot ID')
    identifier(owner_id, 'fixture owner ID')
    if bot_id.lower() == 'all':
        raise ConfigError('fixture cron observation forbids owner-wide Bot selectors')
    if (not isinstance(payload, dict) or type(payload.get('success')) is not bool
            or type(payload.get('error_code')) is not int
            or not isinstance(payload.get('data'), list)
            or not isinstance(payload.get('failed_targets'), list)
            or len(payload['data']) > MAX_ROWS or len(payload['failed_targets']) > 8
            or payload.get('truncated', False) is not False):
        raise ConfigError('cron observation requires a bounded legacy response with explicit failed targets')
    if payload['success'] is not True or payload['error_code'] != 200:
        return {'status': 'BLOCKED', 'reason': 'UPSTREAM_QUERY_FAILED', 'bot_id': bot_id,
                'owner_id': owner_id, 'jobs': [], 'failed_target_count': len(payload['failed_targets']),
                'input_boundary': 'NOT_PROVEN', 'pool_release_authorized': False, 'ready_to_execute': False}
    jobs, failed, seen = [], [], set()
    duplicate_count = 0
    for row in payload['data']:
        if not isinstance(row, dict) or row.get('bot_id') != bot_id or row.get('owner_id') != owner_id:
            raise ConfigError('cron observation returned an unbound Bot or owner')
        job_id = row.get('task_id') or row.get('id')
        identifier(job_id, 'fixture cron ID')
        if row.get('task_id') and row.get('id') and row['task_id'] != row['id']:
            raise ConfigError('cron observation returned conflicting job identities')
        stage = row.get('runtime_stage')
        if stage is not None and (not isinstance(stage, str) or stage not in STAGES):
            raise ConfigError('cron observation returned an unknown runtime stage')
        enabled = row.get('enabled')
        if enabled is not None and type(enabled) is not bool:
            raise ConfigError('cron observation enabled state is invalid')
        key = (job_id, stage)
        if key in seen:
            duplicate_count += 1
        seen.add(key)
        jobs.append({'job_id': job_id, 'runtime_stage': stage, 'enabled': enabled})
    for row in payload['failed_targets']:
        if not isinstance(row, dict) or row.get('bot_id') != bot_id or row.get('owner_id') != owner_id:
            raise ConfigError('cron failure target differs from the bound Bot or owner')
        stage = row.get('runtime_stage')
        if stage is not None and (not isinstance(stage, str) or stage not in STAGES):
            raise ConfigError('cron failure target has an unknown runtime stage')
        # The upstream's arbitrary error/message/name fields can contain private
        # data. Keep only scoped identity and a fixed failure classification.
        failed.append({'runtime_stage': stage, 'status': 'QUERY_FAILED'})
    incomplete = bool(failed or duplicate_count or any(j['enabled'] is None for j in jobs))
    return {'status': 'INCOMPLETE' if incomplete else 'OBSERVED',
            'bot_id': bot_id, 'owner_id': owner_id, 'jobs': jobs,
            'job_count': len(jobs), 'enabled_count': sum(j['enabled'] is True for j in jobs),
            'unknown_enabled_count': sum(j['enabled'] is None for j in jobs),
            'duplicate_count': duplicate_count, 'failed_targets': failed,
            'failed_target_count': len(failed), 'query_coverage': 'RETURNED_RUNTIME_TARGETS_ONLY',
            'empty_meaning': 'NO_CRON_ROWS_RETURNED_NOT_ABSENCE_PROOF',
            'runtime_inventory_completeness': 'NOT_VERIFIED', 'running_jobs': 'NOT_QUERIED',
            'external_callbacks': 'NOT_QUERIED', 'background_work': 'NOT_QUERIED',
            'input_boundary': 'NOT_PROVEN', 'pool_release_authorized': False, 'ready_to_execute': False}
