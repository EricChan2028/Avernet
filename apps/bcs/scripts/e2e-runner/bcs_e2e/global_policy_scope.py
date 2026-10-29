"""Independent scope for global effective-delivery-policy cases (task 6.3).

The effective delivery policy is server-wide configuration observed
through a native BCN admin endpoint. Those cases are not
Case-isolable: their subject is shared global state, so they can
never execute inside an owned, per-Case fixture pool and are
excluded from the owned local full-suite denominator. The gap is
preserved explicitly instead of being silently dropped or
force-run in an operational environment.

Global policy cases execute only through the explicit
operator-attested ``policy-check`` command against a preprod
native Profile. Until that gate is authorized, a time-boxed
exception records the retained full-suite gap together with its
compensating local evidence.
"""
from __future__ import annotations

import time
import uuid
from typing import Iterable

from .profile import identifier

GLOBAL_POLICY_SCOPE = "GLOBAL_EFFECTIVE_POLICY"
CASE_KIND = "EFFECTIVE_POLICY_READ_ONLY"
LOCAL_FULL_SUITE_EXCLUSION = "EXCLUDED_FROM_OWNED_LOCAL_FULL_SUITE"
RUN_ADMISSION = "REFUSED_NO_RUN_ADMISSION"
OPERATIONAL_ENVIRONMENT = "FORBIDDEN_NOT_OPERATIONAL_ENVIRONMENT"
ISOLATION = "NOT_CASE_ISOLABLE"
MAX_EXCUSION_SECONDS = 86400  # A time-boxed exception never exceeds 24 hours.

STANDING_REASON = (
    "the effective delivery policy is server-wide state observed "
    "through one native admin endpoint; per-Case isolation is impossible")
LOCAL_COMPENSATING_EVIDENCE = (
    "delivery policy projection contracts validated against fixture "
    "snapshots (tests/test_delivery_policy_check.py)",
    "operator-attested policy-read review shape validated before any "
    "credential resolution or HTTP (delivery_policy_check.PolicyReadReview)",
)
GLOBAL_POLICY_CASE_IDS = ("EFFECTIVE-DELIVERY-POLICY-READ",)


class GlobalPolicyScopeError(RuntimeError):
    """A global delivery-policy case crossed its declared scope."""


def is_global_policy_case(case_id: object) -> bool:
    return isinstance(case_id, str) and case_id in GLOBAL_POLICY_CASE_IDS


def local_full_suite_denominator(declared_cases: Iterable[object]) -> dict:
    """Split declared cases, preserving the excluded global-policy gap."""
    included: list[str] = []
    gap: list[str] = []
    for case in declared_cases:
        if not isinstance(case, str):
            raise GlobalPolicyScopeError("declared cases must be case identifiers")
        identifier(case, "declared case")
        (gap if is_global_policy_case(case) else included).append(case)
    return {
        "schema_version": 1,
        "scope": GLOBAL_POLICY_SCOPE,
        "isolation": ISOLATION,
        "local_full_suite": LOCAL_FULL_SUITE_EXCLUSION,
        "denominator_cases": included,
        "retained_gap": {
            "excluded_case_ids": gap,
            "reason": STANDING_REASON,
            "compensating_local_evidence": list(LOCAL_COMPENSATING_EVIDENCE),
            "full_suite_coverage_claim": "GAP_PRESERVED_NOT_SILENTLY_DROPPED",
        },
        "run_admission": RUN_ADMISSION,
        "operational_environment": OPERATIONAL_ENVIRONMENT,
    }


def refuse_run_admission(case_id: object) -> None:
    """Global policy cases are never admitted to a Run or pool release."""
    if is_global_policy_case(case_id):
        raise GlobalPolicyScopeError(
            f"global effective-delivery-policy case {case_id!r} is not "
            "admitted to an owned Run; use the explicit policy-check command")


