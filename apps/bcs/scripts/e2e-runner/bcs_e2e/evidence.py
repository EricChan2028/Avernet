"""Safe semantic plans for the future alisddbquery evidence adapter.

This module deliberately separates query planning from MCP invocation. It never
accepts SQL from a Story and never executes a query itself. A deployment-specific
adapter can inject describe/query callables after the DB/environment binding has
been approved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .profile import ConfigError, identifier


class EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidencePlan:
    assertion_id: str
    database: str
    table: str
    select: tuple[str, ...]
    filters: dict[str, str]
    limit: int = 2

    def public(self) -> dict:
        return {
            "assertion_id": self.assertion_id,
            "database": self.database,
            "table": self.table,
            "select": list(self.select),
            "filters": self.filters,
            "limit": self.limit,
        }


# Keep these projections small and exclude object handles, tokens, policy/config
# blobs and arbitrary message content by default.
ASSERTIONS = {
    "group.persisted": ("bcs_groups", ("group_id", "env", "driver_bot", "originator", "status"), "group_id"),
    "session.persisted": ("bcs_group_sessions", ("session_id", "env", "group_id", "status", "session_title"), "session_id"),
    "file.persisted": ("bcs_session_files", ("file_id", "env", "session_id", "status", "size", "sha256", "storage_backend"), "file_id"),
}


def _safe_id(value: str, field: str) -> str:
    try:
        return identifier(value, field)
    except ConfigError as error:
        raise EvidenceError(str(error)) from None


class SemanticEvidenceProvider(Protocol):
    """Stable semantic observer contract shared by DB/MCP and local adapters."""

    def admit(self, assertion_ids: tuple[str, ...]) -> dict[str, Any]: ...
    def observe(self, assertion_id: str, resource_id: str) -> dict[str, Any]: ...


def _make_plan(assertion_id: str, resource_id: str, database: str, environment: str) -> EvidencePlan:
    if assertion_id not in ASSERTIONS:
        raise EvidenceError("unsupported evidence assertion")
    table, fields, key = ASSERTIONS[assertion_id]
    return EvidencePlan(assertion_id, database, table, fields,
                        {"env": environment, key: _safe_id(resource_id, "resource_id")})


def _validate_observation(plan: EvidencePlan, result: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or result.get("success") is not True:
        raise EvidenceError("database query failed")
    rows = result.get("data")
    if not isinstance(rows, list) or len(rows) != 1:
        raise EvidenceError("database query must return exactly one resource row")
    if type(result.get("truncated", False)) is not bool or result.get("truncated", False):
        raise EvidenceError("database result is truncated or has an invalid completeness flag")
    key = next(field for field in plan.filters if field != "env")
    for row in rows:
        if not isinstance(row, dict) or row.get("env") != plan.filters["env"] or row.get(key) != plan.filters[key]:
            raise EvidenceError("database row does not match the exact environment/resource filter")
        if any(field not in row for field in plan.select):
            raise EvidenceError("database row is missing required projection fields")
    return [{field: row[field] for field in plan.select} for row in rows]


def _stored_rows_to_semantic(assertion_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project already-validated DB rows without repairing observed evidence.

    Rust persists FileStatus in PascalCase; the shared API-facing assertion uses
    lowercase. Semantic/mock providers already speak that contract and must not
    use this storage conversion. NULL hashes and every other value stay intact.
    """
    projected = [dict(row) for row in rows]
    if assertion_id == "file.persisted":
        statuses = {"Pending": "pending", "Ready": "ready", "Deleting": "deleting", "Failed": "failed"}
        for row in projected:
            status = row["status"]
            if not isinstance(status, str) or status not in statuses:
                raise EvidenceError("unknown persisted file status")
            row["status"] = statuses[status]
    return projected


def _admission(assertion_ids: tuple[str, ...], source: str, database: str, environment: str) -> dict:
    if not assertion_ids or any(item not in ASSERTIONS for item in assertion_ids):
        raise EvidenceError("unsupported or empty required observation set")
    return {"status": "READY", "source": source, "database": database,
            "environment": environment, "assertion_ids": list(assertion_ids)}


class LocalSemanticEvidenceProvider:
    """Offline/local semantic observer; never calls OB or a company MCP."""

    def __init__(self, *, database: str = "local", environment: str = "local",
                 query: Callable[[EvidencePlan], dict[str, Any]]):
        self.database = _safe_id(database, "database")
        self.environment = _safe_id(environment, "environment")
        self.query = query

    def admit(self, assertion_ids: tuple[str, ...]) -> dict:
        return _admission(assertion_ids, "local-semantic", self.database, self.environment)

    def make_plan(self, assertion_id: str, resource_id: str) -> EvidencePlan:
        return _make_plan(assertion_id, resource_id, self.database, self.environment)

    def observe(self, assertion_id: str, resource_id: str) -> dict[str, Any]:
        plan = self.make_plan(assertion_id, resource_id)
        rows = _validate_observation(plan, self.query(plan))
        return {"status": "OBSERVED", "source": "local-semantic", "plan": plan.public(), "rows": rows}


class AlisdDbEvidenceProvider:
    def __init__(self, *, database: str, environment: str,
                 environment_binding_confirmed: bool,
                 describe: Callable[[str, str], dict[str, Any]],
                 query: Callable[[EvidencePlan], dict[str, Any]]):
        self.database = _safe_id(database, "database")
        self.environment = _safe_id(environment, "environment")
        self.environment_binding_confirmed = environment_binding_confirmed
        self.describe = describe
        self.query = query

    def _validate_schema(self, assertion_id: str) -> None:
        if self.environment_binding_confirmed is not True:
            raise EvidenceError("database environment binding is not confirmed")
        if assertion_id not in ASSERTIONS:
            raise EvidenceError("unsupported evidence assertion")
        table, fields, _key = ASSERTIONS[assertion_id]
        schema = self.describe(self.database, table)
        if (not isinstance(schema, dict) or schema.get("success") is not True
                or type(schema.get("truncated", False)) is not bool or schema.get("truncated", False)):
            raise EvidenceError("database schema discovery failed or is incomplete")
        columns = schema.get("columns")
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise EvidenceError("database schema discovery returned invalid columns")
        if any(field not in columns for field in fields):
            raise EvidenceError("database schema does not match the release contract")

    def admit(self, assertion_ids: tuple[str, ...]) -> dict:
        admission = _admission(assertion_ids, "alisddbquery", self.database, self.environment)
        for assertion_id in dict.fromkeys(assertion_ids):
            self._validate_schema(assertion_id)
        return admission

    def make_plan(self, assertion_id: str, resource_id: str) -> EvidencePlan:
        plan = _make_plan(assertion_id, resource_id, self.database, self.environment)
        self._validate_schema(assertion_id)
        return plan

    def observe(self, assertion_id: str, resource_id: str) -> dict[str, Any]:
        plan = self.make_plan(assertion_id, resource_id)
        result = self.query(plan)
        rows = _stored_rows_to_semantic(assertion_id, _validate_observation(plan, result))
        return {"status": "OBSERVED", "source": "alisddbquery", "plan": plan.public(), "rows": rows}
