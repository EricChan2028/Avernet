"""Read-only execution evidence gate shared by cleanup, reporting and release.

No adapter callbacks or state repair occur here. A terminal resource tombstone
never excuses an unresolved effect. Legacy resource-only ledgers are labelled
NOT_TRACKED, not credited with execution convergence. This local journal is not
an independent source of authorization or proof against malicious tampering.
"""
from __future__ import annotations

import sqlite3

from .ledger import Ledger, LedgerError

MAX_ROWS = 1000  # Each pilot Run has at most five Sessions; fail closed on overflow.
TERMINAL = {"COMPLETED", "CANCELLED", "FAILED"}


def inspect_execution(ledger: Ledger, *, required: bool = False) -> dict:
    """Bounded, constant-query snapshot including deleted and orphan effects."""
    def rows(sql: str) -> list[dict]:
        values = ledger.db.execute(sql).fetchmany(MAX_ROWS + 1)
        if len(values) > MAX_ROWS:
            raise LedgerError("execution evidence exceeds the pilot inspection budget")
        return [dict(value) for value in values]

    try:
        tables = {row[0] for row in ledger.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('execution_contract', 'execution_effect')")}
        if not tables and required:
            raise LedgerError("required execution contract is missing")
        if not tables:
            return {"status": "NOT_TRACKED", "issues": []}
        if tables != {"execution_contract", "execution_effect"}:
            raise LedgerError("execution contract is incomplete")
        contracts = rows("SELECT run_id, version FROM execution_contract")
        if contracts != [{"run_id": ledger.run_id, "version": 1}]:
            raise LedgerError("execution contract binding or version is unsupported")
        effects = rows("SELECT handle, case_id, attempt, kind, parent_handle, session_id, state, "
                       "effect_id, terminal_reason, evidence_request_id FROM execution_effect")
        resources = rows("SELECT * FROM resource")
        proofs = rows("SELECT resource_handle, case_id, attempt, session_id, phase FROM termination_state")
    except (sqlite3.Error, LedgerError):
        return {"status": "BLOCKED", "issues": ["execution evidence unavailable or outside inspection budget"]}

    issues = set()
    sessions = {row["resource_id"]: row for row in resources if row["kind"] == "session"
                and row["resource_id"]}
    proof_by_handle = {row["resource_handle"]: row for row in proofs}
    effects_by_handle = {row["handle"]: row for row in effects}
    covered = set()
    for row in resources:
        if row["kind"] != "session":
            continue
        if row["origin"] != "created" or row["state"] not in {"live", "deleted"} or not row["resource_id"]:
            issues.add("Session ownership or creation remains unresolved")
        proof = proof_by_handle.get(row["handle"])
        if (proof is None or proof["case_id"] != row["case_id"] or proof["attempt"] != row["attempt"]
                or proof["session_id"] != row["resource_id"]
                or proof["phase"] not in {"CLEANUP_READY", "CLEANED"}):
            issues.add("Session termination proof is missing, unresolved or mismatched")
    for effect in effects:
        session = sessions.get(effect.get("session_id"))
        if (session is None or effect["case_id"] != session["case_id"]
                or effect["attempt"] != session["attempt"]):
            issues.add("execution effect is not bound to an exact Case Session")
        else:
            covered.add(session["resource_id"])
        if (effect["state"] not in TERMINAL or not effect["effect_id"]
                or not effect["terminal_reason"] or not effect["evidence_request_id"]):
            issues.add("execution effect has no confirmed terminal evidence")
        if effect["parent_handle"] is not None:
            parent = effects_by_handle.get(effect["parent_handle"])
            if (parent is None or parent["handle"] == effect["handle"]
                    or any(parent[key] != effect[key] for key in ("case_id", "attempt", "session_id"))):
                issues.add("execution effect parent binding is invalid")
    if set(sessions) - covered:
        issues.add("Session has no exact execution effect inventory")
    return {"status": "BLOCKED" if issues else "CONFIRMED", "issues": sorted(issues),
            "effect_count": len(effects), "session_count": len(sessions)}


def require_execution_safe(ledger: Ledger, *, required: bool = False) -> dict:
    result = inspect_execution(ledger, required=required)
    if result["status"] == "BLOCKED":
        raise LedgerError("execution evidence blocks cleanup or release; preserve Run isolation")
    return result
