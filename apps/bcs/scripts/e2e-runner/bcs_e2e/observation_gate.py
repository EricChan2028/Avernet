"""Required persistence admission and qualification, independent of Story steps."""
from __future__ import annotations

from typing import Callable

from .alisddbquery import AlisdDbQueryError, AlisdDbQueryTimeout
from .evidence import EvidenceError, _make_plan, _validate_observation
from .errors import primary_error
from .profile import ConfigError, identifier


class ActorAdmissionError(RuntimeError):
    pass


def validate_pilot_actor_scope(profile: dict) -> dict:
    """Initial pilot actor/relationship admission; no relationship mutation."""
    try:
        human_id = identifier(profile["human"]["user_id"], "human.user_id")
        relationships = profile["relationships"]
        if relationships["friendship"] != "unchanged":
            raise ActorAdmissionError("pilot friendship policy must be unchanged")
        actors = profile["actors"]
        if set(actors) != {"driver", "member"}:
            raise ActorAdmissionError("pilot requires exactly driver and member roles")
        ids = []
        for role in ("driver", "member"):
            actor = actors[role]
            actor_id = identifier(actor["bot_id"], f"actors.{role}.bot_id")
            if actor["owner_id"] != human_id or actor["dedicated"] is not True:
                raise ActorAdmissionError("pilot Bots must be dedicated and owned by the configured Human")
            ids.append(actor_id)
        if ids[0] == ids[1]:
            raise ActorAdmissionError("pilot requires two distinct Bot identities")
        return {"status": "ADMITTED", "human_id": human_id,
                "roles": {"driver": ids[0], "member": ids[1]},
                "friendship": "unchanged", "cross_owner": "NOT_TESTED"}
    except (KeyError, TypeError, ConfigError):
        raise ActorAdmissionError("pilot actor binding is invalid") from None


REQUIRED_OBSERVATIONS = {
    "RETAIN-GROUP-01": ("group.persisted",),
    "RETAIN-SESSION-01": ("group.persisted", "session.persisted"),
    "RETAIN-FILE-01": ("file.persisted",),
    "PRE-01": ("group.persisted",),
    "PRE-02": ("group.persisted", "session.persisted"),
    "PRE-03": ("file.persisted",),
}

# These are explicitly diagnostic in the pilot. Their absence must be visible
# in reports but must not turn an otherwise qualified required API/DB result
# into a PASS or FAIL by implication.
DIAGNOSTIC_EVIDENCE = ("request_correlation", "logs")


def aggregate_diagnostic(statuses: tuple[str, ...]) -> str:
    if not statuses:
        return "NOT_RUN"
    if any(status in {"BLOCKED", "FAIL", "ERROR"} for status in statuses):
        return "INCOMPLETE"
    if "INCOMPLETE" in statuses:
        return "INCOMPLETE"
    return "PASS"


_ADMISSION_OPERATIONS = {
    "db_list", "table_list", "table_describe", "physical_binding", "provider", "unknown"
}
_ADMISSION_CATEGORIES = {
    "timeout", "tool_failure", "metadata_contract", "review_rejected", "unknown"
}


class ObserverAdmissionError(RuntimeError):
    def __init__(self, message: str, diagnostic: dict[str, str] | None = None):
        super().__init__(message)
        self.diagnostic = diagnostic


def _admission_diagnostic(error: BaseException, *, operation: str = "unknown") -> dict[str, str]:
    cause = primary_error(error)
    if isinstance(cause, (AlisdDbQueryTimeout, TimeoutError)):
        category = "timeout"
    elif isinstance(cause, AlisdDbQueryError):
        category = "tool_failure"
    elif isinstance(cause, (ActorAdmissionError, ConfigError)):
        category = "review_rejected"
    elif isinstance(cause, EvidenceError):
        category = "metadata_contract"
    else:
        category = "unknown"
    # Only framework errors may name an operation. An arbitrary third-party
    # exception cannot choose the public diagnostic projection.
    if isinstance(cause, (AlisdDbQueryError, EvidenceError, ConfigError)):
        recorded = getattr(cause, "observer_operation", None)
        if isinstance(recorded, str) and recorded in _ADMISSION_OPERATIONS:
            operation = recorded
    if not isinstance(operation, str) or operation not in _ADMISSION_OPERATIONS:
        operation = "unknown"
    if category not in _ADMISSION_CATEGORIES:
        category = "unknown"
    return {"stage": "observer_admission", "operation": operation, "category": category}


def observer_admission_diagnostic(error: BaseException) -> dict[str, str] | None:
    if not isinstance(error, ObserverAdmissionError) or not isinstance(error.diagnostic, dict):
        return None
    expected = {"stage", "operation", "category"}
    if (set(error.diagnostic) != expected
            or error.diagnostic.get("stage") != "observer_admission"):
        return None
    operation = error.diagnostic.get("operation")
    category = error.diagnostic.get("category")
    if (not isinstance(operation, str) or operation not in _ADMISSION_OPERATIONS
            or not isinstance(category, str) or category not in _ADMISSION_CATEGORIES):
        return None
    return dict(error.diagnostic)


