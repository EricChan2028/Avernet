"""Offline guards for the owned-local Result queue black-box slice."""
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_result_queue_contract import (MARKER_KEYS, PROVIDER_JUDGE_MOCK, REQUIREMENTS,
                                                STORY_SOURCE, result_queue_source_binding)
from bcs_e2e.local_result_queue_reporting import build_result_queue_report
from bcs_e2e.local_result_queue_run import (BINARY_KIND, HUMAN_ID, MOCK_ERR_LOG_NAME, MOCK_LOG_NAME,
                                           MOCK_READY_FILE_NAME, STORY_ERR_LOG_NAME, STORY_LOG_NAME,
                                           _parse_story_marker, _redaction_gate, local_result_queue_run,
                                           validate_result_queue_binary)
from bcs_e2e.run_state import RunStateError, read

GOOD_STDOUT = (b'booting owned runtime\n'
               b'Queued result: owner Send, observer Inject, bound context and unique history verified\n')
SCHEDULED_COUNTS = {'observed_webhook_requests': 3, 'observed_event_webhook_requests': 0,
                   'observed_friend_auth_sync_requests': 0, 'judge_started': False, 'judge_released': False}
MOCK_ORIGIN = 'http://127.0.0.1:41234'
RUNTIME_ORIGIN = 'http://127.0.0.1:39999'


def story_seam(stdout=GOOD_STDOUT, stderr=b'', exit_code=0, timed_out=False, error=None):
    if error is not None:
        def launch(*_args, **_kwargs):
            raise error
        return launch

    def launch(*_args, **_kwargs):
        return {'exit_code': exit_code, 'timed_out': timed_out, 'duration_seconds': 0.42,
                'stdout': stdout, 'stderr': stderr, 'process': None}
    return launch


def executable_binary(directory: Path) -> tuple[Path, str]:
    binary = directory / 'bcs'
    binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    binary.chmod(0o755)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def run_kwargs(state_dir: Path, binary: Path, digest: str, **changes) -> dict:
    base = dict(binary=binary, binary_sha256=digest, state_dir=state_dir, run_id='draft-group-test',
                confirm_run_id='draft-group-test', allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
    base.update(changes)
    return base


class FakeOwnedRuntime:
    instances: list = []
    enter_error: Exception | None = None
    final_status = 'STOPPED'
    final_returncode = 0

    def __init__(self, *, binary, binary_sha256, root, run_id, allow_ambient_local_identity=True):
        self.init_kwargs = {'binary': Path(binary), 'binary_sha256': binary_sha256, 'root': Path(root),
                            'run_id': run_id, 'allow_ambient_local_identity': allow_ambient_local_identity}
        FakeOwnedRuntime.instances.append(self)
        self.root = Path(root).resolve()
        self.inputs = SimpleNamespace(origin=RUNTIME_ORIGIN)
        if self.enter_error is not None:
            raise self.enter_error

    def _persist(self, status: str) -> None:
        ready = status == 'STOPPED'
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.init_kwargs['run_id'], 'status': status,
             'origin': RUNTIME_ORIGIN, 'pid': None, 'returncode': self.final_returncode,
             'binary_sha256': self.init_kwargs['binary_sha256'], 'ready': ready,
             'forced_stop': False}, sort_keys=True, indent=2), encoding='utf-8')

    def __enter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self._persist('STARTING')
        return self

    def __exit__(self, *_args):
        self._persist(self.final_status)
        return False


class FakeMockProcess:
    def __init__(self, returncode=None, write_ready=True):
        self.pid = 999999
        self.returncode = returncode if returncode is not None else 0
        self._alive = returncode is None
        self.terminated = False
        self._write_ready = write_ready

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False

    kill = terminate

    def communicate(self, timeout=None):
        self._alive = False
        return (b'mock control plane idle\n', b'')



class ResultQueueRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []

    def _mock_launcher(self, argv, cwd):
        if '--ready-file' in argv:
            ready = argv[argv.index('--ready-file') + 1]
            if argv[argv.index('--port') + 1] == '0':
                Path(ready).parent.mkdir(parents=True, exist_ok=True)
                Path(ready).write_text(MOCK_ORIGIN + '\n', encoding='utf-8')
        process = FakeMockProcess()
        process.launched_argv = [str(item) for item in argv]
        process.launched_cwd = str(cwd)
        return process

    def _success_run(self, **overrides):
        story = story_seam(**overrides.pop('story', {}))
        counts = overrides.pop('counts', SCHEDULED_COUNTS)
        launcher = self._mock_launcher
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story', story), \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=counts):
            state = self.root / 'state'
            return state, local_result_queue_run(**run_kwargs(state, self.binary, self.digest))

    def test_success_run_publishes_the_black_box_child_receipt_chain(self):
        state, result = self._success_run()
        self.assertEqual(result['status'], 'LOCAL_RESULT_QUEUE_PASS')
        self.assertEqual(result['suite'], 'owned-local-result-queue-v1')
        self.assertEqual(result['human_id'], HUMAN_ID)
        self.assertEqual(result['ready_to_execute'], False)
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['scheduled_traffic'], True)
        names = {'result-queue-binding.json', 'result-queue-flow-report.json', 'result-queue-mock-child.json',
                 'result-queue-child.json', 'owned-bcs.json', 'result-queue.json',
                 STORY_LOG_NAME, STORY_ERR_LOG_NAME, MOCK_LOG_NAME, MOCK_ERR_LOG_NAME,
                 'result-queue-completion.json', 'reports/summary.json', 'reports/junit.xml',
                 'reports/summary.md'}
        self.assertTrue(all((state / name).is_file() for name in names),
                        sorted(p.name for p in state.rglob('*')))
        self.assertFalse((state / 'result-queue-publication-pending.json').exists())
        binding = read(state / 'result-queue-binding.json')
        self.assertEqual(binding['contract'], 'owned-local-result-queue-v1')
        self.assertEqual(binding['case_id'], 'LOCAL-RESULT-QUEUE-01')
        self.assertEqual(binding['human_id'], '39464')
        self.assertEqual(binding['binary_kind'], BINARY_KIND)
        self.assertEqual(binding['auto_build'], False)
        self.assertEqual(binding['bcs_binary_sha256'], self.digest)
        self.assertEqual(binding['selected_requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['mock_child']['expected_webhook_requests'], 3)
        self.assertEqual(binding['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                  'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(binding['source_binding']['parent'], 'story_state_machine_result_queue')
        self.assertEqual(binding['runtime_dir'], str(state.resolve()))
        self.assertEqual(len(binding['legacy_inventory']), 41)
        runtime = FakeOwnedRuntime.instances[0].init_kwargs
        self.assertEqual(runtime['allow_ambient_local_identity'], False)
        self.assertEqual(runtime['binary_sha256'], self.digest)
        self.assertEqual(runtime['root'], state)
        launcher = FakeOwnedRuntime.instances and None
        mock_child = read(state / 'result-queue-mock-child.json')
        self.assertEqual(mock_child['argv'], [sys.executable, str(PROVIDER_JUDGE_MOCK),
                                              '--host', '127.0.0.1', '--port', '0',
                                              '--ready-file', str(state.resolve() / MOCK_READY_FILE_NAME)])
        self.assertEqual(mock_child['origin'], MOCK_ORIGIN)
        self.assertEqual(mock_child['ready'], True)
        self.assertEqual(mock_child['exit_code'], 0)
        self.assertEqual(mock_child['observed_webhook_requests'], 3)
        self.assertEqual(mock_child['observed_event_webhook_requests'], 0)
        self.assertEqual(mock_child['observed_friend_auth_sync_requests'], 0)
        self.assertEqual(mock_child['judge_started'], False)
        self.assertEqual(mock_child['redaction_gate'], True)
        child = read(state / 'result-queue-child.json')
        self.assertEqual(child['argv'][1], str(STORY_SOURCE))
        self.assertEqual(child['argv'][child['argv'].index('--base-url') + 1], RUNTIME_ORIGIN)
        self.assertEqual(child['argv'][child['argv'].index('--human') + 1], HUMAN_ID)
        self.assertEqual(child['argv'][child['argv'].index('--mock-url') + 1], MOCK_ORIGIN)
        self.assertNotIn('Bearer', json.dumps(child))
        self.assertEqual(child['markers'], dict.fromkeys(MARKER_KEYS, True))
        self.assertEqual(child['observed_checks'], 1)
        self.assertEqual(child['redaction_gate'], True)
        self.assertEqual(child['exit_code'], 0)
        self.assertEqual((state / STORY_LOG_NAME).read_bytes(), GOOD_STDOUT)
        self.assertEqual((state / STORY_ERR_LOG_NAME).read_bytes(), b'')
        case = read(state / 'result-queue.json')
        self.assertEqual(case['status'], 'LOCAL_RESULT_QUEUE_PASS')
        self.assertEqual([row['id'] for row in case['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in case['requirements']))
        self.assertEqual(case['story_markers'], {'observed': [True], 'checks': 1})
        self.assertEqual(case['scheduled_traffic']['observed_webhook_requests'], 3)
        self.assertEqual(case['scheduled_traffic']['judge_started'], False)
        self.assertEqual(case['human_id'], HUMAN_ID)
        self.assertEqual(case['resource_disposition'], 'STORY_SCOPED_TEMPORARY_DIRECTORY')
        self.assertEqual(case['runtime_shutdown'], {'status': 'STOPPED', 'returncode': 0})
        flow = read(state / 'result-queue-flow-report.json')
        self.assertEqual(flow['case'], case)
        self.assertEqual(flow['child_receipt'], child)
        self.assertEqual(flow['mock_child_receipt'], mock_child)
        self.assertEqual(read(state / 'owned-bcs.json')['status'], 'STOPPED')
        summary = build_result_queue_report(state)
        self.assertEqual(summary, result['canonical_summary'])
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['legacy_inventory_count'], 41)
        self.assertEqual(summary['scope']['independent_slice'], True)
        self.assertEqual(summary['reports']['expected_assertion_count'], 18)
        self.assertEqual(summary['reports']['observed_assertion_count'], 18)
        self.assertTrue(all(row['status'] == 'PASS' for row in summary['requirements']))
        self.assertIn('friend-auth-sync', ' '.join(summary['mock_boundary']['not_proven']))

    def test_nonzero_exit_missing_marker_zero_checks_and_timeout_block(self):
        variants = {
            'nonzero-exit': dict(story=dict(exit_code=130)),
            'missing-marker': dict(story=dict(stdout=b'booting owned runtime\n')),
            'zero-checks': dict(story=dict(stdout=b'Result queue launch story: 0 live HTTP assertions passed '
                                                b'(including cleanup)\n')),
            'timed-out': dict(story=dict(exit_code=-9, timed_out=True)),
        }
        for name, story_overrides in variants.items():
            with self.subTest(case=name):
                state = self.root / ('state-' + name)
                with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                        patch('bcs_e2e.local_result_queue_run._start_mock_child',
                              side_effect=self._mock_launcher), \
                        patch('bcs_e2e.local_result_queue_run._launch_story', story_seam(**story_overrides['story'])), \
                        patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=SCHEDULED_COUNTS):
                    result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertFalse((state / 'result-queue.json').exists())
                self.assertTrue((state / 'result-queue-child.json').is_file())
                self.assertTrue((state / STORY_LOG_NAME).is_file())
                self.assertTrue((state / MOCK_LOG_NAME).is_file())
                summary = build_result_queue_report(state)
                self.assertEqual(summary['status'], 'BLOCKED')
                self.assertTrue(summary['integrity']['issues'] == [] or summary['integrity']['issues'])

    def test_mock_readiness_timeout_blocks_and_terminates_the_mock_child(self):
        state = self.root / 'state-ready-timeout'
        process = FakeMockProcess(write_ready=False)

        def crash_launcher(argv, cwd):
            return process

        killed = []
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=crash_launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story') as story, \
                patch('bcs_e2e.local_result_queue_run.os.killpg', side_effect=lambda *a: killed.append(a)), \
                patch('bcs_e2e.local_result_queue_run.MOCK_READY_SECONDS', 0.3):
            result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')
        story.assert_not_called()
        self.assertIn((process.pid, signal.SIGTERM), killed)
        mock_child = read(state / 'result-queue-mock-child.json')
        self.assertEqual(mock_child['ready'], False)
        self.assertIsNone(mock_child['origin'])
        self.assertFalse((state / 'result-queue.json').exists())
        self.assertEqual(build_result_queue_report(state)['status'], 'BLOCKED')

    def test_mock_crash_before_readiness_blocks(self):
        state = self.root / 'state-crash'

        def crashed_launcher(argv, cwd):
            return FakeMockProcess(returncode=1, write_ready=False)

        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=crashed_launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story') as story:
            result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        story.assert_not_called()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')
        mock_child = read(state / 'result-queue-mock-child.json')
        self.assertEqual(mock_child['exit_code'], 1)
        self.assertEqual(mock_child['ready'], False)

    def test_webhook_or_judge_traffic_gates_the_pass(self):
        contaminated = {
            'provider-underflow': {'observed_webhook_requests': 2},
            'provider-overflow': {'observed_webhook_requests': 4},
            'event-webhook': {'observed_event_webhook_requests': 1},
            'judge-invoked': {'judge_started': True},
        }
        for name, counts in contaminated.items():
            with self.subTest(case=name):
                state = self.root / ('state-traffic-' + name)
                patched = dict(SCHEDULED_COUNTS)
                patched.update(counts)
                with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                        patch('bcs_e2e.local_result_queue_run._start_mock_child',
                              side_effect=self._mock_launcher), \
                        patch('bcs_e2e.local_result_queue_run._launch_story', story_seam()), \
                        patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=patched):
                    result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertFalse((state / 'result-queue.json').exists())
                self.assertEqual(read(state / 'result-queue-mock-child.json')['observed_webhook_requests'],
                                 patched['observed_webhook_requests'])

    def test_unreachable_mock_control_plane_blocks_without_pass_claims(self):
        state = self.root / 'state-control'
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=self._mock_launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts',
                      side_effect=RunStateError('unreachable')):
            result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')
        mock_child = read(state / 'result-queue-mock-child.json')
        self.assertIsNone(mock_child['observed_webhook_requests'])
        self.assertFalse((state / 'result-queue.json').exists())
        self.assertEqual(build_result_queue_report(state)['status'], 'BLOCKED')

    def test_credential_bearing_streams_block_and_never_land_in_evidence(self):
        state = self.root / 'state-redaction'
        contaminated = b'Queued result: owner Send, observer Inject, bound context and unique history verified\n' \
                       b'provider_admin_token leaked\n'
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=self._mock_launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story',
                      story_seam(stderr=contaminated)), \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=SCHEDULED_COUNTS):
            result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        for name in (STORY_LOG_NAME, STORY_ERR_LOG_NAME, MOCK_LOG_NAME, MOCK_ERR_LOG_NAME):
            self.assertFalse((state / name).exists(), name)
        child = read(state / 'result-queue-child.json')
        self.assertIs(child['redaction_gate'], False)
        self.assertEqual(build_result_queue_report(state)['status'], 'BLOCKED')

    def test_admission_freezes_permissions_state_and_binary_before_any_child(self):
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess') as runtime_ctor, \
                patch('bcs_e2e.local_result_queue_run._start_mock_child') as launcher, \
                patch('bcs_e2e.local_result_queue_run._launch_story') as story, \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts') as probe:
            with tempfile.TemporaryDirectory() as other:
                for changes in ({'allow_local_processes': False}, {'allow_fixture_write': False},
                                {'allow_write': False}, {'confirm_run_id': 'foreign'}):
                    with self.subTest(changes=changes):
                        with self.assertRaises(RunStateError):
                            local_result_queue_run(**run_kwargs(self.root / 's', self.binary,
                                                               self.digest, **changes))
                bad = self.root / 'bad-digest'
                with self.assertRaises(RunStateError):
                    local_result_queue_run(**run_kwargs(bad, self.binary, '0' * 64))
                self.assertFalse(bad.exists())
                existing = self.root / 'existing'
                existing.mkdir(mode=0o700)
                with self.assertRaises(RunStateError):
                    local_result_queue_run(**run_kwargs(existing, self.binary, self.digest))
                symlink = self.root / 'linked-state'
                target = self.root / 'target'
                target.mkdir(mode=0o700)
                symlink.symlink_to(target)
                with self.assertRaises(RunStateError):
                    local_result_queue_run(**run_kwargs(symlink, self.binary, self.digest))
            for seam in (runtime_ctor, launcher, story, probe):
                self.assertEqual(seam.call_count, 0)

    def test_runtime_startup_failure_surfaces_without_evidence_directory(self):
        state = self.root / 'state-runtime'
        FakeOwnedRuntime.enter_error = RuntimeError('owned startup failed')
        try:
            with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                    patch('bcs_e2e.local_result_queue_run._start_mock_child'), \
                    patch('bcs_e2e.local_result_queue_run._launch_story'):
                with self.assertRaises(RuntimeError):
                    local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
            self.assertFalse(state.exists())
        finally:
            FakeOwnedRuntime.enter_error = None

    def test_runtime_shutdown_disposition_gates_the_pass(self):
        state = self.root / 'state-shutdown'
        FakeOwnedRuntime.final_status = 'FAILED_STOP'
        try:
            with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                    patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=self._mock_launcher), \
                    patch('bcs_e2e.local_result_queue_run._launch_story', story_seam()), \
                    patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=SCHEDULED_COUNTS):
                result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        finally:
            FakeOwnedRuntime.final_status = 'STOPPED'
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse((state / 'result-queue.json').exists())
        self.assertEqual(read(state / 'result-queue-flow-report.json')['status'], 'BLOCKED')

    def test_executor_drift_fence_refuses_publication(self):
        state = self.root / 'state-drift'
        seen = {'count': 0}

        def drifting():
            seen['count'] += 1
            return 'first-draft-digest' if seen['count'] == 1 else 'second-draft-digest'

        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=self._mock_launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=SCHEDULED_COUNTS), \
                patch('bcs_e2e.local_result_queue_run.executor_digest', side_effect=drifting), \
                patch('bcs_e2e.local_result_queue_run.file_sha256', return_value=self.digest):
            with self.assertRaises(RunStateError):
                local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        binding = read(state / 'result-queue-binding.json')
        self.assertEqual(binding['executor_digest'], 'first-draft-digest')
        self.assertTrue((state / 'result-queue-flow-report.json').is_file())
        self.assertFalse((state / 'result-queue.json').exists())

    def test_cancellation_canonicalises_without_pass_claims(self):
        state = self.root / 'state-cancel'
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child', side_effect=self._mock_launcher), \
                patch('bcs_e2e.local_result_queue_run._launch_story',
                      story_seam(error=KeyboardInterrupt())), \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=SCHEDULED_COUNTS):
            result = local_result_queue_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        self.assertFalse((state / 'result-queue-child.json').exists())
        summary = build_result_queue_report(state)
        self.assertEqual(summary['status'], 'CANCELLED')


