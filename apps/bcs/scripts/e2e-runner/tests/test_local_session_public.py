"""Safety guards and opt-in live coverage for the owned-local Session helper."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.inventory import current_manifest, executor_digest
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_session_contract import (
    PUBLIC_CASE as CASE_ID, PUBLIC_SUITE as SUITE, REQUIREMENTS, session_source_binding,
)
from bcs_e2e.local_session_public import (
    BINDING_NAME, CONTRACT, JOURNAL_NAME, LEDGER_NAME, OPERATIONS,
    LocalSessionPublicError, OwnedLocalSessionPublic, ROLES,
)
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import read, write
from bcs_e2e.ws_transport import WebSocketTransportError

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
BINARY_SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


def binding(runtime):
    return {
        'schema_version': 1, 'contract': CONTRACT, 'suite': SUITE,
        'case_id': CASE_ID, 'run_id': runtime.run_id,
        'executor_digest': executor_digest(),
        'bcs_binary_sha256': runtime.binary_sha256,
        'source_binding': session_source_binding(),
        'selected_requirement_ids': [REQUIREMENTS[0]],
        'parent_requirement_ids': list(REQUIREMENTS),
        'legacy_inventory': current_manifest()['legacy_inventory'],
        'runtime_dir': str(runtime.root.resolve()), 'store_messages': True,
    }


class LocalSessionPublicGuardTests(unittest.TestCase):
    def test_contract_selects_only_first_of_all_nine_parent_requirements(self):
        self.assertEqual(CASE_ID, 'LOCAL-SESSION-PUBLIC-01')
        self.assertEqual(SUITE, CONTRACT)
        self.assertEqual(REQUIREMENTS[0], 'SESSION.public-jwt-ws')
        self.assertEqual(len(REQUIREMENTS), 9)
        self.assertEqual(len(session_source_binding()['requirements']), 9)

    def test_constructor_rejects_unmanaged_identity(self):
        with self.assertRaisesRegex(LocalSessionPublicError, 'freshly verified'):
            OwnedLocalSessionPublic(object())

    def test_binding_is_exact_and_does_not_accept_extra_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = object.__new__(OwnedLocalSessionPublic)
            case.root = root
            case.binding_path = root / BINDING_NAME
            case.executor_digest = '1' * 64
            case.source_binding = {'source': 'fixed'}
            case.runtime = Mock(run_id='session-unit', binary_sha256='2' * 64)
            expected = {
                'schema_version': 1, 'contract': CONTRACT, 'suite': SUITE,
                'case_id': CASE_ID, 'run_id': 'session-unit', 'executor_digest': '1' * 64,
                'bcs_binary_sha256': '2' * 64, 'source_binding': {'source': 'fixed'},
                'selected_requirement_ids': [REQUIREMENTS[0]],
                'parent_requirement_ids': list(REQUIREMENTS),
                'legacy_inventory': [{'id': 'story_user_runs_and_shares_sessions'}],
                'runtime_dir': str(root.resolve()), 'store_messages': True,
            }
            with patch('bcs_e2e.local_session_public.current_manifest',
                       return_value={'legacy_inventory': expected['legacy_inventory']}):
                write(case.binding_path, expected)
                self.assertEqual(case._validated_binding(), expected)
                for field in expected:
                    changed = dict(expected)
                    changed.pop(field)
                    write(case.binding_path, changed)
                    with self.subTest(field=field), self.assertRaises(LocalSessionPublicError):
                        case._validated_binding()
                write(case.binding_path, {**expected, 'extra': True})
                with self.assertRaises(LocalSessionPublicError):
                    case._validated_binding()

    def make_plan_case(self):
        case = object.__new__(OwnedLocalSessionPublic)
        case._active = Mock()
        case.identities = Mock(human='owner')
        case.identities._entries = {
            'CEO': ('bot-ceo', 'token-ceo'), 'PM': ('bot-pm', 'token-pm'),
            'ENG': ('bot-eng', 'token-eng'), 'QA': ('bot-qa', 'token-qa')}
        case._group_handle = ''
        case._initial_session_handle = ''
        case._explicit_session_handle = ''
        case.state = {'resources': {}, 'operations': []}
        return case

    def test_missing_auth_and_unknown_create_are_fixed_adverse_requests(self):
        case = self.make_plan_case()
        missing = case._spec('groups-missing-principal')
        self.assertEqual(missing['expected_status'], 401)
        self.assertFalse(missing['auth'])
        self.assertEqual(missing['method'], 'GET')
        unknown = case._spec('groups-unknown-field')
        self.assertEqual(unknown['expected_status'], 400)
        self.assertTrue(unknown['auth'])
        self.assertEqual(unknown['body'], {
            'group_kind': 'dm', 'target_actor_id': 'bot-pm', 'originator': 'untrusted'})

    def test_wrong_dynamic_target_is_rejected_by_ledger_before_transport(self):
        case = self.make_plan_case()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'session-public-resources.sqlite'
            with Ledger(path, 'session-target-unit', 'a' * 64, create=True) as ledger:
                handle, initial = ledger.group_creation_intent(CASE_ID, 1, 'request-group')
                ledger.observed(handle, 'owned-group', origin='created')
                ledger.observed(initial, 'owned-group:initial', origin='created')
                case.ledger = ledger
                case._group_handle, case._initial_session_handle = handle, initial
                case.state['resources']['group'] = {
                    'id': 'foreign-group', 'handle': handle,
                    'initial_session_id': 'owned-group:initial', 'initial_session_handle': initial}
                with self.assertRaisesRegex(LocalSessionPublicError, 'exact owned Ledger resource'):
                    case._spec('group-detail')

    def test_group_deletion_path_does_not_require_a_session_path_selector(self):
        case = object.__new__(OwnedLocalSessionPublic)
        case._id = lambda role: {'PM': 'pm', 'ENG': 'eng'}[role]
        case._group_id = lambda: 'owned-group'
        case._session_id = Mock(side_effect=AssertionError('group operation has no Session selector'))
        for operation, status in (('group-delete', 200), ('group-deleted-404', 404)):
            spec = case._spec(operation)
            self.assertEqual(spec['path'], '/openapi/v1/collaboration/groups/owned-group')
            self.assertEqual(spec['expected_status'], status)
        case._session_id.assert_not_called()

    def test_page_membership_never_accepts_missing_required_keys(self):
        with self.assertRaises(LocalSessionPublicError):
            OwnedLocalSessionPublic._page_contains({'items': [{'session_id': 's'}], 'unexpected': 1}, 'session_id', 's')
        OwnedLocalSessionPublic._page_contains({'items': [{'session_id': 's'}], 'total': 1, 'offset': 0, 'limit': 20}, 'session_id', 's')

    def test_token_requires_no_store_and_never_returns_reportable_headers_or_url(self):
        data = {'token': 'private.compact.jwt', 'expires_at': 123}
        self.assertEqual(OwnedLocalSessionPublic._token_response(
            data, {'cache-control': 'no-store', 'pragma': 'no-cache'}), 'private.compact.jwt')
        for headers in ({'cache-control': 'private', 'pragma': 'no-cache'},
                        {'cache-control': 'no-store', 'pragma': None}):
            with self.subTest(headers=headers), self.assertRaises(LocalSessionPublicError):
                OwnedLocalSessionPublic._token_response(data, headers)

    def test_no_store_failure_precedes_websocket_and_all_deletes(self):
        case = object.__new__(OwnedLocalSessionPublic)
        case._connect_websocket = Mock()
        with self.assertRaisesRegex(LocalSessionPublicError, 'cache-safe'):
            token = case._token_response(
                {'token': 'private.compact.jwt', 'expires_at': 123},
                {'cache-control': 'private', 'pragma': 'no-cache'})
            case._connect_websocket(token)
        case._connect_websocket.assert_not_called()
        source = (ROOT / 'bcs_e2e' / 'local_session_public.py').read_text()
        self.assertLess(source.index("token = self._token_response"),
                        source.index('self._connect_websocket(token)'))
        self.assertLess(source.index('self._connect_websocket(token)'),
                        source.index("self._request('session-delete')"))

    def test_websocket_failure_is_terminal_and_does_not_reconnect(self):
        case = object.__new__(OwnedLocalSessionPublic)
        case.state = {'operations': [{'operation': name, 'status': 'ACKNOWLEDGED'}
                                     for name in OPERATIONS[:OPERATIONS.index('session-ws-connect')]],
                      'websocket': {}}
        case._connection_token = ''
        case._ws_connection = None
        case._ws_state = 'NOT_OPENED'
        case._invalidated = False
        case.identities = Mock(origin='http://127.0.0.1:23123')
        case._group_id = Mock(return_value='group-owned')
        case._session_id = Mock(return_value='session-owned')
        case._save = Mock()
        with patch('bcs_e2e.local_session_public.SessionWebSocketClient') as client:
            client.return_value.connect.side_effect = WebSocketTransportError('synthetic')
            with self.assertRaisesRegex(LocalSessionPublicError, 'no reconnect'):
                case._connect_websocket('private.compact.jwt')
        self.assertEqual(client.return_value.connect.call_count, 1)
        kwargs = client.return_value.connect.call_args.kwargs
        self.assertRegex(kwargs['upgrade_request_id'], r'^e2e-session-public-upgrade-[0-9a-f]{32}$')
        self.assertRegex(kwargs['connect_request_id'], r'^e2e-session-public-connect-[0-9a-f]{32}$')
        event = case.state['operations'][-1]
        self.assertEqual(event['submitted_request_id'], kwargs['upgrade_request_id'])
        self.assertEqual(event['connect_request_id'], kwargs['connect_request_id'])
        self.assertEqual(case.state['websocket']['status'], 'FAILED')
        self.assertEqual(case._ws_state, 'FAILED')
        self.assertEqual(case._connection_token, '')
        self.assertNotIn('private.compact.jwt', json.dumps(case.state))

    def test_delete_is_rejected_before_sql_when_websocket_is_not_closed(self):
        case = object.__new__(OwnedLocalSessionPublic)
        case._active = Mock()
        case.runtime = Mock(state='RUNNING', ready=True, child=Mock())
        case.runtime.child.poll.return_value = None
        case.runtime._owns_listener.return_value = True
        case._ws_state = 'CONNECTED'
        case._ws_connection = Mock()
        with self.assertRaisesRegex(LocalSessionPublicError, 'closed first'):
            case._deletion_admission('session')

    def test_journal_schema_has_operations_checks_ledger_and_no_secret_fields(self):
        source = (ROOT / 'bcs_e2e' / 'local_session_public.py').read_text()
        for required in ("'operations': []", "'checks': []", "'ledger': {'status': 'NOT_OPENED'}",
                         "LOCAL_SESSION_PUBLIC_PASS", "LEDGER_NAME = 'session-public-resources.sqlite'"):
            self.assertIn(required, source)
        for forbidden in ('subprocess', 'shell=True', '/api/v1/', 'requests.',
                          'receiver.start', 'engine.start', 'remote_run'):
            self.assertNotIn(forbidden, source)


@unittest.skipUnless(BINARY and BINARY_SHA and shutil.which('lsof'),
                     'approved actual BCS binary required')
class LocalSessionPublicLiveTests(unittest.TestCase):
    def execute(self, hook=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'runtime'
        result = None
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=BINARY_SHA,
                             root=root, run_id='session-public-live',
                             store_messages=True) as runtime:
            bound = binding(runtime)
            write(runtime.root / BINDING_NAME, bound)
            fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                case = OwnedLocalSessionPublic(identities)
                result = case.run() if hook is None else hook(case)
        return root, bound, result

    def test_actual_public_api_jwt_ws_and_exact_deletion(self):
        root, _bound, result = self.execute()
        self.assertEqual(result['status'], 'LOCAL_SESSION_PUBLIC_PASS')
        self.assertEqual(result['assertions'], [{'id': REQUIREMENTS[0], 'status': 'PASS'}])
        self.assertEqual([row['operation'] for row in result['operations']], list(OPERATIONS))
        self.assertEqual(result['websocket']['status'], 'CLOSED')
        self.assertEqual(result['storage_before_delete']['history']['row_count'], 4)
        self.assertEqual(result['history_readback']['message_count'], 1)
        self.assertTrue(result['session_delete_verification']['sqlite']['history_preserved'])
        self.assertTrue(result['group_delete_verification']['sqlite']['history_preserved'])
        self.assertEqual(result['credential_persistence'], {
            'gateway_principal': False, 'connection_token': False,
            'websocket_url': False, 'private_signing_key': False})
        self.assertTrue((root / LEDGER_NAME).is_file())
        self.assertEqual(read(root / JOURNAL_NAME), result)
        serialized = json.dumps(result)
        self.assertNotIn('X-Avernet-Principal', serialized)
        self.assertIn('?token=[REDACTED]', serialized)
        self.assertNotRegex(serialized, r"\?" + "token" + r"=(?!\[REDACTED\])")
        self.assertEqual(read(root / 'owned-bcs.json')['status'], 'STOPPED')

    def test_lost_group_201_is_unknown_once_without_retry_adoption_or_delete(self):
        def lose_committed_group_response(case):
            original = case.identities.opener.open
            creates = []

            def open_then_lose(request, **options):
                if (request.get_method() == 'POST'
                        and request.full_url.endswith('/openapi/v1/collaboration/groups')
                        and request.data is not None
                        and b'"group_kind":"normal"' in request.data):
                    creates.append(request.full_url)
                    response = original(request, **options)
                    response.close()
                    raise URLError('private-lost-group-create-response')
                return original(request, **options)

            with patch.object(case.identities.opener, 'open', side_effect=open_then_lose):
                with self.assertRaises(LocalSessionPublicError):
                    case.run()
            journal = read(case.path)
            journal['_test_group_create_calls'] = len(creates)
            return journal

        root, bound, journal = self.execute(lose_committed_group_response)
        self.assertEqual(journal['_test_group_create_calls'], 1)
        create = next(row for row in journal['operations'] if row['operation'] == 'group-create')
        self.assertEqual(create['status'], 'UNKNOWN')
        self.assertEqual([row['status'] for row in create['transitions']], ['INTENDED', 'UNKNOWN'])
        self.assertFalse(any(row['method'] == 'DELETE' for row in journal['operations']))
        self.assertEqual(journal['assertions'], [{'id': REQUIREMENTS[0], 'status': 'FAIL'}])
        with Ledger(root / LEDGER_NAME, bound['run_id'], fingerprint(bound), read_only=True) as ledger:
            rows = ledger.resource_snapshot()
        self.assertEqual([row['kind'] for row in rows], ['group', 'session'])
        self.assertEqual([row['state'] for row in rows], ['unknown', 'unknown'])
        self.assertTrue(all(row['resource_id'] is None and row['origin'] == 'unknown' for row in rows))
        serialized = json.dumps(journal)
        self.assertNotIn('private-lost-group-create-response', serialized)
        self.assertNotIn('?token=', serialized)
        self.assertEqual(read(root / 'owned-bcs.json')['status'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()
