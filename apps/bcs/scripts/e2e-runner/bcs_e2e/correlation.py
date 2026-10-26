"""Bounded correlation headers; trace propagation is opt-in per route binding."""
from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
import re
import secrets


_TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def new_traceparent() -> str:
    # Sample uniformly from the nonzero domain, without an unbounded retry.
    trace_id = f"{secrets.randbelow((1 << 128) - 1) + 1:032x}"
    span_id = f"{secrets.randbelow((1 << 64) - 1) + 1:016x}"
    return f"00-{trace_id}-{span_id}-01"


def valid_traceparent(value: object) -> bool:
    return (isinstance(value, str) and bool(_TRACEPARENT.fullmatch(value))
            and value[3:35] != '0' * 32 and value[36:52] != '0' * 16)


_RESPONSE_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")


def record_response_id(record: dict, value: object, *, source: str,
                       sensitive_values=()) -> bool:
    """Keep bounded actual IDs; an envelope ID is the public API correlation.

    Header IDs can belong to a gateway rather than BCS. Preserve both sources
    and disagreement, never claim that a differing pair was an exact echo.
    Missing optional headers do not mean that an envelope ID is missing.
    """
    if source not in {'HEADER', 'ENVELOPE'}:
        raise ValueError('unsupported response request ID source')
    field = 'response_header_request_id' if source == 'HEADER' else 'response_envelope_request_id'
    if source == 'HEADER' and (value is None or value == ''):
        return False
    candidates = []
    for item in sensitive_values:
        if isinstance(item, str) and item:
            candidates.append(item)
            # An Authorization header may wrap the actual secret in a scheme.
            if item.lower().startswith(('bearer ', 'basic ')):
                candidates.append(item.split(' ', 1)[1])
            cookies = SimpleCookie()
            try:
                cookies.load(item)
            except CookieError:
                pass
            candidates.extend(morsel.value for morsel in cookies.values())
    if (not isinstance(value, str) or _RESPONSE_ID.fullmatch(value) is None
            or any(secret and (secret == value or len(secret) >= 8 and secret in value) for secret in candidates)):
        record[field + '_rejected'] = True
        if source == 'ENVELOPE':
            record['response_request_id_rejected'] = True
        return False
    record[field] = value
    header, envelope = record.get('response_header_request_id'), record.get('response_envelope_request_id')
    record['response_request_id'] = envelope if envelope is not None else header
    record['response_request_id_source'] = 'ENVELOPE' if envelope is not None else 'HEADER'
    record['response_request_id_conflict'] = header is not None and envelope is not None and header != envelope
    return True


class RequestFailure(RuntimeError):
    """Transport error carrying request metadata only, never bodies/headers."""
    def __init__(self, message: str):
        super().__init__(message)
        self.request_records: list[dict] = []

    def with_record(self, record: dict):
        if not any(item is record for item in self.request_records):
            self.request_records.append(record)
        return self

    def with_prior_records(self, records: list[dict]):
        prior = [{**record, "preceded_failure": True} for record in records
                 if not any(item is record for item in self.request_records)]
        self.request_records = [*prior, *self.request_records]
        return self


_FAILURE_FIELDS = {
    "operation", "method", "path", "submitted_request_id", "response_request_id",
    "http_status", "request_id_echoed", "traceparent_mode", "submitted_traceparent",
    "effective_traceparent", "elapsed_ms", "target_kind", "response_projection", "preceded_failure",
    "protocol", "phase", "connected", "connect_request_id", "socket_close_status",
    "response_header_request_id", "response_envelope_request_id", "response_request_id_source",
    "response_request_id_conflict", "response_request_id_rejected",
    "response_header_request_id_rejected", "response_envelope_request_id_rejected",
}


class CorrelatedEvidence(list):
    """Evidence list that persists safe request/trace correlations as it grows."""

    def __init__(self, ledger, case_id: str, attempt: int):
        super().__init__()
        self.ledger = ledger
        self.case_id = case_id
        self.attempt = attempt

    def append(self, value):
        if isinstance(value, dict):
            self.ledger.record_correlation(self.case_id, self.attempt, value)
        super().append(value)

    def record_failure(self, error: BaseException) -> None:
        if not isinstance(error, RequestFailure):
            return
        for record in error.request_records:
            projected = {key: value for key, value in record.items() if key in _FAILURE_FIELDS}
            if projected.get('socket_close_status') not in ('CLOSED', 'NOT_OPENED', 'UNCONFIRMED'):
                projected.pop('socket_close_status', None)
            for field in ('response_request_id', 'response_header_request_id', 'response_envelope_request_id'):
                response_id = projected.get(field)
                if response_id is not None and (not isinstance(response_id, str)
                                                or _RESPONSE_ID.fullmatch(response_id) is None):
                    projected.pop(field)
                    projected[field + '_rejected'] = True
            projected.update(outcome="COMPLETED_BEFORE_FAILURE" if projected.get("preceded_failure") else "ERROR",
                             failure_type=type(error).__name__)
            # Preserve primary request metadata even when its auxiliary
            # correlation row cannot be stored. Never replace the request error.
            super().append(projected)
            if not projected.get("submitted_request_id"):
                projected["correlation_status"] = "NOT_APPLICABLE"
                continue
            try:
                self.ledger.record_correlation(self.case_id, self.attempt, projected)
                projected["correlation_status"] = "RECORDED"
            except Exception as persistence_error:
                projected["correlation_status"] = "BLOCKED"
                projected["correlation_error_type"] = type(persistence_error).__name__

    def extend(self, values):
        for value in values:
            self.append(value)