class ResultQueueMarkerTests(unittest.TestCase):
    def test_strict_single_fixed_marker(self):
        self.assertEqual(_parse_story_marker(GOOD_STDOUT.decode()), (True, 1))
        self.assertEqual(_parse_story_marker('Queued result: owner Send only\n'), (False, 0))
        self.assertEqual(_parse_story_marker(
            GOOD_STDOUT.decode() + GOOD_STDOUT.decode()), (True, 1))
        self.assertEqual(_parse_story_marker('Marker\n'), (False, 0))

    def test_redaction_gate_covers_all_credential_likelihoods(self):
        self.assertTrue(_redaction_gate(b'clean', b'', b'', b''))
        for token in (b'bot_token', b'provider_admin_token', b'Bearer '):
            self.assertFalse(_redaction_gate(token, b'', b'', b''))
            self.assertFalse(_redaction_gate(b'', token, b'', b''))
            self.assertFalse(_redaction_gate(b'', b'', token, b''))
            self.assertFalse(_redaction_gate(b'', b'', b'', token))


class DraftBinaryValidationTests(unittest.TestCase):
    def test_frozen_regular_executable_with_exact_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary, digest = executable_binary(Path(temporary))
            self.assertEqual(validate_result_queue_binary(binary, digest), binary)
            with self.assertRaises(RunStateError):
                validate_result_queue_binary(binary, '0' * 64)

    def test_symlink_nonregular_or_nonexecutable_binaries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / 'real'
            real.write_text('#!/bin/sh\n', encoding='utf-8')
            real.chmod(0o755)
            link = base / 'link'
            link.symlink_to(real)
            with self.assertRaises(RunStateError):
                validate_result_queue_binary(link, hashlib.sha256(real.read_bytes()).hexdigest())
            plain = base / 'plain'
            plain.write_text('not executable', encoding='utf-8')
            with self.assertRaises(RunStateError):
                validate_result_queue_binary(plain, hashlib.sha256(plain.read_bytes()).hexdigest())


class ResultQueueEvidenceTamperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []
        story = story_seam()
        with patch('bcs_e2e.local_result_queue_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_result_queue_run._start_mock_child',
                      side_effect=ResultQueueEvidenceTamperTests._mock_launcher_straight), \
                patch('bcs_e2e.local_result_queue_run._launch_story', story), \
                patch('bcs_e2e.local_result_queue_run._mock_control_counts', return_value=SCHEDULED_COUNTS):
            self.state = self.root / 'state'
            self.result = local_result_queue_run(
                **run_kwargs(self.state, self.binary, self.digest))
        self.assertEqual(self.result['status'], 'LOCAL_RESULT_QUEUE_PASS')

    @staticmethod
    def _mock_launcher_straight(argv, cwd):
        if '--ready-file' in argv:
            ready = argv[argv.index('--ready-file') + 1]
            Path(ready).write_text(MOCK_ORIGIN + '\n', encoding='utf-8')
        return FakeMockProcess()

    def test_changed_case_cannot_claim_success(self):
        case = read(self.state / 'result-queue.json')
        tampered = json.loads(json.dumps(case))
        tampered['requirements'].pop()
        (self.state / 'result-queue.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_result_queue_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_traffic_evidence_tampering_blocks_the_projection(self):
        mock_child = read(self.state / 'result-queue-mock-child.json')
        tampered = json.loads(json.dumps(mock_child))
        tampered['observed_webhook_requests'] = 3
        (self.state / 'result-queue-mock-child.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_result_queue_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_runtime_disposition_tampering_blocks_the_projection(self):
        owned = read(self.state / 'owned-bcs.json')
        tampered = json.loads(json.dumps(owned))
        tampered['status'] = 'FAILED_STOP'
        (self.state / 'owned-bcs.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_result_queue_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_missing_child_receipt_is_blocked_not_invented(self):
        (self.state / 'result-queue-child.json').unlink()
        summary = build_result_queue_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_publication_gap_and_symlinked_root_are_blocked(self):
        pending = self.state / 'result-queue-publication-pending.json'
        pending.write_text('{}', encoding='utf-8')
        summary = build_result_queue_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        pending.unlink()
        linked = self.root / 'linked-state'
        linked.symlink_to(self.state)
        with self.assertRaises(RunStateError):
            build_result_queue_report(linked)


class ResultQueueIndependenceTests(unittest.TestCase):
    def test_slice_stays_bound_to_the_full_legacy_backlog_parent(self):
        from bcs_e2e.inventory import current_manifest
        legacy = current_manifest()['legacy_inventory']
        self.assertEqual(len(legacy), 41)
        self.assertIn('story_state_machine_result_queue', {row['id'] for row in legacy})
        binding = result_queue_source_binding()
        self.assertEqual(binding['parent'], 'story_state_machine_result_queue')
        self.assertEqual(binding['evidence_granularity'], 'OWNED_CHILD_RECEIPT')
        self.assertEqual(binding['mock_child']['ready_protocol'], 'ready-file')
        self.assertEqual(binding['remote_validation'], 'NOT_RUN')
        self.assertEqual(binding['grants_release_credit'], False)
        self.assertEqual(binding['release_parent_denominator'], False)
        self.assertEqual(binding['owned_runtime_binary']['auto_build'], False)


if __name__ == '__main__':
    unittest.main()
