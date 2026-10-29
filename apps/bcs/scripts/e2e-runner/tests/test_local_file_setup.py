"""Actual PM CLI setup for the unfinished local session-file Story."""
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
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.bounded_process import capture
from bcs_e2e.cli import main
from bcs_e2e.run_state import read

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


class LocalFileSetupAdmissionTests(unittest.TestCase):
    def test_missing_explicit_permission_blocks_before_binary_or_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'fresh'
            arguments = [
                'local-file-setup', '--state-dir', str(root),
                '--binary', '/not/a/binary', '--binary-sha256', 'a' * 64,
                '--cli-binary', '/not/a/cli', '--cli-binary-sha256', 'b' * 64,
                '--run-id', 'file-setup-admission', '--confirm-run-id', 'file-setup-admission',
                '--allow-local-processes', '--allow-fixture-write',
            ]
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(arguments), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertIn('business write permissions', result['reason'])
            self.assertFalse(root.exists())


@unittest.skipUnless(
    BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
    'approved product BCS and CLI binaries required',
)
class LocalFileSetupLiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'runtime'
        self.arguments = [
            'local-file-setup', '--state-dir', str(self.root),
            '--binary', BINARY, '--binary-sha256', DIGEST,
            '--cli-binary', CLI, '--cli-binary-sha256', CLI_DIGEST,
            '--run-id', 'local-file-setup-live',
            '--confirm-run-id', 'local-file-setup-live',
            '--allow-local-processes', '--allow-fixture-write', '--allow-write',
        ]

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            return main(self.arguments), json.loads(output.getvalue())

    def assert_stopped_and_cleared(self):
        runtime = read(self.root / 'owned-bcs.json')
        self.assertEqual((runtime['status'], runtime['returncode']), ('STOPPED', 0))
        contexts = list(self.root.glob('file-setup-cli-*/bot/.bcs/session.json'))
        self.assertTrue(contexts)
        for context in contexts:
            self.assertEqual(read(context)['token'], '')
            self.assertEqual(context.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any(thread.name == 'e2e-local-cli-exchange'
                             for thread in threading.enumerate()))

    def database_counts(self):
        with sqlite3.connect(self.root / 'bcs.sqlite') as database:
            return (
                database.execute("SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0],
                database.execute("SELECT COUNT(*) FROM bcs_group_sessions WHERE env='local'").fetchone()[0],
            )

    def ledger_rows(self):
        with sqlite3.connect(self.root / 'cli-file-resources.sqlite') as database:
            database.row_factory = sqlite3.Row
            return [dict(row) for row in database.execute(
                'SELECT handle,kind,parent,origin,resource_id,state FROM resource ORDER BY rowid')]

    def test_actual_cli_creates_exact_owned_group_and_two_sessions(self):
        rc, result = self.execute()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'LOCAL_CLI_FILE_SETUP_PASS')
        self.assertFalse(result['ready_to_execute'])
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertEqual(result['file_operations'], 'NOT_RUN')
        setup = result['setup']
        self.assertEqual(set(setup['resources']), {'group', 'initial_session', 'session'})
        self.assertEqual([item['http_status'] for item in setup['operations']], [200, 201])
        self.assertTrue(all(item['status'] == 'ACKNOWLEDGED'
                            and item['credential_cleanup'] == 'CLEARED'
                            for item in setup['operations']))
        rows = self.ledger_rows()
        self.assertEqual([row['kind'] for row in rows], ['group', 'session', 'session'])
        self.assertTrue(all(row['origin'] == 'created' and row['state'] == 'live' for row in rows))
        self.assertTrue(all(row['parent'] == rows[0]['handle'] for row in rows[1:]))
        self.assertEqual(self.database_counts(), (1, 2))
        with sqlite3.connect(self.root / 'bcs.sqlite') as database:
            group_id, driver, originator, status = database.execute(
                "SELECT group_id,driver_bot,originator,status FROM bcs_groups WHERE env='local'").fetchone()
            sessions = database.execute(
                "SELECT session_id,group_id,status,session_title FROM bcs_group_sessions "
                "WHERE env='local' ORDER BY rowid").fetchall()
        self.assertEqual(driver, originator)
        self.assertEqual(status, 'active')
        self.assertEqual({row[0] for row in sessions}, {
            setup['resources']['initial_session']['id'], setup['resources']['session']['id']})
        self.assertTrue(all(row[1] == group_id and row[2] == 'running' for row in sessions))
        self.assertEqual(sessions[1][3], 'e2e-cli-file-workspace')
        self.assert_stopped_and_cleared()

    def test_committed_group_with_lost_cli_output_is_not_retried_or_adopted(self):
        calls = []

        def lose_output(*args, **kwargs):
            result = capture(*args, **kwargs)
            calls.append(list(result.args))
            if 'create-group' in result.args:
                return subprocess.CompletedProcess(result.args, 0, b'invalid-group-output', b'')
            return result

        with patch('bcs_e2e.local_file_setup.capture', side_effect=lose_output):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(len(calls), 1)
        journal = read(self.root / 'cli-file-setup.json')
        self.assertEqual([item['operation'] for item in journal['operations']], ['group-create'])
        self.assertEqual(journal['operations'][0]['status'], 'UNKNOWN')
        self.assertEqual(journal['operations'][0]['resource_binding'], 'UNKNOWN')
        self.assertEqual(journal['resources'], {})
        self.assertEqual(self.database_counts(), (1, 1))
        rows = self.ledger_rows()
        self.assertEqual([row['kind'] for row in rows], ['group', 'session'])
        self.assertTrue(all(row['origin'] == 'unknown' and row['state'] == 'intended'
                            and row['resource_id'] is None for row in rows))
        self.assert_stopped_and_cleared()

    def test_committed_explicit_session_with_lost_cli_output_is_not_retried_or_adopted(self):
        calls = []

        def lose_output(*args, **kwargs):
            result = capture(*args, **kwargs)
            calls.append(list(result.args))
            if 'session' in result.args and 'create' in result.args:
                return subprocess.CompletedProcess(result.args, 0, b'invalid-session-json', b'')
            return result

        with patch('bcs_e2e.local_file_setup.capture', side_effect=lose_output):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(len(calls), 2)
        journal = read(self.root / 'cli-file-setup.json')
        self.assertEqual([item['operation'] for item in journal['operations']],
                         ['group-create', 'session-create'])
        self.assertEqual(journal['operations'][0]['status'], 'ACKNOWLEDGED')
        self.assertEqual(journal['operations'][1]['status'], 'UNKNOWN')
        self.assertEqual(journal['operations'][1]['resource_binding'], 'UNKNOWN')
        self.assertNotIn('session', journal['resources'])
        self.assertEqual(self.database_counts(), (1, 2))
        rows = self.ledger_rows()
        self.assertEqual([row['state'] for row in rows], ['live', 'live', 'intended'])
        self.assertEqual(rows[-1]['origin'], 'unknown')
        self.assertIsNone(rows[-1]['resource_id'])
        self.assert_stopped_and_cleared()

    def test_cancellation_after_committed_group_retains_unknown_ids_and_clears_context(self):
        def cancel_after_create(*args, **kwargs):
            capture(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch('bcs_e2e.local_file_setup.capture', side_effect=cancel_after_create):
            rc, result = self.execute()
        self.assertEqual((rc, result['status']), (130, 'CANCELLED'))
        journal = read(self.root / 'cli-file-setup.json')
        self.assertEqual(journal['status'], 'CANCELLED')
        self.assertEqual(len(journal['operations']), 1)
        self.assertEqual(journal['operations'][0]['status'], 'UNKNOWN')
        self.assertEqual(self.database_counts(), (1, 1))
        self.assertTrue(all(row['resource_id'] is None for row in self.ledger_rows()))
        self.assert_stopped_and_cleared()

    def test_participant_admission_failure_never_starts_product_cli(self):
        from bcs_e2e.local_file_setup import OwnedLocalFileSetup
        from bcs_e2e.run_state import RunStateError
        with patch.object(OwnedLocalFileSetup, '_admit_participant',
                          side_effect=RunStateError('synthetic failed prerequisite')), \
             patch('bcs_e2e.local_file_setup.capture') as cli:
            rc, result = self.execute()
        self.assertEqual((rc, result['status']), (2, 'BLOCKED'))
        cli.assert_not_called()
        self.assertEqual(self.database_counts(), (0, 0))
        self.assertEqual(read(self.root / 'owned-bcs.json')['status'], 'STOPPED')
        self.assertEqual(self.ledger_rows(), [])

    def test_reused_state_directory_does_not_overwrite_old_success_or_start_child(self):
        rc, _ = self.execute(); self.assertEqual(rc, 0)
        before = {str(p.relative_to(self.root)):p.read_bytes()
                  for p in self.root.rglob('*') if p.is_file()}
        with patch('bcs_e2e.local_file_setup.OwnedBcsProcess') as runtime:
            rc, result = self.execute()
        self.assertEqual((rc, result['status']), (2, 'BLOCKED'))
        runtime.assert_not_called()
        self.assertEqual(before, {str(p.relative_to(self.root)):p.read_bytes()
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_nonzero_cli_output_does_not_adopt_committed_group(self):
        def wrong_exit(*args, **kwargs):
            result = capture(*args, **kwargs)
            return subprocess.CompletedProcess(result.args, 1, result.stdout, b'synthetic exit')
        with patch('bcs_e2e.local_file_setup.capture', side_effect=wrong_exit):
            rc, result = self.execute()
        self.assertEqual((rc, result['status']), (2, 'BLOCKED'))
        self.assertEqual(self.database_counts(), (1, 1))
        self.assertEqual(read(self.root / 'cli-file-setup.json')['resources'], {})
        self.assertTrue(all(row['resource_id'] is None for row in self.ledger_rows()))
        self.assert_stopped_and_cleared()

    def test_case_can_be_reused_as_setup_within_caller_owned_live_runtime(self):
        from bcs_e2e.local_file_setup import OwnedLocalFileSetup
        from bcs_e2e.owned_bcs import OwnedBcsProcess
        from bcs_e2e.local_fixtures import OwnedLocalFixtures
        from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                             root=self.root, run_id='file-setup-reuse') as runtime:
            fixtures = OwnedLocalFixtures(runtime, fixture_set='cli-file')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles: identities.verify(role)
                setup = OwnedLocalFileSetup(identities, Path(CLI), CLI_DIGEST)
                result = setup.run()
                self.assertEqual(result['status'], 'LOCAL_CLI_FILE_SETUP_PASS')
                self.assertEqual(runtime.state, 'RUNNING')
                self.assertIsNone(runtime.child.poll())
                self.assertEqual(setup._read('session')['session_id'], result['resources']['session']['id'])
        self.assert_stopped_and_cleared()


if __name__ == '__main__':
    unittest.main()
