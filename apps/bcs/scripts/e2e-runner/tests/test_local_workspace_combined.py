"""Actual Workspace API+CLI lifecycle and remaining-parent accounting."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.bounded_process import capture
from bcs_e2e.local_workspace_run import REQUIREMENTS, CLI_REQUIREMENTS
from bcs_e2e.run_state import read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


def arguments(root, permissions=True):
    result = ['local-workspace-run', '--state-dir', str(root), '--run-id', 'workspace-combined',
              '--confirm-run-id', 'workspace-combined', '--binary', BINARY or '/missing/bcs',
              '--binary-sha256', DIGEST or 'a'*64, '--cli-binary', CLI or '/missing/cli',
              '--cli-binary-sha256', CLI_DIGEST or 'b'*64]
    if permissions:
        result += ['--allow-local-processes', '--allow-fixture-write', '--allow-write']
    return result


class WorkspaceCombinedAdmissionTests(unittest.TestCase):
    def test_missing_permission_cannot_start_or_provision(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.local_workspace_run.OwnedBcsProcess') as runtime, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            root = Path(tmp)/'new'
            self.assertEqual(main(arguments(root, permissions=False)), 2)
            self.assertFalse(root.exists())
            runtime.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved actual BCS/CLI binaries required')
class WorkspaceCombinedLiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'runtime'

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(arguments(self.root))
        return rc, json.loads(out.getvalue())

    def closed(self):
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')
        report = read(self.root/'workspace-api-cli-report.json')
        self.assertEqual(report['credentials']['credential_memory'], 'CLEARED')
        self.assertFalse(report['automatic_delete'])
        for path in self.root.glob('*/bot/.bcs/session.json'):
            self.assertEqual(read(path)['token'], '')

    def test_actual_api_and_all_cli_requirements_keep_remaining_parent_unqualified(self):
        rc, result = self.execute()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_API_CLI_PASS')
        self.assertEqual(result['selected_requirement_count'], 11)
        self.assertEqual(result['requirements'][:11], [
            {'id': name, 'status': 'PASS'} for name in REQUIREMENTS+CLI_REQUIREMENTS])
        self.assertEqual([r['status'] for r in result['requirements'][11:]], ['NOT_RUN']*6)
        self.assertEqual(result['parent_requirement_count'], 17)
        self.assertEqual(result['legacy_inventory_count'], 41)
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['fusion_mode'], 'PRODUCT_LOCAL_SIMPLE_FUSION_NO_LLM')
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(read(self.root/'workspace-api-cli-report.json'), result)
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_groups').fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_groups WHERE status='completed'").fetchone()[0], 2)
        self.closed()

    def test_nonzero_cli_exit_cannot_be_accepted_for_json_output(self):
        def nonzero(*args, **kwargs):
            result = capture(*args, **kwargs)
            return subprocess.CompletedProcess(result.args, 1, result.stdout, b'private synthetic CLI failure')
        with patch('bcs_e2e.local_workspace_cli.capture', side_effect=nonzero):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertNotIn('private synthetic CLI failure', json.dumps(result))
        self.assertEqual(result['case']['status'], 'LOCAL_WORKSPACE_API_PASS')
        self.assertNotEqual(result['cli_case'].get('status'), 'LOCAL_WORKSPACE_CLI_PASS')
        self.closed()

    def test_cancellation_after_cli_dispatch_preserves_first_failure_and_resources(self):
        def cancel(*args, **kwargs):
            capture(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch('bcs_e2e.local_workspace_cli.capture', side_effect=cancel):
            rc, result = self.execute()
        self.assertEqual(rc, 130, result)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertTrue((self.root/'workspace-cli-resources.sqlite').exists())
        self.closed()


if __name__ == '__main__':
    unittest.main()
