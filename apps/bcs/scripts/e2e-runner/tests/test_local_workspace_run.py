"""Workspace lifecycle does not promote its API increment to parent coverage."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_workspace_run import REQUIREMENTS
from bcs_e2e.run_state import read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def arguments(root, *, permissions=True):
    args = ['local-workspace-api', '--state-dir', str(root), '--run-id', 'workspace-api',
            '--confirm-run-id', 'workspace-api', '--binary', BINARY or '/missing/bcs',
            '--binary-sha256', DIGEST or 'a'*64]
    if permissions:
        args += ['--allow-local-processes', '--allow-fixture-write', '--allow-write']
    return args


class WorkspaceRunAdmissionTests(unittest.TestCase):
    def test_permissions_checked_before_runtime_and_fixture_write(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.local_workspace_run.OwnedBcsProcess') as runtime, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            root = Path(tmp)/'new'
            self.assertEqual(main(arguments(root, permissions=False)), 2)
            runtime.assert_not_called()
            self.assertFalse(root.exists())
            self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')

    def test_existing_directory_is_never_adopted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.local_workspace_run.OwnedBcsProcess') as runtime, \
             contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            (root/'preserve.txt').write_text('original')
            self.assertEqual(main(arguments(root)), 2)
            runtime.assert_not_called()
            self.assertEqual((root/'preserve.txt').read_text(), 'original')

    def test_broken_symlink_and_wrong_confirmation_block_before_start(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.local_workspace_run.OwnedBcsProcess') as runtime, \
             contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)/'link'
            root.symlink_to(Path(tmp)/'missing')
            self.assertEqual(main(arguments(root)), 2)
            args = arguments(Path(tmp)/'fresh')
            args[args.index('--confirm-run-id')+1] = 'wrong'
            self.assertEqual(main(args), 2)
            runtime.assert_not_called()
            self.assertTrue(root.is_symlink())


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'approved owned BCS binary required')
class WorkspaceRunLiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'run'

    def execute(self):
        previous = signal.getsignal(signal.SIGTERM)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(arguments(self.root))
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        return rc, json.loads(output.getvalue())

    def closed(self):
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')
        result = read(self.root/'workspace-api-report.json')
        self.assertEqual(result['credentials']['credential_memory'], 'CLEARED')
        self.assertFalse(result['automatic_delete'])

    def test_api_obligations_pass_without_granting_full_parent_credit(self):
        rc, result = self.execute()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_API_PASS')
        self.assertEqual(result['case']['assertions'], [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS])
        self.assertEqual(len(result['requirements']), 18)
        self.assertEqual(sum(r['status']=='PASS' for r in result['requirements']), 7)
        self.assertEqual(sum(r['status']=='NOT_RUN' for r in result['requirements']), 11)
        self.assertEqual(result['legacy_inventory_count'], 34)
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(read(self.root/'workspace-api-report.json'), result)
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM bcs_groups').fetchone()[0], 2)
        self.closed()

    def test_missing_obligation_cannot_qualify_even_if_adapter_claims_pass(self):
        from bcs_e2e.local_workspace_api import OwnedLocalWorkspaceApi
        original = OwnedLocalWorkspaceApi.run
        def omit(adapter):
            result = original(adapter)
            result['assertions'].pop()
            return result
        with patch.object(OwnedLocalWorkspaceApi, 'run', omit):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.closed()

    def test_cancellation_preserves_owned_database_and_closes_child(self):
        from bcs_e2e.local_workspace_api import OwnedLocalWorkspaceApi
        original = OwnedLocalWorkspaceApi.run
        def cancel(adapter):
            original(adapter)
            raise KeyboardInterrupt()
        with patch.object(OwnedLocalWorkspaceApi, 'run', cancel):
            rc, result = self.execute()
        self.assertEqual(rc, 130, result)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertTrue((self.root/'workspace-resources.sqlite').is_file())
        self.closed()

    def test_report_publication_failure_does_not_return_success(self):
        from bcs_e2e.run_state import write
        def fail(path, value):
            if path.name == 'workspace-api-report.json':
                raise OSError('synthetic disk failure')
            return write(path, value)
        with patch('bcs_e2e.local_workspace_run.write', side_effect=fail):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(read(self.root/'owned-bcs.json')['status'], 'STOPPED')
        self.assertTrue((self.root/'workspace-resources.sqlite').is_file())
        self.assertFalse((self.root/'workspace-api-report.json').exists())


if __name__ == '__main__':
    unittest.main()
