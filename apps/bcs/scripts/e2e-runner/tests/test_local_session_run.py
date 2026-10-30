"""Public Session helper admission; full nine-step parent remains in reports."""
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
from bcs_e2e.local_session_contract import PUBLIC_CASE, REQUIREMENTS, session_source_binding
from bcs_e2e.local_session_run import local_session_public_run
from bcs_e2e.run_state import RunStateError, read
from bcs_e2e.inventory import current_manifest

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def arguments(root, **overrides):
    return dict(dict(binary=Path(BINARY), binary_sha256=DIGEST, state_dir=root,
                     run_id='session-public-acceptance', confirm_run_id='session-public-acceptance',
                     allow_local_processes=True, allow_fixture_write=True, allow_write=True), **overrides)


class SessionAdmissionTests(unittest.TestCase):
    def test_permissions_confirmation_and_fresh_directory_before_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_session_run.OwnedBcsProcess') as runtime:
            for key, value in (('allow_local_processes', False), ('allow_fixture_write', False),
                               ('allow_write', False), ('confirm_run_id', 'foreign')):
                root = Path(tmp)/key
                with self.assertRaises(RunStateError): local_session_public_run(**arguments(root, **{key: value}))
                self.assertFalse(root.exists())
            with self.assertRaises(RunStateError): local_session_public_run(**arguments(Path(tmp)))
            runtime.assert_not_called()

    def test_original_nine_helpers_order_and_source_drift(self):
        original = session_source_binding()
        self.assertEqual([row['id'] for row in original['requirements']], list(REQUIREMENTS))
        self.assertEqual(len(original['requirements']), 9)
        for kind in ('missing', 'duplicate', 'reverse', 'hash'):
            value = copy.deepcopy(original)
            if kind == 'missing': value['requirements'].pop()
            elif kind == 'duplicate': value['requirements'][-1] = value['requirements'][0]
            elif kind == 'reverse': value['requirements'].reverse()
            else: value['sources']['group_session_ws_probe.py'] = '0'*64
            with self.subTest(kind=kind), patch('bcs_e2e.local_session_contract.read', return_value=value):
                with self.assertRaises(RunStateError): session_source_binding()

    def test_full_local_flow_registration_supersedes_public_slice_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        row = next(r for r in plan['local_registered_executors']
                   if r['case_id'] == 'LOCAL-SESSION-FLOW-01')
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(row['required_assertion_count'], 87)
        self.assertFalse(row['grants_release_credit'])
        self.assertNotIn(PUBLIC_CASE,
                         [item['case_id'] for item in plan['local_partial_executors']])
        self.assertEqual(plan['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(plan['migrated_story_count'], 0)

    def test_cli_no_profile_does_not_grant_permissions(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['local-session-public-run', '--binary', '/unused', '--binary-sha256', '0'*64,
                       '--state-dir', str(Path(tmp)/'run'), '--run-id', 'denied', '--confirm-run-id', 'denied'])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved actual BCS binary required')
class SessionPublicLifecycleLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)/'run'
        cls.result = local_session_public_run(**arguments(cls.root))
        if cls.result['status'] != 'LOCAL_SESSION_PUBLIC_PASS':
            raise AssertionError(json.dumps(cls.result, indent=2))

    def test_actual_ws_closure_and_original_nine_step_denominator(self):
        self.assertEqual(self.result['shutdown']['status'], 'STOPPED')
        self.assertEqual(self.result['credentials']['credential_memory'], 'CLEARED')
        summary = self.result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SLICE_PASS')
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['scope']['full_story_status'], 'NOT_MIGRATED')
        self.assertEqual(summary['requirements'], [{'id': r, 'status': 'PASS' if i == 0 else 'NOT_RUN'}
                                                   for i, r in enumerate(REQUIREMENTS)])
        self.assertEqual(summary['reports']['parent_requirement_count'], 9)
        self.assertEqual(summary['reports']['observed_assertion_count'], 31)
        self.assertEqual(set(self.result['outputs']['outputs']), {'json', 'junit', 'markdown'})
        tree = ET.parse(self.root/'reports/junit.xml').getroot()
        self.assertEqual([tree.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped')], ['1', '0', '0', '0'])
        self.assertEqual(tree.attrib['name'], 'bcs-e2e-session-public')
        for name in REQUIREMENTS:
            self.assertIn(name, (self.root/'reports/summary.md').read_text())

    def test_offline_projection_identical_and_read_only(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('offline')):
            self.assertEqual(build_session_public_report(self.root), self.result['canonical_summary'])
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_source_report_ledger_and_pending_tamper_never_pass(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        completion = read(self.root/'session-public-completion.json')
        for name in [*completion['sources'], *completion['outputs']]:
            path = self.root/name; original = path.read_bytes()
            try:
                path.write_bytes(original+b' ')
                with self.subTest(name=name):
                    try: observed = build_session_public_report(self.root)
                    except (RunStateError, ValueError): continue
                    self.assertNotEqual(observed['status'], 'LOCAL_SLICE_PASS')
            finally: path.write_bytes(original)
        pending = self.root/'session-public-publication-pending.json'
        pending.write_text('{}')
        try: self.assertNotEqual(build_session_public_report(self.root)['status'], 'LOCAL_SLICE_PASS')
        finally: pending.unlink()

    def test_offline_projection_cannot_infer_cleanup_from_pass_label(self):
        from bcs_e2e.local_session_reporting import _business_evidence
        binding = read(self.root/'session-binding.json')
        case = read(self.root/'local-session-public.json')
        self.assertTrue(_business_evidence(self.root, binding, case))
        mutations = []
        item = copy.deepcopy(case); item['operations'].pop(); mutations.append(item)
        item = copy.deepcopy(case); item['checks'].pop(); mutations.append(item)
        item = copy.deepcopy(case); item['websocket']['status'] = 'CONNECTED'; mutations.append(item)
        item = copy.deepcopy(case); item['deletion_admission'][0]['queued_deliveries'] = 1; mutations.append(item)
        item = copy.deepcopy(case); item['fixture_after'] = {}; mutations.append(item)
        item = copy.deepcopy(case); item['unrelated_after'] = {}; mutations.append(item)
        item = copy.deepcopy(case); item['group_delete_verification']['sqlite']['session_rows'] = 1; mutations.append(item)
        item = copy.deepcopy(case); item['ledger']['resources'][0]['resource_id'] = 'foreign'; mutations.append(item)
        for value in mutations:
            self.assertFalse(_business_evidence(self.root, binding, value))

    def test_offline_cli_refuses_overwriting_original(self):
        original = (self.root/'reports/summary.json').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['local-session-public-report', '--state-dir', str(self.root), '--output-dir', str(self.root/'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual((self.root/'reports/summary.json').read_bytes(), original)


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved actual BCS binary required')
class SessionPublicationFailureLiveTests(unittest.TestCase):
    def test_report_write_failure_preserves_pending_and_stopped_owned_child(self):
        from bcs_e2e.local_session_reporting import build_session_public_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.reporting.write_reports', side_effect=OSError('injected publication failure')):
                with self.assertRaises(OSError): local_session_public_run(**arguments(root))
            self.assertEqual(read(root/'owned-bcs.json')['status'], 'STOPPED')
            self.assertTrue((root/'session-public-publication-pending.json').is_file())
            self.assertFalse((root/'session-public-completion.json').exists())
            self.assertNotEqual(build_session_public_report(root)['status'], 'LOCAL_SLICE_PASS')

    def test_cancelled_helper_does_not_become_functional_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            with patch('bcs_e2e.local_session_public.OwnedLocalSessionPublic.run', side_effect=KeyboardInterrupt):
                result = local_session_public_run(**arguments(root))
            self.assertEqual(result['status'], 'CANCELLED')
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
            self.assertEqual(result['canonical_summary']['status'], 'CANCELLED')
            self.assertTrue(all(r['status'] == 'NOT_RUN' for r in result['canonical_summary']['requirements']))


class SessionBootstrapHistoryTests(unittest.TestCase):
    def test_api_history_must_match_exact_scoped_persisted_context(self):
        from unittest.mock import Mock
        from bcs_e2e.local_session_storage import verify_history
        scope = Mock()
        scope._session_id.return_value = 'owned-session'
        scope._id.return_value = 'owned-pm'
        row = {'session_id': 'owned-session', 'owner_bot_id': 'owned-pm',
               'message_id': 'owned-message', 'content': json.dumps('<GroupContext>owned</GroupContext>')}
        good = [{'id': 'owned-message', 'sender': 'system', 'role': 'system',
                 'content': '<GroupContext>owned</GroupContext>'}]
        with patch('bcs_e2e.local_session_storage.history_rows', return_value=[row]):
            self.assertEqual(verify_history(scope, good)['message_count'], 1)
            variants = [[], good+good, [dict(good[0], id='foreign')],
                        [dict(good[0], sender='bot')], [dict(good[0], content='invented')]]
            for value in variants:
                with self.assertRaises(RunStateError): verify_history(scope, value)
