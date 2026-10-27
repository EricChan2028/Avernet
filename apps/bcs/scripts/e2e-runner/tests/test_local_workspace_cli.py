"""Guards and opt-in actual-runtime tests for the Workspace CLI increment."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.bounded_process import capture
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_workspace_api import OwnedLocalWorkspaceApi
from bcs_e2e.local_workspace_cli import (
    CASE_ID,
    COMMAND_SEQUENCE,
    OBLIGATIONS,
    QUESTION,
    OwnedLocalWorkspaceCli,
)
from bcs_e2e.local_workspace_context import prepare_workspace_context
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import RunStateError, read, write

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


class LocalWorkspaceCliGuardTests(unittest.TestCase):
    def case(self):
        case = object.__new__(OwnedLocalWorkspaceCli)
        case._active = Mock()
        case._id = lambda role: {'PM': 'pm-id', 'ENG': 'eng-id', 'QA': 'qa-id', 'CEO': 'ceo-id'}[role]
        case.state = {'operations': [], 'resources': {}}
        case.ledger = Mock()
        case._admit_pm = Mock(side_effect=AssertionError('credential admission must not run'))
        return case

    def test_contract_is_exact_four_ordered_parent_obligations(self):
        self.assertEqual(CASE_ID, 'LOCAL-WORKSPACE-CLI-01')
        self.assertEqual(OBLIGATIONS, (
            'WORKSPACE.cli-create-get', 'WORKSPACE.cli-add-member',
            'WORKSPACE.cli-fuse', 'WORKSPACE.cli-status'))
        self.assertEqual(COMMAND_SEQUENCE, (
            'create-primary', 'get-primary', 'add-member', 'get-after-add', 'fuse',
            'status-primary', 'get-after-status', 'create-termination',
            'get-before-terminate', 'terminate', 'get-termination'))
        self.assertEqual(QUESTION, 'how to align?')

    def test_constructor_rejects_unmanaged_identity_before_binary_use(self):
        with self.assertRaisesRegex(RunStateError, 'Workspace CLI requires'):
            OwnedLocalWorkspaceCli(object(), Path('/not/a/cli'), 'a' * 64)

    def test_foreign_create_body_is_rejected_before_ledger_journal_or_identity(self):
        case = self.case()
        expected = case._expected('create-primary')
        body = dict(expected[2])
        body['participants'] = [{'bot_uuid': 'foreign', 'role': None}]
        with self.assertRaisesRegex(RunStateError, 'finite ordered'):
            case._invoke('create-primary', expected[0], expected[1], body,
                         expected[3], expected[4], expected[5])
        case.ledger.group_creation_intent.assert_not_called()
        case._admit_pm.assert_not_called()
        self.assertEqual(case.state['operations'], [])

    def test_duplicate_create_is_rejected_before_second_ledger_intent(self):
        case = self.case()
        expected = case._expected('create-primary')
        case.state['operations'] = [{'operation': 'create-primary'}]
        with self.assertRaisesRegex(RunStateError, 'finite ordered'):
            case._invoke('create-primary', *expected)
        case.ledger.group_creation_intent.assert_not_called()
        case._admit_pm.assert_not_called()
        self.assertEqual(len(case.state['operations']), 1)

    def test_fuse_plan_is_exact_local_no_auth_wire(self):
        case = self.case()
        case.state['operations'] = [{'operation': name} for name in COMMAND_SEQUENCE[:4]]
        case.state['resources']['primary'] = {'id': 'group_owned'}
        method, path, body, args, status, auth = case._expected('fuse')
        self.assertEqual((method, path, status, auth),
                         ('POST', '/groups/group_owned/fuse', 200, 'fuse-no-auth'))
        self.assertEqual(body, {
            'question': QUESTION, 'participants': ['pm-id', 'eng-id'],
            'focus': None, 'session_id': 'group_owned', 'fusion_mode': None})
        self.assertEqual(args, ['fuse', '--group', 'group_owned', '--question', QUESTION,
                                '--participants', 'pm-id,eng-id'])

    def test_group_get_wire_credential_stays_empty_and_clears_after(self):
        case = self.case()
        case._admit_pm = Mock(return_value=('pm-id', 'owned-pm-token'))
        case._save = Mock()
        case.identities = SimpleNamespace(origin='http://127.0.0.1:1')
        case.runtime = SimpleNamespace(root=Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, case.runtime.root, ignore_errors=True)
        case.state['operations'] = [{'operation': 'create-primary'}]
        case.state['resources']['primary'] = {
            'id': 'group_owned', 'handle': 'h',
            'initial_session': {'id': 'group_owned:1', 'handle': 's'}}
        case.binary = Path('/loopback/fake/bcs-cli')
        case.ledger._get.return_value = {
            'kind': 'group', 'origin': 'created', 'state': 'live',
            'case_id': CASE_ID, 'attempt': 1, 'resource_id': 'group_owned'}
        reply = {'id': 'group_owned', 'driver_bot': 'pm-id', 'status': 'active',
                 'participants': [{'bot_uuid': 'pm-id'}, {'bot_uuid': 'eng-id'}]}
        pegged = []

        class FakeExchange:
            def __init__(self, cli_case, event, *, token, body, auth_contract):
                self.case, self.event = cli_case, event
                self.origin = 'http://127.0.0.1:1'
                self.failure = None
                self.attempts = 1
                self.forwarded = 1
                self.response = reply

            def __enter__(self):
                self.event['http_status'] = 200
                return self

            def __exit__(self, *_exc_info):
                return False

        def fake_capture(argv, *, timeout_seconds, max_output_bytes,
                         environment=None, cwd=None, collect_stderr=False):
            pegged.append(json.loads(
                (Path(cwd) / 'bot' / '.bcs' / 'session.json').read_text()))
            return subprocess.CompletedProcess(
                [], 0, json.dumps(reply).encode(), b'')

        with patch('bcs_e2e.local_workspace_cli.LocalCliExchange', FakeExchange), \
             patch('bcs_e2e.local_workspace_cli.capture', side_effect=fake_capture):
            observed = case._invoke('get-primary', *case._expected('get-primary'))

        self.assertEqual(observed, reply)
        # The legacy no-auth Group read must go out with an empty wire token:
        # the product CLI auto-discovers session.json and would otherwise send
        # a Bearer the exchange admission correctly rejects.
        self.assertEqual(pegged, [{'schema_version': 1, 'token': '',
                                   'bot_uuid': 'pm-id',
                                   'api_base_url': 'http://127.0.0.1:1'}])
        sessions = list(case.runtime.root.glob('workspace-cli-command-*/bot/.bcs/session.json'))
        self.assertEqual(len(sessions), 1)
        self.assertEqual(json.loads(sessions[0].read_text()),
                         {'schema_version': 1, 'token': '',
                          'api_base_url': 'http://127.0.0.1:1'})
        operation = case.state['operations'][-1]
        self.assertEqual(operation['status'], 'ACKNOWLEDGED')
        self.assertEqual(operation['credential_cleanup'], 'CLEARED')
        self.assertEqual(operation['wire_authentication'], 'NONE_LEGACY_LOCAL_GROUP_GET')

    def test_adapter_has_no_remote_cookie_delete_or_broad_stop_surface(self):
        source = (ROOT/'bcs_e2e'/'local_workspace_cli.py').read_text()
        for forbidden in ('Cookie', '/openapi/', 'DELETE', 'mark_deleted(', 'cleanup_plan(',
                          'os.system', 'shell=True', 'killpg', 'pkill'):
            self.assertNotIn(forbidden, source)
        self.assertIn("'group_delete': 'NOT_RUN'", source)
        self.assertIn("'session_delete': 'NOT_RUN'", source)
        self.assertIn("'remote_or_gateway_authentication': 'NOT_PROVEN'", source)


@unittest.skipUnless(
    BINARY and DIGEST and CLI and CLI_DIGEST and shutil.which('lsof'),
    'approved actual BCS and CLI binaries required',
)
class LocalWorkspaceCliLiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'runtime'

    def execute(self, hook=None):
        case = None
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                             root=self.root, run_id='workspace-cli-live') as runtime:
            fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                api = OwnedLocalWorkspaceApi(identities).run()
                self.assertEqual(api['status'], 'LOCAL_WORKSPACE_API_PASS')
                prepare_workspace_context(identities)
                case = OwnedLocalWorkspaceCli(identities, Path(CLI), CLI_DIGEST)
                result = case.run() if hook is None else hook(case)
        return result, read(self.root/'owned-bcs.json')

    def ledger_rows(self):
        with sqlite3.connect(self.root/'workspace-cli-resources.sqlite') as database:
            database.row_factory = sqlite3.Row
            return [dict(row) for row in database.execute(
                'SELECT kind,parent,origin,resource_id,state FROM resource ORDER BY rowid')]

    def counts(self):
        with sqlite3.connect(self.root/'bcs.sqlite') as database:
            return (
                database.execute("SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0],
                database.execute("SELECT COUNT(*) FROM bcs_group_sessions WHERE env='local'").fetchone()[0],
            )

    def assert_contexts_cleared(self):
        contexts = list(self.root.glob('workspace-cli-command-*/bot/.bcs/session.json'))
        operations = read(self.root/'local-workspace-cli.json')['operations']
        self.assertEqual(len(contexts), len(operations))
        self.assertTrue(all(read(path)['token'] == '' for path in contexts))
        self.assertFalse(any(thread.name == 'e2e-local-cli-exchange'
                             for thread in threading.enumerate()))

    def test_actual_cli_passes_all_four_obligations_and_retains_exact_resources(self):
        result, runtime = self.execute()
        self.assertEqual(result, read(self.root/'local-workspace-cli.json'))
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_CLI_PASS')
        self.assertEqual(result['case_id'], CASE_ID)
        self.assertEqual(result['assertions'], [
            {'id': obligation, 'status': 'PASS'} for obligation in OBLIGATIONS])
        self.assertEqual([row['operation'] for row in result['operations']], list(COMMAND_SEQUENCE))
        self.assertTrue(all(row['status'] == 'ACKNOWLEDGED'
                            and row['credential_cleanup'] == 'CLEARED'
                            for row in result['operations']))
        fuse = next(row for row in result['operations'] if row['operation'] == 'fuse')
        self.assertEqual(fuse['wire_authentication'], 'NONE_LEGACY_LOCAL_FUSE')
        self.assertEqual(fuse['actor_contract'], 'OWNED_LOCAL_PM_CONTEXT_NO_WIRE_IDENTITY')
        self.assertEqual(fuse['auth_contract'], 'owned-local-legacy-fuse-no-auth-v1')
        self.assertEqual(result['qa_visibility']['original'], 'protected')
        self.assertEqual(result['qa_visibility']['retained'], 'public')
        self.assertEqual(result['qa_visibility']['restoration'], 'NOT_PERFORMED')
        self.assertEqual(result['storage_verification'], {
            'status': 'PASS', 'groups': 2, 'default_initial_sessions': 2,
            'group_status': 'completed', 'session_status': 'running', 'ledger_resources': 4})
        rows = self.ledger_rows()
        self.assertEqual([row['kind'] for row in rows], ['group', 'session', 'group', 'session'])
        self.assertTrue(all(row['origin'] == 'created' and row['state'] == 'live' for row in rows))
        self.assertEqual(self.counts(), (4, 4))  # two prior API Groups plus two CLI Groups
        self.assertEqual((runtime['status'], runtime['returncode']), ('STOPPED', 0))
        self.assert_contexts_cleared()

    def test_committed_first_create_with_lost_cli_output_is_unknown_and_not_retried(self):
        calls = []

        def hook(case):
            def lose(*args, **kwargs):
                result = capture(*args, **kwargs)
                calls.append(list(result.args))
                return subprocess.CompletedProcess(result.args, 0, b'invalid-create-output', b'')
            with patch('bcs_e2e.local_workspace_cli.capture', side_effect=lose):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)

        journal, runtime = self.execute(hook)
        self.assertEqual(len(calls), 1)
        self.assertEqual([row['operation'] for row in journal['operations']], ['create-primary'])
        self.assertEqual(journal['operations'][0]['status'], 'UNKNOWN')
        self.assertEqual(journal['operations'][0]['resource_binding'], 'UNKNOWN')
        self.assertEqual(journal['resources'], {})
        self.assertEqual([row['state'] for row in self.ledger_rows()], ['unknown', 'unknown'])
        self.assertEqual(self.counts(), (3, 3))
        self.assertEqual((runtime['status'], runtime['returncode']), ('STOPPED', 0))
        self.assert_contexts_cleared()

    def test_context_write_committed_then_failed_still_clears_secret_without_dispatch(self):
        def hook(case):
            def fail(path, value):
                write(path, value)
                if path.name == 'session.json' and value.get('token'):
                    raise OSError('synthetic post-write failure')
            with patch('bcs_e2e.local_workspace_cli.write', side_effect=fail),                  patch('bcs_e2e.local_workspace_cli.capture') as child:
                with self.assertRaises(RunStateError):
                    case.run()
                child.assert_not_called()
            return read(case.path)
        journal, runtime = self.execute(hook)
        self.assertEqual(len(journal['operations']), 1)
        self.assertEqual(journal['operations'][0]['credential_cleanup'], 'CLEARED')
        self.assertEqual(self.counts(), (2, 2))
        self.assertEqual(runtime['status'], 'STOPPED')
        self.assert_contexts_cleared()

    def test_missing_context_marker_fails_fusion_not_just_exit_or_json_shape(self):
        original = OwnedLocalWorkspaceCli._accept_output
        def hook(case):
            def corrupt(adapter, operation, reply, output, handles, request_id):
                if operation == 'fuse':
                    reply = dict(reply, perspectives=[])
                    output = 'Fusion result:\n' + json.dumps(reply)
                return original(adapter, operation, reply, output, handles, request_id)
            with patch.object(OwnedLocalWorkspaceCli, '_accept_output', corrupt):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)
        journal, runtime = self.execute(hook)
        self.assertEqual(journal['operations'][-1]['operation'], 'fuse')
        self.assertNotIn({'id':'WORKSPACE.cli-fuse', 'status':'PASS'}, journal['assertions'])
        self.assertEqual(runtime['status'], 'STOPPED')
        self.assert_contexts_cleared()

    def test_invalid_fuse_stdout_cannot_pass_or_continue_to_status(self):
        def hook(case):
            def corrupt(*args, **kwargs):
                result = capture(*args, **kwargs)
                if 'fuse' in result.args:
                    return subprocess.CompletedProcess(result.args, 0, b'{}\n', b'')
                return result
            with patch('bcs_e2e.local_workspace_cli.capture', side_effect=corrupt):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)

        journal, runtime = self.execute(hook)
        self.assertEqual([row['operation'] for row in journal['operations']],
                         list(COMMAND_SEQUENCE[:5]))
        self.assertEqual(journal['operations'][-1]['operation'], 'fuse')
        self.assertEqual(journal['operations'][-1]['status'], 'UNKNOWN')
        self.assertNotIn({'id': 'WORKSPACE.cli-fuse', 'status': 'PASS'}, journal['assertions'])
        self.assertEqual((runtime['status'], runtime['returncode']), ('STOPPED', 0))
        self.assert_contexts_cleared()


if __name__ == '__main__':
    unittest.main()
