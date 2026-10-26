"""Guards and opt-in real-runtime tests for the owned workspace API slice."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_workspace_api import (
    CASE_ID,
    MAX_WRITE_OPERATIONS,
    WRITE_SEQUENCE,
    OBLIGATIONS,
    REMAINING_PARENT_OBLIGATIONS,
    OwnedLocalWorkspaceApi,
    workspace_source_binding,
)
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import RunStateError, read, write

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


class LocalWorkspaceApiGuardTests(unittest.TestCase):
    def test_contract_is_the_exact_first_seven_parent_obligations(self):
        binding = workspace_source_binding()
        self.assertEqual(CASE_ID, 'LOCAL-WORKSPACE-API-01')
        self.assertEqual(list(OBLIGATIONS), binding['covered_obligations'])
        self.assertEqual(list(REMAINING_PARENT_OBLIGATIONS), binding['remaining_obligations'])
        self.assertEqual(len(OBLIGATIONS), 7)
        self.assertEqual(len(REMAINING_PARENT_OBLIGATIONS), 11)
        self.assertEqual(MAX_WRITE_OPERATIONS, 10)

    def test_constructor_rejects_unmanaged_or_wrong_fixture_identity(self):
        with self.assertRaisesRegex(RunStateError, 'workspace API requires'):
            OwnedLocalWorkspaceApi(object())

    def test_foreign_group_or_wrong_payload_rejected_before_identity_or_http(self):
        case = object.__new__(OwnedLocalWorkspaceApi)
        case._active = Mock()
        case._id = lambda role: 'bot-' + role
        case.state = {'operations': [{'operation': name} for name in WRITE_SEQUENCE[:7]],
                      'resources': {'group-create-primary': {'id': 'owned', 'handle': 'handle'}}}
        case.ledger = Mock()
        case.ledger._get.return_value = {'resource_id': 'owned', 'origin': 'created', 'state': 'live',
                                         'kind': 'group', 'case_id': CASE_ID, 'attempt': 1}
        case._admit_write = Mock(side_effect=AssertionError('no identity read'))
        for path, body in [('/groups/foreign/members/bot-PM', {}),
                           ('/groups/owned/members/bot-ENG', {}),
                           ('/groups/owned/members/bot-PM', {'extra': True})]:
            with self.subTest(path=path, body=body), self.assertRaises(RunStateError):
                case._write('group-member-remove', 'DELETE', path, body)
        case._admit_write.assert_not_called()
        self.assertEqual(len(case.state['operations']), 7)

    def test_repeated_creation_rejected_before_new_ledger_intent(self):
        case = object.__new__(OwnedLocalWorkspaceApi)
        case._active = Mock()
        case.state = {'operations': [{'operation': name} for name in WRITE_SEQUENCE[:4]]}
        case.ledger = Mock()
        with self.assertRaises(RunStateError):
            case._create_group('group-create-primary', ('CEO',))
        case.ledger.group_creation_intent.assert_not_called()

    def test_adapter_has_no_remote_profile_shell_or_group_cleanup_surface(self):
        source = (ROOT / 'bcs_e2e' / 'local_workspace_api.py').read_text()
        for forbidden in ('subprocess', 'os.system', 'shell=True', 'Cookie', '/openapi/',
                          'api_delete', 'cleanup_plan(', 'mark_deleted('):
            self.assertNotIn(forbidden, source)
        self.assertIn("'raw_remote_profile': 'UNSUPPORTED'", source)
        self.assertIn("'cleanup': 'NOT_RUN'", source)


@unittest.skipUnless(
    BINARY and DIGEST and shutil.which('lsof'),
    'approved actual BCS binary required',
)
class LocalWorkspaceApiLiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'runtime'

    def execute(self, hook=None):
        case = None
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                             root=self.root, run_id='workspace-api-live') as runtime:
            fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                case = OwnedLocalWorkspaceApi(identities)
                if hook is None:
                    result = case.run()
                else:
                    result = hook(case)
        return result, read(self.root / 'owned-bcs.json')

    def ledger_rows(self):
        with sqlite3.connect(self.root / 'workspace-resources.sqlite') as database:
            database.row_factory = sqlite3.Row
            return [dict(row) for row in database.execute(
                'SELECT kind,parent,request_id,origin,resource_id,state FROM resource ORDER BY rowid')]

    def test_real_first_seven_obligations_pass_with_exact_retained_resources(self):
        result, runtime = self.execute()
        self.assertEqual(result['status'], 'LOCAL_WORKSPACE_API_PASS')
        self.assertEqual(result['case_id'], CASE_ID)
        self.assertEqual(result['assertions'], [
            {'id': obligation, 'status': 'PASS'} for obligation in OBLIGATIONS])
        self.assertEqual(result['remaining_parent_obligations'], {
            'status': 'NOT_RUN', 'ids': list(REMAINING_PARENT_OBLIGATIONS)})
        self.assertEqual(result['full_story_status'], 'NOT_MIGRATED')
        self.assertEqual(result['resource_disposition'], 'RETAINED_LOCAL_DATABASE')
        self.assertEqual(result['fixture_visibility']['restoration'], 'NOT_PERFORMED')
        self.assertEqual([row['role'] for row in result['fixture_visibility']['actors']], ['CEO', 'PM', 'ENG'])
        self.assertEqual(len(result['operations']), MAX_WRITE_OPERATIONS)
        self.assertTrue(all(row['status'] == 'ACKNOWLEDGED' for row in result['operations']))
        self.assertEqual(len({row['submitted_request_id'] for row in result['operations']}),
                         MAX_WRITE_OPERATIONS)
        self.assertEqual(runtime['status'], 'STOPPED')
        self.assertEqual(runtime['returncode'], 0)
        rows = self.ledger_rows()
        self.assertEqual([row['kind'] for row in rows], ['group', 'session', 'group', 'session'])
        self.assertTrue(all(row['origin'] == 'created' and row['state'] == 'live' for row in rows))
        journal = read(self.root / 'local-workspace-api.json')
        self.assertEqual(rows[1]['parent'], journal['resources']['group-create-primary']['handle'])
        self.assertEqual(rows[3]['parent'], journal['resources']['group-create-with-members']['handle'])
        self.assertEqual(result['actor_contract'], {
            'legacy_helpers': 'BUILT_IN_LOCAL_HUMAN',
            'label_mutation': 'OWNED_DRIVER_BOT',
            'mock_headers': False,
            'local_mock_auth': True,
        })
        label = next(row for row in result['operations'] if row['operation'] == 'group-label')
        self.assertEqual(label['actor_contract'], 'OWNED_LOCAL_BOT')
        self.assertTrue(all(row['actor_contract'] == 'BUILT_IN_LOCAL_HUMAN'
                            for row in result['operations']
                            if row['operation'].startswith('group-') and row['operation'] != 'group-label'))
        with sqlite3.connect(self.root / 'bcs.sqlite') as database:
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0], 2)
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM bcs_group_sessions WHERE env='local'").fetchone()[0], 2)
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM bcs_group_participants WHERE env='local'").fetchone()[0], 4)

    def test_committed_create_with_lost_response_is_unknown_and_not_retried(self):
        def hook(case):
            original = case.identities.opener.open
            posts = []

            def lose(request, **options):
                if request.get_method() == 'POST' and request.full_url.endswith('/groups'):
                    posts.append(request.full_url)
                    response = original(request, **options)
                    response.close()
                    raise URLError('private-lost-create-response')
                return original(request, **options)

            with patch.object(case.identities.opener, 'open', side_effect=lose):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)

        journal, runtime = self.execute(hook)
        self.assertEqual(len([item for item in journal['operations']
                             if item['operation'] == 'group-create-primary']), 1)
        create = next(item for item in journal['operations']
                      if item['operation'] == 'group-create-primary')
        self.assertEqual(create['status'], 'UNKNOWN')
        self.assertEqual(create['resource_binding'], 'UNKNOWN')
        self.assertNotIn('private-lost-create-response', json.dumps(journal))
        self.assertEqual(runtime['status'], 'STOPPED')
        with sqlite3.connect(self.root / 'bcs.sqlite') as database:
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0], 1)
        rows = self.ledger_rows()
        self.assertEqual([row['state'] for row in rows], ['unknown', 'unknown'])
        self.assertTrue(all(row['resource_id'] is None for row in rows))

    def test_label_401_is_blocked_not_counted_as_pass(self):
        def hook(case):
            original = case.identities.opener.open

            def deny(request, **options):
                if request.get_method() == 'PUT' and request.full_url.endswith('/label'):
                    raise HTTPError(request.full_url, 401, 'unauthorized', {}, None)
                return original(request, **options)

            with patch.object(case.identities.opener, 'open', side_effect=deny):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)

        journal, runtime = self.execute(hook)
        label = next(item for item in journal['operations'] if item['operation'] == 'group-label')
        self.assertEqual(label['http_status'], 401)
        self.assertEqual(label['status'], 'UNKNOWN')
        self.assertNotIn({'id': 'WORKSPACE.label', 'status': 'PASS'}, journal['assertions'])
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertEqual(runtime['status'], 'STOPPED')

    def test_wrong_added_member_stops_before_remove_label_or_visibility(self):
        original = OwnedLocalWorkspaceApi._read
        def hook(case):
            def corrupt(adapter, path, **kwargs):
                value = original(adapter, path, **kwargs)
                if adapter.state['operations'][-1]['operation'] == 'group-member-add':
                    value['participants'] = [{'bot_uuid': adapter._id('CEO')},
                                             {'bot_uuid': adapter._id('ENG')}]
                return value
            with patch.object(OwnedLocalWorkspaceApi, '_read', corrupt):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)
        journal, runtime = self.execute(hook)
        self.assertEqual(journal['assertions'][-1], {'id': 'WORKSPACE.members', 'status': 'FAIL'})
        self.assertEqual(journal['operations'][-1]['operation'], 'group-member-add')
        self.assertEqual(runtime['status'], 'STOPPED')

    def test_intent_journal_failure_prevents_first_group_dispatch(self):
        original = write

        def hook(case):
            def fail(path, value):
                if (path == case.path and value.get('operations')
                        and value['operations'][-1]['operation'] == 'group-create-primary'
                        and value['operations'][-1]['status'] == 'INTENDED'):
                    raise OSError('private-workspace-journal-failure')
                return original(path, value)

            with patch('bcs_e2e.local_workspace_api.write', side_effect=fail):
                with self.assertRaises(RunStateError):
                    case.run()
            return read(case.path)

        journal, runtime = self.execute(hook)
        self.assertEqual([item['operation'] for item in journal['operations']], [
            'fixture-ceo-visibility-public', 'fixture-pm-visibility-public', 'fixture-eng-visibility-public'])
        self.assertNotIn('private-workspace-journal-failure', json.dumps(journal))
        self.assertEqual(runtime['status'], 'STOPPED')
        with sqlite3.connect(self.root / 'bcs.sqlite') as database:
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0], 0)
        self.assertEqual([row['state'] for row in self.ledger_rows()], ['intended', 'intended'])


if __name__ == '__main__':
    unittest.main()
