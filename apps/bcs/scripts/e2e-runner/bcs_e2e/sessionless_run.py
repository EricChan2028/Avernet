"""Separately reported native sessionless CRUD; not one of the release pilots.

Single foreground attempt, durable group-only intent, same fixture pool, bounded
API/DB observations. Any uncertain write quarantines the pool: no POST retry,
list/adopt sweep, implicit recovery or blanket cleanup. No DB write is exposed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import signal
import time
import uuid
import xml.etree.ElementTree as ET

from .alisddbquery import AlisdDbQueryObserver
from .case_scope import CaseScopedAdapter
from .correlation import CorrelatedEvidence
from .evidence import EvidenceError, _make_plan
from .ledger import Ledger
from .pool import PoolLease, pool_path, pool_scope
from .profile import ConfigError, fingerprint, identifier
from .remote_observer import InputReviewedBinding, _reviewed_client, binding_summary, validate_binding
from .run_state import read, write
from .write_transport import WriteTransportError
from .sessionless_native import CASE_ID, NativeSessionlessClient, require_profile, validate_review


def assert_created(data: dict, profile: dict) -> str:
    if not isinstance(data, dict):
        raise ConfigError('native create response is not an object')
    group_id = identifier(data.get('id'), 'created group ID')
    driver, member = (profile['actors'][r]['bot_id'] for r in ('driver', 'member'))
    if (data.get('created') is not True or data.get('group_kind') != 'normal'
            or data.get('driver_bot') != driver
            or not isinstance(data.get('participants'), list)
            or not all(isinstance(v, str) for v in data['participants'])
            or not {driver, member} <= set(data['participants'])
            or not set(data['participants']) <= {driver, member, 'human_'+profile['human']['user_id']}
            or type(data.get('context_injected')) is not int or data['context_injected'] != 0
            or any(k not in data or data[k] is not None
                   for k in ('session_id', 'initial_session_id', 'initial_run'))):
        raise ConfigError('native creation did not confirm the reviewed sessionless contract')
    return group_id


def assert_group(data: dict, group_id: str, profile: dict, name: str | None = None):
    driver, member = (profile['actors'][r]['bot_id'] for r in ('driver', 'member'))
    human = 'human_' + profile['human']['user_id']
    expected = {'id': group_id, 'originator': human, 'driver_bot': driver,
                'driver_bot_owner': human, 'group_kind': 'normal', 'group_strategy': 'chat',
                'visibility': 'private', 'status': 'active', 'latest_running_session_id': None}
    if (not isinstance(data, dict) or any(k not in data or data[k] != v for k, v in expected.items())
            or (name is not None and data.get('label') != name)):
        raise ConfigError('group read-back differs from exact owned sessionless group')
    participants = data.get('participants')
    if not isinstance(participants, list) or any(not isinstance(v, dict) for v in participants):
        raise ConfigError('group participant projection is invalid')
    ids = [v.get('bot_uuid') for v in participants]
    if (any(not isinstance(v, str) for v in ids) or len(ids) != len(set(ids))
            or not {driver, member} <= set(ids) or not set(ids) <= {driver, member, human}):
        raise ConfigError('group has unexpected or missing participants')


def publish(state_dir: Path, report: dict):
    write(state_dir/'sessionless-report.json', report)
    root = ET.Element('testsuite', name='native-sessionless-group', tests='1',
                      failures='1' if report['status'] == 'FAIL' else '0',
                      skipped='1' if report['status'] in {'BLOCKED', 'CANCELLED'} else '0')
    case = ET.SubElement(root, 'testcase', name=CASE_ID, classname='independent-native-slice')
    if report['status'] != 'PASS':
        ET.SubElement(case, 'failure' if report['status'] == 'FAIL' else 'skipped',
                      message=report.get('reason', report['status']))
    # Private reports are local artifacts, never automatic uploads.
    for name, body in [('sessionless-junit.xml', ET.tostring(root, encoding='unicode')+'\n'),
                       ('sessionless-summary.md', '# Native sessionless group slice\n\n'
                        + '\n'.join(f'- {k}: {report[k]}' for k in
                                    ('status', 'functional', 'evidence_status', 'cleanup', 'pool', 'full_release_gate'))
                        + '\n\nThis is not PRE-01/02/03, WS, chat, file, coverage or benchmark qualification.\n')]:
        path = state_dir/name
        if path.is_symlink(): raise ConfigError('report cannot be a symlink')
        with path.open('x', encoding='utf-8') as stream:
            path.chmod(0o600); stream.write(body)


def sessionless_run(profile: dict, *, state_dir: Path, run_id: str, review_path: Path,
                    source_review: Path, receipt: Path, executable: Path,
                    allow_read: bool, allow_observer_read: bool, allow_write: bool,
                    confirm_run_id: str) -> dict:
    require_profile(profile); identifier(run_id, 'run_id')
    if not (allow_read is True and allow_observer_read is True and allow_write is True
            and confirm_run_id == run_id):
        raise ConfigError('sessionless run requires explicit HTTP/MCP reads, writes and exact Run confirmation')
    review = validate_review(read(review_path), profile)
    if source_review.is_symlink() or not source_review.is_file():
        raise ConfigError('sessionless source review must be a regular file')
    with source_review.open('rb') as stream:
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024 or hashlib.sha256(raw).hexdigest() != review['source_review_sha256']:
        raise ConfigError('sessionless source review digest mismatch')
    receipt_value = validate_binding(read(receipt), profile)
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('sessionless attempt requires a fresh private state directory')
    state_dir.mkdir(mode=0o700, parents=True)
    digest = fingerprint(profile)
    report = {'schema_version': 1, 'suite': 'native-sessionless-group-v1', 'case_id': CASE_ID,
              'run_id': run_id, 'status': 'BLOCKED', 'functional': 'NOT_RUN',
              'evidence_status': 'NOT_RUN', 'cleanup': 'NOT_ATTEMPTED', 'pool': 'NOT_ACQUIRED',
              'full_release_gate': 'NOT_QUALIFIED', 'counts': {'total': 1, 'passed': 0, 'failed': 0, 'blocked': 1},
              'assertions': [], 'requests': [], 'semantic_evidence': [], 'profile_digest': digest,
              'deployment': {'source': 'AUTHENTICATED_SELF_REPORT_AND_SOURCE_REVIEW',
                             'binary_attestation': 'NOT_PERFORMED', **review},
              'observer_binding': binding_summary(receipt_value), 'started_at': int(time.time())}
    write(state_dir/'profile.json', profile)
    write(state_dir/'source-review-binding.json', review)
    write(state_dir/'observer-binding.json', receipt_value)
    write(state_dir/'sessionless-report.json', report)
    report['runner_source_digest'] = hashlib.sha256(b''.join(
        f.name.encode()+f.read_bytes() for f in sorted(Path(__file__).parent.glob('*.py')))).hexdigest()
    binding = InputReviewedBinding(profile, state_dir/'observer-binding.json')
    mcp = _reviewed_client(profile, executable, binding)
    observer = AlisdDbQueryObserver(client=mcp, database=profile['observer']['database'],
        environment=profile['environment']['expected_bcs_env'], environment_binding_confirmed=True,
        physical_binding_probe=binding)
    client = NativeSessionlessClient(profile, review)
    lease = None
    previous_handler = signal.getsignal(signal.SIGTERM)
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    try:
        lease = PoolLease.acquire(pool_path(pool_scope(profile)), run_id, digest)
        report['pool'] = 'QUARANTINED'
        write(state_dir/'sessionless-state.json', {'schema_version': 1, 'run_id': run_id,
              'profile_digest': digest, 'pool_scope': pool_scope(profile),
              'lock_path': str(pool_path(pool_scope(profile))), 'status': 'RUNNING'})
        with Ledger(state_dir/'ledger.sqlite', run_id, digest, create=True) as ledger:
            client.evidence = CorrelatedEvidence(ledger, CASE_ID, 1)
            try:
                client.preflight()
                observer.admit(('group.persisted',))
                binding.validate()
                report['assertions'].append({'id': 'admission', 'status': 'PASS'})
                request_id = 'e2e-create-'+uuid.uuid4().hex
                handle = ledger.intent(CASE_ID, 1, 'group', request_id)
                # Intent exists before all possible business writes. Never retry create.
                data = client.create('E2E '+run_id, request_id)
                if isinstance(data, dict) and isinstance(data.get('id'), str) and data.get('created') is True:
                    ledger.observed(handle, identifier(data['id'], 'created group ID'), origin='created',
                                    actual_request_id=client.evidence[-1].get('submitted_request_id'))
                group_id = assert_created(data, profile)
                scope = CaseScopedAdapter(None, ledger, digest, {'id': CASE_ID, 'effects': []}, attempt=1)
                def owned():
                    return scope.observation_scope('group.persisted', group_id)
                owned()
                assert_group(client.read(group_id), group_id, profile)
                report['assertions'].append({'id': 'create_and_read', 'status': 'PASS'})
                owned()
                name = 'E2E '+run_id+' renamed'
                renamed = client.rename(group_id, name)
                if (not isinstance(renamed, dict) or renamed.get('group_id') != group_id
                        or renamed.get('name') != name):
                    raise ConfigError('native rename returned an unexpected v1 detail projection')
                assert_group(client.read(group_id), group_id, profile, name)
                report['functional'] = 'PASS'
                report['assertions'].append({'id': 'rename_readback', 'status': 'PASS'})
                before = owned()
                observed = observer.observe('group.persisted', group_id)
                if owned() != before: raise EvidenceError('resource scope changed during DB read')
                rows = observed['rows']
                if (len(rows) != 1 or rows[0]['driver_bot'] != profile['actors']['driver']['bot_id']
                        or rows[0]['originator'] != 'human_'+profile['human']['user_id']
                        or rows[0]['status'] != 'active'):
                    raise EvidenceError('persisted group differs from exact API resource')
                report['semantic_evidence'].append(observed)
                report['evidence_status'] = 'PASS'
                report['assertions'].append({'id': 'group.persisted', 'status': 'PASS'})
                # Delete only after functional + DB validation; recheck exact owner,
                # membership and lack of active Session. Never list Sessions.
                owned(); assert_group(client.read(group_id), group_id, profile, name)
                report['cleanup'] = 'IN_PROGRESS'
                deleted = client.delete(group_id)
                if not isinstance(deleted, dict) or deleted.get('id') != group_id or deleted.get('deleted') is not True:
                    raise ConfigError('native delete was not explicitly confirmed')
                client.verify_deleted(group_id)
                owned()
                plan = _make_plan('group.persisted', group_id, profile['observer']['database'],
                                  profile['environment']['expected_bcs_env'])
                absent = mcp.db_query(plan)
                if (not isinstance(absent, dict) or absent.get('success') is not True
                        or absent.get('data') != [] or absent.get('truncated', False) is not False):
                    raise EvidenceError('database deletion was not confirmed by the exact bounded query')
                owned(); ledger.mark_deleted(handle)
                report.update(status='PASS', cleanup='PASS')
                report['assertions'].append({'id': 'delete_api_and_db_absent', 'status': 'PASS'})
            except BaseException as error:
                if not isinstance(error, (Exception, KeyboardInterrupt)): raise
                report['status'] = 'CANCELLED' if isinstance(error, KeyboardInterrupt) else (
                    'FAIL' if ledger.resource_snapshot() else 'BLOCKED')
                report['reason'] = ('cancelled; inspect the retained ledger before recovery'
                                    if isinstance(error, KeyboardInterrupt) else str(error) if isinstance(error, (ConfigError, EvidenceError, WriteTransportError))
                                    else type(error).__name__)
                if report['cleanup'] == 'IN_PROGRESS': report['cleanup'] = 'BLOCKED'
                if report['functional'] != 'PASS' and ledger.resource_snapshot(): report['functional'] = 'FAIL'
                report['assertions'].append({'id': 'execution', 'status': report['status']})
            finally:
                report['requests'] = list(client.evidence)
                report['resources'] = ledger.resource_snapshot()
                safe = not report['resources'] or all(row['state'] == 'deleted' for row in report['resources'])
                report['quiescence'] = 'CONFIRMED' if safe else 'UNRESOLVED_RETAINED'
                write(state_dir/'sessionless-report.json', report)
                if safe:
                    lease.release(); report['pool'] = 'RELEASED'
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='CANCELLED' if isinstance(error, KeyboardInterrupt) else 'BLOCKED',
                      reason=type(error).__name__)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if lease is not None: lease.close()
    report['finished_at'] = int(time.time())
    report['counts'] = {'total': 1, 'passed': int(report['status']=='PASS'),
                        'failed': int(report['status']=='FAIL'),
                        'blocked': int(report['status']=='BLOCKED'), 'cancelled': int(report['status']=='CANCELLED')}
    write(state_dir/'sessionless-state.json', {'schema_version': 1, 'run_id': run_id,
          'profile_digest': digest, 'pool_scope': pool_scope(profile),
          'lock_path': str(pool_path(pool_scope(profile))), 'status': report['pool'],
          'quiescence': report.get('quiescence', 'UNKNOWN')})
    publish(state_dir, report)
    return report
