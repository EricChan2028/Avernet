"""Whole original nine-helper Session lifecycle and offline qualification."""
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
BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_SHA = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


def arguments(root, **overrides):
    return dict(dict(binary=Path(BINARY), binary_sha256=DIGEST, cli_binary=Path(CLI), cli_binary_sha256=CLI_SHA,
                     state_dir=root, run_id='session-flow-test', confirm_run_id='session-flow-test',
                     allow_local_processes=True, allow_fixture_write=True, allow_write=True, with_completion=True), **overrides)


class SessionFlowAdmissionTests(unittest.TestCase):
    def test_full_selection_requires_real_cli_pair_and_boolean_selection_before_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_session_run.OwnedBcsProcess') as runtime:
            for overrides in ({'cli_binary': None, 'cli_binary_sha256': None}, {'with_completion': 'yes'},
                              {'allow_write': False}, {'confirm_run_id': 'foreign'}):
                with self.assertRaises(RunStateError): local_session_public_run(**arguments(Path(tmp)/'run', **overrides))
            runtime.assert_not_called()
            self.assertFalse((Path(tmp)/'run').exists())

    def test_local_parent_registration_keeps_all_nine_and_no_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        item = next(row for row in plan['local_registered_executors'] if row['case_id'] == 'LOCAL-SESSION-FLOW-01')
        self.assertEqual(item['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(item['required_assertion_count'], 9)
        self.assertEqual(item['canonical_reporting'], 'INTEGRATED_LOCAL')
        self.assertFalse(item['grants_release_credit'])
        superseded = {'LOCAL-SESSION-PUBLIC-01', 'LOCAL-SESSION-SHARING-01'}
        self.assertFalse(any(row['case_id'] in superseded
                             for row in plan['local_partial_executors']))
        self.assertEqual(plan['legacy_inventory_count'], 41)
        self.assertEqual(plan['migrated_story_count'], 0)

    def test_cli_full_entry_keeps_permission_gate_without_remote_profile(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['local-session-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--cli-binary', '/unused-cli', '--cli-binary-sha256', '1'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'denied', '--confirm-run-id', 'denied'])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_SHA and shutil.which('lsof'), 'approved actual BCS/CLI required')
class SessionFlowLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)/'run'
        cls.result = local_session_public_run(**arguments(cls.root))
        if cls.result['status'] != 'LOCAL_SESSION_FLOW_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_complete_original_parent_is_nine_of_nine_but_not_release(self):
        result = self.result; summary = result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertEqual(summary['reports'], {'expected_assertion_count': 9, 'parent_requirement_count': 9,
                                              'observed_assertion_count': 9})
        self.assertEqual(summary['requirements'], [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS])
        self.assertEqual(summary['scope']['full_story_status'], 'LOCAL_INTEGRATED')
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['scope']['legacy_inventory_count'], 41)
        tree = ET.parse(self.root/'session-reports/junit.xml').getroot()
        self.assertEqual(tree.attrib['name'], 'bcs-e2e-session-flow')
        self.assertEqual([tree.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')], ['1', '0', '0', '0'])
        for name in REQUIREMENTS: self.assertIn(name, (self.root/'session-reports/summary.md').read_text())

    def test_all_three_reports_verify_offline_without_changing_sources(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        from bcs_e2e.local_session_flow_reporting import build_session_flow_report
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('offline')):
            self.assertEqual(build_session_public_report(self.root)['status'], 'LOCAL_SLICE_PASS')
            self.assertEqual(build_sharing_report(self.root)['status'], 'LOCAL_SLICE_PASS')
            self.assertEqual(build_session_flow_report(self.root), self.result['canonical_summary'])
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_all_original_evidence_and_outputs_are_bound(self):
        from bcs_e2e.local_session_flow_reporting import build_session_flow_report
        completion = read(self.root/'session-flow-completion.json')
        for name in [*completion['sources'], *completion['outputs']]:
            path = self.root/name; original = path.read_bytes()
            try:
                path.write_bytes(original+b' ')
                with self.subTest(name=name):
                    try: summary = build_session_flow_report(self.root)
                    except (RunStateError, ValueError): continue
                    self.assertNotEqual(summary['status'], 'LOCAL_SUITE_PASS')
            finally: path.write_bytes(original)
        self.assertEqual(build_session_flow_report(self.root)['status'], 'LOCAL_SUITE_PASS')

    def test_business_projection_rejects_inconsistent_success_evidence(self):
        from bcs_e2e.local_session_flow_reporting import _business
        binding = read(self.root/'session-completion-binding.json')
        original = read(self.root/'local-session-completion.json')
        self.assertTrue(_business(self.root, binding, original))
        mutations = {
            'missing_operation': lambda c: c['operations'].pop(),
            'duplicate_request': lambda c: c['operations'][1].update(
                submitted_request_id=c['operations'][0]['submitted_request_id']),
            'lost_ack': lambda c: c['operations'][0].update(status='UNKNOWN'),
            'wrong_http': lambda c: c['operations'][0].update(http_status=403),
            'live_ledger': lambda c: c['resources']['ledger'][0].update(state='live'),
            'engine_started': lambda c: c['deletion_admissions'][0].update(engine='STARTED'),
            'delete_incomplete': lambda c: c['deletion_proofs'][0]['sqlite'].update(session_rows=1),
            'history_changed': lambda c: c['histories']['predecessor_after'].update(row_count=0),
            'missing_message': lambda c: c['helper_evidence']['lifecycle']['message'].update(http_contains_message=False),
            'fake_engine_completion': lambda c: c['helper_evidence']['complete'].update(service_business_execution='COMPLETED'),
            'collection_not_isolated': lambda c: c['helper_evidence']['collection'].update(per_bot_isolation=False),
            'credential_not_cleared': lambda c: c['cli'].update(credential_contexts_cleared=False),
            'fixture_drift': lambda c: c['fixture_state'].update(preserved=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(original)
                mutate(changed)
                self.assertFalse(_business(self.root, binding, changed))
        self.assertEqual(read(self.root/'local-session-completion.json'), original)

    def test_pending_marker_and_export_refuse_to_overwrite_original(self):
        from bcs_e2e.local_session_flow_reporting import build_session_flow_report
        pending = self.root/'session-flow-publication-pending.json'; pending.write_text('{}')
        try: self.assertNotEqual(build_session_flow_report(self.root)['status'], 'LOCAL_SUITE_PASS')
        finally: pending.unlink()
        before = (self.root/'session-reports/summary.json').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['local-session-report', '--state-dir', str(self.root), '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual((self.root/'session-reports/summary.json').read_bytes(), before)


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_SHA and shutil.which('lsof'), 'approved actual BCS/CLI required')
class SessionFlowFailureLiveTests(unittest.TestCase):
    def test_early_public_failure_keeps_requested_full_flow_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.local_session_public.OwnedLocalSessionPublic.run',
                       side_effect=RunStateError('injected public stage failure')):
                result = local_session_public_run(**arguments(root))
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['case_id'], 'LOCAL-SESSION-FLOW-01')
            self.assertEqual(result['completion_status'], 'NOT_RUN')
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
            self.assertFalse((root/'session-completion-binding.json').exists())
            self.assertFalse((root/'session-reports').exists())

    def test_later_failure_preserves_earlier_six_but_not_full_success(self):
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.local_session_completion.OwnedLocalSessionCompletion.run', side_effect=RunStateError('injected completion failure')):
                result = local_session_public_run(**arguments(root))
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertEqual(build_sharing_report(root)['status'], 'LOCAL_SLICE_PASS')
            self.assertTrue(all(row['status'] == 'PASS' for row in result['canonical_summary']['requirements'][:6]))
            self.assertTrue(all(row['status'] == 'NOT_RUN' for row in result['canonical_summary']['requirements'][6:]))

    def test_later_cancellation_keeps_previous_stages_and_cancels_full_flow(self):
        from bcs_e2e.local_session_sharing_reporting import build_sharing_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.local_session_completion.OwnedLocalSessionCompletion.run', side_effect=KeyboardInterrupt):
                result = local_session_public_run(**arguments(root))
            self.assertEqual(result['status'], 'CANCELLED')
            self.assertEqual(build_sharing_report(root)['status'], 'LOCAL_SLICE_PASS')
            self.assertEqual(result['canonical_summary']['status'], 'CANCELLED')
