"""Full foreground remote command contract, using synthetic external services."""
import contextlib
import copy
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.ledger import Ledger
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import read, write
import test_remote_observer_cli as fixtures
from test_remote_pilot_all_cases import RemoteShapedAdapter


class RemoteRunTests(unittest.TestCase):
    setUp = fixtures.RemoteObserverCliTests.setUp
    command = fixtures.RemoteObserverCliTests.command
    process = fixtures.RemoteObserverCliTests.process
    composed = fixtures.RemoteObserverCliTests.composed

    def args(self):
        return ['remote-run', '--profile', str(self.profile_path), '--run-id', self.run_id,
            '--state-dir', str(self.state), '--observer-binding', str(self.receipt_path),
            '--mcporter-binary', str(self.executable), '--allow-read', '--allow-observer-read',
            '--allow-write', '--confirm-run-id', self.run_id]

    @contextlib.contextmanager
    def dependencies(self, fail_preflight=False):
        http = Mock(); http.evidence = []
        def respond(operation, bot_id=None):
            if operation == 'environment.read': data = {'env': 'wrong' if fail_preflight else 'pre'}
            elif operation == 'identity.read': data = {'user_id': 'test-owner'}
            else: data = {'bot_id': bot_id, 'kind': 'bot', 'created_by': 'test-owner', 'env': 'pre', 'status': 'online'}
            return SimpleNamespace(data=data)
        http.read.side_effect = respond
        with self.composed(), patch('bcs_e2e.remote_check.ReadClient', return_value=http), \
             patch('bcs_e2e.owned_bcs.OwnedBcsProcess.start', side_effect=AssertionError('remote start forbidden')), \
             patch('bcs_e2e.owned_bcs.OwnedBcsProcess.stop', side_effect=AssertionError('remote stop forbidden')), \
             patch('bcs_e2e.local_fixtures.OwnedLocalFixtures.apply', side_effect=AssertionError('remote fixture mutation forbidden')), \
             patch('bcs_e2e.cleanup.execute_cleanup', side_effect=AssertionError('standalone recovery forbidden')):
            yield

    def assert_restored(self, previous):
        self.assertEqual({sig: signal.getsignal(sig) for sig in previous}, previous)

    def signals(self):
        return {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def test_foreground_command_runs_same_three_cases_and_generates_all_reports(self):
        previous = self.signals()
        with self.dependencies(): rc, result = self.command(self.args())
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'REMOTE_PILOT_PASS')
        self.assertEqual(result['preflight_status'], 'DISCOVERY_PASSED')
        self.assertEqual(result['pilot_status'], 'PILOT_PASS')
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(result['target_lifecycle'], 'NOT_MANAGED')
        self.assertEqual([c['status'] for c in result['cases']], ['PASS'] * 3)
        self.assertEqual(set(result['reports']), {'json', 'junit', 'markdown'})
        self.assertEqual(read(self.state / 'remote-run.json'), result)
        self.assertFalse(Path(read(self.state / 'run.json')['lock_path']).exists())
        self.assertEqual(read(self.state / 'preflight.json')['observer_preflight']['business_queries'], 0)
        self.assertEqual(len([c for c in self.calls if c[2] == 'test-mcp.db_query']), 4)
        self.assert_restored(previous)

    def test_bad_http_preflight_never_prepares_run_and_preserves_complete_denominator(self):
        with self.dependencies(fail_preflight=True), patch('bcs_e2e.remote_run.prepare_run') as prepare:
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2); self.assertFalse(result['prepared'])
        self.assertEqual(result['preflight_status'], 'BLOCKED')
        self.assertEqual([c['status'] for c in result['cases']], ['NOT_RUN'] * 3)
        self.assertFalse((self.state / 'run.json').exists())
        self.assertFalse((self.root / 'pools').exists())
        self.assertEqual(result['reports'], {})
        self.assertEqual(self.calls, [])
        prepare.assert_not_called()
        self.assertEqual(read(self.state / 'remote-run.json'), result)

    def test_failure_in_second_case_stops_third_and_retains_failed_pool(self):
        with self.dependencies(), patch.object(RemoteShapedAdapter, 'session_update', side_effect=RuntimeError('synthetic failure')):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual([c['status'] for c in result['cases']], ['PASS', 'FAIL', 'NOT_RUN'])
        self.assertEqual(result['cases'][1]['cleanup'], 'PASS')
        state = read(self.state / 'run.json')
        self.assertEqual(state['attempted_cases'], ['PRE-01', 'PRE-02'])
        self.assertTrue(Path(state['lock_path']).exists())
        summary = json.loads((self.state / 'reports/summary.json').read_text())
        self.assertEqual(summary['reports']['declared_case_count'], 3)
        self.assertEqual(summary['cases'][2]['status'], 'BLOCKED')

    def test_sigterm_and_chained_cancellation_quarantine_and_restore_handlers(self):
        for mode in ('sigterm', 'chained'):
            if mode == 'chained': self.setUp()
            previous = self.signals()
            def interrupt(*args):
                if mode == 'sigterm': os.kill(os.getpid(), signal.SIGTERM)
                else:
                    try: raise KeyboardInterrupt()
                    finally: raise OSError('private-secondary')
            with self.subTest(mode=mode), self.dependencies(), \
                 patch.object(RemoteShapedAdapter, 'session_update', side_effect=interrupt):
                rc, result = self.command(self.args())
            self.assertEqual(rc, 130, result)
            self.assertEqual(result['status'], 'CANCELLED')
            self.assertEqual([c['status'] for c in result['cases']], ['PASS', 'CANCELLED', 'NOT_RUN'])
            self.assertEqual(result['cases'][1]['cleanup'], 'UNKNOWN')
            self.assertNotIn('private-secondary', json.dumps(result))
            state = read(self.state / 'run.json')
            self.assertEqual(state['status'], 'QUARANTINED')
            self.assertTrue(Path(state['lock_path']).exists())
            with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
                pending = ledger.cleanup_plan()['resources']
                self.assertEqual(len(pending), 3)
                self.assertEqual({r['case_id'] for r in pending}, {'PRE-02'})
                self.assertFalse(any(r['method'] == 'DELETE' and r['case_id'] == 'PRE-02'
                                     for r in ledger.correlations()))
            self.assertEqual(read(self.state / 'remote-run.json'), result)
            self.assert_restored(previous)

    def test_cleanup_failure_stops_pipeline_without_automatic_recovery(self):
        with self.dependencies(), patch.object(RemoteShapedAdapter, 'group_delete', side_effect=RuntimeError('synthetic refusal')):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(result['cases'][0]['functional'], 'PASS')
        self.assertEqual(result['cases'][0]['cleanup'], 'BLOCKED')
        self.assertEqual([c['status'] for c in result['cases']], ['FAIL', 'NOT_RUN', 'NOT_RUN'])
        with Ledger(self.state / 'run.sqlite', self.run_id, fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(len(ledger.cleanup_plan()['resources']), 1)
        self.assertEqual(read(self.state / 'remote-run.json'), result)

    def test_all_permissions_and_fresh_directory_are_required_before_any_client(self):
        for flag in ('--allow-read', '--allow-observer-read', '--allow-write'):
            args = self.args(); args.remove(flag)
            with self.subTest(flag=flag), patch('bcs_e2e.remote_run.remote_check_dependencies') as construct:
                rc, _ = self.command(args)
            self.assertEqual(rc, 2); construct.assert_not_called()
            self.assertFalse(self.state.exists())
        self.state.mkdir(); sentinel = self.state / 'do-not-touch'; sentinel.write_text('retained')
        with patch('bcs_e2e.remote_run.remote_check_dependencies') as construct:
            rc, _ = self.command(self.args())
        self.assertEqual(rc, 2); construct.assert_not_called()
        self.assertEqual(list(self.state.iterdir()), [sentinel])

    def test_review_change_between_check_and_prepare_blocks_before_cases(self):
        from bcs_e2e.remote_check import check_remote
        def changed(*args, **kwargs):
            result = check_remote(*args, **kwargs)
            updated = copy.deepcopy(self.receipt); updated['reviewer'] = 'another-reviewer'
            write(self.receipt_path, updated)
            return result
        with self.dependencies(), patch('bcs_e2e.remote_run.check_remote', side_effect=changed):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual(result['failure']['phase'], 'prepare')
        self.assertTrue(result['prepared'])
        self.assertEqual([c['status'] for c in result['cases']], ['NOT_RUN'] * 3)
        self.assertEqual(len([c for c in self.calls if c[2] == 'test-mcp.db_query']), 0)

    def test_report_and_receipt_write_failures_cannot_report_pipeline_pass(self):
        for phase in ('reports', 'receipt'):
            if phase == 'receipt': self.setUp()
            previous = self.signals()
            def failing_write(path, value):
                if path.name == 'remote-run.json': raise OSError('private-report-path')
                write(path, value)
            fault = (patch('bcs_e2e.remote_run.write_reports', side_effect=OSError('private-report-path'))
                     if phase == 'reports' else patch('bcs_e2e.remote_run.write', side_effect=failing_write))
            with self.dependencies(), fault:
                rc, result = self.command(self.args())
            self.assertEqual(rc, 2)
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual([c['status'] for c in result['cases']], ['PASS'] * 3)
            self.assertTrue(result['finalization_errors'])
            self.assertNotIn('private-report-path', json.dumps(result))
            self.assert_restored(previous)

    def test_signal_during_final_receipt_is_sealed_as_cancelled(self):
        triggered = False
        def signal_write(path, value):
            nonlocal triggered
            write(path, value)
            if path.name == 'remote-run.json' and not triggered:
                triggered = True
                os.kill(os.getpid(), signal.SIGTERM)
        previous = self.signals()
        with self.dependencies(), patch('bcs_e2e.remote_run.write', side_effect=signal_write):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 130)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertTrue(result['cancellation']['requested'])
        self.assertEqual(read(self.state / 'remote-run.json'), result)
        self.assert_restored(previous)

    def test_cancel_during_report_finalization_preserves_primary_and_receipt(self):
        def interrupted(*args):
            try: raise KeyboardInterrupt()
            finally: raise OSError('private-secondary-report')
        with self.dependencies(), patch('bcs_e2e.remote_run.write_reports', side_effect=interrupted):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 130)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['primary_status'], 'PASS')
        self.assertTrue(result['finalization_errors'])
        self.assertNotIn('private-secondary-report', json.dumps(result))
        self.assertEqual(read(self.state / 'remote-run.json'), result)

    def test_partial_remote_lifecycle_artifact_cannot_be_overwritten_by_prepare(self):
        self.state.mkdir(); write(self.state / 'remote-run.json', {'schema_version': 1, 'status': 'BLOCKED'})
        before = (self.state / 'remote-run.json').read_bytes()
        rc, _ = self.command(['prepare', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                              '--run-id', self.run_id, '--observer-binding', str(self.receipt_path)])
        self.assertEqual(rc, 2)
        self.assertEqual((self.state / 'remote-run.json').read_bytes(), before)
        self.assertFalse((self.state / 'run.json').exists())

    def test_case_checkpoint_cancel_with_secondary_error_does_not_retry_or_claim_success(self):
        def checkpoint(path, value):
            if path.name == 'run.json' and value['status'] == 'QUARANTINED':
                raise OSError('private-quarantine-write')
            write(path, value)
            if (path.name == 'run.json' and value['status'] == 'CASE_COMPLETED'
                    and value.get('last_report') == 'report-PRE-02.json'):
                raise KeyboardInterrupt()
        with self.dependencies(), patch('bcs_e2e.pilot.write_state', side_effect=checkpoint):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 130, result)
        self.assertEqual(result['failure']['error_type'], 'KeyboardInterrupt')
        self.assertEqual([c['status'] for c in result['cases']], ['PASS', 'CANCELLED', 'NOT_RUN'])
        self.assertEqual(read(self.state / 'report-PRE-02.json')['status'], 'PASS')
        self.assertFalse((self.state / 'report-PRE-03.json').exists())
        self.assertNotIn('private-quarantine-write', json.dumps(result))
        self.assertTrue(Path(read(self.state / 'run.json')['lock_path']).exists())

    def test_other_pool_owner_between_cases_blocks_without_releasing_their_reservation(self):
        from bcs_e2e.pilot import run_case
        from bcs_e2e.pool import PoolLease, pool_path, pool_scope
        other = None
        def execute(*args, **kwargs):
            nonlocal other
            result = run_case(*args, **kwargs)
            if result['case_id'] == 'PRE-01':
                other = PoolLease.acquire(pool_path(pool_scope(self.profile)), 'other-run', fingerprint(self.profile))
            return result
        try:
            with self.dependencies(), patch('bcs_e2e.remote_run.run_case', side_effect=execute):
                rc, result = self.command(self.args())
            self.assertEqual(rc, 2)
            self.assertEqual(read(self.state / 'run.json')['attempted_cases'], ['PRE-01'])
            self.assertEqual([c['status'] for c in result['cases']], ['PASS', 'NOT_RUN', 'NOT_RUN'])
            self.assertEqual(json.loads(other.path.read_text())['run_id'], 'other-run')
        finally:
            if other is not None: other.release(); other.close()

    def test_competing_directory_claim_does_not_write_any_owned_receipt(self):
        mkdir = Path.mkdir
        target = self.state.resolve()
        def claim(path, *args, **kwargs):
            if path == target:
                mkdir(path, *args, **kwargs)
                (path / 'external-sentinel').write_text('keep')
                raise FileExistsError('synthetic competing claim')
            return mkdir(path, *args, **kwargs)
        previous = self.signals()
        with self.dependencies(), patch.object(Path, 'mkdir', claim):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertEqual([p.name for p in self.state.iterdir()], ['external-sentinel'])
        self.assertEqual([c['status'] for c in result['cases']], ['NOT_RUN'] * 3)
        self.assertEqual(self.calls, [])
        self.assert_restored(previous)

    def test_partial_prepare_cancellation_is_not_reported_as_no_reservation(self):
        from bcs_e2e.prepare import prepare_run
        def interrupted(*args, **kwargs):
            prepare_run(*args, **kwargs)
            raise KeyboardInterrupt()
        with self.dependencies(), patch('bcs_e2e.remote_run.prepare_run', side_effect=interrupted):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 130)
        self.assertFalse(result['prepared'])
        self.assertEqual(result['preparation_status'], 'UNCONFIRMED')
        self.assertEqual([c['status'] for c in result['cases']], ['NOT_RUN'] * 3)
        self.assertTrue(Path(read(self.state / 'run.json')['lock_path']).exists())
        self.assertEqual(result['reports'], {})

    def test_partial_signal_installation_restores_previous_handlers(self):
        previous = self.signals(); install = signal.signal
        failed = False
        def fail_once(sig, handler):
            nonlocal failed
            if sig == signal.SIGINT and not failed:
                failed = True
                raise OSError('synthetic handler failure')
            return install(sig, handler)
        with self.dependencies(), patch('bcs_e2e.remote_run.signal.signal', side_effect=fail_once):
            rc, result = self.command(self.args())
        self.assertEqual(rc, 2)
        self.assertFalse(self.state.exists())
        self.assertEqual(result['preparation_status'], 'NOT_STARTED')
        self.assert_restored(previous)

    def test_unconfirmed_mcp_child_reaping_survives_exception_projection(self):
        for chained in (False, True):
            if chained: self.setUp()
            cancellation = KeyboardInterrupt()
            cancellation.child_shutdown_status = 'UNCONFIRMED'
            def interrupted(*args, **kwargs):
                if not chained: raise cancellation
                try: raise cancellation
                finally: raise OSError('private-secondary-reap')
            with self.subTest(chained=chained), self.dependencies(), \
                 patch('bcs_e2e.remote_run.check_remote', side_effect=interrupted):
                rc, result = self.command(self.args())
            self.assertEqual(rc, 130)
            self.assertEqual(result['failure']['child_shutdown_status'], 'UNCONFIRMED')
            self.assertNotIn('private-secondary-reap', json.dumps(result))
            self.assertEqual(read(self.state / 'remote-run.json'), result)

    def test_partial_handler_installation_then_second_signal_keeps_denominator(self):
        previous = self.signals(); install = signal.signal
        first = second = False
        def interrupted_install(sig, handler):
            nonlocal first
            value = install(sig, handler)
            if sig == signal.SIGTERM and not first:
                first = True
                os.kill(os.getpid(), signal.SIGTERM)
            return value
        def interrupt_result(value):
            nonlocal second
            if first and not second:
                second = True
                os.kill(os.getpid(), signal.SIGINT)
            return fingerprint(value)
        with self.dependencies(), patch('bcs_e2e.remote_run.signal.signal', side_effect=interrupted_install), \
             patch('bcs_e2e.remote_run.fingerprint', side_effect=interrupt_result):
            rc, result = self.command(self.args())
        self.assertTrue(first and second)
        self.assertEqual(rc, 130)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['cancellation']['signal'], 'SIGTERM')
        self.assertEqual([case['status'] for case in result['cases']], ['NOT_RUN'] * 3)
        self.assertFalse(self.state.exists())
        self.assert_restored(previous)
