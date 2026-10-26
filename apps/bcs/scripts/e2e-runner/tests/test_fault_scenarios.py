"""Task 9.5 fault-injection contracts for the controlled local double.

Each scenario models one audited product-side failure against the
real ExecutionLedger / CaseScopedAdapter / TerminationStateMachine
composition and asserts the 9.5 invariants:

- no false quiescence or PASS is ever reported;
- the fixture pool is never released while an effect is
  unresolved (the run is RETAINED);
- external work is never stopped: a fault in one Session must
  leave other Sessions' effects untouched;
- non-idempotent work is never silently re-issued after a lost
  response; authentication failures never downgrade identity.
"""
import sqlite3
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
from bcs_e2e.execution_ledger import ExecutionLedger, ExecutionLedgerError
from bcs_e2e.ledger import Ledger
from bcs_e2e.profile import fingerprint


def profile():
    return {
        'human': {'user_id': 'owner-1'},
        'actors': {'driver': {'bot_id': 'bot-a', 'owner_id': 'owner-1', 'dedicated': True},
                   'member': {'bot_id': 'bot-b', 'owner_id': 'owner-1', 'dedicated': True}},
    }


class FaultStop:
    """Scripted exact-scope stop double with a fault registry."""

    def __init__(self):
        self.calls: list[dict] = []
        self.faults: dict[str, Exception] = {}

    def __call__(self, handle: str, row: dict) -> dict:
        self.calls.append(dict(row))
        fault = self.faults.get(handle)
        if fault is not None:
            raise fault
        return {"effect_id": f"effect-{handle}", "http_status": 200}


class FaultScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.profile = profile()
        self.path = Path(self.tmp.name) / 'run.sqlite'
        self.ledger = Ledger(self.path, 'run-1', fingerprint({'profile': 'test'}), create=True)
        self.effects = ExecutionLedger(self.ledger)
        self.stop = FaultStop()
        self.control = ControlledEffectControl(
            self.effects, case_id='PRE-02', attempt=1,
            allowed_actors=frozenset({'bot-a', 'bot-b'}),
            stop_callback=self.stop, observation_seconds=1)

    def tearDown(self):
        self.ledger.close()

    def seed(self, state: str, *, session: str = 'sess-1',
             actor: str = 'bot-a', request_id: str | None = None) -> str:
        request_id = request_id or f"req-{uuid.uuid4().hex[:8]}"
        handle = self.effects.intent('PRE-02', 1, 'delivery', request_id,
                                       actor_id=actor, session_id=session)
        self.effects.observe(handle, f"effect-{request_id}",
                               evidence_request_id=request_id)
        self.effects.transition(handle, 'QUEUED', reason='seeded queued')
        if state == 'RUNNING':
            self.effects.transition(handle, 'RUNNING', reason='seeded running')
        return handle

    # ---- 9.5: empty execution ID cannot be admitted ----
    def test_empty_execution_id_is_not_admitted_and_blocks_quiescence(self):
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        handle = self.effects.intent('PRE-02', 1, 'delivery', request_id,
                                       actor_id='bot-a', session_id='sess-1')
        with self.assertRaises(Exception):
            self.effects.observe(handle, "", evidence_request_id=request_id)
        row = self.effects.snapshot()[0]
        self.assertEqual(row['state'], 'INTENDED')
        self.assertIsNone(row['effect_id'])
        with self.assertRaises(ExecutionLedgerError):
            self.effects.require_session_quiescent('sess-1')

    # ---- 9.5: lost create response is never silently re-issued ----
    def test_lost_create_response_is_unknown_and_non_idempotent_retry_is_refused(self):
        handle = self.seed('RUNNING', request_id='create-req-1')
        # The product response to the non-idempotent create is lost:
        # the runner absorbs the effect as UNKNOWN instead of
        # inventing a completion or re-issuing the create.
        self.effects.unknown(handle, reason='create response lost')
        self.assertEqual(self.effects.snapshot()[0]['state'], 'UNKNOWN')
        with self.assertRaises(sqlite3.IntegrityError):
            self.effects.intent('PRE-02', 1, 'delivery', 'create-req-1',
                                  actor_id='bot-a', session_id='sess-1')
        with self.assertRaises(ExecutionLedgerError):
            self.effects.require_session_quiescent('sess-1')

    # ---- 9.5: authentication expiry never downgrades identity ----
    def test_cookie_expiry_is_single_attempt_with_original_identity(self):
        handle = self.seed('RUNNING', actor='bot-a', session='sess-auth')
        self.stop.faults[handle] = RequestFailure(
            '401 session cookie expired')
        with self.assertRaises(RequestFailure):
            self.control.stop_delivery(handle)
        self.assertEqual(len(self.stop.calls), 1)
        issued = self.stop.calls[0]
        self.assertEqual(issued['actor_id'], 'bot-a')
        self.assertEqual(issued['session_id'], 'sess-auth')
        # No state change was invented on the failed stop.
        self.assertEqual(self.effects.snapshot()[0]['state'], 'RUNNING')

    # ---- 9.5: mid-run runtime loss retains the run, external work untouched ----
    def test_runtime_loss_mid_run_retains_without_stopping_external_work(self):
        victim = self.seed('RUNNING', session='sess-victim')
        external = self.seed('RUNNING', session='sess-external')
        # The downstream becomes unresponsive inside the bounded
        # observation window; expiry is absorbed as UNKNOWN.
        with self.assertRaises(EffectControlError):
            self.control.wait_terminal([victim])
        self.assertEqual(self.effects.snapshot(
        )[0]['state'] if len(self.effects.snapshot()) == 1 else
            next(r['state'] for r in self.effects.snapshot() if r['handle'] == victim),
            'UNKNOWN')
        # External Session work was never touched by this fault.
        self.assertEqual(
            next(r['state'] for r in self.effects.snapshot()
                 if r['handle'] == external), 'RUNNING')

    # ---- 9.4/9.5: cancel admitted is not terminal at story level ----
    def test_cancel_admitted_without_terminal_state_retains_session(self):
        adapter = LocalMockAdapter(self.profile)
        adapter.bind_execution_ledger(self.effects, 'PRE-02', 1)
        case = CaseScopedAdapter(
            adapter, self.ledger, fingerprint({'profile': 'test'}),
            {'id': 'PRE-02', 'effects': {'delete:owned_initial_session',
                                            'delete:owned_session'}},
            attempt=1)
        group_handle, session_handle = self.ledger.group_creation_intent('PRE-02', 1, 'group-req')
        group, _ = adapter.group_create({'group_kind': 'normal', 'driver_bot_uuid': 'bot-a',
                                          'participants': [{'actor_id': 'bot-a'}]})
        self.ledger.observed(group_handle, group['group_id'], origin='created')
        self.ledger.observed(session_handle, group['initial_session_id'], origin='created')
        case._initial_sessions.add(session_handle)
        adapter.effect_mode = 'CANCEL_ACKED_RUNNING'
        with self.assertRaises(Exception):
            case.session_delete(group['initial_session_id'])
        self.assertEqual(case.termination_snapshots()[0]['phase'], 'RETAINED')
        states = {row['state'] for row in self.effects.snapshot()}
        self.assertIn('CANCELLING', states)
        self.assertNotIn('COMPLETED', states)
        self.assertNotIn('CANCELLED', states)


if __name__ == '__main__':
    unittest.main()
