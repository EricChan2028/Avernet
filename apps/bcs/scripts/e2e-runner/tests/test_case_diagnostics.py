"""Lifecycle regression using real local journals and an injected fake log client."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import adapter_for
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.log_observer import BoundedLogObserver
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.reporting import build_report, render_junit, render_markdown, _diagnostic_status
from bcs_e2e.run_state import read


class CaseDiagnosticTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / 'state'
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.profile_path = self.root / 'profile.json'
        self.profile_path.write_text(json.dumps(self.profile))
        patcher = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        patcher.start()
        self.addCleanup(patcher.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['prepare', '--profile', str(self.profile_path),
                                   '--state-dir', str(self.state), '--run-id', 'diagnostic-run']), 0)
        self.client = Mock()
        self.client.query.side_effect = lambda request, **_: {
            'success': True, 'records': [{'environment': request.environment,
                'timestamp': request.start_epoch, 'request_id': request.request_id,
                'message': 'private-body-not-retained'}], 'truncated': False,
        }
        self.observer = BoundedLogObserver(client=self.client, environment='local',
                                           environment_binding_confirmed=True)

    def run_case(self, case='PRE-01', **kwargs):
        return run_case(self.profile, self.state, 'diagnostic-run', case, **kwargs)

    def summary(self):
        return build_report(self.profile, self.state, json.loads((ROOT / 'manifest.json').read_text()))

    def test_all_cases_collect_after_cleanup_with_checkpoint_and_correct_scope(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            original_query = self.client.query.side_effect
            def query(request, **kwargs):
                saved = read(self.state / f'report-{case}.json')
                self.assertEqual(saved['functional'], 'PASS')
                self.assertEqual(saved['cleanup'], 'PASS')
                self.assertEqual(saved['diagnostic_status'], 'NOT_RUN')
                self.assertEqual(request.start_epoch, saved['started_at'])
                self.assertEqual(request.end_epoch, saved['finished_at'])
                with Ledger(self.state / 'run.sqlite', 'diagnostic-run', fingerprint(self.profile),
                            read_only=True) as ledger:
                    self.assertEqual(ledger.cleanup_plan()['resources'], [])
                    current_ids = {row['request_id'] for row in ledger.correlations()
                                   if row['case_id'] == case}
                self.assertIn(request.request_id, current_ids)
                return original_query(request, **kwargs)
            self.client.query.side_effect = query
            result = self.run_case(case, log_observer=self.observer)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['evidence_status'], 'LOCAL_SURROGATE')
            self.assertEqual(result['diagnostic_status'], 'PASS')
            self.client.query.side_effect = original_query
        summary = self.summary()
        self.assertEqual(summary['status'], 'PILOT_PASS')
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual([case['diagnostic_status'] for case in summary['cases']], ['PASS'] * 3)
        self.assertNotIn('private-body-not-retained', json.dumps(summary))
        self.assertTrue(summary['diagnostics'])

    def test_no_client_configured_means_no_collection_and_explicit_not_run(self):
        result = self.run_case()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['diagnostic_status'], 'NOT_RUN')
        self.assertTrue(result['diagnostic_warnings'])
        self.client.query.assert_not_called()

    def test_backend_outage_does_not_replace_functional_or_cleanup_results(self):
        self.client.query.side_effect = RuntimeError('private-backend-credential')
        result = self.run_case(log_observer=self.observer)
        self.assertEqual((result['status'], result['functional'], result['cleanup']), ('PASS',) * 3)
        self.assertEqual(result['diagnostic_status'], 'BLOCKED')
        self.assertNotIn('private-backend-credential', json.dumps(result))
        summary = self.summary()
        self.assertEqual(summary['cases'][0]['diagnostic_status'], 'BLOCKED')
        self.assertIn('BLOCKED', render_markdown(summary))
        junit = ET.fromstring(render_junit(summary))
        properties = junit.find("testcase[@name='PRE-01']/properties")
        self.assertEqual(properties.find("property[@name='diagnostic_status']").get('value'), 'BLOCKED')
        self.assertIsNone(junit.find("testcase[@name='PRE-01']/failure"))

    def test_empty_backend_and_collection_budget_are_visible_as_incomplete(self):
        self.client.query.side_effect = lambda *_args, **_kwargs: {
            'success': True, 'records': [], 'truncated': False,
        }
        result = self.run_case(log_observer=self.observer)
        self.assertEqual(result['diagnostic_status'], 'INCOMPLETE')
        self.assertEqual(result['functional'], 'PASS')
        self.assertEqual(_diagnostic_status([{'case_id': 'PRE-01', 'status': 'OBSERVED'}],
                         'PRE-01', {'diagnostic_status': 'INCOMPLETE'}), 'INCOMPLETE')

    def test_wrong_environment_admission_blocks_diagnostics_before_query(self):
        observer = BoundedLogObserver(client=self.client, environment='pre', environment_binding_confirmed=True)
        result = self.run_case(log_observer=observer)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['diagnostic_status'], 'BLOCKED')
        self.client.query.assert_not_called()

    def test_successful_logs_do_not_upgrade_failed_business_assertion(self):
        def factory(*args, **kwargs):
            adapter = adapter_for(*args, **kwargs)
            adapter.group_update = Mock(side_effect=RuntimeError('synthetic update failure'))
            return adapter
        result = self.run_case(log_observer=self.observer, adapter_factory=factory)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual(result['cleanup'], 'PASS')
        self.assertEqual(result['diagnostic_status'], 'PASS')

    def test_diagnostic_storage_error_stays_visible_and_preserves_case_result(self):
        with patch.object(Ledger, 'record_diagnostic', side_effect=sqlite3.OperationalError('disk failure')):
            result = self.run_case(log_observer=self.observer)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['diagnostic_status'], 'BLOCKED')
        self.assertTrue(result['diagnostic_warnings'])
        self.assertEqual(self.summary()['cases'][0]['diagnostic_status'], 'BLOCKED')
