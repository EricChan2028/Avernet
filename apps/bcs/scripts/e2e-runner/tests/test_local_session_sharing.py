"""Guards and opt-in live coverage for owned-local Session sharing helpers."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.bounded_process import ProcessCaptureError
from bcs_e2e.inventory import current_manifest, executor_digest
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.local_session_contract import (
    PUBLIC_CASE, PUBLIC_SUITE, REQUIREMENTS, session_source_binding,
)
from bcs_e2e.local_session_public import OwnedLocalSessionPublic
from bcs_e2e.local_session_sharing import (
    BINDING_NAME, CASE_ID, CLI_OPERATIONS, CONTRACT, JOURNAL_NAME, LEDGER_NAME,
    OPERATIONS, PLAN_NAME, ROLES, SELECTED, LocalSessionSharingError,
    OwnedLocalSessionSharing,
)
from bcs_e2e.local_session_sharing_cli import (
    LocalSessionSharingCliError, SharingCliDriver,
)
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import read, write

BCS_BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
BCS_SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI_BINARY = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_SHA = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


def public_binding(runtime):
    return {
        'schema_version': 1, 'contract': 'owned-local-session-public-v1',
        'suite': PUBLIC_SUITE, 'case_id': PUBLIC_CASE, 'run_id': runtime.run_id,
        'executor_digest': executor_digest(), 'bcs_binary_sha256': runtime.binary_sha256,
        'source_binding': session_source_binding(),
        'selected_requirement_ids': [REQUIREMENTS[0]],
        'parent_requirement_ids': list(REQUIREMENTS),
        'legacy_inventory': current_manifest()['legacy_inventory'],
        'runtime_dir': str(runtime.root.resolve()), 'store_messages': True,
    }


def sharing_plan(runtime, cli_sha):
    return dict(public_binding(runtime), contract=CONTRACT, suite=CONTRACT,
                case_id=CASE_ID, cli_binary_sha256=cli_sha,
                selected_requirement_ids=list(REQUIREMENTS[:6]))


class LocalSessionSharingGuardTests(unittest.TestCase):
    def test_contract_is_next_five_original_helpers_and_exact_ten_cli_calls(self):
        self.assertEqual(CASE_ID, 'LOCAL-SESSION-SHARING-01')
        self.assertEqual(tuple(SELECTED), REQUIREMENTS[1:6])
        self.assertEqual(len(OPERATIONS), 31)
        self.assertEqual(len(CLI_OPERATIONS), 10)
        self.assertEqual(CLI_OPERATIONS, (
            'h1-group-create', 'h1-session-create', 'h1-session-list', 'h1-session-get',
            'h2-group-create', 'h3-group-create', 'h3-session-create', 'h3-session-invite',
            'h4-group-create', 'h5-group-create'))

    def test_constructor_rejects_unmanaged_identity(self):
        with self.assertRaisesRegex(LocalSessionSharingError, 'freshly verified'):
            OwnedLocalSessionSharing(object(), Path('/missing'), '0' * 64)

    def test_missing_pm_token_is_rejected_before_cli_context_or_request(self):
        case = Mock()
        case.identities._entries = {'PM': ('pm-id', '')}
        case._active = Mock()
        driver = object.__new__(SharingCliDriver)
        driver.case = case
        with self.assertRaisesRegex(LocalSessionSharingCliError, 'missing or invalid'):
            driver.require_pm_token()
        case._active.assert_called_once_with()

    def test_foreign_invite_url_is_rejected_without_rewrite_or_forward(self):
        case = object.__new__(OwnedLocalSessionSharing)
        case.identities = Mock(origin='http://127.0.0.1:23123')
        case._sensitive = []
        value = {'invite_token': 'invite-placeholder-token', 'expires_at': 123,
                 'join_url': 'https://foreign.invalid/sessions/join/invite-placeholder-token'}
        with self.assertRaisesRegex(LocalSessionSharingError, 'not bound to the owned origin'):
            case._consume_invite(value, target='session', target_id='group:deadbeef')
        self.assertEqual(case._sensitive, [])

    @staticmethod
    def signed_invite(payload, secret='unit-invite-secret'):
        raw = json.dumps(payload, separators=(',', ':')).encode()
        return base64.urlsafe_b64encode(
            raw + hmac.new(secret.encode(), raw, hashlib.sha256).digest()).rstrip(b'=').decode()

    def invite_case(self):
        case = object.__new__(OwnedLocalSessionSharing)
        case.identities = Mock(origin='http://127.0.0.1:23123')
        case.runtime = Mock()
        case.runtime.inputs.environment = {'BCS_E2E_INVITE_SECRET': 'unit-invite-secret'}
        case.state = {'operations': [{'operation': 'h2-group-invite'}],
                      'resources': {'groups': {'h2': {'id': 'owned-group'}}, 'sessions': {}}}
        case._sensitive = []
        return case

    def test_valid_signed_foreign_target_token_is_blocked_before_join(self):
        case = self.invite_case()
        expiry = int(time.time()) + 300
        token = self.signed_invite(
            {'v': 1, 'id': 'foreign-group', 'exp': expiry, 'target_type': 'group'})
        value = {'invite_token': token, 'expires_at': expiry,
                 'join_url': 'http://127.0.0.1:23123/groups/{group_id}/' + token}
        with self.assertRaisesRegex(LocalSessionSharingError, 'target, type, version, or expiry'):
            case._consume_invite(value, target='group', target_id='owned-group')
        self.assertEqual(case._sensitive, [])

    def test_bad_signature_token_is_blocked_before_join(self):
        case = self.invite_case()
        expiry = int(time.time()) + 300
        token = self.signed_invite(
            {'v': 1, 'id': 'owned-group', 'exp': expiry, 'target_type': 'group'})
        token = token[:-1] + ('A' if token[-1] != 'A' else 'B')
        value = {'invite_token': token, 'expires_at': expiry,
                 'join_url': 'http://127.0.0.1:23123/groups/{group_id}/' + token}
        with self.assertRaisesRegex(LocalSessionSharingError, 'signature or payload'):
            case._consume_invite(value, target='group', target_id='owned-group')
        self.assertEqual(case._sensitive, [])

    def test_group_invite_mint_is_human_auth_not_pm_bot_auth(self):
        index = OPERATIONS.index('h2-group-invite')
        case = self.make_admission_case(index)
        case.state['resources']['groups']['h2'] = {'id': 'group-h2', 'handle': 'g2'}
        path = '/groups/group-h2/invite-link'
        case._admit_operation(operation='h2-group-invite', method='POST', path=path,
            body={'ttl_seconds': 300}, auth_role=None, expected_status=200,
            handles=(), transport='LEGACY_HTTP')
        with self.assertRaisesRegex(LocalSessionSharingError, 'differ'):
            case._admit_operation(operation='h2-group-invite', method='POST', path=path,
                body={'ttl_seconds': 300}, auth_role='PM', expected_status=200,
                handles=(), transport='LEGACY_HTTP')

    def test_bot_groups_read_is_human_auth_not_pm_bot_auth(self):
        index = OPERATIONS.index('h4-bot-groups')
        case = self.make_admission_case(index)
        path = '/bots/pm-owned/groups?limit=50&group_kind=all'
        case._admit_operation(operation='h4-bot-groups', method='GET', path=path,
            body=None, auth_role=None, expected_status=200,
            handles=(), transport='LEGACY_HTTP')
        with self.assertRaisesRegex(LocalSessionSharingError, 'differ'):
            case._admit_operation(operation='h4-bot-groups', method='GET', path=path,
                body=None, auth_role='PM', expected_status=200,
                handles=(), transport='LEGACY_HTTP')

    def test_success_schema_defers_requirement_pass_until_delete_proof(self):
        source = (ROOT / 'bcs_e2e' / 'local_session_sharing.py').read_text()
        pass_line = 'self._assert(assertion_id, evidence)'
        self.assertEqual(source.count(pass_line), 1)
        self.assertGreater(source.index(pass_line), source.index('proof = deletion_proof'))
        self.assertIn("'cleanup_timing': 'ORIGINAL_PER_HELPER_BEFORE_NEXT_CREATE'", source)

    def make_admission_case(self, completed):
        case = object.__new__(OwnedLocalSessionSharing)
        case._active = Mock()
        case._id = lambda role: {'PM': 'pm-owned', 'ENG': 'eng-owned'}[role]
        case.state = {'operations': [{'operation': name} for name in OPERATIONS[:completed]],
                      'resources': {'groups': {}, 'sessions': {}}}
        case._sensitive = []
        case.ledger = Mock()
        return case

    def test_restore_visibility_put_body_is_exactly_protected(self):
        index = OPERATIONS.index('eng-visibility-restore')
        case = self.make_admission_case(index)
        path = '/bots/eng-owned/visibility'
        case._admit_operation(operation='eng-visibility-restore', method='PUT', path=path,
            body={'visibility': 'protected'}, auth_role='ENG', expected_status=200,
            handles=(), transport='LEGACY_HTTP')
        with self.assertRaisesRegex(LocalSessionSharingError, 'differ'):
            case._admit_operation(operation='eng-visibility-restore', method='PUT', path=path,
                body={'visibility': 'public'}, auth_role='ENG', expected_status=200,
                handles=(), transport='LEGACY_HTTP')

    def test_foreign_group_delete_is_rejected_before_opener(self):
        index = OPERATIONS.index('h1-group-delete')
        case = self.make_admission_case(index)
        case.state['resources']['groups']['h1'] = {
            'id': 'owned-group', 'handle': 'owned-handle',
            'initial_session_id': 'owned-group:00000000',
            'initial_session_handle': 'owned-session', 'attempt': 1}
        case._ledger_row = Mock(return_value={
            'kind': 'group', 'resource_id': 'owned-group', 'origin': 'created',
            'state': 'live', 'case_id': CASE_ID, 'attempt': 1})
        with self.assertRaisesRegex(LocalSessionSharingError, 'differ'):
            case._admit_operation(operation='h1-group-delete', method='DELETE',
                path='/groups/foreign-group?bot_id=pm-owned', body=None, auth_role=None,
                expected_status=200, handles=(), transport='LEGACY_HTTP')
        case._ledger_row.assert_called_once_with('owned-handle')

    def test_borrowed_create_handles_are_rejected_before_cli(self):
        index = OPERATIONS.index('h1-group-create')
        case = self.make_admission_case(index)
        rows = [
            {'kind': 'group', 'parent': None, 'case_id': CASE_ID, 'attempt': 1,
             'origin': 'borrowed', 'state': 'live', 'resource_id': 'borrowed-group'},
            {'kind': 'session', 'parent': 'g-handle', 'case_id': CASE_ID, 'attempt': 1,
             'origin': 'borrowed', 'state': 'live', 'resource_id': 'borrowed-session'},
        ]
        case._ledger_row = Mock(side_effect=rows)
        body = {'id': None, 'label': None, 'driver_bot': 'pm-owned',
                'participants': [{'bot_uuid': 'pm-owned', 'role': None},
                                 {'bot_uuid': 'eng-owned', 'role': None}],
                'topic': 'e2e-session-sharing-h1', 'create_initial_session': True}
        args = ['create-group', '--driver', 'pm-owned', '--participants',
                'pm-owned,eng-owned', '--topic', 'e2e-session-sharing-h1']
        with self.assertRaisesRegex(LocalSessionSharingError, 'fresh owned intents'):
            case._admit_operation(operation='h1-group-create', method='POST', path='/groups',
                body=body, auth_role='PM', expected_status=200,
                handles=('g-handle', 's-handle'), transport='PRODUCT_CLI', arguments=args)

    def test_visibility_outsider_target_is_rejected_before_opener(self):
        case = self.make_admission_case(0)
        with self.assertRaisesRegex(LocalSessionSharingError, 'differ'):
            case._admit_operation(operation='eng-visibility-before', method='GET',
                path='/bots/outsider/visibility', body=None, auth_role='ENG',
                expected_status=200, handles=(), transport='LEGACY_HTTP')

    def test_no_invite_secret_stdout_or_raw_response_field_is_durable(self):
        source = (ROOT / 'bcs_e2e' / 'local_session_sharing.py').read_text()
        cli_source = (ROOT / 'bcs_e2e' / 'local_session_sharing_cli.py').read_text()
        self.assertNotIn("event['stdout']", source + cli_source)
        self.assertNotIn("event['response']", source + cli_source)
        self.assertIn("'invite_value_persisted': False", cli_source)
        self.assertIn("/join/[REDACTED]", source)

    def test_group_404_contract_uses_native_code_params_and_status(self):
        source = (ROOT / 'bcs_e2e' / 'local_session_sharing.py').read_text()
        self.assertIn("missing.get('status') != 404", source)
        self.assertIn("missing.get('code') != 'group_not_found'", source)
        self.assertIn("missing.get('params') != {'group_id': group['id']}", source)
        self.assertNotIn("missing.get('error')", source)


@unittest.skipUnless(
    BCS_BINARY and BCS_SHA and CLI_BINARY and CLI_SHA and shutil.which('lsof'),
    'approved actual BCS and bcs-cli binaries required')
class LocalSessionSharingLiveTests(unittest.TestCase):
    def execute(self, hook=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'runtime'
        result = None
        with OwnedBcsProcess(binary=Path(BCS_BINARY), binary_sha256=BCS_SHA,
                             root=root, run_id='session-sharing-live',
                             store_messages=True) as runtime:
            original = public_binding(runtime)
            plan = sharing_plan(runtime, CLI_SHA)
            write(runtime.root / 'session-binding.json', original)
            write(runtime.root / PLAN_NAME, plan)
            fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                public = OwnedLocalSessionPublic(identities).run()
                binding = dict(plan, public_journal_digest=fingerprint(public))
                write(runtime.root / BINDING_NAME, binding)
                case = OwnedLocalSessionSharing(identities, Path(CLI_BINARY), CLI_SHA)
                result = case.run() if hook is None else hook(case)
        return root, original, plan, result

    def test_actual_five_helpers_ten_cli_calls_and_thirteen_tombstones(self):
        root, original, plan, result = self.execute()
        self.assertEqual(result['status'], 'LOCAL_SESSION_SHARING_PASS')
        self.assertEqual(result['assertions'], [{'id': name, 'status': 'PASS'} for name in SELECTED])
        self.assertEqual([row['operation'] for row in result['operations']], list(OPERATIONS))
        self.assertTrue(all(row['status'] == 'ACKNOWLEDGED' for row in result['operations']))
        self.assertEqual(result['cli'], {'operation_count': 10, 'acknowledged_count': 10})
        self.assertEqual(result['resources']['group_count'], 5)
        self.assertEqual(result['resources']['session_count'], 8)
        self.assertEqual(result['resources']['ledger_resource_count'], 13)
        self.assertEqual(result['resources']['ledger_deleted_count'], 13)
        self.assertTrue(all(row['origin'] == 'created' and row['state'] == 'deleted'
                            and row['attempt'] == 1 for row in result['resources']['ledger']))
        self.assertEqual(len(result['checks']), 5)
        self.assertEqual(len(result['deletion_proofs']), 5)
        self.assertEqual(result['visibility'], {
            'before': 'protected', 'admitted': 'public',
            'after': 'protected', 'restored': True})
        self.assertEqual(result['predecessor']['retained_history']['row_count'], 4)
        self.assertEqual(result['retained_histories']['before_delete'],
                         result['retained_histories']['after_delete'])
        self.assertTrue(result['retained_histories']['preserved'])
        self.assertEqual(result['retained_histories']['before_delete']['bootstrap_row_count'], 16)
        self.assertEqual(result['credential_contexts_cleared'], {'count': 10, 'all_cleared': True})
        self.assertEqual(read(root / 'session-binding.json'), original)
        self.assertEqual(result['retained_histories']['evidence_basis'], 'OVERALL_ROW_DIGEST_AND_PER_GROUP_HASH_EQUALITY')
        self.assertNotIn('max_groups_as_driver', json.loads((root/'bcs-config.json').read_text()))
        names = [row['operation'] for row in result['operations']]
        for n in range(1, 5):
            self.assertLess(names.index(f'h{n}-group-deleted-404'), names.index(f'h{n+1}-group-create'))
        self.assertEqual(read(root / PLAN_NAME), plan)
        self.assertEqual(read(root / JOURNAL_NAME), result)
        serialized = json.dumps(result)
        for forbidden in ('invite_token', 'join_url', 'Authorization', 'Bearer '):
            self.assertNotIn(forbidden, serialized)

    def test_lost_cli_group_create_response_is_unknown_once_without_cleanup(self):
        from bcs_e2e import local_session_sharing_cli as cli_module
        actual_capture = cli_module.capture

        def hook(case):
            calls = []

            def lose_after_actual(command, **kwargs):
                result = actual_capture(command, **kwargs)
                if 'create-group' in command and not calls:
                    calls.append(command)
                    raise ProcessCaptureError('private-lost-cli-create-response')
                return result

            with patch.object(cli_module, 'capture', side_effect=lose_after_actual):
                with self.assertRaises(LocalSessionSharingError):
                    case.run()
            journal = read(case.path)
            journal['_test_create_calls'] = len(calls)
            return journal

        root, _original, _plan, journal = self.execute(hook)
        self.assertEqual(journal['_test_create_calls'], 1)
        create = next(row for row in journal['operations'] if row['operation'] == 'h1-group-create')
        self.assertEqual(create['status'], 'UNKNOWN')
        self.assertEqual(create['credential_cleanup'], 'CLEARED')
        self.assertFalse(any(row['method'] == 'DELETE' for row in journal['operations']))
        binding = read(root / BINDING_NAME)
        with Ledger(root / LEDGER_NAME, binding['run_id'], fingerprint(binding), read_only=True) as ledger:
            rows = ledger.resource_snapshot()
        self.assertEqual([row['state'] for row in rows], ['unknown', 'unknown'])
        self.assertTrue(all(row['resource_id'] is None for row in rows))
        with sqlite3.connect(root / 'bcs.sqlite') as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bcs_groups WHERE env='local'").fetchone()[0], 1)
        self.assertNotIn('private-lost-cli-create-response', json.dumps(journal))

    def test_nonzero_cli_after_actual_create_is_unknown_and_output_is_not_persisted(self):
        from bcs_e2e import local_session_sharing_cli as cli_module
        actual_capture = cli_module.capture

        def hook(case):
            changed = []

            def nonzero_after_actual(command, **kwargs):
                result = actual_capture(command, **kwargs)
                if 'create-group' in command and not changed:
                    changed.append(True)
                    return subprocess.CompletedProcess(command, 9, b'private-nonzero-output', b'')
                return result

            with patch.object(cli_module, 'capture', side_effect=nonzero_after_actual):
                with self.assertRaises(LocalSessionSharingError):
                    case.run()
            return read(case.path)

        root, _original, _plan, journal = self.execute(hook)
        create = next(row for row in journal['operations'] if row['operation'] == 'h1-group-create')
        self.assertEqual(create['status'], 'UNKNOWN')
        self.assertEqual(create['credential_cleanup'], 'CLEARED')
        self.assertNotIn('private-nonzero-output', json.dumps(journal))
        self.assertFalse(any(row['method'] == 'DELETE' for row in journal['operations']))

    def test_missing_cli_token_fails_before_visibility_or_resource_intent(self):
        def hook(case):
            pm_id, _token = case.identities._entries['PM']
            case.identities._entries['PM'] = (pm_id, '')
            with self.assertRaises(LocalSessionSharingError):
                case.run()
            return read(case.path)

        root, _original, _plan, journal = self.execute(hook)
        self.assertEqual(journal['operations'], [])
        self.assertEqual(journal['visibility']['before'], 'NOT_READ')
        binding = read(root / BINDING_NAME)
        with Ledger(root / LEDGER_NAME, binding['run_id'], fingerprint(binding), read_only=True) as ledger:
            self.assertEqual(ledger.resource_snapshot(), [])

    def test_foreign_invite_from_actual_mint_is_never_followed(self):
        def hook(case):
            original_consume = case._consume_invite
            opened_hosts = []
            original_open = case.identities.opener.open

            def observe_open(request, **kwargs):
                opened_hosts.append(urlsplit(request.full_url).hostname)
                return original_open(request, **kwargs)

            def foreign_consume(self, value, *, target, target_id):
                changed = dict(value)
                changed['join_url'] = 'https://foreign.invalid/' + value['invite_token']
                return original_consume(changed, target=target, target_id=target_id)

            case._consume_invite = types.MethodType(foreign_consume, case)
            with patch.object(case.identities.opener, 'open', side_effect=observe_open):
                with self.assertRaisesRegex(LocalSessionSharingError, 'not bound to the owned origin'):
                    case.run()
            journal = read(case.path)
            journal['_test_opened_hosts'] = opened_hosts
            return journal

        root, _original, _plan, journal = self.execute(hook)
        self.assertTrue(journal['_test_opened_hosts'])
        self.assertEqual(set(journal['_test_opened_hosts']), {'127.0.0.1'})
        invite = next(row for row in journal['operations'] if row['operation'] == 'h2-group-invite')
        self.assertEqual(invite['status'], 'ACKNOWLEDGED')
        self.assertFalse(any(row['operation'] == 'h2-group-join' for row in journal['operations']))
        # The first helper already cleaned its own Group before the second
        # helper's bad invitation. Only that earlier exact delete is allowed.
        self.assertEqual([row['operation'] for row in journal['operations'] if row['method'] == 'DELETE'],
                         ['h1-group-delete'])
        binding = read(root / BINDING_NAME)
        with Ledger(root / LEDGER_NAME, binding['run_id'], fingerprint(binding), read_only=True) as ledger:
            rows = ledger.resource_snapshot()
        self.assertEqual([row['state'] for row in rows], ['deleted', 'deleted', 'deleted', 'live', 'live'])
        self.assertNotIn('foreign.invalid', json.dumps(journal))


if __name__ == '__main__':
    unittest.main()
