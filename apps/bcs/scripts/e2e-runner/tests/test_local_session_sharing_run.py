"""Composite first-six Session lifecycle and immutable report qualification."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_session_contract import REQUIREMENTS
from bcs_e2e.local_session_run import local_session_public_run
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.inventory import current_manifest
BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_SHA = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


def arguments(root, **overrides):
    return dict(dict(binary=Path(BINARY), binary_sha256=DIGEST, cli_binary=Path(CLI), cli_binary_sha256=CLI_SHA,
                     state_dir=root, run_id='session-sharing-test', confirm_run_id='session-sharing-test',
                     allow_local_processes=True, allow_fixture_write=True, allow_write=True), **overrides)


class SharingAdmissionTests(unittest.TestCase):
    def test_cli_pair_permission_and_confirmation_reject_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_session_run.OwnedBcsProcess') as runtime:
            for key, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                               ('allow_write', False), ('confirm_run_id', 'other'), ('cli_binary_sha256', None)):
                root = Path(tmp)/key
                with self.assertRaises(RunStateError): local_session_public_run(**arguments(root, **{key: value}))
                self.assertFalse(root.exists())
            runtime.assert_not_called()

    def test_inventory_full_flow_supersedes_sharing_slice_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        row = next(r for r in plan['local_registered_executors']
                   if r['case_id'] == 'LOCAL-SESSION-FLOW-01')
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertFalse(row['grants_release_credit'])
        self.assertNotIn('LOCAL-SESSION-SHARING-01',
                         [item['case_id'] for item in plan['local_partial_executors']])
        self.assertEqual(plan['migrated_story_count'], 0)

    def test_cli_entry_needs_no_remote_profile_and_still_requires_permissions(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['local-session-sharing-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--cli-binary', '/unused-cli', '--cli-binary-sha256', '1'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'denied', '--confirm-run-id', 'denied'])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_SHA and shutil.which('lsof'), 'approved actual BCS/CLI required')
class SharingLifecycleLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)/'run'
        cls.result = local_session_public_run(**arguments(cls.root))
        if cls.result['status'] != 'LOCAL_SESSION_SHARING_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_six_helpers_and_remaining_three_are_reported_without_release_credit(self):
        result = self.result; summary = result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SLICE_PASS')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertEqual(summary['reports']['expected_assertion_count'], 6)
        self.assertEqual(summary['reports']['observed_assertion_count'], 6)
        self.assertEqual(summary['reports']['parent_requirement_count'], 9)
        self.assertEqual(summary['requirements'], [{'id': name, 'status': 'PASS' if i < 6 else 'NOT_RUN'}
                                                   for i, name in enumerate(REQUIREMENTS)])
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['scope']['full_story_status'], 'NOT_MIGRATED')
        tree = ET.parse(self.root/'sharing-reports/junit.xml').getroot()
        self.assertEqual(tree.attrib['name'], 'bcs-e2e-session-sharing')
        self.assertEqual([tree.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')], ['1', '0', '0', '0'])
        for name in REQUIREMENTS: self.assertIn(name, (self.root/'sharing-reports/summary.md').read_text())

    def test_public_original_report_and_composite_both_verify_without_network(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('offline')):
            self.assertEqual(build_session_public_report(self.root)['status'], 'LOCAL_SLICE_PASS')
            self.assertEqual(build_sharing_report(self.root), self.result['canonical_summary'])
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_every_source_and_output_tamper_is_detected(self):
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        completion = read(self.root/'session-sharing-completion.json')
        for name in [*completion['sources'], *completion['outputs']]:
            path = self.root/name; original = path.read_bytes()
            try:
                path.write_bytes(original+b' ')
                with self.subTest(name=name):
                    try: summary = build_sharing_report(self.root)
                    except (RunStateError, ValueError): continue
                    self.assertNotEqual(summary['status'], 'LOCAL_SLICE_PASS')
            finally: path.write_bytes(original)
        self.assertEqual(build_sharing_report(self.root)['status'], 'LOCAL_SLICE_PASS')

    def test_projection_requires_cli_cleanup_history_and_exact_ledger_evidence(self):
        from bcs_e2e.local_session_sharing_reporting import _business
        case = read(self.root/'local-session-sharing.json')
        binding = read(self.root/'session-sharing-binding.json')
        self.assertTrue(_business(self.root, binding, case))
        variants = []
        value = copy.deepcopy(case); value['operations'].pop(); variants.append(value)
        value = copy.deepcopy(case); value['checks'].pop(); variants.append(value)
        value = copy.deepcopy(case); value['visibility']['restored'] = False; variants.append(value)
        value = copy.deepcopy(case); value['credential_contexts_cleared']['all_cleared'] = False; variants.append(value)
        value = copy.deepcopy(case); value['resources']['ledger'][0]['resource_id'] = 'foreign'; variants.append(value)
        value = copy.deepcopy(case); value['deletion_proofs'][0]['sqlite']['group_rows'] = 1; variants.append(value)
        value = copy.deepcopy(case); value['retained_histories']['after_delete'] = {}; variants.append(value)
        value = copy.deepcopy(case)
        next(op for op in value['operations'] if op['transport'] == 'PRODUCT_CLI')['cli_exit_code'] = 1
        variants.append(value)
        for value in variants:
            self.assertFalse(_business(self.root, binding, value))

    def test_pending_publication_and_cli_export_cannot_overwrite_original(self):
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        pending = self.root/'session-sharing-publication-pending.json'; pending.write_text('{}')
        try: self.assertNotEqual(build_sharing_report(self.root)['status'], 'LOCAL_SLICE_PASS')
        finally: pending.unlink()
        before = (self.root/'sharing-reports/summary.json').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-session-sharing-report', '--state-dir', str(self.root), '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual((self.root/'sharing-reports/summary.json').read_bytes(), before)


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_SHA and shutil.which('lsof'), 'approved actual BCS/CLI required')
class SharingFailureLifecycleLiveTests(unittest.TestCase):
    def test_failed_sharing_keeps_public_success_but_never_qualifies_six_helpers(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.local_session_sharing.OwnedLocalSessionSharing.run', side_effect=RunStateError('injected stage failure')):
                result = local_session_public_run(**arguments(root))
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertEqual(build_session_public_report(root)['status'], 'LOCAL_SLICE_PASS')
            self.assertEqual(result['canonical_summary']['requirements'][0]['status'], 'PASS')
            self.assertTrue(all(row['status'] == 'NOT_RUN' for row in result['canonical_summary']['requirements'][1:]))

    def test_composite_publication_failure_keeps_pending_and_public_original(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        from bcs_e2e.reporting import write_reports as actual_write
        def fail_composite(summary, output_dir, formats):
            if Path(output_dir).name == 'sharing-reports': raise OSError('injected composite publication failure')
            return actual_write(summary, output_dir, formats)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.reporting.write_reports', side_effect=fail_composite):
                with self.assertRaises(OSError): local_session_public_run(**arguments(root))
            self.assertEqual(read(root/'owned-bcs.json')['status'], 'STOPPED')
            self.assertEqual(build_session_public_report(root)['status'], 'LOCAL_SLICE_PASS')
            self.assertTrue((root/'session-sharing-publication-pending.json').is_file())
            self.assertNotEqual(build_sharing_report(root)['status'], 'LOCAL_SLICE_PASS')
