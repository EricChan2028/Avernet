"""Expanded actor provisioning is local-only, explicit and not Story admission."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.local_fixtures import FIXTURE_SETS, LocalFixtureError, OwnedLocalFixtures
from bcs_e2e.migration_inventory import REQUIRED_ROLES
from bcs_e2e.owned_bcs import local_runtime_smoke
from bcs_e2e.runtime import RuntimeContractError
import test_local_fixtures as fixtures


class LocalFixtureSetTests(unittest.TestCase):
    setUp = fixtures.LocalFixtureTests.setUp

    def response(self, operation, **kwargs):
        self.operations.append(operation)
        human = self.inputs.human_id
        if operation == 'identity': return {'user_id': human}
        if operation == 'ensure-human':
            return {'actor_uuid': 'human_' + human, 'failed_bots': [], 'errors': []}
        if operation == 'registration-token': return {'token': 'test-registration'}
        if operation == 'register':
            role = kwargs['name'][4:-(len(human) + 1)]
            return {'bot_uuid': 'bot-' + role, 'bot_token': 'test-token-' + role}
        if operation == 'read-bot':
            role = kwargs['bot_id'].removeprefix('bot-')
            return {'bot_uuid': kwargs['bot_id'], 'created_by': human, 'actor_kind': 'bot',
                    'env': 'local', 'status': 'online', 'capabilities': {'name': f'e2e-{role}-{human}'}}
        raise AssertionError(operation)

    def make_handler(self, selected):
        return OwnedLocalFixtures(self.runtime, fixture_set=selected)

    def test_roles_match_the_frozen_parent_story_requirements(self):
        for selected, key in (('workspace', 'WORKSPACE'), ('trusted-team', 'FRIEND'), ('cli-file', 'CLI-FILE')):
            self.assertEqual(set(FIXTURE_SETS[selected]), REQUIRED_ROLES[key])
        self.assertEqual(FIXTURE_SETS['pilot'], ('driver', 'member'))

    def test_each_set_creates_once_and_rechecks_without_repair_or_role_aliasing(self):
        for selected in ('workspace', 'trusted-team', 'cli-file'):
            with self.subTest(selected=selected):
                handler = self.make_handler(selected)
                with patch.object(handler, '_request', side_effect=self.response):
                    result = handler.apply(allow_write=True)
                    self.assertEqual(result['schema_version'], 1)
                    self.assertEqual(result['fixture_contract'], 'owned-local-role-fixtures-v1')
                    self.assertEqual(result['fixture_set'], selected)
                    self.assertEqual(set(result['roles']), set(FIXTURE_SETS[selected]))
                    self.assertEqual(len({v['bot_id'] for v in result['roles'].values()}), len(FIXTURE_SETS[selected]))
                    original = handler.path.read_bytes()
                    self.operations.clear()
                    verified = handler.check()
                    self.assertEqual(self.operations, ['identity'] + ['read-bot'] * len(FIXTURE_SETS[selected]))
                    self.assertEqual(handler.path.read_bytes(), original)
                    self.assertEqual(verified['relationship_baseline'], 'NOT_VERIFIED')
                    self.assertEqual(verified['story_admission'], 'NOT_EVALUATED')
                    self.assertFalse(verified['ready_to_execute'])
                    with self.assertRaises(LocalFixtureError): handler.apply(allow_write=True)
                # Test fixture teardown only; production code never clears the receipt.
                handler.path.unlink()

    def test_default_pilot_cannot_adopt_expanded_fixture_and_other_sets_cannot_reinitialize(self):
        handler = self.make_handler('workspace')
        with patch.object(handler, '_request', side_effect=self.response): handler.apply(allow_write=True)
        original = handler.path.read_bytes()
        for selected in ('pilot', 'trusted-team', 'cli-file'):
            other = self.make_handler(selected)
            with patch.object(other, '_request') as request:
                with self.assertRaises(LocalFixtureError): other.check()
                with self.assertRaises(LocalFixtureError): other.apply(allow_write=True)
                request.assert_not_called()
            self.assertEqual(handler.path.read_bytes(), original)

    def test_expanded_sets_discard_tokens_and_refuse_the_pilot_handoff(self):
        handler = self.make_handler('trusted-team')
        sink = Mock()
        with patch.object(handler, '_request') as request:
            with self.assertRaises(LocalFixtureError):
                handler.apply(allow_write=True, driver_token_sink=sink)
            request.assert_not_called(); sink.assert_not_called()
            self.assertFalse(handler.path.exists())
        with patch.object(handler, '_request', side_effect=self.response):
            result = handler.apply(allow_write=True)
        text = handler.path.read_text() + json.dumps(result)
        self.assertNotIn('test-token-', text)
        self.assertNotIn('test-registration', text)
        self.assertEqual(handler.path.stat().st_mode & 0o777, 0o600)

    def test_owner_failure_blocks_remaining_roles_without_retry(self):
        handler = self.make_handler('workspace')
        def wrong_owner(operation, **kwargs):
            result = self.response(operation, **kwargs)
            if operation == 'read-bot' and kwargs['bot_id'] == 'bot-PM': result['created_by'] = 'foreign'
            return result
        with patch.object(handler, '_request', side_effect=wrong_owner):
            with self.assertRaises(LocalFixtureError):
                handler.apply(allow_write=True)
        state = json.loads(handler.path.read_text())
        self.assertEqual(state['status'], 'BLOCKED')
        self.assertEqual(set(state['roles']), {'CEO', 'PM'})
        with patch.object(handler, '_request') as request:
            with self.assertRaises(LocalFixtureError): handler.apply(allow_write=True)
            request.assert_not_called()

    def test_expanded_set_does_not_require_unused_bot_tokens(self):
        handler = self.make_handler('cli-file')
        def no_tokens(operation, **kwargs):
            result = self.response(operation, **kwargs); result.pop('bot_token', None); return result
        with patch.object(handler, '_request', side_effect=no_tokens):
            self.assertEqual(handler.apply(allow_write=True)['status'], 'APPLIED')

    def test_lost_or_cancelled_registration_at_each_position_retains_exact_progress(self):
        roles = FIXTURE_SETS['trusted-team']
        for index, role in enumerate(roles):
            for cancellation in (False, True):
                with self.subTest(role=role, cancellation=cancellation):
                    handler = self.make_handler('trusted-team')
                    count = 0
                    def fail(operation, **kwargs):
                        nonlocal count
                        if operation == 'register':
                            count += 1
                            if count == index + 1:
                                raise KeyboardInterrupt() if cancellation else LocalFixtureError('lost response')
                        return self.response(operation, **kwargs)
                    with patch.object(handler, '_request', side_effect=fail), \
                         self.assertRaises(KeyboardInterrupt if cancellation else LocalFixtureError):
                        handler.apply(allow_write=True)
                    state = json.loads(handler.path.read_text())
                    self.assertEqual(state['status'], 'BLOCKED')
                    self.assertEqual(set(state['roles']), set(roles[:index]))
                    self.assertEqual(state['pending_operation'], 'register-' + role)
                    self.assertEqual(count, index + 1)
                    with patch.object(handler, '_request') as request:
                        with self.assertRaises(LocalFixtureError): handler.apply(allow_write=True)
                        request.assert_not_called()
                    handler.path.unlink()  # Isolated test fixture only.

    def test_late_duplicate_registration_and_persistence_failure_never_become_applied(self):
        for failure in ('duplicate', 'persist'):
            with self.subTest(failure=failure):
                handler = self.make_handler('workspace')
                from bcs_e2e.run_state import write
                def respond(operation, **kwargs):
                    result = self.response(operation, **kwargs)
                    if failure == 'duplicate' and operation == 'register' and '-QA-' in kwargs['name']:
                        result['bot_uuid'] = 'bot-CEO'
                    return result
                def persist(path, value):
                    if failure == 'persist' and value.get('status') == 'APPLIED':
                        raise OSError('injected publication failure')
                    return write(path, value)
                with patch.object(handler, '_request', side_effect=respond), \
                     patch('bcs_e2e.local_fixtures.write', side_effect=persist), \
                     self.assertRaises(LocalFixtureError if failure == 'duplicate' else OSError):
                    handler.apply(allow_write=True)
                saved = json.loads(handler.path.read_text())
                self.assertEqual(saved['status'], 'BLOCKED')
                if failure == 'duplicate':
                    self.assertEqual(saved['pending_operation'], 'register-QA')
                    self.assertEqual(set(saved['roles']), {'CEO', 'PM', 'ENG'})
                else:
                    self.assertEqual(set(saved['roles']), set(FIXTURE_SETS['workspace']))
                with patch.object(handler, '_request') as request:
                    with self.assertRaises(LocalFixtureError): handler.check()
                    request.assert_not_called()
                handler.path.unlink()  # Isolated test fixture teardown.

    def test_wrong_set_or_schema_in_saved_receipt_blocks_without_http(self):
        handler = self.make_handler('workspace')
        with patch.object(handler, '_request', side_effect=self.response): handler.apply(allow_write=True)
        original = json.loads(handler.path.read_text())
        for change in ({'schema_version': 2}, {'schema_version': True}, {'fixture_set': 'trusted-team'},
                       {'roles': {k:v for k,v in original['roles'].items() if k != 'QA'}}):
            handler.path.write_text(json.dumps({**original, **change}))
            with patch.object(handler, '_request') as request, self.assertRaises(LocalFixtureError):
                handler.check()
            request.assert_not_called()

    def test_unsupported_set_and_nonfixture_smoke_refuse_before_start(self):
        for selected in ('unknown', None, ['workspace']):
            with self.assertRaises(LocalFixtureError): self.make_handler(selected)
        with patch('bcs_e2e.owned_bcs.OwnedBcsProcess') as process, self.assertRaises(RuntimeContractError):
            local_runtime_smoke(fixture_set='workspace')
        process.assert_not_called()

    def test_cli_nonfixture_action_or_missing_write_permission_never_starts_child(self):
        for action, extra in (('smoke', ['--allow-fixture-write']), ('fixture-smoke', []),
                              ('check', ['--allow-fixture-write'])):
            with self.subTest(action=action), contextlib.redirect_stdout(io.StringIO()), \
                 patch('bcs_e2e.cli.local_runtime_smoke') as smoke:
                rc = main(['runtime', action, '--profile', str(ROOT / 'profiles/local.singlebox.example.json'),
                    '--state-dir', str(self.inputs.root / 'unused'), '--fixture-set', 'workspace',
                    '--allow-local-processes', '--run-id', 'expanded', '--binary', '/not/a/binary',
                    '--binary-sha256', 'a' * 64, *extra])
                self.assertEqual(rc, 2)
                smoke.assert_not_called()
