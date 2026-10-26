"""Exact-scope termination state machine for one E2E Session effect set.

This is a runner lifecycle contract, not a product stop API.  Every callback is
injected by the adapter and must operate on the exact Session/run scope.  A
missing callback is treated as a no-op only for a controlled local adapter; a
remote adapter must expose an already-reviewed implementation before it is
allowed to use this path.
"""
from __future__ import annotations

from collections.abc import Callable
import math
from time import monotonic, time as wall_time


class TerminationError(RuntimeError):
    pass


PHASES = {
    "OPEN",
    "STOPPING_NEW_ACTIONS",
    "PRODUCTION_CLOSED",
    "WAITING_TERMINAL",
    "QUIESCENT",
    "CONTROL_CLOSED",
    "CLEANUP_READY",
    "CLEANED",
    "RETAINED",
}


class TerminationStateMachine:
    """Monotonic, exact-Session termination workflow.

    The state machine deliberately has no retry, discovery, broad stop, or
    lease-release operation.  Callers must retain the run when any callback
    fails or when the execution ledger remains uncertain.
    """

    def __init__(self, session_id: str, *,
                 persist: Callable[[dict], object] | None = None,
                 phase: str = "OPEN", history: list[dict[str, str]] | None = None,
                 timeout_seconds: int = 30):
        if not isinstance(session_id, str) or not session_id:
            raise TerminationError("termination requires an exact Session ID")
        if phase not in PHASES:
            raise TerminationError("unknown termination phase")
        if history is not None and (not isinstance(history, list) or not history):
            raise TerminationError("termination history is invalid")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise TerminationError("termination timeout must be an integer in 1..60 seconds")
        self.session_id = session_id
        self.phase = phase
        if history is None:
            self.deadline_epoch = wall_time() + timeout_seconds
            self.timeout_seconds = timeout_seconds
            self.history = [{"phase": "OPEN", "reason": "created",
                             "deadline_epoch": str(self.deadline_epoch),
                             "timeout_seconds": str(timeout_seconds)}]
        else:
            try:
                self.history = [dict(item) for item in history]
                self.deadline_epoch = float(self.history[0]["deadline_epoch"])
                self.timeout_seconds = int(self.history[0]["timeout_seconds"])
            except (KeyError, TypeError, ValueError):
                raise TerminationError("persisted termination deadline is missing or invalid") from None
            if (not math.isfinite(self.deadline_epoch) or self.deadline_epoch < 0
                    or not 1 <= self.timeout_seconds <= 60):
                raise TerminationError("persisted termination deadline is invalid")
        # Wall time preserves expiry across reopening; monotonic time prevents a
        # backward wall-clock adjustment from extending a live command's budget.
        remaining = min(self.timeout_seconds, self.deadline_epoch - wall_time())
        self._monotonic_deadline = monotonic() + max(0.0, remaining)
        self._persist = persist

    @classmethod
    def from_snapshot(cls, snapshot: dict, *, persist: Callable[[dict], object] | None = None):
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("history"), list)
                or not snapshot["history"] or not isinstance(snapshot.get("phase"), str)
                or not isinstance(snapshot.get("session_id"), str)):
            raise TerminationError("persisted termination snapshot requires its original history")
        return cls(snapshot["session_id"], persist=persist, phase=snapshot["phase"],
                   history=snapshot["history"])

    def _persist_snapshot(self) -> None:
        if self._persist is not None:
            try:
                self._persist(self.snapshot())
            except Exception as error:
                raise TerminationError(f"termination state persistence failed: {type(error).__name__}") from None

    def _move(self, phase: str, reason: str) -> None:
        if phase not in PHASES:
            raise TerminationError("unknown termination phase")
        self.phase = phase
        self.history.append({"phase": phase, "reason": reason})
        self._persist_snapshot()

    def _require(self, phase: str) -> None:
        if self.phase != phase:
            raise TerminationError(f"Session termination requires {phase}, got {self.phase}")

    def remaining_seconds(self) -> float:
        remaining = min(self._monotonic_deadline - monotonic(), self.deadline_epoch - wall_time())
        if remaining <= 0:
            self.retain("termination deadline exceeded")
            raise TerminationError("termination deadline exceeded; effects require reconciliation")
        return remaining

    def _invoke(self, callback: Callable[[], object] | None, label: str) -> None:
        self.remaining_seconds()
        try:
            if callback is not None:
                callback()
        except Exception as error:
            raise TerminationError(f"{label} failed: {type(error).__name__}") from None
        # Late success is not success. Transport callbacks remain responsible
        # for bounded I/O; this check does not interrupt or abandon a worker.
        self.remaining_seconds()

    def stop_new_actions(self) -> None:
        self._require("OPEN")
        self.remaining_seconds()
        self._move("STOPPING_NEW_ACTIONS", "runner action fence installed")

    def close_production(self, callback: Callable[[], object] | None = None) -> None:
        self._require("STOPPING_NEW_ACTIONS")
        self._invoke(callback, "production connection close")
        self._move("PRODUCTION_CLOSED", "production connections/subscriptions closed")

    def await_terminal(self, callback: Callable[[], object] | None = None) -> None:
        self._require("PRODUCTION_CLOSED")
        self._move("WAITING_TERMINAL", "waiting for exact effects to settle")
        try:
            self._invoke(callback, "exact effect quiescence")
        except Exception:
            self.retain("effect quiescence was not confirmed")
            raise
        self._move("QUIESCENT", "all exact effects reached confirmed terminal states")

    def close_control(self, callback: Callable[[], object] | None = None) -> None:
        self._require("QUIESCENT")
        self._invoke(callback, "termination/observation control close")
        self._move("CONTROL_CLOSED", "termination and observation channels closed")

    def ready_for_cleanup(self) -> None:
        self._require("CONTROL_CLOSED")
        self.remaining_seconds()
        self._move("CLEANUP_READY", "resource cleanup may begin")
        self.remaining_seconds()  # Persistence time also consumes the termination budget.

    def cleanup_complete(self) -> None:
        self._require("CLEANUP_READY")
        self._move("CLEANED", "Session resource deletion was verified")

    def retain(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason:
            raise TerminationError("retained termination requires a reason")
        if self.phase in {"CLEANED", "RETAINED"}:
            return
        self._move("RETAINED", reason)

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "deadline_epoch": self.deadline_epoch,
            "timeout_seconds": self.timeout_seconds,
            "phase": self.phase,
            "history": [dict(item) for item in self.history],
            "cleanup_ready": self.phase in {"CLEANUP_READY", "CLEANED"},
            "quiescence": "CONFIRMED" if self.phase in {"QUIESCENT", "CONTROL_CLOSED", "CLEANUP_READY", "CLEANED"}
            else "UNKNOWN",
        }
