import sys
import tempfile
import unittest
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.case_scope import CaseScopedAdapter
from bcs_e2e.correlation import RequestFailure
from bcs_e2e.execution_control import ControlledEffectControl, EffectControlError
from bcs_e2e.execution_ledger import ExecutionLedger
from bcs_e2e.ledger import Ledger
from bcs_e2e.profile import fingerprint


def profile():
    return {
        'human': {'user_id': 'owner-1'},
        'actors': {'driver': {'bot_id': 'bot-a', 'owner_id': 'owner-1', 'dedicated': True},
                   'member': {'bot_id': 'bot-b', 'owner_id': 'owner-1', 'dedicated': True}},
    }


class ScriptedStop:
    """Exact-scope stop double; records every issued request."""

    def __init__(self, outcomes: dict | None = None):
        self.outcomes = outcomes or {}
        self.calls: list[dict] = []

    def __call__(self, handle: str, row: dict) -> dict:
        self.calls.append(dict(row))
        outcome = self.outcomes.get(handle, {"effect_id": f"effect-{handle}", "http_status": 200})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ExecutionControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.profile = profile()
        self.path = Path(self.tmp.name) / 'run.sqlite'
        self.ledger = Ledger(self.path, 'run-1', fingerprint({'profile': 'test'}), create=True)
        self.effects = ExecutionLedger(self.ledger)
        self.stop = ScriptedStop()
        self.control = ControlledEffectControl(
            self.effects, case_id='PRE-02', attempt=1,
            allowed_actors=frozenset({'bot-a', 'bot-b'}),
            stop_callback=self.stop, observation_seconds=1)

    def tearDown(self):
        self.ledger.close()

    def seed(self, state: str, *, kind: str = 'delivery', case_id: str = 'PRE-02',
             attempt: int = 1, actor: str = 'bot-a', session: str = 'sess-1',
             request_id: str | None = None) -> str:
        request_id = request_id or f"req-{uuid.uuid4().hex[:8]}"
        handle = self.effects.intent(case_id, attempt, kind, request_id,
                                       actor_id=actor, session_id=session)
        self.effects.observe(handle, f"effect-{request_id}",
                               evidence_request_id=request_id)
        self.effects.transition(handle, 'QUEUED', reason='seeded queued')
        if state == 'RUNNING':
            self.effects.transition(handle, 'RUNNING', reason='seeded running')
        elif state != 'QUEUED':
            self.effects.transition(handle, state, reason=f'seeded {state}')
        return handle

    # ---- 9.3: durable association exists before any control write ----
    def test_effect_is_durably_associated_before_stop_write(self):
        handle = self.seed('RUNNING', request_id='business-req-42')
        before = self.effects.snapshot()
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0]['request_id'], 'business-req-42')
        self.control.stop_delivery(handle)
        after = self.effects.snapshot()[0]
        self.assertEqual(after['state'], 'CANCELLING')
        self.assertEqual(after['evidence_request_id'], f'stop-{handle}')
        self.assertEqual(self.stop.calls[0]['request_id'], f'stop-{handle}')
        self.assertEqual(self.stop.calls[0]['case_id'], 'PRE-02')
        self.assertEqual(self.stop.calls[0]['session_id'], 'sess-1')

    def test_borrowed_object_from_other_case_or_attempt_is_refused(self):
        for case_id, attempt in [('OTHER-CASE', 1), ('PRE-02', 2)]:
            handle = self.seed('RUNNING', case_id=case_id, attempt=attempt)
            with self.assertRaises(EffectControlError) as caught:
                self.control.stop_delivery(handle)
            self.assertEqual(caught.exception.code, 'BORROWED_OBJECT')

    def test_effect_with_foreign_actor_is_refused(self):
        handle = self.seed('RUNNING', actor='bot-foreign')
        with self.assertRaises(EffectControlError) as caught:
            self.control.stop_delivery(handle)
        self.assertEqual(caught.exception.code, 'BORROWED_OBJECT')

    def test_unknown_handle_is_not_proven_owned(self):
        with self.assertRaises(EffectControlError) as caught:
            self.control.stop_delivery('deadbeef')
        self.assertEqual(caught.exception.code, 'BORROWED_OBJECT')

    # ---- 9.3: broad stop / drain shapes are refused, not invented ----
    def test_wildcard_and_drain_shapes_are_refused(self):
        for selector in ['*', 'all', 'queue', 'every', 'bot']:
            with self.assertRaises(EffectControlError) as caught:
                self.control.validate_scope([selector])
            self.assertEqual(caught.exception.code, 'DRAIN_SHAPE')

    def test_non_handle_selector_keys_are_refused(self):
        with self.assertRaises(EffectControlError) as caught:
            self.control.validate_scope({'bot_id': 'bot-a', 'queue': 'default'})
        self.assertEqual(caught.exception.code, 'BROAD_STOP')

    def test_multi_handle_stop_is_not_a_single_stop_operation(self):
        first = self.seed('RUNNING')
        second = self.seed('RUNNING')
        with self.assertRaises(EffectControlError):
            self.control.stop_delivery([first, second])
        self.assertEqual(self.stop.calls, [])
        self.assertEqual(
            {row['state'] for row in self.effects.snapshot()}, {'RUNNING'})

    # ---- 9.3: natural completion for queued effects; precise cancel for running ----
    def test_queued_effect_is_not_force_cancelled(self):
        handle = self.seed('QUEUED')
        with self.assertRaises(EffectControlError) as caught:
            self.control.stop_delivery(handle)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCOPE')
        self.assertEqual(self.stop.calls, [])
        self.effects.transition(handle, 'COMPLETED', reason='natural completion')
        self.control.wait_terminal([handle])
        self.assertEqual(self.effects.snapshot()[0]['state'], 'COMPLETED')

    def test_non_delivery_effects_are_out_of_scope(self):
        handle = self.seed('RUNNING', kind='subscription')
        with self.assertRaises(EffectControlError) as caught:
            self.control.stop_delivery(handle)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCOPE')

    # ---- 9.3/9.4: cancel accepted is not terminal; expiry never PASSES ----
    def test_cancel_accepted_without_terminal_state_never_passes(self):
        handle = self.seed('RUNNING')
        self.control.stop_delivery(handle)
        self.assertEqual(self.effects.snapshot()[0]['state'], 'CANCELLING')
        with self.assertRaises(EffectControlError):
            self.control.wait_terminal([handle])
        row = self.effects.snapshot()[0]
        self.assertEqual(row['state'], 'UNKNOWN')
        with self.assertRaises(Exception):
            self.effects.require_session_quiescent('sess-1')

    def test_wait_terminal_resolves_on_observed_terminal_state(self):
        handle = self.seed('RUNNING')
        self.control.stop_delivery(handle)
        self.stop.outcomes[handle] = {"effect_id": f"effect-{handle}", "http_status": 200}
        self.effects.transition(handle, 'CANCELLED', reason='observed cancelled')
        self.control.wait_terminal([handle])
        self.assertEqual(self.effects.snapshot()[0]['state'], 'CANCELLED')

    def test_stop_callback_without_effect_identity_is_absorbed_as_unknown(self):
        handle = self.seed('RUNNING')
        self.stop.outcomes[handle] = {"http_status": 202}
        with self.assertRaises(EffectControlError):
            self.control.stop_delivery(handle)
        self.assertEqual(self.effects.snapshot()[0]['state'], 'UNKNOWN')

    # ---- 9.3: authentication failure never retries or downgrades identity ----
    def test_auth_failure_is_single_attempt_without_identity_fallback(self):
        handle = self.seed('RUNNING')
        failure = RequestFailure('401 unauthorized')
        self.stop.outcomes[handle] = failure
        with self.assertRaises(RequestFailure):
            self.control.stop_delivery(handle)
        self.assertEqual(len(self.stop.calls), 1)
        self.assertEqual(self.effects.snapshot()[0]['state'], 'RUNNING')

    # ---- 9.3/9.4 integration: unresolved effects retain the run ----
    def test_unresolved_effects_retain_run_and_block_cleanup(self):
        mock_adapter = LocalMockAdapter(self.profile)
        mock_adapter.bind_execution_ledger(self.effects, 'PRE-02', 1)
        case = CaseScopedAdapter(
            mock_adapter, self.ledger, fingerprint({'profile': 'test'}),
            {'id': 'PRE-02', 'effects': {'delete:owned_initial_session',
                                            'delete:owned_session'}},
            attempt=1)
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-req')
        group, _ = mock_adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
                                               'participants': [{'actor_id': 'bot-a'}]})
        self.ledger.observed(group_handle, group['group_id'], origin='created')
        self.ledger.observed(session_handle, group['initial_session_id'], origin='created')
        case._initial_sessions.add(session_handle)
        handle = self.seed('RUNNING', session=group['initial_session_id'])
        self.control.stop_delivery(handle)
        control2 = ControlledEffectControl(
            self.effects, case_id='PRE-02', attempt=1,
            allowed_actors=frozenset({'bot-a'}),
            stop_callback=self.stop, observation_seconds=1)
        with self.assertRaises(EffectControlError):
            control2.wait_terminal([handle])
        with self.assertRaises(Exception):
            case.session_delete(group['initial_session_id'])
        self.assertEqual(case.termination_snapshots()[0]['phase'], 'RETAINED')


if __name__ == '__main__':
    unittest.main()
