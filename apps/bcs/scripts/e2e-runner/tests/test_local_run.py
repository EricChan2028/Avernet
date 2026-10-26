"""Foreground orchestration fault injection with an owned real loopback child.

Stories/fixtures are stubbed here; actual three-Story CLI is tested separately.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_run import local_run
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256
from bcs_e2e.profile import ConfigError
from bcs_e2e.run_state import read
from test_owned_bcs import HELPER


@unittest.skipUnless(shutil.which('lsof'), 'owned local workflow requires lsof')
class LocalRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.binary = self.root / 'fake-bcs'
        self.binary.write_text('#!' + sys.executable + '\n' + HELPER.replace('MODE',
                               'signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))'))
        self.binary.chmod(0o700)
        self.args = dict(binary=self.binary, binary_sha256=file_sha256(self.binary),
                         state_dir=self.root / 'state', run_id='local-workflow',
                         allow_local_processes=True, allow_fixture_write=True, allow_write=True,
                         confirm_run_id='local-workflow', startup_timeout=5, stop_timeout=1)
        self.previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def assert_stopped(self, result):
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['shutdown']['returncode'], 0)
        self.assertTrue((self.root / 'state/runtime/console.log').is_file())
        self.assertEqual(read(self.root / 'state/local-run.json'), result)
        for sig, handler in self.previous.items(): self.assertEqual(signal.getsignal(sig), handler)

    def test_every_permission_and_exact_confirmation_is_required_before_start(self):
        for field in ('allow_local_processes', 'allow_fixture_write', 'allow_write', 'confirm_run_id'):
            with patch('bcs_e2e.local_run.OwnedBcsProcess') as runtime:
                with self.assertRaises(ConfigError):
                    local_run(**{**self.args, field: 'wrong' if field == 'confirm_run_id' else False})
                runtime.assert_not_called()
            self.assertFalse(self.args['state_dir'].exists())

    def test_existing_root_is_never_adopted_or_overwritten(self):
        state = self.args['state_dir']
        state.mkdir()
        (state / 'keep').write_text('user data')
        with self.assertRaises(ConfigError): local_run(**self.args)
        self.assertEqual((state / 'keep').read_text(), 'user data')
        self.assertFalse((state / 'local-run.json').exists())

    def test_fixture_failure_stops_owned_child_and_preserves_sanitized_primary_error(self):
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply', side_effect=RuntimeError('secret-url')), \
             patch('bcs_e2e.local_run.prepare_run') as prepare, patch('bcs_e2e.local_run.run_case') as case:
            result = local_run(**self.args)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure'], {'phase': 'fixtures', 'error_type': 'RuntimeError'})
        self.assertNotIn('secret-url', json.dumps(result))
        prepare.assert_not_called(); case.assert_not_called()
        self.assert_stopped(result)

    def test_sigterm_then_sigint_during_finalization_does_not_interrupt_reaping(self):
        original = OwnedBcsProcess.stop
        def stop(runtime):
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)
            return original(runtime)
        def interrupt(*_args, **_kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply', side_effect=interrupt), \
             patch.object(OwnedBcsProcess, 'stop', stop):
            result = local_run(**self.args)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['failure']['phase'], 'fixtures')
        self.assert_stopped(result)

    def test_secondary_marker_failure_does_not_hide_cancellation(self):
        def marker_failure(*args, **kwargs):
            try:
                raise KeyboardInterrupt()
            except BaseException:
                raise OSError('private-path')
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply', side_effect=marker_failure):
            result = local_run(**self.args)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['failure']['error_type'], 'KeyboardInterrupt')
        self.assertEqual(result['failure']['surfaced_error_type'], 'OSError')
        self.assert_stopped(result)

    def test_primary_error_survives_stop_receipt_failure_without_second_stop(self):
        original = OwnedBcsProcess.stop
        calls = []
        def stop(runtime):
            calls.append(runtime)
            original(runtime)
            raise OSError('private-marker-path')
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply', side_effect=RuntimeError('primary')), \
             patch.object(OwnedBcsProcess, 'stop', stop):
            result = local_run(**self.args)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['failure']['error_type'], 'RuntimeError')
        self.assertEqual(result['finalization_errors'], [{'phase': 'shutdown', 'error_type': 'OSError'}])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assert_stopped(result)

    def test_failed_case_stops_sequence_and_report_error_does_not_skip_shutdown(self):
        failed = {'case_id': 'PRE-01', 'status': 'FAIL', 'functional': 'FAIL', 'cleanup': 'BLOCKED'}
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply'), \
             patch('bcs_e2e.local_run.resolve_owned_profile', return_value={'schema_version': 1}), \
             patch('bcs_e2e.local_run.prepare_run'), \
             patch('bcs_e2e.local_run.run_case', return_value=failed) as case, \
             patch('bcs_e2e.local_run.build_report', side_effect=OSError('report-secret')):
            result = local_run(**self.args)
        self.assertEqual(case.call_count, 1)
        self.assertEqual(result['primary_status'], 'FAIL')
        self.assertEqual(result['finalization_errors'], [{'phase': 'reports', 'error_type': 'OSError'}])
        self.assert_stopped(result)

    def test_startup_failure_attempts_stop_once_not_again_in_outer_finalizer(self):
        original = OwnedBcsProcess._reap
        calls = []
        def reap(runtime):
            calls.append(runtime)
            return original(runtime)
        self.binary.write_text('#!' + sys.executable + '\nraise SystemExit(9)\n')
        with patch.object(OwnedBcsProcess, '_reap', reap):
            result = local_run(**{**self.args, 'binary_sha256': file_sha256(self.binary)})
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure']['phase'], 'startup')
        self.assertEqual(result['shutdown']['status'], 'FAILED')

    def test_cli_cancellation_returns_130_without_profile(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
             patch('bcs_e2e.cli.local_run', return_value={'status': 'CANCELLED'}) as run:
            rc = main(['local-run', '--binary', str(self.binary), '--binary-sha256', file_sha256(self.binary),
                       '--state-dir', str(self.root / 'state'), '--run-id', 'r', '--confirm-run-id', 'r',
                       '--allow-local-processes', '--allow-fixture-write', '--allow-write'])
        self.assertEqual(rc, 130)
        self.assertNotIn('profile', run.call_args.kwargs)

    def test_lifecycle_receipt_write_failure_is_secondary_and_non_success(self):
        from bcs_e2e.run_state import write as actual_write
        def write(path, data):
            if path.name == 'local-run.json': raise OSError('private-path')
            actual_write(path, data)
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply', side_effect=ValueError('primary-secret')),              patch('bcs_e2e.local_run.write', side_effect=write):
            result = local_run(**self.args)
        self.assertEqual(result['failure']['error_type'], 'ValueError')
        self.assertEqual(result['finalization_errors'], [{'phase': 'lifecycle-receipt', 'error_type': 'OSError'}])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertFalse((self.root / 'state/local-run.json').exists())
        self.assertNotIn('primary-secret', json.dumps(result))

    def test_forced_stop_cannot_become_pass_after_all_case_reports_pass(self):
        self.binary.write_text('#!' + sys.executable + '\n' + HELPER.replace('MODE',
                               'signal.signal(signal.SIGTERM, signal.SIG_IGN)'))
        def passed(profile, state, run_id, case, **kwargs):
            return {'case_id': case, 'status': 'PASS', 'functional': 'PASS', 'cleanup': 'PASS'}
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply'),              patch('bcs_e2e.local_run.resolve_owned_profile', return_value={'schema_version': 1}),              patch('bcs_e2e.local_run.prepare_run'),              patch('bcs_e2e.local_run.run_case', side_effect=passed),              patch('bcs_e2e.local_run.build_report', return_value={'status': 'PILOT_PASS'}),              patch('bcs_e2e.local_run.write_reports', return_value={'outputs': {}}):
            result = local_run(**{**self.args, 'binary_sha256': file_sha256(self.binary)})
        self.assertEqual(result['primary_status'], 'PASS')
        self.assertEqual(result['pilot_status'], 'PILOT_PASS')
        self.assertEqual(result['shutdown']['status'], 'FAILED_STOP')
        self.assertTrue(result['shutdown']['forced_stop'])
        self.assertEqual(result['status'], 'BLOCKED')

    def test_first_signal_during_startup_reaping_preserves_failure_and_completes_one_stop(self):
        from bcs_e2e.runtime import RuntimeContractError
        self.binary.write_text('#!' + sys.executable + '\n' + HELPER.replace('MODE',
                               'signal.signal(signal.SIGTERM, signal.SIG_IGN)'))
        original_listener, original_reap = OwnedBcsProcess._owns_listener, OwnedBcsProcess._reap
        calls = []
        def fail_readiness(runtime, timeout):
            if original_listener(runtime, timeout):
                raise RuntimeContractError('synthetic startup failure')
            return False
        def reap(runtime):
            calls.append(runtime)
            original_wait = runtime.child.wait
            first = True
            def wait(*args, **kwargs):
                nonlocal first
                if first:
                    first = False
                    os.kill(os.getpid(), signal.SIGTERM)
                return original_wait(*args, **kwargs)
            with patch.object(runtime.child, 'wait', side_effect=wait):
                return original_reap(runtime)
        with patch.object(OwnedBcsProcess, '_owns_listener', fail_readiness), \
             patch.object(OwnedBcsProcess, '_reap', reap):
            result = local_run(**{**self.args, 'binary_sha256': file_sha256(self.binary)})
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0].child.poll())
        self.assertTrue(result['shutdown']['forced_stop'])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failure'], {'phase': 'startup', 'error_type': 'RuntimeContractError'})
        self.assertEqual(result['cancellation'], {'requested': True, 'signal': 'SIGTERM'})

    def test_first_cancellation_in_stop_or_reports_prevents_success_without_interrupting_finalization(self):
        for phase in ('stop', 'reports'):
            state = self.root / phase
            original_stop = OwnedBcsProcess.stop
            calls = []
            def stop(runtime):
                calls.append(runtime)
                if phase == 'stop': os.kill(os.getpid(), signal.SIGINT)
                return original_stop(runtime)
            def render(*args):
                if phase == 'reports': os.kill(os.getpid(), signal.SIGTERM)
                return {'outputs': {}}
            def passed(profile, state, run_id, case, **kwargs):
                return {'case_id': case, 'status': 'PASS', 'functional': 'PASS', 'cleanup': 'PASS'}
            with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply'), \
                 patch('bcs_e2e.local_run.resolve_owned_profile', return_value={'schema_version': 1}), \
                 patch('bcs_e2e.local_run.prepare_run'), \
                 patch('bcs_e2e.local_run.run_case', side_effect=passed), \
                 patch('bcs_e2e.local_run.build_report', return_value={'status': 'PILOT_PASS'}), \
                 patch('bcs_e2e.local_run.write_reports', side_effect=render), \
                 patch.object(OwnedBcsProcess, 'stop', stop):
                result = local_run(**{**self.args, 'state_dir': state})
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['primary_status'], 'PASS')
            self.assertEqual(result['status'], 'CANCELLED')
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertTrue(result['cancellation']['requested'])
            self.assertEqual(read(state / 'local-run.json'), result)

    def test_cancellation_during_partial_handler_installation_restores_both_handlers(self):
        original = signal.signal
        first = True
        def install(sig, handler):
            nonlocal first
            previous = original(sig, handler)
            if first and sig == signal.SIGTERM and callable(handler):
                first = False
                handler(sig, None)
            return previous
        with patch('bcs_e2e.local_run.signal.signal', side_effect=install):
            result = local_run(**self.args)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['failure']['phase'], 'signal-handlers')
        self.assertFalse(self.args['state_dir'].exists())
        for sig, handler in self.previous.items(): self.assertEqual(signal.getsignal(sig), handler)

    def test_competing_directory_claim_prevents_all_receipt_writes(self):
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path == self.args['state_dir'].resolve():
                original(path, *args, **kwargs)
                (path / 'other-owner').write_text('retained')
                raise FileExistsError('concurrent claim')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            result = local_run(**self.args)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(list(self.args['state_dir'].iterdir()), [self.args['state_dir'] / 'other-owner'])

    def test_first_signal_during_receipt_publication_is_saved_as_cancelled(self):
        from bcs_e2e.run_state import write as actual_write
        writes = []
        def write(path, data):
            if path.name == 'local-run.json':
                writes.append(data['status'])
                if len(writes) == 1: os.kill(os.getpid(), signal.SIGTERM)
            actual_write(path, data)
        def passed(profile, state, run_id, case, **kwargs):
            return {'case_id': case, 'status': 'PASS', 'functional': 'PASS', 'cleanup': 'PASS'}
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply'), \
             patch('bcs_e2e.local_run.resolve_owned_profile', return_value={'schema_version': 1}), \
             patch('bcs_e2e.local_run.prepare_run'), \
             patch('bcs_e2e.local_run.run_case', side_effect=passed), \
             patch('bcs_e2e.local_run.build_report', return_value={'status': 'PILOT_PASS'}), \
             patch('bcs_e2e.local_run.write_reports', return_value={'outputs': {}}), \
             patch('bcs_e2e.local_run.write', side_effect=write):
            result = local_run(**self.args)
        self.assertEqual(writes, ['LOCAL_PILOT_PASS', 'CANCELLED'])
        self.assertEqual(result['status'], 'CANCELLED')
        self.assert_stopped(result)

    def test_signal_during_outcome_construction_cannot_publish_success_with_cancellation(self):
        from bcs_e2e.pilot import evaluate as actual_evaluate
        def evaluate(summary):
            os.kill(os.getpid(), signal.SIGTERM)
            return actual_evaluate(summary)
        def passed(profile, state, run_id, case, **kwargs):
            return {'case_id': case, 'status': 'PASS', 'functional': 'PASS', 'cleanup': 'PASS'}
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply'),              patch('bcs_e2e.local_run.resolve_owned_profile', return_value={'schema_version': 1}),              patch('bcs_e2e.local_run.prepare_run'),              patch('bcs_e2e.local_run.run_case', side_effect=passed),              patch('bcs_e2e.local_run.build_report', return_value={'status': 'PILOT_PASS'}),              patch('bcs_e2e.local_run.write_reports', return_value={'outputs': {}}),              patch('bcs_e2e.local_run.evaluate', side_effect=evaluate):
            result = local_run(**self.args)
        self.assertEqual(result['primary_status'], 'PASS')
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['cancellation'], {'requested': True, 'signal': 'SIGTERM'})
        self.assert_stopped(result)

    def test_stubbed_api_passes_cannot_satisfy_selected_but_unexecuted_cli_check(self):
        def passed(profile, state, run_id, case, **kwargs):
            return {'case_id': case, 'status': 'PASS', 'functional': 'PASS', 'cleanup': 'PASS'}
        with patch('bcs_e2e.local_run.OwnedLocalFixtures.apply'), \
             patch('bcs_e2e.local_run.resolve_owned_profile', return_value={'schema_version': 1}), \
             patch('bcs_e2e.local_run.prepare_run'), \
             patch('bcs_e2e.local_run.run_case', side_effect=passed) as case, \
             patch('bcs_e2e.local_run.build_report', return_value={'status': 'PILOT_PASS'}), \
             patch('bcs_e2e.local_run.write_reports', return_value={'outputs': {}}):
            result = local_run(**self.args, cli_binary=self.binary, cli_binary_sha256=file_sha256(self.binary))
        self.assertEqual(case.call_count, 2)
        self.assertEqual(result['product_cli']['status'], 'NOT_RUN')
        self.assertEqual(result['product_cli']['attempts'], 0)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assert_stopped(result)