class AdmittedObserver:
    def __init__(self, adapter, profile: dict, case_id: str, *,
                 resource_scope: Callable[[str, str], tuple[dict, ...]]):
        if not callable(resource_scope):
            failure = EvidenceError("required resource scope is unavailable")
            raise ObserverAdmissionError(
                "required resource scope is unavailable",
                _admission_diagnostic(failure)) from None
        self.resource_scope = resource_scope
        self.assertion_ids = REQUIRED_OBSERVATIONS[case_id]
        try:
            self.actor_scope = validate_pilot_actor_scope(profile)
        except ActorAdmissionError as error:
            failure = ActorAdmissionError("pilot actor admission failed")
            raise ObserverAdmissionError(
                "pilot actor admission failed",
                _admission_diagnostic(failure)) from error
        local = profile["entrypoint"]["kind"] == "mock" and profile["environment"]["tier"] == "local"
        sqlite = (profile["entrypoint"]["kind"] == "singlebox"
                  and profile["environment"]["tier"] == "local"
                  and profile["observer"]["kind"] == "local-sqlite")
        self.source = "local-semantic" if local else "local-sqlite" if sqlite else "alisddbquery"
        self.database = "local" if local else "owned-local" if sqlite else profile["observer"]["database"]
        self.environment = "local" if local else profile["environment"]["expected_bcs_env"]
        try:
            provider = adapter.semantic_observer()
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            raise ObserverAdmissionError(
                "required semantic observer admission failed",
                _admission_diagnostic(error, operation="provider")) from None
        if provider is None:
            failure = EvidenceError("required provider is unavailable")
            raise ObserverAdmissionError(
                "required semantic observer admission failed",
                _admission_diagnostic(failure, operation="provider")) from None
        try:
            admission = provider.admit(self.assertion_ids)
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            # Emit only closed diagnostic enums; never exception text or payloads.
            raise ObserverAdmissionError(
                "required semantic observer admission failed",
                _admission_diagnostic(error)) from None
        expected = {"status": "READY", "source": self.source, "database": self.database,
                    "environment": self.environment, "assertion_ids": list(self.assertion_ids)}
        if not isinstance(admission, dict) or any(admission.get(k) != v for k, v in expected.items()):
            failure = EvidenceError("invalid observer admission")
            raise ObserverAdmissionError(
                "required semantic observer admission failed",
                _admission_diagnostic(failure, operation="provider")) from None
        self.provider = provider

    def observe(self, assertion_id: str, resource_id: str) -> tuple[str, dict]:
        try:
            if assertion_id not in self.assertion_ids:
                raise EvidenceError("undeclared semantic observation")
            plan = _make_plan(assertion_id, resource_id, self.database, self.environment)
            # Metadata admission precedes creation. Business-row reads require
            # a fresh, exact current-Case resource and complete owned parent chain.
            before = self.resource_scope(assertion_id, resource_id)
            result = self.provider.observe(assertion_id, resource_id)
            if self.resource_scope(assertion_id, resource_id) != before:
                raise EvidenceError("resource ownership changed during observation")
            if (not isinstance(result, dict) or result.get("status") != "OBSERVED"
                    or result.get("source") != self.source or result.get("plan") != plan.public()):
                raise EvidenceError("invalid semantic observation envelope")
            rows = _validate_observation(plan, {"success": True, "data": result.get("rows"),
                                                "truncated": result.get("truncated", False)})
            parent_field = {"session.persisted": "group_id", "file.persisted": "session_id"}.get(assertion_id)
            if parent_field and rows[0][parent_field] != before[1]["resource_id"]:
                raise EvidenceError("persisted resource parent differs from owned ledger parent")
            status = "LOCAL_SURROGATE" if self.source == "local-semantic" else "PASS"
            return status, {"status": "OBSERVED", "source": self.source,
                            "plan": plan.public(), "rows": rows}
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            return "BLOCKED", {"status": "BLOCKED", "source": self.source,
                               "assertion_id": assertion_id, "resource_id": resource_id,
                               "error": type(error).__name__}


def aggregate_evidence(statuses: tuple[str, ...]) -> str:
    if not statuses:
        return "INCOMPLETE"
    if any(status not in {"PASS", "LOCAL_SURROGATE", "INCOMPLETE"} for status in statuses):
        return "BLOCKED"
    if "INCOMPLETE" in statuses:
        return "INCOMPLETE"
    return "LOCAL_SURROGATE" if "LOCAL_SURROGATE" in statuses else "PASS"


def case_status(functional: str, cleanup: str, assertions: list[dict], evidence: str) -> str:
    if functional == "NOT_RUN":
        return "BLOCKED"
    if functional != "PASS" or cleanup != "PASS" or any(a["status"] in {"FAIL", "ERROR"} for a in assertions):
        return "FAIL"
    return ("PASS" if evidence in {"PASS", "LOCAL_SURROGATE"}
            and all(a["status"] == "PASS" for a in assertions) else "BLOCKED")
