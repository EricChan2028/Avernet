"""Focused fixed-SQL tests for original Structured Story persistence evidence."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_structured_storage import OwnedLocalStructuredStorage, LocalStructuredStorageError


SCHEMA = """
CREATE TABLE bcs_groups(group_id TEXT, env TEXT);
CREATE TABLE bcs_group_sessions(session_id TEXT, group_id TEXT, env TEXT, status TEXT, session_kind TEXT, activation_count INTEGER, error_message TEXT);
CREATE TABLE bcs_group_runtime_bindings(group_id TEXT, env TEXT);
CREATE TABLE bcs_state_machine_runs(run_id TEXT, session_id TEXT, group_id TEXT, definition_id TEXT, definition_version INTEGER, status TEXT, record_status TEXT, env TEXT, created_at_ms INTEGER);
CREATE TABLE bcs_state_machine_node_runs(run_id TEXT, node_id TEXT, status TEXT, attempt INTEGER, max_attempts INTEGER, assignee_bot_id TEXT, delivery_request_id TEXT, record_status TEXT, env TEXT);
CREATE TABLE bcs_collaboration_definitions(definition_id TEXT, version INTEGER, name TEXT, source_format TEXT, content_hash TEXT, record_status TEXT, env TEXT);
"""


class FixtureStorage(OwnedLocalStructuredStorage):
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @contextmanager
    def _connection(self):
        try:
            yield self.db
        finally:
            if self.db.in_transaction:
                self.db.rollback()

    def close(self):
        self.db.close()


class StructuredStorageTests(unittest.TestCase):
    def setUp(self):
        self.storage = FixtureStorage()
        db = self.storage.db
        db.executemany("INSERT INTO bcs_group_sessions VALUES(?,?,?,?,?,?,?)", [
            ("session-initial", "group-1", "local", "completed", "service_invocation", 1, "aborted"),
            ("session-explicit", "group-1", "local", "completed", "service_invocation", 1, "aborted"),
        ])
        db.executemany("INSERT INTO bcs_state_machine_runs VALUES(?,?,?,?,?,?,?,?,?)", [
            ("run-initial", "session-initial", "group-1", "definition-1", 1, "aborted", "active", "local", 1),
            ("run-explicit", "session-explicit", "group-1", "definition-1", 2, "aborted", "active", "local", 2),
        ])
        db.executemany("INSERT INTO bcs_state_machine_node_runs VALUES(?,?,?,?,?,?,?,?,?)", [
            ("run-initial", "answer", "running", 0, 1, "ceo", "smnode-run-initial-answer-0", "active", "local"),
            ("run-explicit", "answer", "aborted", 0, 1, "ceo", "smnode-run-explicit-answer-0", "active", "local"),
        ])
        db.executemany("INSERT INTO bcs_collaboration_definitions VALUES(?,?,?,?,?,?,?)", [
            ("definition-1", 1, "E2E", "yaml", "hash-1", "active", "local"),
            ("definition-1", 2, "E2E", "yaml", "hash-2", "active", "local"),
        ])

    def tearDown(self):
        self.storage.close()

    def test_group_runs_and_run_keep_distinct_session_and_delivery_identity(self):
        rows = self.storage.group_runs("group-1")
        self.assertEqual([row["run_id"] for row in rows], ["run-initial", "run-explicit"])
        result = self.storage.run("run-explicit")
        self.assertEqual(result["session"]["session_id"], "session-explicit")
        self.assertEqual(result["nodes"][0]["delivery_request_id"], "smnode-run-explicit-answer-0")
        self.assertEqual(set(result), {"run", "nodes", "session"})

    def test_more_than_two_group_runs_is_rejected(self):
        self.storage.db.execute("INSERT INTO bcs_group_sessions VALUES(?,?,?,?,?,?,?)",
                                ("session-third", "group-1", "local", "running", "service_invocation", 1, None))
        self.storage.db.execute("INSERT INTO bcs_state_machine_runs VALUES(?,?,?,?,?,?,?,?,?)",
                                ("run-third", "session-third", "group-1", "definition-1", 2, "running", "active", "local", 3))
        with self.assertRaises(LocalStructuredStorageError):
            self.storage.group_runs("group-1")

    def test_group_deleted_requires_absence_and_retained_history(self):
        self.storage.db.execute("DELETE FROM bcs_group_sessions WHERE group_id='group-1'")
        result = self.storage.group_deleted(
            "group-1", ["run-initial", "run-explicit"], "definition-1"
        )
        self.assertFalse(result["group_present"])
        self.assertEqual(result["session_count"], 0)
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual([row["version"] for row in result["definitions"]], [1, 2])

    def test_group_deleted_rejects_surviving_session_or_missing_history(self):
        self.storage.db.execute("DELETE FROM bcs_group_sessions WHERE group_id='group-1'")
        self.storage.db.execute("INSERT INTO bcs_groups VALUES(?,?)", ("group-1", "local"))
        with self.assertRaises(LocalStructuredStorageError):
            self.storage.group_deleted("group-1", ["run-initial", "run-explicit"], "definition-1")
        self.storage.db.execute("DELETE FROM bcs_groups")
        self.storage.db.execute("DELETE FROM bcs_state_machine_node_runs WHERE run_id='run-initial'")
        with self.assertRaises(LocalStructuredStorageError):
            self.storage.group_deleted("group-1", ["run-initial", "run-explicit"], "definition-1")
