"""Deterministic deadline checks: no sleeps, worker abandonment or timeout retries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_case
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint
from bcs_e2e.run_state import read
from bcs_e2e.termination import TerminationError, TerminationStateMachine


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.wall = 1000.0
        self.mono = 100.0
        for name, clock in (('wall_time', lambda: self.wall), ('monotonic', lambda: self.mono)):
            mock = patch('bcs_e2e.termination.' + name, side_effect=clock)
            mock.start()
            self.addCleanup(mock.stop)

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def machine(self, persist=None):
        return TerminationStateMachine('session-1', timeout_seconds=10, persist=persist)

    def test_invalid_budget_rejected_before_persist(self):
        for value in (0, -1, 61, True, 1.5, '10'):
            with self.subTest(value=value), self.assertRaises(TerminationError):
                TerminationStateMachine('session-1', timeout_seconds=value)

    def test_one_deadline_is_shared_by_all_phases(self):
        machine = self.machine()
        machine.stop_new_actions()
        self.advance(3)
        machine.close_production()
        self.advance(3)
        machine.await_terminal()
        self.advance(3)
        machine.close_control()
        machine.ready_for_cleanup()
        self.assertEqual(machine.snapshot()['deadline_epoch'], 1010.0)
        self.assertEqual(machine.remaining_seconds(), 1.0)

    def test_expired_before_production_close_does_not_call_adapter(self):
        machine = self.machine()
        machine.stop_new_actions()
        self.advance(10)
        callback = Mock()
        with self.assertRaises(TerminationError):
            machine.close_production(callback)
        callback.assert_not_called()
        self.assertEqual(machine.phase, 'RETAINED')

    def test_late_success_is_retained_and_durable(self):
        snapshots = []
        machine = self.machine(persist=snapshots.append)
        machine.stop_new_actions()
        machine.close_production()
        callback = Mock(side_effect=lambda: self.advance(11))
        with self.assertRaises(TerminationError):
            machine.await_terminal(callback)
        callback.assert_called_once()
        self.assertEqual(snapshots[-1]['phase'], 'RETAINED')
        self.assertEqual(snapshots[-1]['quiescence'], 'UNKNOWN')
        self.assertFalse(snapshots[-1]['cleanup_ready'])
        self.assertIn('deadline', snapshots[-1]['history'][-1]['reason'])

    def test_backward_wall_clock_does_not_extend_live_command(self):
        machine = self.machine()
        machine.stop_new_actions()
        self.wall -= 100
        self.mono += 11
        with self.assertRaises(TerminationError):
            machine.close_production()

    def test_forward_wall_clock_fails_closed(self):
        machine = self.machine()
        self.wall += 11
        with self.assertRaises(TerminationError):
            machine.stop_new_actions()

    def test_reopen_preserves_deadline_instead_of_renewing_it(self):
        machine = self.machine()
        snapshot = machine.snapshot()
        self.advance(8)
        self.mono = 400.0
        reopened = TerminationStateMachine.from_snapshot(snapshot)
        self.assertEqual(reopened.remaining_seconds(), 2.0)
        self.assertEqual(reopened.snapshot()['deadline_epoch'], 1010.0)
        self.advance(3)
        with self.assertRaises(TerminationError):
            reopened.stop_new_actions()

    def test_missing_or_corrupt_persisted_deadline_has_no_default_extension(self):
        for value in (None, 'nan', 'inf', '-1', 'invalid'):
            snapshot = self.machine().snapshot()
            if value is None:
                del snapshot['history'][0]['deadline_epoch']
            else:
                snapshot['history'][0]['deadline_epoch'] = value
            with self.subTest(value=value), self.assertRaises(TerminationError):
                TerminationStateMachine.from_snapshot(snapshot)

    def test_restore_without_history_never_uses_new_instance_defaults(self):
        for value in (None, [], "invalid", "missing"):
            snapshot = self.machine().snapshot()
            snapshot['deadline_epoch'] = 1.0
            if value == "missing":
                del snapshot['history']
            else:
                snapshot['history'] = value
            with self.subTest(value=value), self.assertRaises(TerminationError):
                TerminationStateMachine.from_snapshot(snapshot)

    def test_expiry_after_quiescence_stops_control_completion(self):
        machine = self.machine()
        machine.stop_new_actions()
        machine.close_production()
        machine.await_terminal()
        self.advance(11)
        close = Mock()
        with self.assertRaises(TerminationError):
            machine.close_control(close)
        close.assert_not_called()
        self.assertEqual(machine.phase, 'RETAINED')

    def test_pre02_deadline_retains_resources_closes_socket_and_keeps_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = load(ROOT / 'profiles/local.mock.json')
            self.assertEqual(profile['policy']['timeout_seconds'], 10)
            state = root / 'state'
            raw = LocalMockAdapter(profile)
            original = raw.quiesce_execution

            def late(session_id):
                original(session_id)
                self.advance(11)

            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                prepare_run(profile, state, 'deadline-story')
                with patch.object(raw, 'quiesce_execution', side_effect=late) as settle:
                    report = run_case(profile, state, 'deadline-story', 'PRE-02',
                                      adapter_factory=lambda *a, **k: raw)
            self.assertNotEqual(report['status'], 'PASS')
            self.assertEqual(report['quiescence'], 'UNKNOWN')
            self.assertEqual(report['cleanup'], 'BLOCKED')
            settle.assert_called_once()
            self.assertFalse(raw.connected_sessions)
            self.assertFalse(raw.deleted_sessions)
            self.assertFalse(raw.deleted)
            self.assertTrue(Path(read(state / 'run.json')['lock_path']).exists())
            with Ledger(state / 'run.sqlite', 'deadline-story', fingerprint(profile), read_only=True) as ledger:
                rows = ledger.termination_states(case_id='PRE-02', attempt=1)
                self.assertTrue(any(r['phase'] == 'RETAINED' for r in rows))
                self.assertEqual(rows[0]['history'][0]['deadline_epoch'], '1010.0')
