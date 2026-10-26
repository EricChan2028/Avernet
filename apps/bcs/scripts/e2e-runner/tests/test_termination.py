import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e.termination import TerminationError, TerminationStateMachine


class TerminationStateMachineTests(unittest.TestCase):
    def test_order_preserves_observation_until_effects_are_terminal(self):
        machine = TerminationStateMachine("session-1")
        events = []
        machine.stop_new_actions()
        machine.close_production(lambda: events.append("production"))
        machine.await_terminal(lambda: events.append("terminal"))
        machine.close_control(lambda: events.append("control"))
        machine.ready_for_cleanup()
        machine.cleanup_complete()
        self.assertEqual(events, ["production", "terminal", "control"])
        self.assertEqual(machine.phase, "CLEANED")
        self.assertEqual(machine.snapshot()["quiescence"], "CONFIRMED")

    def test_cannot_skip_quiescence_or_cleanup(self):
        machine = TerminationStateMachine("session-1")
        machine.stop_new_actions()
        with self.assertRaises(TerminationError):
            machine.close_control()
        machine.close_production()
        machine.await_terminal()
        with self.assertRaises(TerminationError):
            machine.cleanup_complete()
        machine.close_control()
        machine.ready_for_cleanup()
        machine.cleanup_complete()

    def test_failure_is_retained_and_never_becomes_quiescent(self):
        machine = TerminationStateMachine("session-1")
        machine.stop_new_actions()
        machine.close_production()
        with self.assertRaises(TerminationError):
            machine.await_terminal(lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
        self.assertEqual(machine.phase, "RETAINED")
        self.assertEqual(machine.snapshot()["quiescence"], "UNKNOWN")
        with self.assertRaises(TerminationError):
            machine.close_control()

    def test_retain_is_idempotent_for_recovery_reporting(self):
        machine = TerminationStateMachine("session-1")
        machine.retain("lease lost")
        machine.retain("late observer")
        self.assertEqual(machine.phase, "RETAINED")
        self.assertEqual(len(machine.history), 2)


if __name__ == "__main__":
    unittest.main()
