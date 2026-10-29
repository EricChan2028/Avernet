"""Durable, exact-scope execution-effect journal.

This is a correlation and state contract, not a stop API and not server
authorization. It refuses unknown IDs, foreign parents and invalid terminal
transitions. Product adapters must supply exact effect identifiers; this module
never discovers or cancels broad Bot/queue/provider scopes.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Iterable

from .correlation import valid_traceparent
from .ledger import Ledger, LedgerError
from .profile import identifier

EFFECT_KINDS = {"delivery", "context", "run", "subscription"}
EFFECT_STATES = {
    "INTENDED", "ADMITTED", "QUEUED", "RUNNING", "CANCELLING", "CANCELLED",
    "COMPLETED", "FAILED", "UNKNOWN", "RETAINED",
}
TERMINAL = {"CANCELLED", "COMPLETED", "FAILED", "RETAINED", "UNKNOWN"}
TRANSITIONS = {
    "INTENDED": {"ADMITTED", "UNKNOWN", "RETAINED"},
    "ADMITTED": {"QUEUED", "RUNNING", "CANCELLED", "FAILED", "UNKNOWN", "RETAINED"},
    "QUEUED": {"RUNNING", "CANCELLING", "CANCELLED", "COMPLETED", "FAILED", "UNKNOWN", "RETAINED"},
    "RUNNING": {"CANCELLING", "COMPLETED", "FAILED", "UNKNOWN", "RETAINED"},
    "CANCELLING": {"CANCELLED", "COMPLETED", "FAILED", "UNKNOWN", "RETAINED"},
    "CANCELLED": set(), "COMPLETED": set(), "FAILED": set(),
    "UNKNOWN": set(), "RETAINED": set(),
}


class ExecutionLedgerError(LedgerError):
    pass


class ExecutionLedger:
    """Use only with a writable, already-bound Run Ledger."""

    def __init__(self, ledger: Ledger):
        if ledger.db.execute("PRAGMA query_only").fetchone()[0]:
            raise ExecutionLedgerError("execution ledger requires a writable Run ledger")
        self.ledger = ledger
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.ledger.db.execute("""
            CREATE TABLE IF NOT EXISTS execution_contract (
                run_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL
            )
        """)
        self.ledger.db.execute("""
            CREATE TABLE IF NOT EXISTS execution_effect (
                handle TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                kind TEXT NOT NULL,
                parent_handle TEXT,
                request_id TEXT NOT NULL UNIQUE,
                effect_id TEXT,
                actor_id TEXT,
                session_id TEXT,
                state TEXT NOT NULL,
                terminal_reason TEXT,
                evidence_request_id TEXT,
                evidence_traceparent TEXT,
                FOREIGN KEY(parent_handle) REFERENCES execution_effect(handle)
            )
        """)
        columns = {row[1] for row in self.ledger.db.execute("PRAGMA table_info(execution_effect)")}
        if "session_id" not in columns:
            self.ledger.db.execute("ALTER TABLE execution_effect ADD COLUMN session_id TEXT")
        self.ledger.db.execute(
            "INSERT OR IGNORE INTO execution_contract(run_id,version) VALUES (?, 1)",
            (self.ledger.run_id,),
        )
        self.ledger.db.commit()

    def intent(self, case_id: str, attempt: int, kind: str, request_id: str,
               *, parent: str | None = None, actor_id: str | None = None,
               session_id: str | None = None) -> str:
        identifier(case_id, "case_id"); identifier(request_id, "request_id")
        if type(attempt) is not int or attempt < 1 or kind not in EFFECT_KINDS:
            raise ExecutionLedgerError("invalid execution effect intent")
        if parent is not None:
            parent_row = self._row(parent)
            if parent_row["state"] in TERMINAL or parent_row["case_id"] != case_id \
                    or parent_row["attempt"] != attempt:
                raise ExecutionLedgerError("execution effect parent is not live in this Case")
        if actor_id is not None:
            identifier(actor_id, "effect actor")
        if session_id is not None:
            identifier(session_id, "effect session")
        handle = uuid.uuid4().hex
        with self.ledger.db:
            self.ledger.db.execute(
                "INSERT INTO execution_effect(handle,case_id,attempt,kind,parent_handle,request_id,actor_id,session_id,state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'INTENDED')",
                (handle, case_id, attempt, kind, parent, request_id, actor_id, session_id),
            )
        return handle

    def observe(self, handle: str, effect_id: str, *, state: str = "ADMITTED",
                evidence_request_id: str | None = None,
                evidence_traceparent: str | None = None) -> None:
        identifier(effect_id, "effect_id")
        if state not in TRANSITIONS["INTENDED"] - {"UNKNOWN", "RETAINED"}:
            raise ExecutionLedgerError("initial observed execution state is not admissible")
        if evidence_request_id is not None: identifier(evidence_request_id, "evidence request")
        if evidence_traceparent is not None and not valid_traceparent(evidence_traceparent):
            raise ExecutionLedgerError("invalid execution evidence traceparent")
        row = self._row(handle)
        if row["effect_id"] == effect_id and row["state"] == state:
            return
        if row["state"] != "INTENDED" or row["effect_id"] is not None:
            raise ExecutionLedgerError("cannot rebind an execution effect")
        with self.ledger.db:
            self.ledger.db.execute(
                "UPDATE execution_effect SET effect_id=?,state=?,evidence_request_id=?,evidence_traceparent=? WHERE handle=?",
                (effect_id, state, evidence_request_id, evidence_traceparent, handle),
            )

    def unknown(self, handle: str, *, reason: str = "") -> None:
        self.transition(handle, "UNKNOWN", reason=reason)

    def transition(self, handle: str, state: str, *, reason: str = "",
                   evidence_request_id: str | None = None,
                   evidence_traceparent: str | None = None) -> None:
        if state not in EFFECT_STATES:
            raise ExecutionLedgerError("unknown execution state")
        if evidence_request_id is not None: identifier(evidence_request_id, "evidence request")
        if evidence_traceparent is not None and not valid_traceparent(evidence_traceparent):
            raise ExecutionLedgerError("invalid execution evidence traceparent")
        row = self._row(handle)
        if state == row["state"]:
            return
        if state not in TRANSITIONS[row["state"]]:
            raise ExecutionLedgerError(f"invalid execution transition {row['state']} -> {state}")
        if state in {"CANCELLED", "COMPLETED", "FAILED", "RETAINED", "UNKNOWN"} and not reason:
            raise ExecutionLedgerError("terminal execution state requires a reason")
        with self.ledger.db:
            self.ledger.db.execute(
                "UPDATE execution_effect SET state=?,terminal_reason=?,evidence_request_id=COALESCE(?,evidence_request_id),"
                "evidence_traceparent=COALESCE(?,evidence_traceparent) WHERE handle=?",
                (state, reason or None, evidence_request_id, evidence_traceparent, handle),
            )

    def require_session_quiescent(self, session_id: str) -> None:
        identifier(session_id, "execution session")
        try:
            rows = [dict(row) for row in self.ledger.db.execute(
                "SELECT * FROM execution_effect WHERE session_id=? ORDER BY rowid", (session_id,)
            )]
        except sqlite3.OperationalError:
            raise ExecutionLedgerError("execution effect session binding is unavailable") from None
        if not rows:
            raise ExecutionLedgerError("Session has no exact execution-effect binding")
        self.require_handles_quiescent(row["handle"] for row in rows)

    def snapshot(self, *, case_id: str | None = None, attempt: int | None = None) -> list[dict]:
        sql = "SELECT * FROM execution_effect"
        values: list[object] = []
        clauses = []
        if case_id is not None:
            identifier(case_id, "case_id"); clauses.append("case_id=?"); values.append(case_id)
        if attempt is not None:
            if type(attempt) is not int or attempt < 1: raise ExecutionLedgerError("invalid attempt")
            clauses.append("attempt=?"); values.append(attempt)
        if clauses: sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rowid"
        return [dict(row) for row in self.ledger.db.execute(sql, values)]

    def require_handles_quiescent(self, handles: Iterable[str]) -> None:
        rows = [self._row(handle) for handle in handles]
        unresolved = [row["handle"] for row in rows if row["state"] not in TERMINAL]
        if unresolved:
            raise ExecutionLedgerError("execution effects are not terminal")
        if any(row["state"] in {"UNKNOWN", "RETAINED"} for row in rows):
            raise ExecutionLedgerError("execution effects remain uncertain or retained")

    def require_quiescent(self, *, case_id: str, attempt: int) -> None:
        rows = self.snapshot(case_id=case_id, attempt=attempt)
        self.require_handles_quiescent(row["handle"] for row in rows)

    def _row(self, handle: str) -> sqlite3.Row:
        if not isinstance(handle, str) or not handle:
            raise ExecutionLedgerError("invalid execution effect handle")
        row = self.ledger.db.execute("SELECT * FROM execution_effect WHERE handle=?", (handle,)).fetchone()
        if row is None: raise ExecutionLedgerError("unknown execution effect handle")
        return row
