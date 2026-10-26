"""Focused fixed-SQL tests for Event Webhook persistence evidence."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_event_storage import LocalEventStorageError, OwnedLocalEventStorage


SCHEMA = """
CREATE TABLE bcs_groups(group_id TEXT, env TEXT, status TEXT, driver_bot TEXT, group_kind TEXT, record_status TEXT, lifecycle_status TEXT);
CREATE TABLE bcs_group_participants(group_id TEXT, bot_uuid TEXT, env TEXT);
CREATE TABLE bcs_group_sessions(session_id TEXT, group_id TEXT, env TEXT, status TEXT);
CREATE TABLE bcs_group_runtime_bindings(group_id TEXT, env TEXT);
CREATE TABLE bcs_event_subscriptions(subscription_id TEXT, scope_type TEXT, scope_id TEXT, status TEXT, current_revision INTEGER, env TEXT);
CREATE TABLE bcs_events(event_id TEXT, event_type TEXT, group_id TEXT, session_id TEXT, fanout_status TEXT, env TEXT);
CREATE TABLE bcs_event_deliveries(delivery_id TEXT, event_id TEXT, subscription_id TEXT, subscription_revision INTEGER, status TEXT, attempt_count INTEGER, env TEXT);
CREATE TABLE bcs_event_delivery_attempts(delivery_id TEXT, attempt_no INTEGER, result TEXT, http_status INTEGER);
"""


class FixtureStorage(OwnedLocalEventStorage):
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


class LocalEventStorageTests(unittest.TestCase):
    def setUp(self):
        self.storage = FixtureStorage()
        db = self.storage.db
        db.execute("INSERT INTO bcs_groups VALUES(?,?,?,?,?,?,?)",
                   ("group-1", "local", "active", "pm", "normal", "active", "active"))
        db.executemany("INSERT INTO bcs_group_participants VALUES(?,?,?)", [
            ("group-1", "pm", "local"), ("group-1", "eng", "local")])
        db.execute("INSERT INTO bcs_group_sessions VALUES(?,?,?,?)",
                   ("session-implicit", "group-1", "local", "running"))
        db.execute("INSERT INTO bcs_event_subscriptions VALUES(?,?,?,?,?,?)",
                   ("subscription-1", "group", "group-1", "active", 1, "local"))
        self.add_history("delivery-1", "event-1", "group.created", None, 1)

    def tearDown(self):
        self.storage.close()

    def add_history(self, delivery_id, event_id, event_type, session_id, revision=3):
        db = self.storage.db
        db.execute("INSERT INTO bcs_events VALUES(?,?,?,?,?,?)",
                   (event_id, event_type, "group-1", session_id, "completed", "local"))
        db.execute("INSERT INTO bcs_event_deliveries VALUES(?,?,?,?,?,?,?)",
                   (delivery_id, event_id, "subscription-1", revision, "succeeded", 1, "local"))
        db.execute("INSERT INTO bcs_event_delivery_attempts VALUES(?,?,?,?)",
                   (delivery_id, 1, "success", 200))

    def test_group_returns_exact_identity_sessions_and_subscription_shapes(self):
        result = self.storage.group("group-1", "subscription-1")
        self.assertEqual(set(result), {"group", "sessions", "subscription"})
        self.assertEqual(result["group"]["member_ids"], ["eng", "pm"])
        self.assertEqual(result["sessions"], [{
            "session_id": "session-implicit", "group_id": "group-1", "status": "running"}])
        self.assertEqual(result["subscription"], {
            "subscription_id": "subscription-1", "scope_type": "group",
            "scope_id": "group-1", "status": "active", "revision": 1})
        self.storage.db.execute("INSERT INTO bcs_group_sessions VALUES(?,?,?,?)",
                                ("session-explicit", "group-1", "local", "running"))
        self.storage.db.execute("UPDATE bcs_event_subscriptions SET current_revision=3")
        result = self.storage.group("group-1", "subscription-1")
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(result["subscription"]["revision"], 3)

    def test_group_rejects_wrong_scope_environment_and_extra_identity_rows(self):
        db = self.storage.db
        db.execute("UPDATE bcs_event_subscriptions SET scope_id='foreign'")
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")
        db.execute("UPDATE bcs_event_subscriptions SET scope_id='group-1', env='prod'")
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")
        db.execute("UPDATE bcs_event_subscriptions SET env='local'")
        db.execute("INSERT INTO bcs_groups VALUES(?,?,?,?,?,?,?)",
                   ("group-1", "local", "active", "pm", "normal", "active", "active"))
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")

    def test_group_rejects_missing_or_different_revision_status_and_overflow(self):
        db = self.storage.db
        db.execute("DELETE FROM bcs_event_subscriptions")
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")
        db.execute("INSERT INTO bcs_event_subscriptions VALUES(?,?,?,?,?,?)",
                   ("subscription-1", "group", "group-1", "disabled", 2, "local"))
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")
        db.execute("UPDATE bcs_event_subscriptions SET status='active', current_revision=9")
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")
        db.execute("UPDATE bcs_event_subscriptions SET current_revision=1")
        db.executemany("INSERT INTO bcs_group_sessions VALUES(?,?,?,?)", [
            (f"session-{index:02d}", "group-1", "local", "running") for index in range(32)])
        with self.assertRaises(LocalEventStorageError):
            self.storage.group("group-1", "subscription-1")

    def test_delivery_returns_only_safe_successful_event_and_attempt_proof(self):
        result = self.storage.delivery("delivery-1", "group-1", "subscription-1")
        self.assertEqual(set(result), {"delivery", "event", "attempts"})
        self.assertEqual(result["delivery"], {
            "delivery_id": "delivery-1", "event_id": "event-1",
            "subscription_id": "subscription-1", "subscription_revision": 1,
            "status": "succeeded", "attempt_count": 1})
        self.assertEqual(result["event"], {
            "event_id": "event-1", "event_type": "group.created", "group_id": "group-1",
            "session_id": None, "fanout_status": "completed"})
        self.assertEqual(result["attempts"], [{
            "delivery_id": "delivery-1", "attempt_no": 1,
            "result": "success", "http_status": 200}])

    def test_delivery_rejects_wrong_group_subscription_env_or_terminal_status(self):
        for args in (("delivery-1", "foreign", "subscription-1"),
                     ("delivery-1", "group-1", "foreign")):
            with self.subTest(args=args), self.assertRaises(LocalEventStorageError):
                self.storage.delivery(*args)
        db = self.storage.db
        db.execute("UPDATE bcs_event_deliveries SET env='prod'")
        with self.assertRaises(LocalEventStorageError):
            self.storage.delivery("delivery-1", "group-1", "subscription-1")
        db.execute("UPDATE bcs_event_deliveries SET env='local', status='retry_wait'")
        with self.assertRaises(LocalEventStorageError):
            self.storage.delivery("delivery-1", "group-1", "subscription-1")
        db.execute("UPDATE bcs_event_deliveries SET status='succeeded'")
        db.execute("UPDATE bcs_event_delivery_attempts SET result='retryable', http_status=503")
        with self.assertRaises(LocalEventStorageError):
            self.storage.delivery("delivery-1", "group-1", "subscription-1")

    def delete_live_rows(self):
        db = self.storage.db
        db.execute("DELETE FROM bcs_groups WHERE group_id='group-1' AND env='local'")
        db.execute("DELETE FROM bcs_group_sessions WHERE group_id='group-1' AND env='local'")
        db.execute("UPDATE bcs_event_subscriptions SET status='deleted', current_revision=4")

    def test_deleted_proves_absence_tombstone_and_preserved_bounded_history(self):
        self.storage.db.execute("INSERT INTO bcs_group_sessions VALUES(?,?,?,?)",
                                ("session-explicit", "group-1", "local", "running"))
        self.add_history("delivery-2", "event-2", "message.created", "session-implicit", 3)
        self.delete_live_rows()
        result = self.storage.deleted(
            "group-1", "subscription-1", ["session-implicit", "session-explicit"])
        self.assertEqual(set(result), {"group_id", "subscription_id", "group_present",
                                      "session_count", "subscription", "deliveries", "events"})
        self.assertFalse(result["group_present"])
        self.assertEqual(result["session_count"], 0)
        self.assertEqual(result["subscription"]["status"], "deleted")
        self.assertEqual(result["subscription"]["revision"], 4)
        self.assertEqual({row["delivery_id"] for row in result["deliveries"]},
                         {"delivery-1", "delivery-2"})
        self.assertEqual({row["event_id"] for row in result["events"]},
                         {"event-1", "event-2"})

    def test_deleted_rejects_physical_purge_wrong_tombstone_or_surviving_state(self):
        self.delete_live_rows()
        self.storage.db.execute("DELETE FROM bcs_events")
        self.storage.db.execute("DELETE FROM bcs_event_deliveries")
        with self.assertRaises(LocalEventStorageError):
            self.storage.deleted("group-1", "subscription-1", ["session-implicit"])
        self.storage.close()
        self.storage = FixtureStorage()
        self.setUp_fixture_for_deleted_failure()
        self.storage.db.execute("UPDATE bcs_event_subscriptions SET current_revision=3")
        with self.assertRaises(LocalEventStorageError):
            self.storage.deleted("group-1", "subscription-1", ["session-implicit"])
        self.storage.db.execute("UPDATE bcs_event_subscriptions SET current_revision=4")
        self.storage.db.execute("INSERT INTO bcs_group_runtime_bindings VALUES(?,?)",
                                ("group-1", "local"))
        with self.assertRaises(LocalEventStorageError):
            self.storage.deleted("group-1", "subscription-1", ["session-implicit"])

    def setUp_fixture_for_deleted_failure(self):
        db = self.storage.db
        db.execute("INSERT INTO bcs_event_subscriptions VALUES(?,?,?,?,?,?)",
                   ("subscription-1", "group", "group-1", "deleted", 4, "local"))
        self.add_history("delivery-1", "event-1", "group.created", None, 1)

    def test_deleted_rejects_foreign_scope_env_and_history_overflow(self):
        self.delete_live_rows()
        self.storage.db.execute("UPDATE bcs_event_subscriptions SET scope_id='foreign'")
        with self.assertRaises(LocalEventStorageError):
            self.storage.deleted("group-1", "subscription-1", ["session-implicit"])
        self.storage.db.execute("UPDATE bcs_event_subscriptions SET scope_id='group-1', env='prod'")
        with self.assertRaises(LocalEventStorageError):
            self.storage.deleted("group-1", "subscription-1", ["session-implicit"])
        self.storage.db.execute("UPDATE bcs_event_subscriptions SET env='local'")
        for index in range(2, 34):
            self.add_history(f"delivery-{index}", f"event-{index}", "session.created",
                             "session-implicit", 3)
        with self.assertRaises(LocalEventStorageError):
            self.storage.deleted("group-1", "subscription-1", ["session-implicit"])


if __name__ == "__main__":
    unittest.main()


class EventLogicalMessageTests(unittest.TestCase):
    def setUp(self):
        self.storage=FixtureStorage();self.addCleanup(self.storage.close)
        self.storage.db.execute('CREATE TABLE bcs_messages(message_id TEXT,group_id TEXT,session_id TEXT,sender_id TEXT,env TEXT,message_type TEXT,content TEXT)')
        self.ids=['session-implicit','session-explicit']

    def test_exact_absence_then_unique_logical_message_is_not_legacy_api_identity(self):
        self.assertEqual(self.storage.message('group-1',self.ids,'pm',absent=True)['matching_messages'],0)
        self.storage.db.execute('INSERT INTO bcs_messages VALUES(?,?,?,?,?,?,?)',
            ('logical-id','group-1','session-explicit','pm','local','chat','"Webhook delivery verification"'))
        proof=self.storage.message('group-1',self.ids,'pm')
        self.assertEqual(proof['message_id'],'logical-id')
        self.assertEqual(proof['identity_kind'],'PERSISTED_LOGICAL_MESSAGE_NOT_LEGACY_RESPONSE_ID')
        with self.assertRaises(LocalEventStorageError):self.storage.message('group-1',self.ids,'pm',absent=True)

    def test_wrong_scope_sender_content_environment_and_ambiguous_duplicates_rejected(self):
        original=['logical-id','group-1','session-explicit','pm','local','chat','"Webhook delivery verification"']
        for index,value in [(1,'foreign'),(2,'foreign'),(3,'eng'),(4,'production'),(6,'"other"')]:
            row=list(original);row[index]=value
            self.storage.db.execute('DELETE FROM bcs_messages');self.storage.db.execute('INSERT INTO bcs_messages VALUES(?,?,?,?,?,?,?)',row)
            with self.subTest(index=index),self.assertRaises(LocalEventStorageError):self.storage.message('group-1',self.ids,'pm')
        self.storage.db.execute('DELETE FROM bcs_messages')
        self.storage.db.executemany('INSERT INTO bcs_messages VALUES(?,?,?,?,?,?,?)',[original,original])
        with self.assertRaises(LocalEventStorageError):self.storage.message('group-1',self.ids,'pm')
