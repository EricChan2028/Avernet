"""Bounded, replaceable log-observation contract.

This module does not import an internal logging SDK or execute a query. A
runtime-specific adapter supplies a client that accepts the normalized
LogQuery. Stories can use the returned records as diagnostic evidence only;
required semantic persistence remains the EvidenceProvider contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Protocol

from .correlation import valid_traceparent
from .errors import primary_error
from .profile import ConfigError, identifier


class LogObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LogQuery:
    environment: str
    request_id: str | None = None
    traceparent: str | None = None
    start_epoch: int = 0
    end_epoch: int = 0
    limit: int = 50

    def validate(self, expected_environment: str, *, max_window_seconds: int = 900) -> None:
        try:
            environment = identifier(self.environment, "log.environment")
        except ConfigError:
            raise LogObservationError("log environment binding is invalid") from None
        if environment != expected_environment:
            raise LogObservationError("log query environment does not match the bound target")
        if (self.request_id is None) == (self.traceparent is None):
            raise LogObservationError("log query requires exactly one correlation selector")
        if self.request_id is not None:
            try:
                identifier(self.request_id, "log.request_id")
            except ConfigError:
                raise LogObservationError("log request ID is invalid") from None
        if self.traceparent is not None and not valid_traceparent(self.traceparent):
            raise LogObservationError("log traceparent is invalid")
        if (type(self.start_epoch) is not int or type(self.end_epoch) is not int
                or self.start_epoch < 0 or self.end_epoch < self.start_epoch
                or self.end_epoch - self.start_epoch > max_window_seconds):
            raise LogObservationError("log time window is outside the bounded range")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise LogObservationError("log result limit is outside the bounded range")

    def public(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "request_id": self.request_id,
            "traceparent": self.traceparent,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "limit": self.limit,
        }


class LogObserverClient(Protocol):
    def query(self, request: LogQuery, *, timeout_seconds: float) -> dict[str, Any]:
        """The deployment client must enforce the supplied transport timeout."""
        ...


def _safe_record(value: object, request: LogQuery) -> dict[str, Any]:
    """Verify returned scope and project only correlation fields, not log text."""
    if not isinstance(value, dict) or value.get("environment") != request.environment:
        raise LogObservationError("log record has a different or missing environment")
    timestamp = value.get("timestamp")
    if type(timestamp) is not int or not request.start_epoch <= timestamp <= request.end_epoch:
        raise LogObservationError("log record is outside the requested window")
    result = {"environment": request.environment, "timestamp": timestamp}
    if request.request_id is not None:
        if value.get("request_id") != request.request_id:
            raise LogObservationError("log record has a different request ID")
        result["request_id"] = request.request_id
    else:
        effective = value.get("traceparent")
        if (not valid_traceparent(effective)
                or effective.split("-")[1] != request.traceparent.split("-")[1]):
            raise LogObservationError("log record has a different trace ID")
        result["traceparent"] = effective
    return result


class BoundedLogObserver:
    """Read-only log observer with exact environment/correlation scoping."""

    def __init__(self, *, client: LogObserverClient, environment: str,
                 environment_binding_confirmed: bool, max_window_seconds: int = 900):
        try:
            self.environment = identifier(environment, "log.environment")
        except ConfigError:
            raise LogObservationError("log environment binding is invalid") from None
        if type(max_window_seconds) is not int or not 1 <= max_window_seconds <= 3600:
            raise LogObservationError("log maximum window is outside the supported bound")
        self.client = client
        self.environment_binding_confirmed = environment_binding_confirmed
        self.max_window_seconds = max_window_seconds

    def admit(self) -> dict[str, Any]:
        if self.environment_binding_confirmed is not True:
            raise LogObservationError("log physical environment binding is not confirmed")
        return {"status": "READY", "source": "logs",
                "environment": self.environment, "max_window_seconds": self.max_window_seconds}

    def observe(self, request: LogQuery, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        if self.environment_binding_confirmed is not True:
            raise LogObservationError("log physical environment binding is not confirmed")
        request.validate(self.environment, max_window_seconds=self.max_window_seconds)
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 30):
            raise LogObservationError("log transport timeout is outside the supported bound")
        try:
            result = self.client.query(request, timeout_seconds=timeout_seconds)
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            raise LogObservationError("log query failed") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            raise LogObservationError("log query returned an error")
        records = result.get("records")
        if not isinstance(records, list) or len(records) > request.limit:
            raise LogObservationError("log query returned an invalid record set")
        truncated = result.get("truncated", False)
        if type(truncated) is not bool:
            raise LogObservationError("log truncation flag is invalid")
        safe_records = [_safe_record(record, request) for record in records]
        return {"status": "INCOMPLETE" if truncated or not records else "OBSERVED",
                "source": "logs", "query": request.public(),
                "record_count": len(safe_records), "truncated": truncated,
                "records": safe_records}


class DeferredLogObserver:
    """Initialize an optional source only inside the diagnostic admission hook."""

    def __init__(self, factory: Callable[[], BoundedLogObserver]):
        self.factory = factory
        # No provider exists until admission succeeds. Construction may fail
        # without changing a Case's already-recorded business/cleanup outcomes.
        self._active: BoundedLogObserver | None = None

    def admit(self) -> dict[str, Any]:
        self._active = None
        observer = self.factory()
        admission = observer.admit()
        if not isinstance(admission, dict) or admission.get('status') != 'READY':
            raise LogObservationError('optional log provider was not admitted')
        self._active = observer
        return admission

    def observe(self, request: LogQuery, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        if self._active is None:
            raise LogObservationError('optional log provider was not admitted')
        return self._active.observe(request, timeout_seconds=timeout_seconds)
