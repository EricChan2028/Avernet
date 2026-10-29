"""Offline guards for the promoted retired service policy run lifecycle."""
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
from bcs_e2e.local_service_policy_contract import (MARKER_KEYS, REQUIREMENTS, STORY_SOURCE,
                                                    service_policy_source_binding)
from bcs_e2e.local_service_policy_reporting import build_service_policy_report
from bcs_e2e.local_service_policy_run import (BINARY_KIND, STORY_CHECKS, STORY_ERR_LOG_NAME,
                                              STORY_LOG_NAME, _parse_story_marker, _redaction_gate,
                                              local_service_policy_run, validate_service_policy_binary)
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.inventory import REVIEWED_RELEASE_PARENT_COUNT, current_manifest
from bcs_e2e.profile import fingerprint
from bcs_e2e.local_story_commands import CASES, list_cases
from bcs_e2e.migration_inventory import migration_plan

GOOD_STDOUT = (b'booting owned runtime\n'
               b'Retired service policy story: 23 HTTP/response checks passed\n')
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
    base = dict(binary=binary, binary_sha256=digest, state_dir=state_dir, run_id='service-policy-test',
                confirm_run_id='service-policy-test', allow_local_processes=True,
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


class ServicePolicyRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []

    def _success_run(self, **overrides):
        story = story_seam(**overrides.pop('story', {}))
        state = overrides.pop('state_dir', None) or (self.root / 'state')
        with patch('bcs_e2e.local_service_policy_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_service_policy_run._launch_story', story), \
                patch('bcs_e2e.local_service_policy_run.file_sha256', return_value=self.digest):
            return state, local_service_policy_run(**run_kwargs(state, self.binary, self.digest, **overrides))

    def test_success_run_publishes_the_black_box_child_receipt_chain(self):
        state, result = self._success_run()
        self.assertEqual(result['status'], 'LOCAL_SERVICE_POLICY_PASS')
        self.assertEqual(result['suite'], 'owned-local-service-policy-v1')
        self.assertEqual(result['case_id'], 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(result['ready_to_execute'], False)
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertNotIn('human_id', result)
        self.assertNotIn('mock_child', result)
        names = {'service-policy-binding.json', 'service-policy-flow-report.json', 'service-policy-child.json',
                 'service-policy.json', 'owned-bcs.json', 'service-policy-completion.json',
                 STORY_LOG_NAME, STORY_ERR_LOG_NAME, 'reports/summary.json',
                 'reports/junit.xml', 'reports/summary.md'}
        self.assertTrue(all((state / name).is_file() for name in names),
                        sorted(p.name for p in state.rglob('*')))
        binding = read(state / 'service-policy-binding.json')
        self.assertEqual(binding['contract'], 'owned-local-service-policy-v1')
        self.assertEqual(binding['case_id'], 'LOCAL-SERVICE-POLICY-01')
        self.assertNotIn('human_id', binding)
        self.assertEqual(binding['binary_kind'], BINARY_KIND)
        self.assertEqual(binding['auto_build'], False)
        self.assertEqual(binding['bcs_binary_sha256'], self.digest)
        self.assertEqual(binding['selected_requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                 'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(binding['source_binding']['parent'], 'story_retired_service_policy')
        self.assertEqual(binding['source_binding']['release_parent_denominator'], True)
        self.assertEqual(binding['legacy_inventory'], current_manifest()['legacy_inventory'])
        runtime = FakeOwnedRuntime.instances[0].init_kwargs
        self.assertEqual(runtime['allow_ambient_local_identity'], True)
        self.assertEqual(runtime['binary_sha256'], self.digest)
        self.assertEqual(runtime['root'], state)
        child = read(state / 'service-policy-child.json')
        self.assertEqual(child['argv'][1], str(STORY_SOURCE))
        self.assertEqual(child['argv'][child['argv'].index('--base-url') + 1], RUNTIME_ORIGIN)
        self.assertNotIn('Bearer', json.dumps(child))
        self.assertEqual(child['markers'], dict.fromkeys(MARKER_KEYS, True))
        self.assertEqual(child['observed_checks'], 23)
        self.assertEqual(child['redaction_gate'], True)
        self.assertEqual(child['exit_code'], 0)
        self.assertEqual((state / STORY_LOG_NAME).read_bytes(), GOOD_STDOUT)
        self.assertEqual((state / STORY_ERR_LOG_NAME).read_bytes(), b'')
        case = read(state / 'service-policy.json')
        self.assertEqual(case['status'], 'LOCAL_SERVICE_POLICY_PASS')
        self.assertEqual([row['id'] for row in case['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in case['requirements']))
        self.assertEqual(case['story_markers'], {'observed': [True], 'http_checks': 23})
        self.assertEqual(case['runtime_shutdown'], {'status': 'STOPPED', 'returncode': 0})
        self.assertEqual(case['resource_disposition'], 'STORY_SCOPED_TEMPORARY_DIRECTORY')
        flow = read(state / 'service-policy-flow-report.json')
        self.assertEqual(flow['case'], case)
        self.assertEqual(flow['child_receipt'], child)
        self.assertEqual(read(state / 'owned-bcs.json')['status'], 'STOPPED')
        summary = build_service_policy_report(state)
        self.assertEqual(summary, result['canonical_summary'])
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['independent_slice'], False)
        self.assertEqual(summary['scope']['release_parent_denominator'], True)
        self.assertEqual(summary['scope']['legacy_inventory_count'],
                         len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['reports']['expected_assertion_count'], 8)
        self.assertEqual(summary['reports']['observed_assertion_count'], 8)
        self.assertTrue(all(row['status'] == 'PASS' for row in summary['requirements']))
        self.assertIn('verification_boundary', summary)
        completion = read(state / 'service-policy-completion.json')
        self.assertEqual(completion['contract'], 'owned-local-service-policy-completion-v1')
        self.assertEqual(completion['binding_digest'], fingerprint(binding))

    def test_wrong_checks_count_and_failure_variants_block(self):
        variants = {
            'nonzero-exit': {'exit_code': 130},
            'missing-marker': {'stdout': b'booting owned runtime\n'},
            'wrong-checks': {'stdout': b'Retired service policy story: 22 HTTP/response checks passed\n'},
            'zero-checks': {'stdout': b'Retired service policy story: 0 HTTP/response checks passed\n'},
            'timed-out': {'exit_code': -9, 'timed_out': True},
        }
        for name, story_overrides in variants.items():
            with self.subTest(variant=name):
                state, result = self._success_run(story=story_overrides,
                                                  state_dir=self.root / f'state-{name}')
                self.assertEqual(result['status'], 'BLOCKED', name)
                self.assertFalse((state / 'service-policy.json').exists(), name)
                summary = build_service_policy_report(state)
                self.assertEqual(summary['status'], 'BLOCKED', name)

    def test_credential_bearing_streams_block_and_never_land_in_evidence(self):
        state, result = self._success_run(story=dict(stdout=b'booting owned runtime\nbot_token=abc\n'
                                                           b'Retired service policy story: 23 HTTP/response checks passed\n'))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse((state / STORY_LOG_NAME).exists())
        self.assertFalse((state / STORY_ERR_LOG_NAME).exists())

    def test_permissions_fresh_state_and_exact_confirmation_are_enforced(self):
        state = self.root / 'state'
        with patch('bcs_e2e.local_service_policy_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_service_policy_run._launch_story', story_seam()):
            with self.assertRaises(RunStateError):
                local_service_policy_run(**run_kwargs(state, self.binary, self.digest, allow_local_processes=False))
            with self.assertRaises(RunStateError):
                local_service_policy_run(**run_kwargs(state, self.binary, self.digest, allow_fixture_write=False))
            with self.assertRaises(RunStateError):
                local_service_policy_run(**run_kwargs(state, self.binary, self.digest, allow_write=False))
            with self.assertRaises(RunStateError):
                local_service_policy_run(**run_kwargs(state, self.binary, self.digest, confirm_run_id='other'))
            local_service_policy_run(**run_kwargs(state, self.binary, self.digest))
            # The fresh state-directory fence refuses replay into existing evidence.
            with self.assertRaises(RunStateError):
                local_service_policy_run(**run_kwargs(state, self.binary, self.digest))

    def test_runtime_startup_failure_surfaces_without_evidence_directory(self):
        state = self.root / 'state-broken'
        FakeOwnedRuntime.enter_error = OSError('runtime bootstrap failed')
        try:
            with patch('bcs_e2e.local_service_policy_run.OwnedBcsProcess', FakeOwnedRuntime), \
                    patch('bcs_e2e.local_service_policy_run._launch_story', story_seam()):
                with self.assertRaises(OSError):
                    local_service_policy_run(**run_kwargs(state, self.binary, self.digest))
            self.assertFalse(state.exists())
        finally:
            FakeOwnedRuntime.enter_error = None

    def test_runtime_shutdown_disposition_gates_the_pass(self):
        FakeOwnedRuntime.final_status = 'FAILED_STOP'
        FakeOwnedRuntime.final_returncode = 1
        try:
            state, result = self._success_run()
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(read(state / 'owned-bcs.json')['status'], 'FAILED_STOP')
        finally:
            FakeOwnedRuntime.final_status = 'STOPPED'
            FakeOwnedRuntime.final_returncode = 0

    def test_executor_drift_fence_refuses_the_pass(self):
        state = self.root / 'state-drift'
        seen = {'count': 0}

        def drifting():
            seen['count'] += 1
            # Digests drift only while the child ran (call two); publication and
            # the frozen binding stay aligned to the first digest afterwards.
            return 'second-policy-digest' if seen['count'] == 2 else 'first-policy-digest'

        with patch('bcs_e2e.local_service_policy_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_service_policy_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_service_policy_run.executor_digest', side_effect=drifting), \
                patch('bcs_e2e.local_service_policy_reporting.executor_digest', side_effect=drifting), \
                patch('bcs_e2e.local_service_policy_run.file_sha256', return_value=self.digest):
            result = local_service_policy_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        binding = read(state / 'service-policy-binding.json')
        self.assertEqual(binding['executor_digest'], 'first-policy-digest')
        self.assertTrue((state / 'service-policy-flow-report.json').is_file())
        self.assertFalse((state / 'service-policy.json').exists())

    def test_cancellation_canonicalises_without_pass_claims(self):
        state = self.root / 'state-cancel'
        with patch('bcs_e2e.local_service_policy_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_service_policy_run._launch_story',
                      story_seam(error=KeyboardInterrupt())):
            result = local_service_policy_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        self.assertFalse((state / 'service-policy-child.json').exists())
        self.assertFalse((state / 'service-policy.json').exists())
        summary = build_service_policy_report(state)
        self.assertEqual(summary['status'], 'CANCELLED')


class ServicePolicyMarkerTests(unittest.TestCase):
    def test_strict_single_marker_with_checks_count(self):
        self.assertEqual(_parse_story_marker(GOOD_STDOUT.decode()), (True, 23))
        self.assertEqual(_parse_story_marker(''), (False, 0))
        self.assertEqual(_parse_story_marker('Retired service policy story: not-a-number\n'), (False, 0))

    def test_redaction_gate_covers_all_credential_likelihoods(self):
        self.assertTrue(_redaction_gate(b'clean', b''))
        for token in (b'bot_token', b'Bearer '):
            self.assertFalse(_redaction_gate(token, b''))
            self.assertFalse(_redaction_gate(b'', token))


class ServicePolicyBinaryValidationTests(unittest.TestCase):
    def test_frozen_regular_executable_with_exact_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary, digest = executable_binary(Path(temporary))
            self.assertEqual(validate_service_policy_binary(binary, digest), binary)
            with self.assertRaises(RunStateError):
                validate_service_policy_binary(binary, '0' * 64)

    def test_symlink_nonregular_or_nonexecutable_binaries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / 'real'
            real.write_text('#!/bin/sh\n', encoding='utf-8')
            real.chmod(0o755)
            link = base / 'link'
            link.symlink_to(real)
            with self.assertRaises(RunStateError):
                validate_service_policy_binary(link, hashlib.sha256(real.read_bytes()).hexdigest())
            plain = base / 'plain'
            plain.write_text('not executable', encoding='utf-8')
            with self.assertRaises(RunStateError):
                validate_service_policy_binary(plain, hashlib.sha256(plain.read_bytes()).hexdigest())


class ServicePolicyEvidenceTamperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []
        with patch('bcs_e2e.local_service_policy_run.OwnedBcsProcess', FakeOwnedRuntime), \
                patch('bcs_e2e.local_service_policy_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_service_policy_run.file_sha256', return_value=self.digest):
            self.state = self.root / 'state'
            self.result = local_service_policy_run(
                **run_kwargs(self.state, self.binary, self.digest))
        self.assertEqual(self.result['status'], 'LOCAL_SERVICE_POLICY_PASS')

    def test_changed_case_cannot_claim_success(self):
        case = read(self.state / 'service-policy.json')
        tampered = json.loads(json.dumps(case))
        tampered['requirements'].pop()
        (self.state / 'service-policy.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_service_policy_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_runtime_disposition_tampering_blocks_the_projection(self):
        owned = read(self.state / 'owned-bcs.json')
        tampered = json.loads(json.dumps(owned))
        tampered['status'] = 'FAILED_STOP'
        (self.state / 'owned-bcs.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_service_policy_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_missing_child_receipt_is_blocked_not_invented(self):
        (self.state / 'service-policy-child.json').unlink()
        summary = build_service_policy_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_publication_gap_and_symlinked_root_are_blocked(self):
        pending = self.state / 'service-policy-publication-pending.json'
        pending.write_text('{}', encoding='utf-8')
        summary = build_service_policy_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        pending.unlink()
        linked = self.root / 'linked-state'
        linked.symlink_to(self.state)
        with self.assertRaises(RunStateError):
            build_service_policy_report(linked)


class ServicePolicyPromotionTests(unittest.TestCase):
    def test_promoted_binding_is_registered_and_aligned(self):
        self.assertIn(('LOCAL-SERVICE-POLICY-01', 'local-service-policy-run'),
                      {(case.case_id, case.command) for case in CASES})
        plan = migration_plan()
        row = next(item for item in plan['local_registered_executors']
                   if item['case_id'] == 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(row['legacy_id'], 'story_retired_service_policy')
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['required_assertion_count'], 8)
        self.assertEqual(row['canonical_reporting'], 'INTEGRATED_LOCAL')
        self.assertEqual(row['execution_command'], 'local-service-policy-run')
        self.assertEqual(row['report_command'], 'local-service-policy-report')
        self.assertTrue(row['release_parent_denominator'])
        self.assertFalse(row['independent_slice'])
        bound = {case.case_id for case in CASES}
        self.assertIn('LOCAL-SERVICE-POLICY-01', bound)
        listing = list_cases()
        self.assertEqual(listing['case_count'], REVIEWED_RELEASE_PARENT_COUNT)
        self.assertEqual(listing['status'], 'READY')
        self.assertEqual(REVIEWED_RELEASE_PARENT_COUNT, 30)

    def test_promoted_row_leaves_the_independent_slice_registry(self):
        plan = migration_plan()
        independent_ids = {row['case_id'] for row in plan['local_independent_slices']}
        self.assertNotIn('LOCAL-SERVICE-POLICY-01', independent_ids)
        executor_ids = {row['case_id'] for row in plan['local_registered_executors']}
        self.assertIn('LOCAL-SERVICE-POLICY-01', executor_ids)
        extra = [row for row in plan['local_registered_executors']
                 if row.get('release_parent_denominator') is True]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]['case_id'], 'LOCAL-SERVICE-POLICY-01')


if __name__ == '__main__':
    unittest.main()
