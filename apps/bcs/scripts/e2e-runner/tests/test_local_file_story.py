"""Real six-leaf CLI File acceptance and bounded failure retention."""
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
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.run_state import read, write
from bcs_e2e.local_file_story import CASE_ID, LEAVES, REQUIREMENTS, PAYLOAD, OwnedLocalFileStory
from bcs_e2e.local_file_reporting import build_file_report
from bcs_e2e.bounded_process import capture
from bcs_e2e.inventory import current_manifest

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


class LocalFileStoryAdmissionTests(unittest.TestCase):
    def test_offline_plan_exposes_local_parent_without_migration_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        item = next(v for v in plan['local_registered_executors'] if v['case_id']==CASE_ID)
        self.assertEqual(item['requirement_ids'], ['CLI-FILE.'+r for r in REQUIREMENTS])
        self.assertEqual(item['required_assertion_count'], 12)
        self.assertFalse(item['grants_release_credit'])
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertEqual(plan['passed_assertion_count'], 0)

    def test_deleted_share_observations_use_distinct_anonymous_routes(self):
        for operation, suffix in (('shared-deleted-metadata', '/sessions/shared-file'),
                                  ('shared-deleted-content', '/sessions/shared-file/content')):
            for status in (200, 404):
                with self.subTest(operation=operation, status=status):
                    response = io.BytesIO(b'{}')
                    response.code = status
                    opener = Mock()
                    opener.open.return_value = response
                    story = object.__new__(OwnedLocalFileStory)
                    story._active = Mock()
                    story._save = Mock()
                    story.session_id, story.file_id, story.capability = 'session', 'file', 'test-capability'
                    story.identities = SimpleNamespace(origin='http://127.0.0.1:12345', opener=opener)
                    story.state = {'http_observations': []}
                    self.assertEqual(story._http(operation), status == 404)
                    request = opener.open.call_args.args[0]
                    self.assertEqual(request.full_url, 'http://127.0.0.1:12345'+suffix+'?token=test-capability')
                    self.assertEqual(request.header_items(), [])
                    self.assertTrue(response.closed)
                    self.assertEqual(story.state['http_observations'][0]['http_status'], status)

    def test_missing_permission_does_not_start_any_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('bcs_e2e.local_file_story.OwnedBcsProcess') as runtime, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            rc = main(['local-file-run', '--state-dir', str(Path(tmp)/'run'), '--run-id', 'deny',
                '--confirm-run-id', 'deny', '--binary', '/missing/bcs', '--binary-sha256', 'a'*64,
                '--cli-binary', '/missing/cli', '--cli-binary-sha256', 'b'*64])
            self.assertEqual(rc, 2); runtime.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')


@unittest.skipUnless(BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
                     'approved BCS/CLI binaries required')
class LocalFileStoryLiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'runtime'
        self.args = ['local-file-run', '--state-dir', str(self.root), '--run-id', 'local-file-live',
            '--confirm-run-id', 'local-file-live', '--binary', BINARY, '--binary-sha256', DIGEST,
            '--cli-binary', CLI, '--cli-binary-sha256', CLI_DIGEST,
            '--allow-local-processes', '--allow-fixture-write', '--allow-write']

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(self.args)
        return rc, json.loads(out.getvalue())

    def closed(self):
        child = read(self.root/'owned-bcs.json')
        self.assertEqual((child['status'], child['returncode']), ('STOPPED', 0))
        for path in self.root.glob('*/bot/.bcs/session.json'):
            self.assertEqual(read(path)['token'], '')
        self.assertFalse(any('e2e-local' in t.name for t in threading.enumerate()))

    def test_actual_six_cli_leaves_and_all_twelve_parent_assertions(self):
        rc, result = self.execute()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'LOCAL_SUITE_PASS')
        case = read(self.root/'local-file-story.json')
        self.assertEqual([op['operation'] for op in case['operations']], list(LEAVES))
        self.assertEqual([a['id'] for a in case['assertions']], ['CLI-FILE.'+r for r in REQUIREMENTS])
        self.assertTrue(all(a['status']=='PASS' for a in case['assertions']))
        self.assertTrue(all(op['status']=='ACKNOWLEDGED' and op['credential_cleanup']=='CLEARED' for op in case['operations']))
        self.assertEqual(case['file_delete'], 'PASS')
        deleted = [item for item in case['http_observations'] if 'deleted' in item['operation']]
        self.assertEqual([(item['operation'], item['http_status'], item['anonymous']) for item in deleted],
            [('deleted-metadata', 404, False), ('shared-deleted-metadata', 404, True),
             ('shared-deleted-content', 404, True)])
        self.assertTrue(case['file_id'])
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_session_files').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_group_sessions').fetchone()[0], 2)
        with sqlite3.connect(self.root/'cli-file-resources.sqlite') as db:
            self.assertEqual(db.execute("SELECT state FROM resource WHERE kind='file'").fetchone()[0], 'deleted')
        summary = build_file_report(self.root)
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['reports']['expected_assertion_count'], 12)
        for path in (self.root/'reports').iterdir():
            contents = path.read_text()
            self.assertNotIn('share_token', contents)
            self.assertNotIn('Bearer ', contents)
            self.assertIn(CASE_ID, contents)
        self.closed()

    def test_upload_cli_nonzero_keeps_created_file_without_fallback_cleanup(self):
        def nonzero(*args, **kwargs):
            value = capture(*args, **kwargs)
            return subprocess.CompletedProcess(value.args, 1, value.stdout, b'synthetic nonzero')
        with patch('bcs_e2e.local_file_story.capture', side_effect=nonzero):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        case = read(self.root/'local-file-story.json')
        self.assertEqual(case['status'], 'FAIL'); self.assertEqual(len(case['operations']), 1)
        self.assertEqual(case['file_delete'], 'NOT_RUN')
        self.assertTrue(case['file_id'])
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_session_files').fetchone()[0], 1)
        self.assertEqual(build_file_report(self.root)['status'], 'FAIL')
        self.closed()

    def test_cancellation_after_upload_keeps_file_and_publishes_cancelled(self):
        def cancel(*args, **kwargs):
            capture(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch('bcs_e2e.local_file_story.capture', side_effect=cancel):
            rc, result = self.execute()
        self.assertEqual((rc, result['status']), (130, 'CANCELLED'), result)
        self.assertEqual(build_file_report(self.root)['status'], 'CANCELLED')
        case = read(self.root/'local-file-story.json'); self.assertEqual(case['file_delete'], 'NOT_RUN')
        self.assertEqual(len(case['operations']), 1)
        self.closed()

    def test_wrong_shared_bytes_fail_before_business_delete(self):
        original = OwnedLocalFileStory._http
        def corrupt(story, operation):
            value = original(story, operation)
            return b'wrong' if operation=='shared-content' else value
        with patch.object(OwnedLocalFileStory, '_http', corrupt):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        case = read(self.root/'local-file-story.json')
        self.assertEqual(case['assertions'][-1], {'id':'CLI-FILE.shared-content-bytes','status':'FAIL'})
        self.assertEqual([op['operation'] for op in case['operations']], list(LEAVES[:-1]))
        self.closed()

    def test_offline_reports_reject_missing_completion_pending_and_source_drift(self):
        rc, result = self.execute(); self.assertEqual(rc, 0, result)
        complete = (self.root/'file-completion.json').read_bytes()
        (self.root/'file-completion.json').unlink()
        self.assertEqual(build_file_report(self.root)['status'], 'BLOCKED')
        (self.root/'file-completion.json').write_bytes(complete)
        write(self.root/'file-publication-pending.json', {'schema_version':1})
        self.assertEqual(build_file_report(self.root)['status'], 'BLOCKED')
        (self.root/'file-publication-pending.json').unlink()
        case = read(self.root/'local-file-story.json'); case['assertions'].pop()
        write(self.root/'local-file-story.json', case)
        self.assertEqual(build_file_report(self.root)['status'], 'BLOCKED')
        self.closed()

    def test_completion_publication_failure_keeps_pending_and_never_reports_pass(self):
        import bcs_e2e.local_file_reporting as reporting
        original = reporting._exclusive
        def fail(path, value):
            if path.name=='file-completion.json': raise OSError('synthetic report disk failure')
            return original(path, value)
        with patch.object(reporting, '_exclusive', fail):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertTrue((self.root/'file-publication-pending.json').exists())
        self.assertEqual(build_file_report(self.root)['status'], 'BLOCKED')
        self.closed()

    def test_complete_failure_does_not_forward_product_cli_fallback_delete(self):
        from bcs_e2e.local_file_exchange import LocalFileExchange, LocalFileExchangeError
        original = LocalFileExchange._forward
        def fail_complete(exchange, phase, *args):
            if phase == 'complete':
                raise LocalFileExchangeError('synthetic lost complete response')
            return original(exchange, phase, *args)
        with patch.object(LocalFileExchange, '_forward', fail_complete):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        case = read(self.root/'local-file-story.json')
        self.assertEqual(len(case['operations']), 1)
        self.assertEqual(case['file_delete'], 'NOT_RUN')
        self.assertEqual([r['method'] for r in case['operations'][0]['wire']], ['POST', 'PUT'])
        with sqlite3.connect(self.root/'bcs.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bcs_session_files').fetchone()[0], 1)
        self.closed()

    def test_delete_failure_is_not_replaced_by_shutdown_success(self):
        from bcs_e2e.local_file_exchange import LocalFileExchange, LocalFileExchangeError
        original = LocalFileExchange._forward
        def fail_delete(exchange, phase, *args):
            if phase == 'delete':
                raise LocalFileExchangeError('synthetic delete failure')
            return original(exchange, phase, *args)
        with patch.object(LocalFileExchange, '_forward', fail_delete):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        case = read(self.root/'local-file-story.json')
        self.assertEqual(len(case['operations']), 6)
        self.assertEqual(case['status'], 'FAIL')
        self.assertNotEqual(case['file_delete'], 'PASS')
        self.assertEqual(build_file_report(self.root)['status'], 'FAIL')
        self.closed()

    def test_shared_metadata_still_accessible_after_delete_cannot_pass(self):
        original = OwnedLocalFileStory._http
        def stale_metadata(story, operation):
            if operation == 'shared-deleted-metadata':
                response = io.BytesIO(b'{"name":"source.txt"}')
                response.code = 200
                with patch.object(story.identities.opener, 'open', return_value=response):
                    return original(story, operation)
            return original(story, operation)
        with patch.object(OwnedLocalFileStory, '_http', stale_metadata):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        case = read(self.root/'local-file-story.json')
        self.assertEqual(case['assertions'][-1], {'id':'CLI-FILE.delete','status':'FAIL'})
        observed = {item['operation']: item['http_status'] for item in case['http_observations']}
        self.assertEqual(observed['shared-deleted-metadata'], 200)
        self.assertEqual(observed['shared-deleted-content'], 404)
        self.assertNotEqual(case['file_delete'], 'PASS')
        self.assertEqual(build_file_report(self.root)['status'], 'FAIL')
        self.closed()

    def test_missing_business_requirement_cannot_qualify(self):
        original = OwnedLocalFileStory._assert
        def omit(story, requirement, condition):
            if requirement == 'metadata': return
            return original(story, requirement, condition)
        with patch.object(OwnedLocalFileStory, '_assert', omit):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertNotEqual(build_file_report(self.root)['status'], 'LOCAL_SUITE_PASS')
        self.closed()

    def test_false_ledger_deletion_prevents_publication_pass(self):
        original = OwnedLocalFileStory.run
        def drift(story):
            value = original(story)
            with story._ledger() as ledger:
                ledger.db.execute("UPDATE resource SET state='live' WHERE kind='file'")
                ledger.db.commit()
            return value
        with patch.object(OwnedLocalFileStory, 'run', drift):
            rc, result = self.execute()
        self.assertEqual(rc, 2, result)
        self.assertEqual(build_file_report(self.root)['status'], 'BLOCKED')
        self.closed()


if __name__ == '__main__': unittest.main()
