"""Bounded read-only SQLite evidence for the original Structured Story."""
from __future__ import annotations

from .local_sqlite import OwnedSqliteDatabase
from .profile import identifier
from .run_state import RunStateError


class LocalStructuredStorageError(RunStateError):
    pass


def _require(condition: object, message: str) -> None:
    if not condition:
        raise LocalStructuredStorageError(message)


def _rows(db, sql: str, params: tuple, maximum: int) -> list[dict]:
    rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
    _require(len(rows) <= maximum, "Structured SQLite evidence exceeded its finite bound")
    return rows


def _one(db, sql: str, params: tuple) -> dict:
    rows = _rows(db, sql, params, 1)
    _require(len(rows) == 1, "Structured SQLite evidence requires one exact row")
    return rows[0]


_SCHEMA = {
    "bcs_groups": {"group_id", "env"},
    "bcs_group_sessions": {
        "session_id", "group_id", "env", "status", "session_kind", "activation_count",
        "error_message",
    },
    "bcs_group_runtime_bindings": {"group_id", "env"},
    "bcs_state_machine_runs": {
        "run_id", "session_id", "group_id", "definition_id", "definition_version",
        "status", "record_status", "env",
    },
    "bcs_state_machine_node_runs": {
        "run_id", "node_id", "status", "attempt", "max_attempts", "assignee_bot_id",
        "delivery_request_id", "record_status", "env",
    },
    "bcs_collaboration_definitions": {
        "definition_id", "version", "name", "source_format", "content_hash",
        "record_status", "env",
    },
}


