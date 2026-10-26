"""Safety guards and opt-in live tests for the complete local network executor."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.inventory import current_manifest, executor_digest
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_network_api import (
    BINDING_NAME,
    CONTRACT,
    LEDGER_CONTRACT,
    LEDGER_NAME,
    LocalNetworkApiError,
    OwnedLocalNetworkApi,
    ROLES,
)
from bcs_e2e.local_network_contract import CASE_ID, REQUIREMENTS, SUITE, network_source_binding
from bcs_e2e.local_panel_asset import LocalPanelAsset
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.run_state import read, write

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
BINARY_SHA = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
PANEL = os.environ.get('BCS_E2E_TEST_PANEL_ASSET', '')
PANEL_SHA = (os.environ.get('BCS_E2E_TEST_PANEL_ASSET_SHA256', '')
             or os.environ.get('BCS_E2E_TEST_PANEL_SHA256', ''))


def binding(runtime):
    return {
        'schema_version': 1,
        'contract': CONTRACT,
        'suite': SUITE,
        'case_id': CASE_ID,
        'run_id': runtime.run_id,
        'executor_digest': executor_digest(),
        'bcs_binary_sha256': runtime.binary_sha256,
        'panel_sha256': runtime.panel_asset.sha256,
        'source_binding': network_source_binding(),
        'selected_requirement_ids': list(REQUIREMENTS),
        'legacy_inventory': current_manifest()['legacy_inventory'],
        'runtime_dir': str(runtime.root),
    }


class LocalNetworkApiGuardTests(unittest.TestCase):
    def test_complete_contract_has_all_eight_helpers_in_order(self):
        self.assertEqual(CASE_ID, 'LOCAL-NETWORK-01')
        self.assertEqual(tuple(REQUIREMENTS), (
            'NETWORK.platform', 'NETWORK.actor-list', 'NETWORK.actor-search',
            'NETWORK.actor-status', 'NETWORK.bots-my', 'NETWORK.bots-paged',
            'NETWORK.bots-query', 'NETWORK.register-onboard'))
        self.assertEqual(len(network_source_binding()['requirements']), 8)

    def test_constructor_rejects_unmanaged_identity(self):
        with self.assertRaisesRegex(LocalNetworkApiError, 'freshly verified'):
            OwnedLocalNetworkApi(object())

    def test_binding_requires_suite_and_every_exact_runtime_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = object.__new__(OwnedLocalNetworkApi)
            case.root = root
            case.binding_path = root / BINDING_NAME
            case.executor_digest = '1' * 64
            case.source_binding = {'reviewed': True}
            case.runtime = Mock(run_id='network-unit', binary_sha256='2' * 64,
                                panel_asset=Mock(sha256='3' * 64))
            expected = {
                'schema_version': 1, 'contract': CONTRACT, 'suite': SUITE,
                'case_id': CASE_ID, 'run_id': 'network-unit', 'executor_digest': '1' * 64,
                'bcs_binary_sha256': '2' * 64, 'panel_sha256': '3' * 64,
                'source_binding': {'reviewed': True},
                'selected_requirement_ids': list(REQUIREMENTS),
                'legacy_inventory': [{'id': 'story_user_prepares_agent_network'}],
                'runtime_dir': str(root),
            }
            with patch('bcs_e2e.local_network_api.current_manifest',
                       return_value={'legacy_inventory': expected['legacy_inventory']}):
                write(case.binding_path, expected)
                self.assertEqual(case._validated_binding(), expected)
                for field in expected:
                    changed = dict(expected)
                    if field == 'suite':
                        changed[field] = 'wrong-suite'
                    else:
                        changed.pop(field)
                    write(case.binding_path, changed)
                    with self.subTest(field=field), self.assertRaises(LocalNetworkApiError):
                        case._validated_binding()
                    write(case.binding_path, expected)

    def test_operation_persists_intent_transition_before_dispatch_shape(self):
        case = object.__new__(OwnedLocalNetworkApi)
        case._active = Mock()
        case._save = Mock()
        case._current_requirement = 'NETWORK.actor-status'
        case.state = {'operations': []}
        event = case._append_operation('actor-status-hidden', 'PUT', '/actors/owned/status',
                                       mutation=True, request_id='request-fixed')
        self.assertEqual(event, {
            'name': 'actor-status-hidden',
            'requirement_id': 'NETWORK.actor-status',
            'method': 'PUT',
            'path': '/actors/owned/status',
            'kind': 'MUTATION',
            'submitted_request_id': 'request-fixed',
            'status': 'INTENDED',
            'transitions': [{'status': 'INTENDED'}],
        })
        case._save.assert_called_once_with()
        with self.assertRaisesRegex(LocalNetworkApiError, 'replayed'):
            case._append_operation('actor-status-hidden', 'PUT', '/actors/owned/status',
                                   mutation=True)

    def test_registration_ledger_intent_is_single_attempt_and_unknown_is_terminal(self):
        case = object.__new__(OwnedLocalNetworkApi)
        case.identities = Mock(human='human-owner')
        case.ledger = {
            'status': 'OPEN', 'transitions': [{'status': 'OPEN', 'resource_state': 'NONE'}],
            'resource': {'handle': 'temporary-network-bot', 'kind': 'bot', 'state': 'NONE',
                         'resource_id': None, 'token_persisted': False},
        }
        case._save_ledger = Mock()
        case._ledger_intent('temporary-name', 'register-request')
        self.assertEqual(case.ledger['resource']['state'], 'INTENDED')
        self.assertEqual(case.ledger['resource']['request_id'], 'register-request')
        self.assertFalse(case.ledger['resource']['token_persisted'])
        self.assertEqual(case.ledger['transitions'][-1], {
            'status': 'REGISTRATION_INTENDED', 'resource_state': 'INTENDED',
            'request_id': 'register-request'})
        with self.assertRaisesRegex(LocalNetworkApiError, 'cannot be retried'):
            case._ledger_intent('temporary-name', 'another-request')
        case._ledger_unknown()
        self.assertEqual(case.ledger['resource']['state'], 'UNKNOWN')
        self.assertEqual(case.ledger['status'], 'UNKNOWN_OUTCOME_NO_CLEANUP_OR_ADOPTION')

    def test_unreviewed_mutations_reject_before_opener_call(self):
        case = object.__new__(OwnedLocalNetworkApi)
        opener = Mock()
        case.identities = Mock(human='owner', opener=opener)
        case.identities._entries = {'CEO': ('owned-ceo', 'ceo-token')}
        case.identities.origin = 'http://127.0.0.1:21000'
        case.ledger = {'resource': {'state': 'ADMIN_REVIEWED', 'resource_id': 'owned-new',
                                    'owner_id': 'owner', 'registration_name': 'temporary'}}
        case._temporary_id = 'owned-new'
        case._bot_token = 'new-token'
        case.state = {'operations': [{'name': 'delete-owner-proof', 'status': 'ACKNOWLEDGED'}]}
        case._current_requirement = 'NETWORK.register-onboard'
        case._role_id = Mock(return_value='owned-ceo')
        cases = (
            ('foreign-delete', 'delete-owned-bot', 'DELETE', '/bots/foreign', None, '', 200, True),
            ('outsider-status', 'actor-status-hidden', 'PUT', '/actors/outsider/status',
             {'status': 'hidden'}, 'ceo-token', 200, True),
            ('wrong-phase', 'actor-status-hidden', 'PUT', '/actors/owned-ceo/status',
             {'status': 'hidden'}, 'ceo-token', 200, True),
        )
        for label, name, method, path, body, token, status, mutation in cases:
            case._current_requirement = ('NETWORK.platform' if label == 'wrong-phase'
                                         else 'NETWORK.register-onboard')
            with self.subTest(label=label), self.assertRaisesRegex(
                    LocalNetworkApiError, 'outside the finite owned request plan'):
                case._request(name, method, path, body=body, token=token,
                              expected_status=status, mutation=mutation)
        opener.open.assert_not_called()
        self.assertEqual(case.state['operations'], [
            {'name': 'delete-owner-proof', 'status': 'ACKNOWLEDGED'}])

    def test_mutation_endpoint_cannot_use_read_mode(self):
        case = object.__new__(OwnedLocalNetworkApi)
        opener = Mock()
        case.identities = Mock(human='owner', opener=opener)
        case.identities._entries = {'CEO': ('owned-ceo', 'ceo-token')}
        case.identities.origin = 'http://127.0.0.1:21000'
        case.ledger = {'resource': {'state': 'LIVE', 'resource_id': 'owned-new',
                                    'owner_id': 'owner', 'registration_name': 'temporary'}}
        case._temporary_id = 'owned-new'
        case._bot_token = 'new-token'
        case.state = {'operations': []}
        case._current_requirement = 'NETWORK.register-onboard'
        case._role_id = Mock(return_value='owned-ceo')
        with self.assertRaisesRegex(LocalNetworkApiError, 'read request cannot target'):
            case._request('not-reviewed', 'POST', '/bots/onboard', body={}, mutation=False)
        opener.open.assert_not_called()

    def test_foreign_or_unbound_resource_is_rejected_before_owner_read(self):
        case = object.__new__(OwnedLocalNetworkApi)
        case._active = Mock()
        case.identities = Mock(human='owner')
        case._temporary_id = 'exact-bot'
        case._bot_token = 'held-token'
        case._read_bot = Mock(side_effect=AssertionError('must not read foreign resource'))
        case.ledger = {'resource': {'state': 'LIVE', 'resource_id': 'foreign-bot',
                                    'owner_id': 'owner'}}
        with self.assertRaisesRegex(LocalNetworkApiError, 'exact live owned'):
            case._owned_temporary('delete-owner-proof')
        case._read_bot.assert_not_called()

    def test_failure_marks_current_requirement_and_leaves_remaining_not_run(self):
        case = object.__new__(OwnedLocalNetworkApi)
        case.state = {'assertions': [
            {'id': requirement, 'status': 'NOT_RUN'} for requirement in REQUIREMENTS]}
        case._save = Mock()
        case._current_requirement = ''
        with self.assertRaisesRegex(ValueError, 'stop'):
            case._run_requirement(REQUIREMENTS[2], lambda: (_ for _ in ()).throw(ValueError('stop')))
        self.assertEqual(case.state['assertions'][2]['status'], 'FAIL')
        self.assertTrue(all(row['status'] == 'NOT_RUN'
                            for row in case.state['assertions'][3:]))

    def test_adapter_has_no_remote_shell_retry_or_broad_bot_delete_surface(self):
        source = (ROOT / 'bcs_e2e' / 'local_network_api.py').read_text()
        # Operation-label prefixes dispatch finite role checks, not resource-name cleanup.
        # Foreign deletion is verified behaviorally above, not by banning startswith.
        for forbidden in ('subprocess', 'os.system', 'shell=True', '/openapi/',
                          'prefix deletion', 'list-and-delete',
                          'registration_retry', 'retry_registration'):
            self.assertNotIn(forbidden, source)
        self.assertIn("'unknown_outcome_policy': 'NO_CLEANUP_OR_ADOPTION'", source)
        self.assertIn("safe_path='/onboard/url'", source)


@unittest.skipUnless(
    BINARY and BINARY_SHA and PANEL and PANEL_SHA and shutil.which('lsof'),
    'approved actual BCS binary and real panel asset required',
)
class LocalNetworkApiLiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'runtime'

    def execute(self, hook=None):
        result = None
        asset = LocalPanelAsset(Path(PANEL), PANEL_SHA).validate()
        with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=BINARY_SHA,
                             root=self.root, run_id='network-api-live',
                             panel_asset=asset) as runtime:
            write(runtime.root / BINDING_NAME, binding(runtime))
            fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
            with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                fixtures.apply(allow_write=True, role_identity=identities)
                for role in fixtures.roles:
                    identities.verify(role)
                case = OwnedLocalNetworkApi(identities)
                result = case.run() if hook is None else hook(case)
        return result, read(self.root / 'owned-bcs.json')

    def test_all_eight_obligations_delete_only_temporary_bot_and_retain_fixtures(self):
        result, runtime = self.execute()
        self.assertEqual(result['status'], 'LOCAL_NETWORK_PASS')
        self.assertEqual(result['assertions'], [
            {'id': requirement, 'status': 'PASS'} for requirement in REQUIREMENTS])
        self.assertEqual(result['credentials'], {
            'new_bot_memory': 'CLEARED_REFERENCE_ONLY'})
        self.assertEqual(result['resource_disposition'],
                         'TEMPORARY_BOT_DELETED_FIXTURES_RETAINED')
        self.assertEqual(result['non_target_before'], result['non_target_after'])
        self.assertTrue(all(result['non_target_after'][role]['status'] == 'online'
                            for role in ROLES))
        self.assertEqual(result['owned_bot_inventory_before'],
                         result['owned_bot_inventory_after'])
        ledger = read(self.root / LEDGER_NAME)
        self.assertEqual(ledger['contract'], LEDGER_CONTRACT)
        self.assertEqual(ledger['status'], 'RELEASED_EXACT_RESOURCE')
        self.assertEqual(ledger['resource']['state'], 'DELETED_CONFIRMED_404')
        self.assertFalse(ledger['resource']['token_persisted'])
        self.assertEqual([row['resource_state'] for row in ledger['transitions']], [
            'NONE', 'INTENDED', 'LIVE', 'ONBOARDED', 'ADMIN_REVIEWED',
            'DELETED_CONFIRMED_404'])
        register = next(row for row in result['operations']
                        if row['name'] == 'register-owned-bot')
        self.assertEqual(register['submitted_request_id'],
                         ledger['resource']['request_id'])
        self.assertEqual([row['status'] for row in register['transitions']],
                         ['INTENDED', 'ACKNOWLEDGED'])
        self.assertTrue(all(row['transitions'][0] == {'status': 'INTENDED'}
                            and row['status'] == 'ACKNOWLEDGED'
                            for row in result['operations']))
        browser = next(row for row in result['operations']
                       if row['name'] == 'browser-onboard-url')
        self.assertEqual(browser['path'], '/onboard/url')
        serialized = json.dumps({'journal': result, 'ledger': ledger})
        self.assertNotIn('/bcn/register?', serialized)
        self.assertNotIn('bot_token', serialized)
        self.assertNotIn('token=', serialized)
        self.assertEqual(runtime['status'], 'STOPPED')
        self.assertEqual(runtime['returncode'], 0)

    def test_committed_registration_with_lost_response_is_unknown_and_not_retried_or_deleted(self):
        def hook(case):
            original = case.identities.opener.open
            calls = []

            def lose(request, **options):
                if request.get_method() == 'POST' and '/register?' in request.full_url:
                    calls.append(request.full_url)
                    response = original(request, **options)
                    response.close()
                    raise URLError('private-lost-registration-response')
                return original(request, **options)

            with patch.object(case.identities.opener, 'open', side_effect=lose):
                with self.assertRaises(LocalNetworkApiError):
                    case.run()
            journal = read(case.path)
            journal['_test_registration_calls'] = len(calls)
            return journal

        journal, runtime = self.execute(hook)
        self.assertEqual(journal['_test_registration_calls'], 1)
        self.assertEqual(journal['status'], 'FAIL')
        self.assertEqual(journal['assertions'][-1], {
            'id': 'NETWORK.register-onboard', 'status': 'FAIL'})
        self.assertFalse(any(row['name'] == 'delete-owned-bot'
                             for row in journal['operations']))
        ledger = read(self.root / LEDGER_NAME)
        self.assertEqual(ledger['resource']['state'], 'UNKNOWN')
        self.assertEqual(ledger['status'], 'UNKNOWN_OUTCOME_NO_CLEANUP_OR_ADOPTION')
        self.assertIsNone(ledger['resource']['resource_id'])
        self.assertEqual(journal['resource_disposition'],
                         'UNKNOWN_NO_CLEANUP_OR_ADOPTION')
        self.assertNotIn('private-lost-registration-response', json.dumps(journal))
        self.assertEqual(runtime['status'], 'STOPPED')

    def test_committed_delete_with_lost_response_is_not_retried_or_inferred_deleted(self):
        def hook(case):
            original = case.identities.opener.open
            deletes = []

            def lose(request, **options):
                if request.get_method() == 'DELETE' and '/bots/' in request.full_url:
                    deletes.append(request.full_url)
                    response = original(request, **options)
                    response.close()
                    raise URLError('private-lost-delete-response')
                return original(request, **options)

            with patch.object(case.identities.opener, 'open', side_effect=lose):
                with self.assertRaises(LocalNetworkApiError):
                    case.run()
            journal = read(case.path)
            journal['_test_delete_calls'] = len(deletes)
            return journal

        journal, runtime = self.execute(hook)
        self.assertEqual(journal['_test_delete_calls'], 1)
        delete = next(row for row in journal['operations'] if row['name'] == 'delete-owned-bot')
        self.assertEqual(delete['status'], 'UNKNOWN')
        self.assertFalse(any(row['name'] == 'deleted-bot-404'
                             for row in journal['operations']))
        ledger = read(self.root / LEDGER_NAME)
        self.assertEqual(ledger['resource']['state'], 'ADMIN_REVIEWED')
        self.assertEqual(ledger['status'], 'BLOCKED_RETAINED_NO_CLEANUP')
        self.assertEqual(journal['resource_disposition'], 'RETAINED_FAILURE_EVIDENCE')
        self.assertNotIn('private-lost-delete-response', json.dumps(journal))
        self.assertEqual(runtime['status'], 'STOPPED')

    def test_ceo_restore_rejection_fails_status_requirement_before_registration(self):
        def hook(case):
            original = case.identities.opener.open

            def deny(request, **options):
                if (request.get_method() == 'PUT' and request.full_url.endswith('/status')
                        and request.data == b'{"status":"online"}'):
                    raise HTTPError(request.full_url, 403, 'forbidden', {}, None)
                return original(request, **options)

            with patch.object(case.identities.opener, 'open', side_effect=deny):
                with self.assertRaises(LocalNetworkApiError):
                    case.run()
            return read(case.path)

        journal, runtime = self.execute(hook)
        self.assertEqual(journal['assertions'][3], {
            'id': 'NETWORK.actor-status', 'status': 'FAIL'})
        self.assertTrue(all(row['status'] == 'NOT_RUN' for row in journal['assertions'][4:]))
        self.assertFalse(any(row['name'] == 'register-owned-bot'
                             for row in journal['operations']))
        self.assertEqual(read(self.root / LEDGER_NAME)['resource']['state'], 'NONE')
        self.assertEqual(runtime['status'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()
