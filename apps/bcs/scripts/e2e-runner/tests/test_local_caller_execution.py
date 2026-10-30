"""Offline guards for the owned-local caller-execution black-box slice."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_caller_execution_contract import (MARKER_KEYS, REQUIREMENTS, STORY_SOURCE,
                                                     caller_execution_source_binding)
from bcs_e2e.local_caller_execution_reporting import build_caller_execution_report
from bcs_e2e.local_caller_execution_run import (BINARY_KIND, STORY_ERR_LOG_NAME, STORY_LOG_NAME,
                                                _parse_story_markers, local_caller_execution_run,
                                                validate_caller_execution_binary)
from bcs_e2e.run_state import RunStateError, read

GOOD_STDOUT = (b'booting owned child\n'
               b'Caller HTTP/queue/provider E2E: 27 HTTP assertions passed\n'
               b'dispatch queue converged\n'
               b'Durable queue/cache-loss restart fails closed: PASS\n'
               b'story synthetic stderr kept minimal\n'
               b'Caller credential persistence and log redaction: PASS\n')


def story_seam(stdout=GOOD_STDOUT, exit_code=0, timed_out=False, error=None):
    if error is not None:
        def launch(*_args, **_kwargs):
            raise error
        return launch

    def launch(*_args, **_kwargs):
        return {'exit_code': exit_code, 'timed_out': timed_out, 'duration_seconds': 0.42,
                'stdout': stdout, 'stderr': b''}
    return launch


def executable_binary(directory: Path) -> tuple[Path, str]:
    binary = directory / 'bcs-caller-e2e-test-utils'
    binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    binary.chmod(0o755)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def run_kwargs(state_dir: Path, binary: Path, digest: str, **changes) -> dict:
    base = dict(binary=binary, binary_sha256=digest, state_dir=state_dir, run_id='caller-test',
                confirm_run_id='caller-test', allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
    base.update(changes)
    return base


class MarkerOrderingTests(unittest.TestCase):
    def test_strict_order_interpretation(self):
        text = GOOD_STDOUT.decode()
        self.assertEqual(_parse_story_markers(text), ((True, True, True), 27))
        self.assertEqual(_parse_story_markers('Durable queue/cache-loss restart fails closed: PASS\n'), ((False, False, False), 0))
        missing_middle = (GOOD_STDOUT.replace(b'Durable queue/cache-loss restart fails closed: PASS\n', b'')
                        .replace(b'Caller credential persistence and log redaction: PASS\n', b'next\n').decode())
        self.assertEqual(_parse_story_markers(missing_middle), ((True, False, False), 27))
        self.assertEqual(_parse_story_markers('Caller HTTP/queue/provider E2E: 0 HTTP assertions passed\n'
                                              'Durable queue/cache-loss restart fails closed: PASS\n'
                                              'Caller credential persistence and log redaction: PASS\n'),
                         ((True, True, True), 0))


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_upstream_map(self):
        value = caller_execution_source_binding()
        self.assertEqual(value['parent'], 'caller_execution_story')
        self.assertEqual(len(value['requirements']), 9)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['story_markers'][0],
                         'Caller HTTP/queue/provider E2E: {http_assertions} HTTP assertions passed')
        self.assertEqual(set(value['sources']), {'caller_execution_story.py', 'caller_e2e_coverage.sh'})
        self.assertFalse(value['grants_release_credit'])
        self.assertEqual(value['evidence_granularity'], 'SHARED_CHILD_RECEIPT')

    def test_contract_tamper_matrix_is_rejected(self):
        original = caller_execution_source_binding()
        for mutation in ('drop-requirement', 'markers', 'binary-auto-build', 'revision', 'credit',
                         'granularity', 'environment', 'independent'):
            value = copy.deepcopy(original)
            if mutation == 'drop-requirement':
                value['requirements'].pop()
            elif mutation == 'markers':
                value['story_markers'][1] = 'Durable queue restarts: PASS'
            elif mutation == 'binary-auto-build':
                value['test_utils_binary']['auto_build'] = True
            elif mutation == 'revision':
                value['source_revision'] = 'z' * 40
            elif mutation == 'credit':
                value['grants_release_credit'] = True
            elif mutation == 'granularity':
                value['evidence_granularity'] = 'PER_OBLIGATION_RECEIPT'
            elif mutation == 'environment':
                value['environment']['preprod'] = 'SUPPORTED'
            else:
                value['independent_slice'] = False
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.local_caller_execution_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                caller_execution_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'caller_execution_story.py'
            drifted.write_text('#!/usr/bin/env python3\n# drifted\n', encoding='utf-8')
            with patch('bcs_e2e.local_caller_execution_contract.STORY_SOURCE', drifted):
                with self.assertRaises(RunStateError):
                    caller_execution_source_binding()


class BinaryValidationTests(unittest.TestCase):
    def test_frozen_regular_executable_with_exact_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary, digest = executable_binary(Path(temporary))
            self.assertEqual(validate_caller_execution_binary(binary, digest), binary)
            wrong = Path(temporary) / 'other'
            wrong.write_bytes(b'x')
            with self.assertRaises(RunStateError):
                validate_caller_execution_binary(binary, '0' * 64)

    def test_symlink_nonregular_or_nonexecutable_binaries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / 'real'
            real.write_text('#!/bin/sh\n', encoding='utf-8')
            real.chmod(0o755)
            link = base / 'link'
            link.symlink_to(real)
            with self.assertRaises(RunStateError):
                validate_caller_execution_binary(link, hashlib.sha256(real.read_bytes()).hexdigest())
            plain = base / 'plain'
            plain.write_text('not executable', encoding='utf-8')
            with self.assertRaises(RunStateError):
                validate_caller_execution_binary(plain, hashlib.sha256(plain.read_bytes()).hexdigest())


class CallerExecutionRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)

    def test_success_run_publishes_the_shared_child_receipt_chain(self):
        state = self.root / 'state'
        with patch('bcs_e2e.local_caller_execution_run._launch_story', story_seam()):
            result = local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'LOCAL_CALLER_EXECUTION_PASS')
        self.assertEqual(result['ready_to_execute'], False)
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['credentials'], {'credential_memory': 'NOT_CAPTURED',
                                                 'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'})
        names = {'caller-execution-binding.json', 'caller-execution-child.json', 'caller-execution.json',
                 'caller-execution-flow-report.json', STORY_LOG_NAME, STORY_ERR_LOG_NAME,
                 'caller-execution-completion.json', 'reports/summary.json', 'reports/junit.xml',
                 'reports/summary.md'}
        self.assertTrue(all((state / name).is_file() for name in names), sorted(p.name for p in state.rglob('*')))
        self.assertFalse((state / 'caller-execution-publication-pending.json').exists())
        child = read(state / 'caller-execution-child.json')
        self.assertEqual(child['exit_code'], 0)
        self.assertEqual(child['markers'], dict.fromkeys(MARKER_KEYS, True))
        self.assertEqual(child['http_assertions'], 27)
        self.assertTrue(child['redaction_gate'])
        self.assertEqual(child['argv'][1], str(STORY_SOURCE))
        self.assertIn('--binary', child['argv'])
        self.assertNotIn('test-token', json.dumps(child))
        case = read(state / 'caller-execution.json')
        self.assertEqual(case['status'], 'LOCAL_CALLER_EXECUTION_PASS')
        self.assertEqual([row['id'] for row in case['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in case['requirements']))
        self.assertEqual(case['child_receipt'], child)
        self.assertEqual(case['story_lines'], {row['id']: list(row['story_lines'])
                                              for row in caller_execution_source_binding()['requirements']})
        flow = read(state / 'caller-execution-flow-report.json')
        self.assertEqual(flow['child_receipt'], child)
        self.assertEqual(flow['case'], case)
        summary = build_caller_execution_report(state)
        self.assertEqual(summary, result['canonical_summary'])
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['legacy_inventory_count'], 41)
        self.assertEqual(summary['scope']['independent_slice'], True)
        self.assertEqual(summary['scope']['release_parent_denominator'], False)
        self.assertEqual(summary['reports']['observed_assertion_count'], 9)
        self.assertIn('per-obligation dynamic isolation (shared black-box child receipt)',
                      summary['mock_boundary']['not_proven'])

    def test_missing_marker_nonzero_exit_or_timeout_block_without_pass_claims(self):
        for name, seam in (
                ('missing-marker3', story_seam(GOOD_STDOUT.replace(b'Caller credential persistence and log redaction: PASS\n', b''))),
                ('nonzero-exit', story_seam(exit_code=130)),
                ('timed-out', story_seam(timed_out=True))):
            with self.subTest(case=name):
                state = self.root / ('state-' + name)
                with patch('bcs_e2e.local_caller_execution_run._launch_story', seam):
                    result = local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertFalse((state / 'caller-execution.json').exists())
                self.assertTrue((state / 'caller-execution-child.json').is_file())
                self.assertTrue((state / STORY_LOG_NAME).is_file())
                summary = build_caller_execution_report(state)
                self.assertNotEqual(summary['status'], 'LOCAL_SUITE_PASS')

    def test_raw_token_in_child_streams_blocks_and_never_lands_in_evidence(self):
        state = self.root / 'state-token'
        contaminated = b'Caller HTTP/queue/provider E2E: 27 HTTP assertions passed\nDurable queue/cache-loss restart fails closed: PASS\ntest-token leaked\nCaller credential persistence and log redaction: PASS\n'
        with patch('bcs_e2e.local_caller_execution_run._launch_story', story_seam(contaminated)):
            result = local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse((state / STORY_LOG_NAME).exists())
        self.assertFalse((state / STORY_ERR_LOG_NAME).exists())
        child = read(state / 'caller-execution-child.json')
        self.assertIs(child['redaction_gate'], False)
        self.assertNotIn('test-token', json.dumps(child))
        summary = build_caller_execution_report(state)
        self.assertEqual(summary['status'], 'BLOCKED')

    def test_zero_http_assertions_and_launch_failure_are_blocked(self):
        zero = GOOD_STDOUT.replace(b': 27 HTTP assertions passed', b': 0 HTTP assertions passed')
        state = self.root / 'state-zero'
        with patch('bcs_e2e.local_caller_execution_run._launch_story', story_seam(zero)):
            result = local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        child = read(state / 'caller-execution-child.json')
        self.assertEqual(child['http_assertions'], 0)
        state = self.root / 'state-launch'
        with patch('bcs_e2e.local_caller_execution_run._launch_story',
                   story_seam(error=RunStateError('deadline'))):
            result = local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['error_type'], 'RunStateError')
        self.assertEqual(result['child_receipt'], {'status': 'NOT_CREATED'})
        self.assertFalse((state / 'caller-execution-child.json').exists())
        summary = build_caller_execution_report(state)
        # A self-consistent BLOCKED run must not fabricate integrity issues: the
        # launch failure surfaces through status, not through issues.
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertEqual(summary['cases'][0]['status'], 'BLOCKED')
        self.assertEqual(summary['cases'][0]['functional'], 'INCOMPLETE')
        self.assertEqual(summary['integrity']['issues'], [])

    def test_cancellation_canonicalises_without_pass_claims(self):
        state = self.root / 'state-cancel'
        with patch('bcs_e2e.local_caller_execution_run._launch_story',
                   story_seam(error=KeyboardInterrupt())):
            result = local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        summary = build_caller_execution_report(state)
        self.assertEqual(summary['status'], 'CANCELLED')

    def test_admission_freezes_permissions_state_and_binary_before_process(self):
        with patch('bcs_e2e.local_caller_execution_run._launch_story') as seam:
            seam.side_effect = AssertionError('child must not start')
            with tempfile.TemporaryDirectory() as other:
                for changes in ({'allow_local_processes': False}, {'allow_fixture_write': False},
                                {'allow_write': False}, {'confirm_run_id': 'foreign'}):
                    with self.subTest(changes=changes):
                        with self.assertRaises(RunStateError):
                            local_caller_execution_run(**run_kwargs(self.root / 's', self.binary,
                                                                    self.digest, **changes))
                bad = self.root / 'bad-digest'
                with self.assertRaises(RunStateError):
                    local_caller_execution_run(**run_kwargs(bad, self.binary, '0' * 64))
                self.assertFalse(bad.exists())
                existing = self.root / 'existing'
                existing.mkdir(mode=0o700)
                with self.assertRaises(RunStateError):
                    local_caller_execution_run(**run_kwargs(existing, self.binary, self.digest))
                symlink = self.root / 'linked-state'
                target = self.root / 'target'
                target.mkdir(mode=0o700)
                symlink.symlink_to(target)
                with self.assertRaises(RunStateError):
                    local_caller_execution_run(**run_kwargs(symlink, self.binary, self.digest))
        self.assertEqual(seam.call_count, 0)

    def test_executor_drift_fence_refuses_publication(self):
        state = self.root / 'state-drift'
        seen = {'count': 0}

        def drifting():
            seen['count'] += 1
            return 'first-digest' if seen['count'] == 1 else 'second-digest'

        with patch('bcs_e2e.local_caller_execution_run._launch_story', story_seam()), \
                patch('bcs_e2e.local_caller_execution_run.executor_digest', side_effect=drifting):
            with self.assertRaises(RunStateError):
                local_caller_execution_run(**run_kwargs(state, self.binary, self.digest))
        binding = read(state / 'caller-execution-binding.json')
        self.assertEqual(binding['executor_digest'], 'first-digest')
        self.assertTrue((state / 'caller-execution-flow-report.json').is_file())


class EvidenceTamperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        self.state = self.root / 'state'
        with patch('bcs_e2e.local_caller_execution_run._launch_story', story_seam()):
            self.result = local_caller_execution_run(**run_kwargs(self.state, self.binary, self.digest))
        self.assertEqual(self.result['status'], 'LOCAL_CALLER_EXECUTION_PASS')

    def test_changed_evidence_cannot_claim_success(self):
        case = read(self.state / 'caller-execution.json')
        tampered = copy.deepcopy(case)
        tampered['requirements'].pop()
        (self.state / 'caller-execution.json').write_text(json.dumps(tampered), encoding='utf-8')
        summary = build_caller_execution_report(self.state)
        self.assertNotEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertTrue(summary['integrity']['issues'])

    def test_missing_child_receipt_is_blocked_not_invented(self):
        (self.state / 'caller-execution-child.json').unlink()
        summary = build_caller_execution_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_publication_gap_and_symlinked_root_are_blocked(self):
        pending = self.state / 'caller-execution-publication-pending.json'
        pending.write_text('{}', encoding='utf-8')
        summary = build_caller_execution_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        pending.unlink()
        linked = self.root / 'linked-state'
        linked.symlink_to(self.state)
        with self.assertRaises(RunStateError):
            build_caller_execution_report(linked)


if __name__ == '__main__':
    unittest.main()
