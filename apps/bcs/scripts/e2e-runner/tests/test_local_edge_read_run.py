"""Owned-local Edge read lifecycle, CLI registration, and frozen evidence."""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.edge_read_case import (
    ADMISSION_CASE_ID,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_CONNECTIONS_CASE_ID,
)
from bcs_e2e.edge_search_case import CASE_ID as BOT_SEARCH_CASE_ID
from bcs_e2e.local_edge_read_reporting import build_edge_read_report
from bcs_e2e.local_edge_read_run import local_edge_read_run
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.owned_bcs import file_sha256
from bcs_e2e.run_state import RunStateError

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def arguments(root, **changes):
    values = {
        'binary': Path(BINARY), 'binary_sha256': SHA, 'state_dir': root,
        'run_id': 'edge-read-run-test', 'confirm_run_id': 'edge-read-run-test',
        'allow_local_processes': True, 'allow_fixture_write': True, 'allow_write': True,
    }
    values.update(changes)
    return values


class EdgeReadRunAdmissionTests(unittest.TestCase):
    def test_permissions_confirmation_and_fresh_directory_precede_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.local_edge_read_run.OwnedBcsProcess') as runtime:
            for key, value in (
                ('allow_local_processes', False), ('allow_fixture_write', False),
                ('allow_write', False), ('confirm_run_id', 'foreign')):
                with self.subTest(key=key), self.assertRaises(RunStateError):
                    local_edge_read_run(**arguments(Path(tmp) / 'run', **{key: value}))
            with self.assertRaises(RunStateError):
                local_edge_read_run(**arguments(Path(tmp)))
            runtime.assert_not_called()

    def test_cli_registers_the_owned_local_edge_read_pair(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                main(['local-edge-read-run', '--help'])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('--binary-sha256', output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                main(['local-edge-read-report', '--help'])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('--state-dir', output.getvalue())

    def test_registry_preserves_read_only_and_release_scope(self):
        plan = migration_plan()
        rows = {row['case_id']: row for row in plan['local_registered_executors']
                if row['suite'] == 'owned-local-edge-read-v1'}
        self.assertEqual(set(rows), {ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID,
                                     FRIEND_CONNECTIONS_CASE_ID, BOT_SEARCH_CASE_ID})
        self.assertEqual(rows[ADMISSION_CASE_ID]['legacy_id'], 'test_ep_admission')
        self.assertEqual(rows[ADMISSION_CASE_ID]['required_assertion_count'], 2)
        self.assertEqual(rows[FRIEND_REQUESTS_CASE_ID]['legacy_id'],
                         'test_ep_friend_connections_friend_requests_list')
        self.assertEqual(rows[FRIEND_REQUESTS_CASE_ID]['required_assertion_count'], 3)
        self.assertEqual(rows[BOT_SEARCH_CASE_ID]['legacy_id'], 'test_ep_bot_search')
        self.assertEqual(rows[FRIEND_CONNECTIONS_CASE_ID]['legacy_id'],
                         'test_ep_friend_connections_friend_request_and_list')
        self.assertEqual(rows[FRIEND_CONNECTIONS_CASE_ID]['status'], 'LOCAL_READ_SLICE_ONLY')
        self.assertEqual(rows[FRIEND_CONNECTIONS_CASE_ID]['required_assertion_count'], 2)
        self.assertEqual(rows[BOT_SEARCH_CASE_ID]['required_assertion_count'], 12)
        expected_status = {
            ADMISSION_CASE_ID: 'LOCAL_EXECUTOR_ONLY',
            FRIEND_REQUESTS_CASE_ID: 'LOCAL_EXECUTOR_ONLY',
            FRIEND_CONNECTIONS_CASE_ID: 'LOCAL_READ_SLICE_ONLY',
            BOT_SEARCH_CASE_ID: 'LOCAL_EXECUTOR_ONLY',
        }
        for row in rows.values():
            self.assertEqual(row['status'], expected_status[row['case_id']])
            self.assertEqual(row['execution_command'], 'local-edge-read-run')
            self.assertEqual(row['report_command'], 'local-edge-read-report')
            self.assertEqual(row['remote_validation'], 'NOT_RUN')
            self.assertFalse(row['grants_release_credit'])
        rewrites = {row['case_id']: row for row in plan['cross_environment_case_rewrites']}
        for row in rewrites.values():
            self.assertEqual(row['status'], 'PENDING')
            self.assertFalse(row['ready_to_execute'])
            self.assertFalse(row['grants_release_credit'])


@unittest.skipUnless(BINARY and SHA and shutil.which('lsof'), 'approved actual BCS required')
class EdgeReadRunLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name) / 'run'
        cls.result = local_edge_read_run(**arguments(cls.root))
        if cls.result['status'] != 'LOCAL_EDGE_READ_PASS':
            raise AssertionError(cls.result)

    def test_all_read_only_cases_complete_without_release_or_remote_credit(self):
        result = self.result
        self.assertEqual(result['status'], 'LOCAL_EDGE_READ_PASS')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(result['shutdown']['status'], 'STOPPED')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        expected = {
            ADMISSION_CASE_ID: 2,
            FRIEND_REQUESTS_CASE_ID: 3,
            FRIEND_CONNECTIONS_CASE_ID: 2,
            BOT_SEARCH_CASE_ID: 12,
        }
        self.assertEqual([case['case_id'] for case in result['cases']], list(expected))
        for case in result['cases']:
            with self.subTest(case_id=case['case_id']):
                self.assertEqual(case['status'], 'PASS')
                self.assertEqual(case['completed_count'], expected[case['case_id']])
                self.assertEqual(case['total_count'], expected[case['case_id']])
                self.assertFalse(any('Bearer' in str(row) for row in case['steps']))

    def test_canonical_report_is_complete_and_offline_reproducible(self):
        summary = self.result['canonical_summary']
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['reports'], {'expected_assertion_count': 19,
                                              'observed_assertion_count': 19})
        self.assertEqual([case['id'] for case in summary['cases']],
                         [ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID,
                          FRIEND_CONNECTIONS_CASE_ID, BOT_SEARCH_CASE_ID])
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['scope']['remote_validation'], 'NOT_RUN')
        junit = ET.parse(self.root / 'reports/junit.xml').getroot()
        self.assertEqual(junit.attrib['name'], 'bcs-e2e-edge-read')
        self.assertEqual([junit.attrib[key] for key in ('tests', 'failures', 'errors', 'skipped')],
                         ['4', '0', '0', '0'])
        markdown = (self.root / 'reports/summary.md').read_text()
        self.assertIn('# BCS E2E Edge Read Report', markdown)
        self.assertIn('full release qualification', markdown)
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.root.rglob('*') if path.is_file()}
        with patch('http.client.HTTPConnection.request', side_effect=AssertionError('offline')):
            self.assertEqual(build_edge_read_report(self.root), summary)
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_offline_cli_refuses_report_tampering(self):
        path = self.root / 'edge-read-flow-report.json'
        raw = path.read_bytes()
        try:
            path.write_bytes(raw + b' ')
            report = build_edge_read_report(self.root)
            self.assertNotEqual(report['status'], 'LOCAL_SUITE_PASS')
        finally:
            path.write_bytes(raw)

if __name__ == '__main__':
    unittest.main()
