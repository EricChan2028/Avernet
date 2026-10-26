import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcs_e2e.execution_ledger import ExecutionLedger, ExecutionLedgerError
from bcs_e2e.ledger import Ledger


class ExecutionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "run.sqlite"
        self.ledger = Ledger(self.path, "run-1", "a" * 64, create=True)
        self.addCleanup(self.ledger.close)
        self.effects = ExecutionLedger(self.ledger)

    def test_exact_request_and_effect_chain_reaches_quiescent(self):
        delivery = self.effects.intent("PRE-02", 1, "delivery", "req-delivery", actor_id="bot-a")
        self.effects.observe(delivery, "delivery-1", evidence_request_id="req-delivery")
        self.effects.transition(delivery, "QUEUED")
        self.effects.transition(delivery, "RUNNING")
        self.effects.transition(delivery, "COMPLETED", reason="bot terminal", evidence_request_id="req-done")
        self.effects.require_quiescent(case_id="PRE-02", attempt=1)
        row = self.effects.snapshot()[0]
        self.assertEqual(row["effect_id"], "delivery-1")
        self.assertEqual(row["evidence_request_id"], "req-done")

    def test_child_effect_requires_same_case_and_live_parent(self):
        parent = self.effects.intent("PRE-02", 1, "run", "req-run")
        child = self.effects.intent("PRE-02", 1, "delivery", "req-child", parent=parent)
        self.assertTrue(child)
        self.effects.observe(parent, "run-1")
        self.effects.transition(parent, "RUNNING")
        self.effects.transition(parent, "COMPLETED", reason="finished")
        with self.assertRaises(ExecutionLedgerError):
            self.effects.intent("PRE-02", 1, "subscription", "req-late", parent=parent)

    def test_unknown_and_retained_never_count_as_quiescent(self):
        h = self.effects.intent("PRE-02", 1, "context", "req-context")
        self.effects.unknown(h, reason="lost response")
        with self.assertRaises(ExecutionLedgerError):
            self.effects.require_quiescent(case_id="PRE-02", attempt=1)
        self.assertEqual(self.effects.snapshot()[0]["state"], "UNKNOWN")

    def test_cancel_is_request_then_terminal_not_direct_ack(self):
        h = self.effects.intent("PRE-02", 1, "delivery", "req-cancel")
        self.effects.observe(h, "delivery-cancel")
        self.effects.transition(h, "QUEUED")
        self.effects.transition(h, "CANCELLING", reason="cancel requested")
        with self.assertRaises(ExecutionLedgerError):
            self.effects.require_quiescent(case_id="PRE-02", attempt=1)
        self.effects.transition(h, "CANCELLED", reason="server terminal")
        self.effects.require_quiescent(case_id="PRE-02", attempt=1)

    def test_terminal_effects_cannot_be_reopened_or_rebound(self):
        h = self.effects.intent("PRE-02", 1, "delivery", "req-terminal")
        self.effects.observe(h, "delivery-terminal")
        self.effects.transition(h, "FAILED", reason="provider error")
        with self.assertRaises(ExecutionLedgerError): self.effects.transition(h, "RUNNING")
        with self.assertRaises(ExecutionLedgerError): self.effects.observe(h, "other-id")
