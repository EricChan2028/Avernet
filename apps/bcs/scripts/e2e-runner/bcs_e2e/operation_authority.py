"""Monotonic operation fence for a held single-host fixture-pool lease.

This is not server-side fencing or a distributed lease. Once authority is lost,
this adapter never resumes, even if an operator restores a file or credential.
Only closing a previously held WS socket is exempt; CaseScopedAdapter continues
to enforce socket ownership. No remote cancellation authority is invented.
"""
from __future__ import annotations

from functools import wraps
import sys

from .correlation import RequestFailure
from .errors import has_cancellation
from .pool import PoolLease, PoolLeaseError


class AuthorizedAdapter:
    def __init__(self, adapter, lease: PoolLease):
        self._adapter = adapter
        self._lease = lease
        self.invalidated = False
        self.socket_close_status = "NOT_REQUIRED"

    def require_active(self) -> None:
        # Story finally blocks may run while a cancellation raised outside this
        # adapter (observer/evidence/checkpoint) is still unwinding. Fence that
        # path too, including a secondary exception chained over cancellation.
        pending = sys.exc_info()[1]
        if pending is not None and has_cancellation(pending):
            self.invalidated = True
        if self.invalidated:
            raise PoolLeaseError("Run operation authority was invalidated; explicit recovery required")
        try:
            self._lease.require_held()
        except PoolLeaseError:
            self.invalidated = True
            raise

    def __getattr__(self, name):
        operation = getattr(self._adapter, name)
        if not callable(operation) or name == "session_ws_close":
            return operation

        @wraps(operation)
        def checked(*args, **kwargs):
            self.require_active()
            try:
                result = operation(*args, **kwargs)
            except BaseException as error:
                if has_cancellation(error):
                    self.invalidated = True
                if isinstance(error, RequestFailure) and any(
                        record.get("http_status") in {401, 403}
                        for record in error.request_records):
                    self.invalidated = True
                # Preserve the original operation failure, including correlation
                # evidence, even if the lease disappeared during that request.
                try:
                    self.require_active()
                except PoolLeaseError:
                    pass
                raise
            try:
                self.require_active()
            except PoolLeaseError:
                # A successful connect has not yet handed ownership to the Case
                # wrapper. Close only that exact newly obtained socket here.
                if name == "session_ws_connect":
                    session_id = args[1] if len(args) > 1 else kwargs["session_id"]
                    self.socket_close_status = "UNCONFIRMED"
                    try:
                        self._adapter.session_ws_close(session_id)
                    except Exception:
                        pass  # Unconfirmed close is explicitly retained in the report.
                    else:
                        self.socket_close_status = "CLOSED"
                raise
            return result
        return checked