class OwnedLocalStructuredStorage(OwnedSqliteDatabase):
    """Expose only fixed, ID-filtered, env=local Structured Story projections."""

    @staticmethod
    def _schema(db, *tables: str) -> None:
        for table in tables:
            _require(table in _SCHEMA, "Structured SQLite table is not admitted")
            kinds = db.execute(
                "SELECT type FROM sqlite_master WHERE name=? LIMIT 2", (table,)
            ).fetchall()
            _require(len(kinds) == 1 and kinds[0]["type"] == "table",
                     "Structured SQLite table is missing or is not a table")
            columns = db.execute(f'PRAGMA table_info("{table}")').fetchmany(101)
            _require(len(columns) <= 100 and _SCHEMA[table] <= {row["name"] for row in columns},
                     "Structured SQLite schema differs from the reviewed projection")

    def group_runs(self, group_id: str) -> list[dict]:
        group_id = identifier(group_id, "Structured Group ID")
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db, "bcs_state_machine_runs", "bcs_group_sessions")
            rows = _rows(db, """SELECT r.run_id, r.session_id, r.group_id,
                r.definition_id, r.definition_version, r.status
                FROM bcs_state_machine_runs r
                JOIN bcs_group_sessions s ON s.env=r.env AND s.session_id=r.session_id
                WHERE r.env=? AND r.group_id=? AND s.group_id=r.group_id
                ORDER BY r.created_at_ms, r.run_id LIMIT 3""", ("local", group_id), 2)
        _require(rows and all(row["group_id"] == group_id for row in rows),
                 "Structured Group has no exact retained Run/Session identity")
        _require(len({row["run_id"] for row in rows}) == len(rows)
                 and len({row["session_id"] for row in rows}) == len(rows),
                 "Structured implicit and explicit Runs must use distinct identities")
        return rows

    def run(self, run_id: str) -> dict:
        run_id = identifier(run_id, "Structured Run ID")
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db, "bcs_state_machine_runs", "bcs_state_machine_node_runs",
                         "bcs_group_sessions")
            run = _one(db, """SELECT run_id, session_id, group_id, definition_id,
                definition_version, status FROM bcs_state_machine_runs
                WHERE env=? AND run_id=? LIMIT 2""", ("local", run_id))
            nodes = _rows(db, """SELECT run_id, node_id, status, attempt, max_attempts,
                assignee_bot_id, delivery_request_id, record_status
                FROM bcs_state_machine_node_runs WHERE env=? AND run_id=?
                ORDER BY node_id LIMIT 2""", ("local", run_id), 1)
            session = _one(db, """SELECT session_id, group_id, status, session_kind,
                activation_count, error_message FROM bcs_group_sessions
                WHERE env=? AND session_id=? LIMIT 2""", ("local", run["session_id"]))
        _require(len(nodes) == 1, "Structured Run requires one exact answer Node")
        _require(session["group_id"] == run["group_id"] and nodes[0]["run_id"] == run_id,
                 "Structured Run, Session, and Node identity differ")
        _require(session["session_kind"] == "service_invocation"
                 and session["status"] == "completed"
                 and session["error_message"] == "aborted",
                 "Structured cancelled Run Session terminal state differs")
        node = nodes[0]
        _require(node["node_id"] == "answer" and type(node["attempt"]) is int
                 and node["attempt"] >= 0 and type(node["max_attempts"]) is int
                 and node["max_attempts"] >= 1,
                 "Structured answer Node attempt differs from the original Story")
        identifier(node["assignee_bot_id"], "Structured Node assignee")
        expected_request = f"smnode-{run_id}-answer-{node['attempt']}"
        _require(node["delivery_request_id"] == expected_request,
                 "Structured Node delivery request identity differs")
        return {"run": run, "nodes": nodes, "session": session}

    def group_deleted(self, group_id: str, run_ids, definition_id: str) -> dict:
        group_id = identifier(group_id, "deleted Structured Group ID")
        definition_id = identifier(definition_id, "Structured definition ID")
        _require(isinstance(run_ids, (list, tuple)) and len(run_ids) == 2,
                 "Structured deletion requires the implicit and explicit Run IDs")
        run_ids = tuple(identifier(value, "retained Structured Run ID") for value in run_ids)
        _require(len(set(run_ids)) == 2, "retained Structured Run IDs must be distinct")
        placeholders = ",".join("?" for _ in run_ids)
        with self._connection() as db:
            db.execute("BEGIN")
            self._schema(db, "bcs_groups", "bcs_group_sessions",
                         "bcs_group_runtime_bindings", "bcs_state_machine_runs",
                         "bcs_state_machine_node_runs", "bcs_collaboration_definitions")
            groups = _rows(db, "SELECT group_id FROM bcs_groups WHERE env=? AND group_id=? LIMIT 2",
                           ("local", group_id), 1)
            sessions = _rows(db, """SELECT session_id FROM bcs_group_sessions
                WHERE env=? AND group_id=? LIMIT 2""", ("local", group_id), 1)
            bindings = _rows(db, """SELECT group_id FROM bcs_group_runtime_bindings
                WHERE env=? AND group_id=? LIMIT 2""", ("local", group_id), 1)
            runs = _rows(db, """SELECT run_id, session_id, group_id, definition_id,
                definition_version, status FROM bcs_state_machine_runs
                WHERE env=? AND group_id=? ORDER BY run_id LIMIT 3""",
                         ("local", group_id), 2)
            nodes = _rows(db, f"""SELECT run_id, node_id, status, attempt, max_attempts,
                assignee_bot_id, delivery_request_id, record_status
                FROM bcs_state_machine_node_runs WHERE env=? AND run_id IN ({placeholders})
                ORDER BY run_id, node_id LIMIT 3""", ("local", *run_ids), 2)
            definitions = _rows(db, """SELECT definition_id, version, name, source_format,
                content_hash, record_status FROM bcs_collaboration_definitions
                WHERE env=? AND definition_id=? ORDER BY version LIMIT 3""",
                                ("local", definition_id), 2)
        _require(not groups and not sessions and not bindings,
                 "Structured Group, Session, or runtime binding survived deletion")
        _require({row["run_id"] for row in runs} == set(run_ids)
                 and all(row["group_id"] == group_id
                         and row["definition_id"] == definition_id
                         and row["status"] == "aborted" for row in runs),
                 "Structured Run history was not retained exactly")
        _require(len(nodes) == 2 and {row["run_id"] for row in nodes} == set(run_ids)
                 and all(row["node_id"] == "answer" for row in nodes),
                 "Structured Node history was not retained exactly")
        versions = {row["version"] for row in definitions}
        _require(len(definitions) == 2
                 and {row["definition_version"] for row in runs} <= versions,
                 "Structured definition history was not retained exactly")
        return {
            "source": "local-sqlite",
            "group_id": group_id,
            "group_present": False,
            "session_count": 0,
            "runtime_binding_count": 0,
            "runs": runs,
            "nodes": nodes,
            "definitions": definitions,
        }