def _build_exception(*, gap: list[str],
                      evidence: list[str],
                      reason: str,
                      valid_seconds: int,
                      exception_id: str,
                      issued_at: int) -> dict:
    return {
        "schema_version": 1,
        "exception_id": exception_id,
        "scope": GLOBAL_POLICY_SCOPE,
        "isolation": ISOLATION,
        "reason": reason,
        "retained_gap_case_ids": gap,
        "compensating_local_evidence": evidence,
        "operational_environment": OPERATIONAL_ENVIRONMENT,
        "issued_at": issued_at,
        "expires_at": issued_at + valid_seconds,
        "full_suite_status": "GAP_RETAINED_WITH_TIME_BOXED_EXCEPTION",
    }


def time_boxed_exception(*, gap: Iterable[str],
                          compensating_evidence: Iterable[str],
                          reason: str = STANDING_REASON,
                          valid_seconds: int = 8 * 3600) -> dict:
    """Build the time-boxed exception record for the retained gap."""
    gap = list(gap)
    evidence = list(compensating_evidence)
    if not gap:
        raise GlobalPolicyScopeError("a time-boxed exception requires a non-empty retained gap")
    for case_id in gap:
        if not isinstance(case_id, str):
            raise GlobalPolicyScopeError("retained gap entries must be case identifiers")
        identifier(case_id, "retained gap case")
    if not evidence or any(not isinstance(item, str) or not item.strip()
                           for item in evidence):
        raise GlobalPolicyScopeError("compensating local evidence is required")
    if type(reason) is not str or not reason.strip():
        raise GlobalPolicyScopeError("a reason is required")
    if type(valid_seconds) is not int or not 0 < valid_seconds <= MAX_EXCUSION_SECONDS:
        raise GlobalPolicyScopeError(
            f"time-boxed exception must expire within {MAX_EXCUSION_SECONDS} seconds")
    return _build_exception(gap=gap, evidence=evidence, reason=reason,
                             valid_seconds=valid_seconds,
                             exception_id=uuid.uuid4().hex,
                             issued_at=int(time.time()))


def _standing_exception(now: int) -> dict:
    """Deterministic standing exception, re-issued once per UTC day.

    Exception identity and timestamps are derived from the UTC day so
    that run reports stay reproducible across rebuilds within the same
    validity window instead of carrying a fresh random exception on
    every report.
    """
    issued = now - (now % MAX_EXCUSION_SECONDS)
    exception_id = uuid.uuid5(
        uuid.NAMESPACE_DNS, f"global-effective-policy-{issued}").hex
    return _build_exception(gap=list(GLOBAL_POLICY_CASE_IDS),
                             evidence=list(LOCAL_COMPENSATING_EVIDENCE),
                             reason=STANDING_REASON,
                             valid_seconds=MAX_EXCUSION_SECONDS,
                             exception_id=exception_id,
                             issued_at=issued)


def validate_exception(record: object, *, now: int | None = None) -> dict:
    """Reject expired or malformed exception records."""
    if not isinstance(record, dict):
        raise GlobalPolicyScopeError("exception record must be a mapping")
    required = {"exception_id", "scope", "reason", "retained_gap_case_ids",
                "compensating_local_evidence", "issued_at", "expires_at"}
    missing = required - set(record)
    if missing:
        raise GlobalPolicyScopeError(f"exception record is missing {sorted(missing)}")
    if record.get("scope") != GLOBAL_POLICY_SCOPE:
        raise GlobalPolicyScopeError("exception record scope is not global effective policy")
    if not record["retained_gap_case_ids"] or not record["compensating_local_evidence"]:
        raise GlobalPolicyScopeError("exception record has an empty gap or evidence list")
    issued, expires = record["issued_at"], record["expires_at"]
    if (type(issued) is not int or type(expires) is not int
            or not 0 < expires - issued <= MAX_EXCUSION_SECONDS):
        raise GlobalPolicyScopeError("exception record time box is invalid")
    current = int(time.time()) if now is None else now
    if not issued <= current < expires:
        raise GlobalPolicyScopeError("time-boxed exception is not currently valid")
    return record


def global_policy_view() -> dict:
    """Standing scope declaration carried by every Run report."""
    denominator = local_full_suite_denominator(GLOBAL_POLICY_CASE_IDS)
    return {
        **denominator,
        "exception": _standing_exception(int(time.time())),
    }
