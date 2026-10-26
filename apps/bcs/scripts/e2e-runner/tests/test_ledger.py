from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.ledger import Ledger, LedgerError
from bcs_e2e.diagnostics import collect_log_diagnostics


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "run.sqlite"
        self.digest = "a" * 64
        self.ledger = Ledger(self.path, "e2e-run-1", self.digest, create=True)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def group(self, name="g-1", origin="created"):
        handle = self.ledger.intent("PRE-01", 1, "group", "req-" + name)
        self.ledger.observed(handle, name, origin=origin)
        return handle

    def test_intent_survives_reopen_without_create(self):
        self.ledger.intent("PRE-01", 1, "group", "req-lost")
        with Ledger(self.path, "e2e-run-1", self.digest) as recovery:
            self.assertEqual(recovery.cleanup_plan()["status"], "BLOCKED")
            self.assertEqual(recovery.cleanup_plan()["resources"], [])

    def test_normal_group_and_initial_session_intents_are_atomic_and_durable(self):
        group, session = self.ledger.group_creation_intent("PRE-01", 1, "normal-create")
        with Ledger(self.path, "e2e-run-1", self.digest, read_only=True) as recovery:
            rows = recovery.resource_snapshot()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["parent"], group)
            self.assertEqual({row["state"] for row in rows}, {"intended"})
            self.assertEqual(recovery.cleanup_plan()["status"], "BLOCKED")
        self.ledger.observed(group, "normal-group", origin="created")
        with self.assertRaises(LedgerError): self.ledger.mark_deleted(group)
        self.ledger.observed(session, "initial-session", origin="created")
        self.assertEqual([row["kind"] for row in self.ledger.cleanup_plan()["resources"]], ["session", "group"])

    def test_failure_to_journal_initial_session_rolls_back_group_intent(self):
        self.ledger.db.execute("CREATE TRIGGER reject_session BEFORE INSERT ON resource WHEN NEW.kind='session' BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.group_creation_intent("PRE-01", 1, "normal-create")
        self.assertEqual(self.ledger.resource_snapshot(), [])

    def test_missing_ledger_not_created_in_recovery(self):
        path = self.path.parent / "missing"
        with self.assertRaises(LedgerError):
            Ledger(path, "e2e-run-1", self.digest)
        self.assertFalse(path.exists())

    def test_existing_ledger_not_overwritten(self):
        self.group()
        with self.assertRaises(FileExistsError):
            Ledger(self.path, "different", self.digest, create=True)
        self.assertEqual(len(self.ledger.cleanup_plan()["resources"]), 1)

    def test_wrong_run_or_environment_refused(self):
        for run, digest in (("different", self.digest), ("e2e-run-1", "b" * 64)):
            with self.assertRaises(LedgerError):
                Ledger(self.path, run, digest)

    def test_borrowed_and_reused_never_deletable(self):
        for name, origin in (("g-borrowed", "borrowed"), ("g-reused", "reused")):
            handle = self.group(name, origin)
            with self.assertRaises(LedgerError):
                self.ledger.mark_deleted(handle)
            with self.assertRaises(LedgerError):
                self.ledger.intent("PRE-01", 1, "session", "req-child-" + name, handle)
        self.assertEqual(self.ledger.cleanup_plan()["resources"], [])
        self.assertEqual(len(self.ledger.cleanup_plan()["retained_handles"]), 2)

    def test_inverse_dependency_order_and_idempotence(self):
        group = self.group()
        session = self.ledger.intent("PRE-01", 1, "session", "req-s", group)
        self.ledger.observed(session, "g-1:abcd1234", origin="created")
        file = self.ledger.intent("PRE-01", 1, "file", "req-f", session)
        self.ledger.observed(file, "file-1", origin="created")
        plan = self.ledger.cleanup_plan()
        self.assertEqual([r["kind"] for r in plan["resources"]], ["file", "session", "group"])
        self.assertFalse(plan["executable"])
        with self.assertRaises(LedgerError):
            self.ledger.mark_deleted(group)
        for handle in (file, session, group):
            self.ledger.mark_deleted(handle)
            self.ledger.mark_deleted(handle)
        self.assertEqual(self.ledger.cleanup_plan()["resources"], [])

    def test_unknown_child_blocks_parent_cleanup(self):
        group = self.group()
        self.ledger.intent("PRE-01", 1, "session", "req-child", group)
        self.assertEqual(self.ledger.cleanup_plan()["status"], "BLOCKED")
        self.assertEqual(self.ledger.cleanup_plan()["resources"], [])

    def test_borrowed_child_prevents_parent_cleanup(self):
        group = self.group()
        session = self.ledger.intent("PRE-01", 1, "session", "req-reused", group)
        self.ledger.observed(session, "existing-session", origin="reused")
        self.assertEqual(self.ledger.cleanup_plan()["status"], "BLOCKED")
        self.assertEqual(self.ledger.cleanup_plan()["resources"], [])

    def test_cross_case_parent_refused(self):
        group = self.group()
        with self.assertRaises(LedgerError):
            self.ledger.intent("PRE-02", 1, "session", "req-child", group)

    def test_observed_id_immutable_and_duplicates_refused(self):
        group = self.group()
        self.ledger.observed(group, "g-1", origin="created")
        with self.assertRaises(LedgerError):
            self.ledger.observed(group, "other", origin="created")
        other = self.ledger.intent("PRE-01", 1, "group", "req-other")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.observed(other, "g-1", origin="created")
        self.assertEqual(self.ledger.cleanup_plan()["status"], "BLOCKED")

    def test_unsupported_resources_cannot_be_registered(self):
        for kind in ("bot", "bucket", "tenant", "dm"):
            with self.assertRaises(LedgerError):
                self.ledger.intent("PRE-01", 1, kind, "req-x")

    def test_correlation_records_actual_response_and_resource_binding(self):
        evidence = {
            "operation": "group.create", "method": "POST", "path": "/groups",
            "submitted_request_id": "wire-1", "response_request_id": "server-1",
            "submitted_traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
            "effective_traceparent": "00-" + "a" * 32 + "-" + "c" * 16 + "-01",
        }
        self.ledger.record_correlation("PRE-01", 1, evidence)
        handle = self.ledger.intent("PRE-01", 1, "group", "planned-1")
        self.ledger.observed(handle, "g-wire", origin="created", actual_request_id="wire-1")
        rows = self.ledger.correlations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["response_request_id"], "server-1")
        self.assertEqual(rows[0]["resource_id"], "g-wire")

    def test_correlation_rejects_invalid_trace_and_does_not_store_body(self):
        with self.assertRaises(LedgerError):
            self.ledger.record_correlation("PRE-01", 1, {
                "operation": "group.read", "submitted_request_id": "wire-2",
                "submitted_traceparent": "not-a-trace",
                "body": "private message",
            })
        self.ledger.record_correlation("PRE-01", 1, {
            "operation": "group.read", "submitted_request_id": "wire-3",
            "body": "private message",
        })
        self.assertNotIn("body", self.ledger.correlations()[0])
    def test_diagnostic_collection_is_bounded_and_persists_summary_only(self):
        self.ledger.record_correlation("PRE-01", 1, {
            "operation": "group.read", "method": "GET", "path": "/groups/g-1",
            "submitted_request_id": "wire-1",
        })
        self.ledger.record_correlation("PRE-01", 1, {
            "operation": "group.update", "method": "PATCH", "path": "/groups/g-1",
            "submitted_request_id": "wire-2",
        })
        class Observer:
            def __init__(self):
                self.queries = []
            def observe(self, query, *, timeout_seconds):
                self.queries.append(query)
                return {"status": "OBSERVED", "source": "logs", "query": query.public(),
                        "record_count": 1, "truncated": False,
                        "records": [{"message": "private-log-text", "request_id": query.request_id}]}
        observer = Observer()
        result = collect_log_diagnostics(self.ledger, observer, case_id="PRE-01", attempt=1,
                                         environment="pre", start_epoch=100, end_epoch=120,
                                         max_queries=1)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(len(observer.queries), 1)
        rows = self.ledger.diagnostics()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_id"], "wire-1")
        self.assertNotIn("private-log-text", str(rows))

    def test_diagnostic_collection_without_correlations_is_not_run(self):
        result = collect_log_diagnostics(self.ledger, object(), case_id="PRE-01", attempt=1,
                                         environment="pre", start_epoch=100, end_epoch=120)
        self.assertEqual(result["status"], "NOT_RUN")
        self.assertEqual(self.ledger.diagnostics(), [])

    def test_journal_initial_permissions_private(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
