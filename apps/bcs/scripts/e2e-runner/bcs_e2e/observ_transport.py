"""Explicit, bounded Observ WS acquisition for an existing retained Run.

The CLI composition root selects this optional deployment adapter. It does not
participate in Story execution, qualification, resource cleanup or pool release.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time

from .bounded_process import ProcessCaptureError, ProcessCaptureTimeout, capture
from . import observ_wire as wire
from .profile import ConfigError, fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write
from .transport_plan import transport_plan
from .transport_projection import CONTRACT, match_transport

MAX_CALLS, MAX_PAGES, MAX_INSTANCES = 64, 3, 8
DEADLINE_SECONDS = 120


class ObservCalls:
    def __init__(self, executable: Path, report: dict, persist, source_check):
        self.executable, self.report = executable, report
        self.persist, self.source_check = persist, source_check
        self.deadline = time.monotonic() + DEADLINE_SECONDS

    def call(self, arguments: list[str], kind: str) -> dict:
        self.source_check()
        remaining = self.deadline - time.monotonic()
        wire.require(remaining > 0 and len(self.report['calls']) < MAX_CALLS,
                     'cumulative acquisition budget exhausted')
        timeout = min(15, remaining)
        entry = {'kind': kind, 'status': 'STARTED', 'ordinal': len(self.report['calls'])+1}
        self.report['calls'].append(entry)
        self.persist()  # Durable intent before the read; crashes cannot replay this directory.
        try:
            result = capture([str(self.executable), *arguments, '--timeout', str(max(1, math.ceil(timeout))), '--output', 'json'],
                             timeout_seconds=timeout, max_output_bytes=2_097_152)
            wire.require(result.returncode == 0, 'CLI read failed; no retry or identity fallback')
            value = json.loads(result.stdout.decode('utf-8'), object_pairs_hook=unique_keys)
            wire.require(isinstance(value, dict), 'CLI did not return an object')
            self.source_check()
            wire.require(time.monotonic() < self.deadline, 'acquisition deadline exceeded')
        except ProcessCaptureTimeout:
            entry['status'] = 'TIMEOUT'
            raise
        except (ValueError, UnicodeError, RecursionError):
            entry['status'] = 'INVALID_JSON'
            raise ConfigError('Observ: CLI did not return valid UTF-8 JSON') from None
        except BaseException:
            entry['status'] = 'FAILED'
            raise
        entry['status'] = 'RETURNED'
        self.persist()
        return value


def _discover(client: ObservCalls, config: dict) -> list[dict]:
    instances, cursors = [], set()
    cursor = None
    for _ in range(MAX_PAGES):
        args = ['entity', 'relation', 'list', '--from-uri', config['from_uri'],
                '--to-uri', config['to_uri'], '--limit', '100']
        if cursor is not None:
            args += ['--cursor', cursor]
        selected, cursor = wire.server_page(client.call(args, 'INSTANCE_DISCOVERY'), config)
        instances.extend(selected)
        wire.require(len(instances) <= MAX_INSTANCES, 'instance budget exceeded; do not select a prefix')
        wire.require(len({x['ip'] for x in instances}) == len(instances)
                     and len({x['uri'] for x in instances}) == len(instances), 'ambiguous instance inventory')
        if cursor is None:
            wire.require(bool(instances), 'no eligible target-environment instances')
            return instances
        wire.require(cursor not in cursors, 'instance pagination repeated a cursor')
        cursors.add(cursor)
    raise ConfigError('Observ: instance discovery is incomplete at page budget')


def _acquire(client: ObservCalls, report: dict, config: dict, plan: dict) -> None:
    instances = _discover(client, config)
    report['discovery'] = {'observed_at_epoch': int(time.time()), 'complete': True,
                           'instances': instances, 'historical_coverage': 'NOT_VERIFIED'}
    start, end = plan['window']['start_epoch'], plan['window']['end_epoch']
    minutes = list(range(start, end, 60))
    expected_calls = len(instances) * (1 + len(minutes)*len(plan['deliveries']))
    wire.require(expected_calls + len(report['calls']) <= MAX_CALLS,
                 'complete query plan exceeds remaining call budget; choose a smaller explicit window')
    for instance in instances:
        value = client.call(['log', 'list', '--app-name', config['app_name'], '--prefer-host', instance['ip'],
                             '--scan-dir', config['log_directory']], 'LOG_DISCOVERY')
        instance['full_path'] = wire.log_target(value, instance, config)
    queries = []
    for delivery in plan['deliveries']:
        for instance in instances:
            for minute in minutes:
                queries.append({'delivery_id': delivery['delivery_id'], 'instance_uri': instance['uri'],
                                'ip': instance['ip'], 'fullPath': instance['full_path'], 'time': minute*1000,
                                'limit': 6, 'keyword': [delivery['request_id'], 'bot_accept']})
    report['query_plan'] = queries
    report['query_plan_digest'] = fingerprint(queries)
    client.persist()  # Never query a target absent from this round's frozen discovery/plan.
    by_uri = {x['uri']: x for x in instances}
    by_delivery = {x['delivery_id']: x for x in plan['deliveries']}
    for query in queries:
        response = client.call(['log', 'grep', '--ip', query['ip'], '--full-path', query['fullPath'],
                                '--keyword', ','.join(query['keyword']), '--time', str(query['time']),
                                '--limit', str(query['limit'])], 'WS_ACK_QUERY')
        records = wire.ack_rows(response, query, by_uri[query['instance_uri']],
                                by_delivery[query['delivery_id']], config)
        wire.require(len(report['partial_records'])+len(records) <= plan['maximum_projected_rows'],
                     'projected row budget exceeded; no trimming or deduplication')
        report['partial_records'].extend(records)
        client.persist()
    projection = {'schema_version': 1, 'contract': CONTRACT, 'plan_digest': plan['plan_digest'],
                  'source_reference': report['acquisition_id'], 'environment': plan['environment'],
                  'status': 'OBSERVED', 'error_code': None, 'truncated': False,
                  'records': report['partial_records']}
    report['projection'] = projection
    report['matching'] = match_transport(plan, projection)
    report['status'] = ('TRANSPORT_OBSERVED' if report['matching']['matching_result'] == 'MATCHED'
                        else 'INCOMPLETE')


def observe_retained(profile: dict, *, state_dir: Path, run_id: str, case_id: str,
                     observation_id: str, start_epoch: int, end_epoch: int,
                     binding_path: Path, executable: Path, output_dir: Path,
                     allow_read: bool, confirm_run_id: str) -> dict:
    wire.require(allow_read is True and confirm_run_id == run_id, 'explicit read permission and exact Run confirmation required')
    wire.require(profile['environment']['tier'] == 'preprod', 'this collector is admitted only for preproduction')
    wire.require(start_epoch % 60 == 0 and end_epoch % 60 == 0,
                 'use an explicitly minute-aligned window; never silently expand it')
    plan = transport_plan(profile, state_dir, run_id, case_id, observation_id, start_epoch, end_epoch)
    wire.require(0 <= time.time()-start_epoch < 86400 and end_epoch <= time.time(),
                 'log window must be in the past 24 hours')
    config = wire.binding(read(binding_path), plan['environment'])
    wire.require(executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK),
                 'an explicit executable Observ binary is required')
    wire.require(output_dir.is_absolute() and not output_dir.exists() and not output_dir.is_symlink(),
                 'acquisition output directory must be new and absolute')
    wire.require(not output_dir.resolve().is_relative_to(state_dir.resolve())
                 and not state_dir.resolve().is_relative_to(output_dir.resolve()),
                 'acquisition artifacts must be outside the original Run')
    wire.require(output_dir.parent.is_dir() and not any(p.is_symlink() for p in (output_dir, *output_dir.parents)),
                 'output parent must exist without symlinks')
    acquisition_id = identifier(output_dir.name, 'Observ acquisition ID')
    report = {'schema_version': 1, 'contract': 'observ-retained-ws-acquisition-v1',
              'status': 'RUNNING', 'acquisition_id': acquisition_id, 'run_id': run_id, 'case_id': case_id,
              'plan': plan, 'binding': config, 'binding_digest': fingerprint(config), 'calls': [], 'partial_records': [],
              'maximum_calls': MAX_CALLS, 'deadline_seconds': DEADLINE_SECONDS,
              'collector': 'OBSERV_CLI_WS', 'scope_status': 'UNCONFIRMED', 'execution_status': 'UNCONFIRMED',
              'pool_release_authorized': False, 'ready_to_execute': False}
    def unchanged():
        current = transport_plan(profile, state_dir, run_id, case_id, observation_id, start_epoch, end_epoch)
        wire.require(current == plan and wire.binding(read(binding_path), plan['environment']) == config,
                     'source observation or environment binding changed')
    def persist():
        write(output_dir/'acquisition.json', report)
    output_dir.mkdir(mode=0o700, exist_ok=False)
    persist()
    try:
        _acquire(ObservCalls(executable, report, persist, unchanged), report, config, plan)
        unchanged()
    except (ConfigError, RunStateError, ProcessCaptureError, OSError) as error:
        report['status'] = 'BLOCKED'
        report['reason'] = (str(error) if isinstance(error, ConfigError) else 'Observ acquisition transport failed')
        if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
            report['child_shutdown_status'] = 'UNCONFIRMED'
    except KeyboardInterrupt as error:
        report['status'] = 'CANCELLED'
        if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
            report['child_shutdown_status'] = 'UNCONFIRMED'
    report['finished_at_epoch'] = int(time.time())
    persist()
    return report
