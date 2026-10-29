"""Bounded collection of optional log diagnostics for a prepared Run."""
from __future__ import annotations

from typing import Any
import time

from .ledger import Ledger, LedgerError
from .errors import primary_error
from .log_observer import LogObservationError, LogQuery


class DiagnosticCollectionError(RuntimeError):
    pass


def collect_log_diagnostics(ledger: Ledger, observer, *, case_id: str, attempt: int,
                             environment: str, start_epoch: int, end_epoch: int,
                             max_queries: int = 32, timeout_seconds: int = 30) -> dict[str, Any]:
    """Query unique request IDs with a finite budget and store summaries only."""
    if type(max_queries) is not int or not 1 <= max_queries <= 100:
        raise DiagnosticCollectionError("diagnostic query budget is outside the supported bound")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise DiagnosticCollectionError("diagnostic time budget is outside the supported bound")
    deadline = time.monotonic() + timeout_seconds
    correlations = [row for row in ledger.correlations()
                    if row["case_id"] == case_id and row["attempt"] == attempt]
    request_ids = list(dict.fromkeys(row.get("response_request_id") or row["request_id"]
                                     for row in correlations))
    if not request_ids:
        return {"status": "NOT_RUN", "source": "logs", "queried": 0,
                "incomplete": 0, "blocked": 0}
    queried = incomplete = blocked = 0
    time_exhausted = False
    for request_id in request_ids[:max_queries]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            time_exhausted = True
            break
        query = LogQuery(environment=environment, request_id=request_id,
                         start_epoch=start_epoch, end_epoch=end_epoch, limit=100)
        try:
            query.validate(environment)
            result = observer.observe(query, timeout_seconds=min(5.0, remaining))
            if (not isinstance(result, dict) or result.get("source") != "logs"
                    or result.get("query") != query.public()
                    or result.get("status") not in {"OBSERVED", "INCOMPLETE", "BLOCKED"}
                    or type(result.get("record_count")) is not int
                    or not 0 <= result["record_count"] <= query.limit
                    or type(result.get("truncated")) is not bool):
                raise LogObservationError("invalid diagnostic observation")
            if result["status"] == "OBSERVED" and (result["truncated"] or not result["record_count"]):
                result = {**result, "status": "INCOMPLETE"}
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            # A diagnostic outage must not leak backend text or replace the
            # independently recorded business outcome. Interrupts still propagate.
            result = {"status": "BLOCKED", "source": "logs", "query": query.public(),
                      "record_count": 0, "truncated": False}
        try:
            ledger.record_diagnostic(case_id, attempt, result)
        except LedgerError as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            raise DiagnosticCollectionError("diagnostic result could not be persisted") from error
        queried += 1
        incomplete += result.get("status") == "INCOMPLETE"
        blocked += result.get("status") == "BLOCKED"
    time_exhausted = time_exhausted or time.monotonic() >= deadline
    status = "BLOCKED" if blocked else ("INCOMPLETE" if incomplete or time_exhausted
                                        or len(request_ids) > max_queries else "PASS")
    return {"status": status, "source": "logs", "queried": queried,
            "incomplete": incomplete, "blocked": blocked,
            "budget_exhausted": len(request_ids) > max_queries,
            "time_exhausted": time_exhausted}


def case_log_diagnostics(ledger: Ledger, observer, *, case_id: str, environment: str,
                         start_epoch: int, end_epoch: int) -> dict[str, Any]:
    """Optional lifecycle hook; never changes functional/required/cleanup status."""
    try:
        admission = observer.admit()
        if (not isinstance(admission, dict) or admission.get('status') != 'READY'
                or admission.get('source') != 'logs' or admission.get('environment') != environment):
            raise LogObservationError('log observer admission mismatch')
        summary = collect_log_diagnostics(ledger, observer, case_id=case_id, attempt=1,
                                          environment=environment, start_epoch=start_epoch,
                                          end_epoch=end_epoch)
    except Exception as error:
        cause = primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            raise cause from None
        # Do not leak observer exceptions (including database/process errors).
        # Any persistence failure stays explicit in the final case report.
        summary = {'status': 'BLOCKED', 'source': 'logs',
                   'reason': 'diagnostic admission, collection or persistence failed'}
    status = summary['status']
    return {'diagnostic_status': status, 'diagnostic_evidence': [summary],
            'diagnostic_warnings': ([] if status == 'PASS' else
                                    ['optional log evidence is unavailable or incomplete'])}
