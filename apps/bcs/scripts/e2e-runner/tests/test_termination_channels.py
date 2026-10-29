"""Producer shutdown must not close the exact observation channel prematurely."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.case_scope import CaseScopedAdapter, CaseScopeError
from bcs_e2e.execution_ledger import ExecutionLedger
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_case
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint
from bcs_e2e.run_state import read
from bcs_e2e.termination import TerminationError


class TerminationChannelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.digest = fingerprint(self.profile)
        self.ledger = Ledger(self.root / 'run.sqlite', 'channels', self.digest, create=True)
        self.addCleanup(self.ledger.close)
        self.effects = ExecutionLedger(self.ledger)
        self.raw = LocalMockAdapter(self.profile)
        self.raw.bind_execution_ledger(self.effects, 'PRE-02', 1)
        case = next(c for c in json.loads((ROOT / 'manifest.json').read_text())['cases'] if c['id'] == 'PRE-02')
        self.scope = CaseScopedAdapter(self.raw, self.ledger, self.digest, case, attempt=1)
        self.group_handle, self.session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-request')
        group, _ = self.scope.group_create({'group_kind': 'normal', 'participants': [
            {'actor_id': self.profile['actors']['driver']['bot_id']}]})
        self.group_id = group['group_id']
        self.session_id = group['initial_session_id']
        self.ledger.observed(self.group_handle, self.group_id, origin='created')
        self.ledger.observed(self.session_handle, self.session_id, origin='created')

    def connect(self):
        connection_value = self.scope.session_token(self.session_id)[0]['token']
        return self.scope.session_ws_connect(self.group_id, self.session_id, connection_value)

    def test_production_subscription_closes_but_control_socket_survives_until_terminal(self):
        _, record = self.connect()
        subscription = next(r for r in self.effects.snapshot() if r['kind'] == 'subscription')
        self.assertEqual(subscription['request_id'], record['submitted_request_id'])
        original = self.raw.quiesce_execution

        def observe(session_id):
            self.assertIn(session_id, self.raw.connected_sessions)
            self.assertIn(session_id, self.raw._production_closed)
            rows = self.effects.snapshot()
            self.assertEqual(next(r for r in rows if r['kind'] == 'subscription')['state'], 'COMPLETED')
            self.assertTrue(any(r['state'] == 'QUEUED' for r in rows if r['kind'] != 'subscription'))
            with self.assertRaises(CaseScopeError):
                self.scope.session_update(session_id, {'title': 'late mutation'})
            original(session_id)

        with patch.object(self.raw, 'quiesce_execution', observe):
            self.scope.session_quiesce(self.session_id)
        self.assertEqual(self.ledger.termination_state(self.session_handle)['phase'], 'QUIESCENT')
        self.assertIn(self.session_id, self.raw.connected_sessions)
        with self.assertRaises(CaseScopeError):
            self.scope.session_delete(self.session_id)
        self.scope.session_ws_close(self.session_id)
        self.scope.session_delete(self.session_id)
        self.scope.session_verify_deleted(self.session_id)
        self.assertEqual(self.ledger.termination_state(self.session_handle)['phase'], 'CLEANED')

    def test_early_control_close_cannot_be_used_as_execution_proof(self):
        self.connect()
        self.scope.session_ws_close(self.session_id)
        with self.assertRaises(TerminationError):
            self.scope.session_quiesce(self.session_id)
        self.assertEqual(self.ledger.termination_state(self.session_handle)['phase'], 'RETAINED')
        self.assertFalse(self.raw.deleted_sessions)

    def test_cancel_accepted_but_not_terminal_preserves_resources_and_allows_socket_release(self):
        self.connect()

        def cancel_accepted(session_id):
            for effect in self.effects.snapshot():
                if effect['kind'] != 'subscription':
                    self.effects.transition(effect['handle'], 'CANCELLING', reason='accepted only')
            self.effects.require_session_quiescent(session_id)

        with patch.object(self.raw, 'quiesce_execution', cancel_accepted):
            with self.assertRaises(TerminationError):
                self.scope.session_quiesce(self.session_id)
        self.assertIn(self.session_id, self.raw.connected_sessions)
        self.scope.session_ws_close(self.session_id)
        with self.assertRaises(TerminationError):
            self.scope.session_delete(self.session_id)
        self.assertFalse(self.raw.deleted_sessions)

    def test_other_session_effects_are_not_settled_by_this_channel(self):
        handle = self.ledger.intent('PRE-02', 1, 'session', 'other-session', self.group_handle)
        data, _ = self.scope.session_create(self.group_id, {})
        self.ledger.observed(handle, data['session_id'], origin='created')
        self.connect()
        self.scope.session_quiesce(self.session_id)
        others = self.ledger.execution_effects(session_id=data['session_id'])
        self.assertTrue(others)
        self.assertEqual({r['state'] for r in others}, {'QUEUED'})
        self.scope.session_ws_close(self.session_id)

    def test_late_effect_after_observation_blocks_control_completion_and_delete(self):
        self.connect()
        self.scope.session_quiesce(self.session_id)
        self.scope.session_ws_close(self.session_id)
        effect = self.effects.intent('PRE-02', 1, 'context', 'late-request', session_id=self.session_id)
        self.effects.unknown(effect, reason='late effect unconfirmed')
        with self.assertRaises(TerminationError):
            self.scope.session_delete(self.session_id)
        self.assertFalse(self.raw.deleted_sessions)
        self.assertEqual(self.ledger.termination_state(self.session_handle)['phase'], 'RETAINED')

    def test_remote_label_without_callbacks_does_not_admit_termination(self):
        self.raw.termination_mode = 'REVIEWED_REMOTE'
        self.raw.close_production_channels = None
        with self.assertRaises(CaseScopeError):
            self.scope.session_quiesce(self.session_id)
        self.assertFalse(self.raw.deleted_sessions)

    def test_shared_story_orders_terminal_observation_before_ws_close(self):
        state = self.root / 'story'
        raw = LocalMockAdapter(self.profile)
        events = []
        operations = ('close_production_channels', 'quiesce_execution', 'session_ws_close',
                      'close_termination_channel', 'session_delete')
        from contextlib import ExitStack
        with patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools'), ExitStack() as stack:
            prepare_run(self.profile, state, 'story-channels')
            for name in operations:
                original = getattr(raw, name)
                def operation(session_id, _original=original, _name=name):
                    events.append((_name, session_id))
                    return _original(session_id)
                stack.enter_context(patch.object(raw, name, operation))
            report = run_case(self.profile, state, 'story-channels', 'PRE-02',
                              adapter_factory=lambda *a, **k: raw)
        self.assertEqual(report['status'], 'PASS', report)
        explicit_id = next(s for name, s in events if name == 'session_ws_close')
        self.assertEqual([name for name, s in events if s == explicit_id], list(operations))
        self.assertFalse(raw.connected_sessions)
        self.assertFalse(Path(read(state / 'run.json')['lock_path']).exists())

    def test_shared_story_on_unknown_effect_closes_socket_but_retains_resources(self):
        state = self.root / 'failure-story'
        raw = LocalMockAdapter(self.profile)
        raw.effect_mode = 'UNKNOWN_EFFECT'
        with patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools'):
            prepare_run(self.profile, state, 'failed-channels')
            report = run_case(self.profile, state, 'failed-channels', 'PRE-02',
                              adapter_factory=lambda *a, **k: raw)
        self.assertNotEqual(report['status'], 'PASS')
        self.assertEqual(report['quiescence'], 'UNKNOWN')
        self.assertFalse(raw.connected_sessions)
        self.assertFalse(raw.deleted_sessions)
        self.assertFalse(raw.deleted)
        self.assertTrue(Path(read(state / 'run.json')['lock_path']).exists())
