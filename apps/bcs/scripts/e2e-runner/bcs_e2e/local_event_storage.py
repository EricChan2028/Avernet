"""Bounded read-only SQLite evidence for the original Event Webhook Story.

The observer exposes only code-owned identifier/status/revision projections. It
never returns callback URLs, credentials, payload bodies, or caller-defined SQL.
"""
from __future__ import annotations

import hashlib
import json

from .local_sqlite import OwnedSqliteDatabase
from .profile import identifier
from .run_state import RunStateError


class LocalEventStorageError(RunStateError):
    pass


def _require(condition: object, message: str) -> None:
    if not condition:
        raise LocalEventStorageError(message)


def _rows(db, sql: str, params: tuple, maximum: int = 32) -> list[dict]:
    _require(0 <= maximum <= 32, "Event SQLite row bound is invalid")
    rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
    _require(len(rows) <= maximum, "Event SQLite evidence exceeded its 32-row bound")
    return rows


def _one(db, sql: str, params: tuple, label: str) -> dict:
    rows = _rows(db, sql, params, 1)
    _require(len(rows) == 1, f"Event SQLite evidence requires one exact {label}")
    return rows[0]


_SCHEMA = {
    "bcs_messages": {"message_id","group_id","session_id","sender_id","env","message_type","content"},
    "bcs_groups": {
        "group_id", "env", "status", "driver_bot", "group_kind", "record_status",
        "lifecycle_status",
    },
    "bcs_group_participants": {"group_id", "bot_uuid", "env"},
    "bcs_group_sessions": {"session_id", "group_id", "env", "status"},
    "bcs_group_runtime_bindings": {"group_id", "env"},
    "bcs_event_subscriptions": {
        "subscription_id", "scope_type", "scope_id", "status", "current_revision", "env",
    },
    "bcs_events": {
        "event_id", "event_type", "group_id", "session_id", "fanout_status", "env",
    },
    "bcs_event_deliveries": {
        "delivery_id", "event_id", "subscription_id", "subscription_revision", "status",
        "attempt_count", "env",
    },
    "bcs_event_delivery_attempts": {
        "delivery_id", "attempt_no", "result", "http_status",
    },
}

_ALLOWED_EVENT_TYPES = {
    "group.created", "event_subscription.test", "session.created", "message.created",
}


