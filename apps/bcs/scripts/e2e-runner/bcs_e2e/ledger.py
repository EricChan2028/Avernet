"""Durable resource journal, not a substitute for server-side authorization.

This increment only produces cleanup plans; it never issues deletion requests.
Use a private run directory. A writable/tampered journal is not trusted evidence
for a future destructive executor without independent ownership verification.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import uuid

from .correlation import valid_traceparent
from .profile import identifier


class LedgerError(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path, run_id: str, profile_digest: str, *,
                 create: bool = False, read_only: bool = False):
        if create and read_only:
            raise LedgerError("read-only ledger cannot be created")
        identifier(run_id, "run_id")
        if len(profile_digest) != 64 or any(c not in "0123456789abcdef" for c in profile_digest):
            raise LedgerError("invalid profile digest")
        # Never silently create a new empty journal during recovery.
        if not create and (not path.is_file() or path.is_symlink()):
            raise LedgerError("existing regular ledger required")
        if create:
            # Exclusive creation prevents adopting another run's journal.
            import os
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        mode = "ro" if read_only else "rw"
        # URI escaping preserves paths containing '?' or '#'; mode=rw also
        # prevents recovery from creating a replacement DB after a path race.
        self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=" + mode, uri=True)
        self.db.row_factory = sqlite3.Row
        self.run_id = run_id
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            if not create:
                # Validate the Run before any additive schema operation.
                self._check_binding(run_id, profile_digest)
            if read_only:
                self.db.execute("PRAGMA query_only=ON")
                return
            if create:
                self.db.executescript("""
                    CREATE TABLE run (run_id TEXT PRIMARY KEY, profile_digest TEXT NOT NULL);
                    CREATE TABLE resource (
                        handle TEXT PRIMARY KEY, case_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                        kind TEXT NOT NULL, parent TEXT REFERENCES resource(handle),
                        request_id TEXT NOT NULL UNIQUE, origin TEXT NOT NULL,
                        resource_id TEXT, state TEXT NOT NULL,
                        UNIQUE(kind, resource_id)
                    );
                """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS correlation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                    step TEXT NOT NULL, operation TEXT NOT NULL,
                    method TEXT, path TEXT, request_id TEXT NOT NULL,
                    response_request_id TEXT, submitted_traceparent TEXT,
                    effective_traceparent TEXT, resource_id TEXT
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS termination_state (
                    resource_handle TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    last_reason TEXT,
                    history_json TEXT NOT NULL
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS diagnostic (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                    source TEXT NOT NULL, status TEXT NOT NULL,
                    environment TEXT NOT NULL, request_id TEXT,
                    traceparent TEXT, start_epoch INTEGER NOT NULL,
                    end_epoch INTEGER NOT NULL, record_count INTEGER NOT NULL,
                    truncated INTEGER NOT NULL
                )
            """)
            if create:
                with self.db:
                    self.db.execute("INSERT INTO run VALUES (?, ?)", (run_id, profile_digest))
            self._check_binding(run_id, profile_digest)
        except Exception:
            self.db.close()
            raise

    def _check_binding(self, run_id: str, profile_digest: str) -> None:
        binding = self.db.execute("SELECT run_id, profile_digest FROM run").fetchall()
        if len(binding) != 1 or tuple(binding[0]) != (run_id, profile_digest):
            raise LedgerError("run or environment/profile binding mismatch")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def intent(self, case_id: str, attempt: int, kind: str, request_id: str,
               parent: str | None = None) -> str:
        identifier(case_id, "case_id")
        identifier(request_id, "request_id")
        if type(attempt) is not int or attempt < 1 or kind not in {"group", "session", "file"}:
            raise LedgerError("invalid attempt or unsupported resource type")
        if kind == "group":
            if parent is not None:
                raise LedgerError("group cannot have a parent in this pilot")
        else:
            row = self._get(parent)
            expected = "group" if kind == "session" else "session"
            if (row["kind"] != expected or row["origin"] != "created" or row["state"] != "live"
                    or row["case_id"] != case_id or row["attempt"] != attempt):
                raise LedgerError("parent must be a live owned resource in the same case attempt")
        handle = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO resource VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (handle, case_id, attempt, kind, parent, request_id, "unknown", None, "intended"))
        return handle

    def group_creation_intent(self, case_id: str, attempt: int, request_id: str) -> tuple[str, str]:
        """Journal the normal-group API's group and implicit initial Session atomically."""
        identifier(case_id, "case_id")
        identifier(request_id, "request_id")
        if type(attempt) is not int or attempt < 1:
            raise LedgerError("invalid group creation attempt")
        group, session = uuid.uuid4().hex, uuid.uuid4().hex
        with self.db:
            self.db.executemany("INSERT INTO resource VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (group, case_id, attempt, "group", None, request_id, "unknown", None, "intended"),
                (session, case_id, attempt, "session", group, "e2e-initial-" + session,
                 "unknown", None, "intended"),
            ])
        return group, session

    def record_correlation(self, case_id: str, attempt: int, evidence: dict) -> None:
        """Persist only bounded correlation metadata, never headers or bodies."""
        identifier(case_id, "case_id")
        if type(attempt) is not int or attempt < 1:
            raise LedgerError("invalid correlation attempt")
        request_id = evidence.get("submitted_request_id") or evidence.get("connect_request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            return
        operation = evidence.get("operation") or "unknown"
        if not isinstance(operation, str) or not 1 <= len(operation) <= 128:
            raise LedgerError("invalid correlation operation")
        for value, field in ((request_id, "request_id"),
                             (evidence.get("response_request_id"), "response_request_id")):
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128
                                      or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise LedgerError(f"invalid correlation {field}")
        submitted = evidence.get("submitted_traceparent")
        effective = evidence.get("effective_traceparent")
        if submitted is not None and not valid_traceparent(submitted):
            raise LedgerError("invalid submitted traceparent")
        if effective is not None and not valid_traceparent(effective):
            raise LedgerError("invalid effective traceparent")
        method = evidence.get("method")
        path = evidence.get("path")
        for value, field, maximum in ((method, "method", 16), (path, "path", 1024)):
            if value is not None and (not isinstance(value, str) or len(value) > maximum
                                      or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise LedgerError(f"invalid correlation {field}")
        with self.db:
            self.db.execute(
                "INSERT INTO correlation(case_id,attempt,step,operation,method,path,request_id,"
                "response_request_id,submitted_traceparent,effective_traceparent,resource_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, attempt, operation, operation, method, path, request_id,
                 evidence.get("response_request_id"), submitted, effective,
                 evidence.get("resource_id")),
            )

    def correlations(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT case_id,attempt,step,operation,method,path,request_id,response_request_id,"
            "submitted_traceparent,effective_traceparent,resource_id "
            "FROM correlation ORDER BY id")]

    def save_termination(self, *, resource_handle: str, case_id: str, attempt: int,
                         session_id: str, phase: str, reason: str | None,
                         history: list[dict[str, str]]) -> None:
        identifier(resource_handle, "termination resource handle")
        identifier(case_id, "termination case_id")
        identifier(session_id, "termination session_id")
        if type(attempt) is not int or attempt < 1 or not isinstance(phase, str) or not phase:
            raise LedgerError("invalid termination binding")
        if not isinstance(history, list) or len(history) > 64:
            raise LedgerError("termination history is out of bounds")
        encoded = json.dumps(history, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > 16_384:
            raise LedgerError("termination history is too large")
        with self.db:
            self.db.execute(
                "INSERT INTO termination_state(resource_handle,case_id,attempt,session_id,phase,last_reason,history_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(resource_handle) DO UPDATE SET "
                "case_id=excluded.case_id,attempt=excluded.attempt,session_id=excluded.session_id,phase=excluded.phase,"
                "last_reason=excluded.last_reason,history_json=excluded.history_json",
                (resource_handle, case_id, attempt, session_id, phase, reason, encoded),
            )

    def termination_state(self, resource_handle: str) -> dict | None:
        identifier(resource_handle, "termination resource handle")
        row = self.db.execute(
            "SELECT resource_handle,case_id,attempt,session_id,phase,last_reason,history_json "
            "FROM termination_state WHERE resource_handle=?", (resource_handle,)).fetchone()
        if row is None:
            return None
        try:
            history = json.loads(row["history_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise LedgerError("termination history is unreadable") from None
        if not isinstance(history, list):
            raise LedgerError("termination history is invalid")
        result = dict(row)
        result["history"] = history
        del result["history_json"]
        return result

    def termination_states(self, *, case_id: str | None = None, attempt: int | None = None) -> list[dict]:
        sql = "SELECT resource_handle,case_id,attempt,session_id,phase,last_reason,history_json FROM termination_state"
        values: list[object] = []
        clauses = []
        if case_id is not None:
            identifier(case_id, "termination case_id")
            clauses.append("case_id=?"); values.append(case_id)
        if attempt is not None:
            if type(attempt) is not int or attempt < 1: raise LedgerError("invalid termination attempt")
            clauses.append("attempt=?"); values.append(attempt)
        if clauses: sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rowid"
        results = []
        for row in self.db.execute(sql, values):
            item = dict(row)
            try:
                item["history"] = json.loads(item.pop("history_json"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raise LedgerError("termination history is unreadable") from None
            results.append(item)
        return results

    def has_execution_contract(self) -> bool:
        try:
            return self.db.execute(
                "SELECT 1 FROM execution_contract WHERE run_id=?", (self.run_id,)
            ).fetchone() is not None
        except sqlite3.OperationalError:
            return False

    def execution_effects(self, *, session_id: str | None = None,
                          case_id: str | None = None, attempt: int | None = None) -> list[dict]:
        try:
            sql = "SELECT * FROM execution_effect"
            values: list[object] = []
            clauses = []
            if session_id is not None:
                identifier(session_id, "execution session_id")
                clauses.append("session_id=?"); values.append(session_id)
            if case_id is not None:
                identifier(case_id, "execution case_id")
                clauses.append("case_id=?"); values.append(case_id)
            if attempt is not None:
                if type(attempt) is not int or attempt < 1: raise LedgerError("invalid execution attempt")
                clauses.append("attempt=?"); values.append(attempt)
            if clauses: sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY rowid"
            return [dict(row) for row in self.db.execute(sql, values)]
        except sqlite3.OperationalError as error:
            raise LedgerError("execution effect contract is unavailable") from None

    def record_diagnostic(self, case_id: str, attempt: int, result: dict) -> None:
        """Persist a bounded diagnostic summary, never raw log records."""
        identifier(case_id, "case_id")
        if type(attempt) is not int or attempt < 1 or not isinstance(result, dict):
            raise LedgerError("invalid diagnostic result")
        status = result.get("status")
        source = result.get("source")
        query = result.get("query")
        if status not in {"OBSERVED", "INCOMPLETE", "BLOCKED"} or not isinstance(source, str):
            raise LedgerError("invalid diagnostic status")
        if not isinstance(query, dict) or not isinstance(query.get("environment"), str):
            raise LedgerError("diagnostic query binding is missing")
        request_id = query.get("request_id")
        traceparent = query.get("traceparent")
        for value, field in ((request_id, "request_id"), (traceparent, "traceparent")):
            if value is not None and (not isinstance(value, str) or len(value) > 128
                                      or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise LedgerError(f"invalid diagnostic {field}")
        start_epoch, end_epoch = query.get("start_epoch"), query.get("end_epoch")
        count, truncated = result.get("record_count"), result.get("truncated", False)
        if (type(start_epoch) is not int or type(end_epoch) is not int
                or start_epoch < 0 or end_epoch < start_epoch
                or type(count) is not int or not 0 <= count <= 100
                or type(truncated) is not bool):
            raise LedgerError("diagnostic bounds are invalid")
        with self.db:
            self.db.execute(
                "INSERT INTO diagnostic(case_id,attempt,source,status,environment,request_id,"
                "traceparent,start_epoch,end_epoch,record_count,truncated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, attempt, source, status, query["environment"], request_id, traceparent,
                 start_epoch, end_epoch, count, int(truncated)),
            )

    def diagnostics(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT case_id,attempt,source,status,environment,request_id,traceparent,"
            "start_epoch,end_epoch,record_count,truncated FROM diagnostic ORDER BY id")]

    def observed(self, handle: str, resource_id: str, *, origin: str,
                 actual_request_id: str | None = None) -> None:
        identifier(resource_id, "resource_id")
        if origin not in {"created", "borrowed", "reused"}:
            raise LedgerError("explicit observed origin required")
        with self.db:
            row = self._get(handle)
            if row["state"] == "live" and row["origin"] == origin and row["resource_id"] == resource_id:
                return
            if row["state"] not in {"intended", "unknown"}:
                raise LedgerError("cannot rebind an existing resource")
            self.db.execute("UPDATE resource SET resource_id=?, origin=?, state='live' WHERE handle=?",
                            (resource_id, origin, handle))
            if actual_request_id is not None:
                if (not isinstance(actual_request_id, str) or not 1 <= len(actual_request_id) <= 128
                        or any(ord(char) < 32 or ord(char) == 127 for char in actual_request_id)):
                    raise LedgerError("invalid actual request ID")
                self.db.execute("UPDATE correlation SET resource_id=? WHERE request_id=?",
                                (resource_id, actual_request_id))

    def unknown(self, handle: str) -> None:
        with self.db:
            if self._get(handle)["state"] not in {"intended", "unknown"}:
                raise LedgerError("cannot forget a known resource")
            self.db.execute("UPDATE resource SET state='unknown' WHERE handle=?", (handle,))

    def adopt_created_file(self, session_handle: str, case_id: str, attempt: int,
                           request_id: str, resource_id: str) -> str:
        """Adopt one post-hoc implicitly created File with exact API evidence.

        The caller must first prove the Session ownership, Actor ownership,
        message association and reviewed implicit-effect admission. Unlike an
        intent row, this is an explicit recovery record; it never broadens the
        Session scope or infer ownership from a list name.
        """
        identifier(session_handle, "adoption Session handle")
        identifier(case_id, "adoption case_id")
        identifier(request_id, "adoption request_id")
        identifier(resource_id, "adoption resource_id")
        if type(attempt) is not int or attempt < 1:
            raise LedgerError("adoption attempt is invalid")
        with self.db:
            session = self._get(session_handle)
            if (session["kind"] != "session" or session["origin"] != "created"
                    or session["state"] != "live" or session["case_id"] != case_id
                    or session["attempt"] != attempt or not session["resource_id"]):
                raise LedgerError("adoption parent is not the exact live owned Session")
            handle = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO resource VALUES (?, ?, ?, ?, ?, ?, 'created', ?, 'live')",
                (handle, case_id, attempt, "file", session_handle, request_id, resource_id),
            )
        return handle

    def mark_deleted(self, handle: str) -> None:
        """Record externally verified business deletion; no HTTP occurs here."""
        with self.db:
            row = self._get(handle)
            if row["state"] == "deleted":
                return
            if row["origin"] != "created" or row["state"] != "live":
                raise LedgerError("only known owned resources can be marked deleted")
            if self.db.execute("SELECT 1 FROM resource WHERE parent=? AND state!='deleted'",
                               (handle,)).fetchone():
                raise LedgerError("children must be reconciled first")
            self.db.execute("UPDATE resource SET state='deleted' WHERE handle=?", (handle,))

    def resource_snapshot(self) -> list[dict]:
        """Include tombstones/retained rows for independent inventory reconciliation."""
        return [dict(r) for r in self.db.execute("SELECT * FROM resource ORDER BY rowid")]

    def cleanup_plan(self) -> dict:
        rows = self.resource_snapshot()
        blockers = [r["handle"] for r in rows if r["state"] not in {"live", "deleted"}
                    or r["kind"] not in {"group", "session", "file"}
                    or r["origin"] not in {"created", "borrowed", "reused"}
                    or type(r["attempt"]) is not int or r["attempt"] < 1
                    or (r["parent"] is not None and r["origin"] != "created" and r["state"] != "deleted")]
        # An unresolved child can be lost if its parent is deleted. Fail closed for the whole run.
        candidates = [] if blockers else [r for r in rows if r["origin"] == "created" and r["state"] == "live"]
        order = {"file": 0, "session": 1, "group": 2}
        candidates.sort(key=lambda r: order[r["kind"]])
        return {
            "run_id": self.run_id, "dry_run": True, "executable": False,
            "status": "BLOCKED" if blockers else "PLANNED",
            "unresolved_handles": blockers,
            "resources": candidates,
            "retained_handles": [r["handle"] for r in rows if r["origin"] in {"borrowed", "reused"}],
            "requires": ["independent ownership verification", "authorized API cleanup executor"],
        }

    def _get(self, handle: str | None) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM resource WHERE handle=?", (handle,)).fetchone()
        if row is None:
            raise LedgerError("unknown resource handle")
        return row
