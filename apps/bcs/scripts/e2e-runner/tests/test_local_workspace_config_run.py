"""Owned incident configuration composition preserves the full parent scope."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_workspace_run import REQUIREMENTS, CLI_REQUIREMENTS, INCIDENT_REQUIREMENTS
from bcs_e2e.run_state import read, write

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


def arguments(root, *, allowed=True):
    args = ['local-workspace-run', '--with-incident-config', '--state-dir', str(root),
            '--run-id', 'workspace-config', '--confirm-run-id', 'workspace-config',
            '--binary', BINARY or '/missing/bcs', '--binary-sha256', DIGEST or 'a'*64,
            '--cli-binary', CLI or '/missing/cli', '--cli-binary-sha256', CLI_DIGEST or 'b'*64]
    return args + (['--allow-local-processes', '--allow-fixture-write', '--allow-write'] if allowed else [])


class ConfigRunAdmissionTests(unittest.TestCase):
    def test_denied_before_runtime_or_fixture_creation(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
             patch('bcs_e2e.local_workspace_run.OwnedBcsProcess') as runtime:
            root = Path(tmp)/'new'
            self.assertEqual(main(arguments(root, allowed=False)), 2)
            self.assertFalse(root.exists())
            runtime.assert_not_called()


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved real BCS/CLI binaries required')
class ConfigRunLiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'runtime'

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(arguments(self.root))
        return rc, json.loads(output.getvalue())

    def closed(self):
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')
        self.assertEqual(read(self.root/'workspace-config-report.json')['credentials']['credential_memory'], 'CLEARED')

    def test_api_cli_and_incident_configuration_are_one_owned_lifecycle(self):
        rc, result = self.execute()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_CONFIG_PASS')
        self.assertEqual(result['selected_requirement_count'], 15)
        expected = REQUIREMENTS+CLI_REQUIREMENTS+INCIDENT_REQUIREMENTS
        self.assertEqual(result['requirements'][:15], [{'id': name, 'status': 'PASS'} for name in expected])
        self.assertEqual(result['requirements'][15:], [
            {'id':'WORKSPACE.messages','status':'NOT_RUN'},
            {'id':'WORKSPACE.incident-delete','status':'NOT_RUN'}])
        self.assertEqual(result['parent_requirement_count'], 17)
        self.assertEqual(result['legacy_inventory_count'], 34)
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertFalse(result['automatic_delete'])
        self.assertEqual(result['incident_case'], read(self.root/'local-workspace-incident.json'))
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_groups').fetchone()[0], 5)
        self.closed()

    def test_configuration_report_write_failure_never_claims_success(self):
        def fail(path, value):
            if path.name == 'workspace-config-report.json':
                raise OSError('synthetic final report failure')
            return write(path, value)
        with patch('bcs_e2e.local_workspace_run.write', side_effect=fail):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')
        self.assertTrue((self.root/'workspace-incident-resources.sqlite').is_file())
        self.assertFalse((self.root/'workspace-config-report.json').exists())

    def test_missing_incident_obligation_blocks_success(self):
        from bcs_e2e.local_workspace_incident import OwnedLocalWorkspaceIncident
        original = OwnedLocalWorkspaceIncident.run
        def omit(adapter):
            result = original(adapter)
            result['assertions'].pop()
            return result
        with patch.object(OwnedLocalWorkspaceIncident, 'run', omit):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.closed()

    def test_cancel_after_incident_write_keeps_cancelled_and_retained(self):
        from bcs_e2e.local_workspace_incident import OwnedLocalWorkspaceIncident
        original = OwnedLocalWorkspaceIncident.run
        def cancel(adapter):
            original(adapter)
            raise KeyboardInterrupt()
        with patch.object(OwnedLocalWorkspaceIncident, 'run', cancel):
            rc, result = self.execute()
        self.assertEqual(rc, 130, result)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertTrue((self.root/'workspace-incident-resources.sqlite').is_file())
        self.closed()


if __name__ == '__main__':
    unittest.main()