class OwnedLocalEventStorage(OwnedSqliteDatabase):
    """Fixed ID-filtered, env=local projections for one owned webhook story."""

    @staticmethod
    def _schema(db, *tables: str) -> None:
        for table in tables:
            _require(table in _SCHEMA, "Event SQLite table is not admitted")
            kinds = db.execute(
                "SELECT type FROM sqlite_master WHERE name=? LIMIT 2", (table,)
            ).fetchall()
            _require(len(kinds) == 1 and kinds[0]["type"] == "table",
                     "Event SQLite table is missing or is not a table")
            columns = db.execute(f'PRAGMA table_info("{table}")').fetchmany(101)
            _require(len(columns) <= 100 and _SCHEMA[table] <= {row["name"] for row in columns},
                     "Event SQLite schema differs from the reviewed projection")

    @staticmethod
    def _subscription(db, group_id: str, subscription_id: str) -> dict:
        row = _one(db, """SELECT subscription_id, scope_type, scope_id, status,
            current_revision AS revision FROM bcs_event_subscriptions
            WHERE env=? AND subscription_id=? AND scope_type=? AND scope_id=? LIMIT 2""",
                   ("local", subscription_id, "group", group_id), "Group Subscription")
        _require(row["status"] == "active" and row["revision"] in {1, 3},
                 "Group Subscription status or revision differs from an observable Story phase")
        return row

    def message(self, group_id: str, session_ids, sender_id: str, *, absent: bool = False) -> dict:
        group_id=identifier(group_id,"message Group");sender_id=identifier(sender_id,"message sender")
        _require(type(absent) is bool and isinstance(session_ids,(list,tuple)) and len(set(session_ids))==2,
                 "message observation requires two exact owned Sessions")
        for sid in session_ids:identifier(sid,"message Session")
        text="Webhook delivery verification"
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db,"bcs_messages")
            rows=_rows(db,"""SELECT message_id,group_id,session_id,sender_id,message_type FROM bcs_messages
                WHERE env=? AND group_id=? AND sender_id=? AND message_type=? AND content=? LIMIT 2""",
                ("local",group_id,sender_id,"chat",json.dumps(text)),1)
        if absent:
            _require(not rows,"test message already exists before the one admitted send")
            return {"group_id":group_id,"sender_id":sender_id,"matching_messages":0}
        _require(len(rows)==1 and rows[0]["session_id"] in session_ids,
                 "one exact persisted test message was not found")
        return {**rows[0],"content_sha256":hashlib.sha256(text.encode()).hexdigest(),
                "identity_kind":"PERSISTED_LOGICAL_MESSAGE_NOT_LEGACY_RESPONSE_ID"}

    def group(self, group_id: str, subscription_id: str) -> dict:
        group_id = identifier(group_id, "Event Group ID")
        subscription_id = identifier(subscription_id, "Event Subscription ID")
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db, "bcs_groups", "bcs_group_participants", "bcs_group_sessions",
                         "bcs_event_subscriptions")
            group = _one(db, """SELECT group_id, status, driver_bot, group_kind,
                record_status, lifecycle_status FROM bcs_groups
                WHERE env=? AND group_id=? LIMIT 2""", ("local", group_id), "Group")
            members = _rows(db, """SELECT bot_uuid FROM bcs_group_participants
                WHERE env=? AND group_id=? ORDER BY bot_uuid LIMIT 33""",
                            ("local", group_id))
            sessions = _rows(db, """SELECT session_id, group_id, status
                FROM bcs_group_sessions WHERE env=? AND group_id=?
                ORDER BY session_id LIMIT 33""", ("local", group_id))
            subscription = self._subscription(db, group_id, subscription_id)
        member_ids = [row["bot_uuid"] for row in members]
        _require(len(member_ids) == 2 and len(set(member_ids)) == 2,
                 "Event Group member identity differs from PM and ENG")
        _require(group["status"] == "active" and group["group_kind"] == "normal"
                 and group["record_status"] == "active"
                 and group["lifecycle_status"] == "active"
                 and group["driver_bot"] in member_ids,
                 "Event Group identity or active state differs")
        _require(len(sessions) in {1, 2}
                 and len({row["session_id"] for row in sessions}) == len(sessions)
                 and all(row["group_id"] == group_id and row["status"] == "running"
                         for row in sessions),
                 "Event Group must have exactly one or two distinct running Sessions")
        group["member_ids"] = member_ids
        return {"group": group, "sessions": sessions, "subscription": subscription}

    def delivery(self, delivery_id: str, group_id: str, subscription_id: str) -> dict:
        delivery_id = identifier(delivery_id, "Event Delivery ID")
        group_id = identifier(group_id, "Event Group ID")
        subscription_id = identifier(subscription_id, "Event Subscription ID")
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db, "bcs_event_deliveries", "bcs_events",
                         "bcs_event_delivery_attempts")
            delivery = _one(db, """SELECT d.delivery_id, d.event_id, d.subscription_id,
                d.subscription_revision, d.status, d.attempt_count
                FROM bcs_event_deliveries d JOIN bcs_events e
                  ON e.env=d.env AND e.event_id=d.event_id
                WHERE d.env=? AND d.delivery_id=? AND d.subscription_id=?
                  AND e.group_id=? LIMIT 2""",
                            ("local", delivery_id, subscription_id, group_id), "Delivery")
            event = _one(db, """SELECT e.event_id, e.event_type, e.group_id, e.session_id,
                e.fanout_status FROM bcs_events e JOIN bcs_event_deliveries d
                  ON d.env=e.env AND d.event_id=e.event_id
                WHERE e.env=? AND e.event_id=? AND e.group_id=?
                  AND d.delivery_id=? AND d.subscription_id=? LIMIT 2""",
                         ("local", delivery["event_id"], group_id, delivery_id, subscription_id),
                         "Delivery Event")
            attempts = _rows(db, """SELECT a.delivery_id, a.attempt_no, a.result, a.http_status
                FROM bcs_event_delivery_attempts a JOIN bcs_event_deliveries d
                  ON d.delivery_id=a.delivery_id
                WHERE d.env=? AND d.delivery_id=? AND d.subscription_id=?
                ORDER BY a.attempt_no LIMIT 33""",
                             ("local", delivery_id, subscription_id))
        _require(delivery["status"] == "succeeded"
                 and type(delivery["subscription_revision"]) is int
                 and delivery["subscription_revision"] in {1, 3}
                 and type(delivery["attempt_count"]) is int
                 and delivery["attempt_count"] == len(attempts) > 0,
                 "Delivery is not an exact successful Story delivery")
        _require(event["event_type"] in _ALLOWED_EVENT_TYPES
                 and event["group_id"] == group_id
                 and event["fanout_status"] == "completed",
                 "Delivery Event scope, type, or fanout status differs")
        _require([row["attempt_no"] for row in attempts] == list(range(1, len(attempts) + 1))
                 and attempts[-1]["result"] == "success"
                 and isinstance(attempts[-1]["http_status"], int)
                 and 200 <= attempts[-1]["http_status"] < 300
                 and all(row["delivery_id"] == delivery_id for row in attempts),
                 "Delivery Attempts do not prove a bounded successful terminal attempt")
        return {"delivery": delivery, "event": event, "attempts": attempts}

    def deleted(self, group_id: str, subscription_id: str, session_ids) -> dict:
        group_id = identifier(group_id, "deleted Event Group ID")
        subscription_id = identifier(subscription_id, "deleted Event Subscription ID")
        _require(isinstance(session_ids, (list, tuple)) and len(session_ids) in {1, 2},
                 "Event deletion requires the exact one or two Session IDs")
        session_ids = tuple(identifier(value, "deleted Event Session ID") for value in session_ids)
        _require(len(set(session_ids)) == len(session_ids),
                 "deleted Event Session IDs must be distinct")
        placeholders = ",".join("?" for _ in session_ids)
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db, "bcs_groups", "bcs_group_sessions", "bcs_group_runtime_bindings",
                         "bcs_event_subscriptions", "bcs_event_deliveries", "bcs_events")
            groups = _rows(db, "SELECT group_id FROM bcs_groups WHERE env=? AND group_id=? LIMIT 2",
                           ("local", group_id), 1)
            sessions = _rows(db, """SELECT session_id FROM bcs_group_sessions
                WHERE env=? AND group_id=? LIMIT 33""", ("local", group_id))
            named_sessions = _rows(db, f"""SELECT session_id FROM bcs_group_sessions
                WHERE env=? AND group_id=? AND session_id IN ({placeholders}) LIMIT 33""",
                                   ("local", group_id, *session_ids))
            bindings = _rows(db, """SELECT group_id FROM bcs_group_runtime_bindings
                WHERE env=? AND group_id=? LIMIT 2""", ("local", group_id), 1)
            subscription = _one(db, """SELECT subscription_id, scope_type, scope_id, status,
                current_revision AS revision FROM bcs_event_subscriptions
                WHERE env=? AND subscription_id=? AND scope_type=? AND scope_id=? LIMIT 2""",
                                ("local", subscription_id, "group", group_id),
                                "deleted Group Subscription")
            deliveries = _rows(db, """SELECT d.delivery_id, d.event_id, d.subscription_id,
                d.subscription_revision, d.status, d.attempt_count
                FROM bcs_event_deliveries d JOIN bcs_events e
                  ON e.env=d.env AND e.event_id=d.event_id
                WHERE d.env=? AND d.subscription_id=? AND e.group_id=?
                ORDER BY d.delivery_id LIMIT 33""", ("local", subscription_id, group_id))
            events = _rows(db, """SELECT DISTINCT e.event_id, e.event_type, e.group_id,
                e.session_id, e.fanout_status FROM bcs_events e
                JOIN bcs_event_deliveries d ON d.env=e.env AND d.event_id=e.event_id
                WHERE e.env=? AND e.group_id=? AND d.subscription_id=?
                ORDER BY e.event_id LIMIT 33""", ("local", group_id, subscription_id))
        _require(not groups and not sessions and not named_sessions and not bindings,
                 "Event Group, Session, or runtime binding survived deletion")
        _require(subscription == {
            "subscription_id": subscription_id, "scope_type": "group", "scope_id": group_id,
            "status": "deleted", "revision": 4,
        }, "Event Subscription tombstone differs from deleted revision four")
        _require(deliveries and events
                 and {row["event_id"] for row in deliveries} == {row["event_id"] for row in events}
                 and all(row["subscription_id"] == subscription_id
                         and row["status"] == "succeeded"
                         and row["subscription_revision"] in {1, 3}
                         and type(row["attempt_count"]) is int and row["attempt_count"] > 0
                         for row in deliveries)
                 and all(row["group_id"] == group_id
                         and row["event_type"] in _ALLOWED_EVENT_TYPES
                         and row["fanout_status"] == "completed" for row in events),
                 "bounded retained Delivery/Event history differs")
        return {"group_id": group_id, "subscription_id": subscription_id,
                "group_present": False, "session_count": 0,
                "subscription": subscription, "deliveries": deliveries, "events": events}
