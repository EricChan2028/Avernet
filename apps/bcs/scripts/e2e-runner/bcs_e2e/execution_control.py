"""Exact-scope control port for this round's execution effects (task 9.3).

This is a runner-side control contract, not a product stop API and not
server authorization. It admits exactly one effect handle per operation,
proves the effect belongs to this Run/Case/attempt and actor, and asks
an injected exact-scope stop callback to act on that single handle.

The capability budget is frozen by task 9.2 (design D23) from the task
9.1 audit of the deployed version:

- an owned, actively running delivery may be stopped through its exact
  handle (a Human-initiated stop on a subscribed channel);
- queued system-sender deliveries have no cancel capability and must
  reach natural completion inside the bounded observation window;
- there is no proven full-Session drain or all-Bot stop, so any
  stop-shaped request covering more than one handle, a wildcard, a
  whole Bot or a whole queue is refused instead of invented;
- effects proven neither owned nor bound to this attempt (borrowed
  objects) are never operated on, claimed or released.

Authentication failures are propagated without retry and without
switching to a lower-privilege identity: the port records exactly one
stop attempt per admitted handle.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

from collections.abc import Iterable

from .correlation import RequestFailure
from .execution_ledger import ExecutionLedger, ExecutionLedgerError
from .profile import identifier

REFUSAL_CODES = {"BORROWED_OBJECT", "BROAD_STOP", "DRAIN_SHAPE", "UNSUPPORTED_SCOPE"}
ACTIVE_STATES = {"RUNNING"}
WAITABLE_STATES = {"RUNNING", "QUEUED", "ADMITTED"}
POLL_INTERVAL_SECONDS = 0.25


class EffectControlError(RuntimeError):
    """A control request was refused or the bounded window expired."""

    def __init__(self, code: str, message: str):
        if code not in REFUSAL_CODES:
            raise ValueError("unknown effect control refusal code")
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ControlledEffectControl:
    """Exact-scope stop/observe port bound to one Run attempt.

    ``stop_callback`` must perform the product-side stop for exactly one
    effect handle and return its observed record. It is injected by the
    adapter layer; this port never discovers Bot, queue or provider
    scopes on its own.
    """

    def __init__(self, ledger: ExecutionLedger, *, case_id: str, attempt: int,
                 allowed_actors: frozenset[str],
                 stop_callback: Callable[[str, dict], dict],
                 observation_seconds: int = 30):
        identifier(case_id, "case_id")
        if type(attempt) is not int or attempt < 1:
            raise ExecutionLedgerError("invalid control attempt")
        if not allowed_actors:
            raise ExecutionLedgerError("controlled effects require at least one possible actor")
        for actor in allowed_actors:
            identifier(actor, "allowed actor")
        if not callable(stop_callback):
            raise ExecutionLedgerError("controlled effects require an exact stop callback")
        if type(observation_seconds) is not int or observation_seconds < 1:
            raise ExecutionLedgerError("bounded observation window is required")
        self.ledger = ledger
        self.case_id = case_id
        self.attempt = attempt
        self.allowed_actors = frozenset(allowed_actors)
        self._stop_callback = stop_callback
        self.observation_seconds = observation_seconds
        self.stop_attempts: list[str] = []

    def validate_scope(self, selector: object) -> None:
        """Refuse any stop-shaped selector that is not an exact handle set."""
        if isinstance(selector, str):
            identifier(selector, "effect handle")
            return
        if isinstance(selector, dict):
            if any(key not in {"handle", "handles"} for key in selector):
                raise EffectControlError("BROAD_STOP", "selector keys beyond exact handles are refused")
            targets = selector.get("handles", [selector.get("handle")])
        else:
            targets = selector
        if targets is None:
            raise EffectControlError("BROAD_STOP", "empty stop selector is refused")
        if isinstance(targets, (str, bytes)) or not isinstance(targets, Iterable):
            raise EffectControlError("BROAD_STOP", "non-enumerable stop target is refused")
        targets = list(targets)
        if not targets:
            raise EffectControlError("BROAD_STOP", "empty stop selector is refused")
        if len({str(target) for target in targets}) != len(targets):
            raise EffectControlError("BROAD_STOP", "duplicate stop targets are refused")
        for target in targets:
            if not isinstance(target, str):
                raise EffectControlError("BROAD_STOP", "non-handle stop target is refused")
            if target in {"*", "all", "queue", "every", "bot", "default", "session"}:
                raise EffectControlError("DRAIN_SHAPE", f"wildcard stop target '{target}' is refused")
            identifier(target, "effect handle")

    def _row(self, handle: str) -> dict:
        if not isinstance(handle, str):
            raise EffectControlError("BORROWED_OBJECT", "effect handle must be a string")
        row = next((item for item in self.ledger.snapshot() if item["handle"] == handle), None)
        if row is None:
            raise EffectControlError("BORROWED_OBJECT",
                "effect handle is not proven by any effect in this Run ledger")
        if row["case_id"] != self.case_id or row["attempt"] != self.attempt:
            raise EffectControlError("BORROWED_OBJECT",
                "effect is not proven owned by this Case and attempt")
        if row["actor_id"] is not None and row["actor_id"] not in self.allowed_actors:
            raise EffectControlError("BORROWED_OBJECT",
                "effect actor is not admitted for this Run")
        return row

    def stop_delivery(self, handle: str) -> None:
        """Stop exactly one owned, actively running delivery effect."""
        self.validate_scope(handle)
        row = self._row(handle)
        if row["kind"] != "delivery":
            raise EffectControlError("UNSUPPORTED_SCOPE",
                "only delivery effects are stoppable through this port")
        if row["state"] not in ACTIVE_STATES:
            raise EffectControlError("UNSUPPORTED_SCOPE",
                "only an actively running delivery is stoppable; queued effects "
                "have no cancel capability and must complete naturally")
        self.stop_attempts.append(handle)
        issued_request_id = f"stop-{handle}"
        try:
            observed = self._stop_callback(handle, {**row, "request_id": issued_request_id})
        except RequestFailure:
            # Authentication/authorization failure: propagate without retry
            # and without falling back to another identity.
            raise
        if not isinstance(observed, dict) or not observed.get("effect_id"):
            self.ledger.unknown(handle, reason="stop callback returned no effect identity")
            raise EffectControlError("UNSUPPORTED_SCOPE",
                "stop callback did not observe an effect identity")
        pre_state = self._row(handle)["state"]
        if pre_state in {"COMPLETED", "FAILED"}:
            self.ledger.transition(handle, pre_state,
                                   reason="effect finished while stop was in flight")
            return
        self.ledger.transition(handle, "CANCELLING", reason="exact-scope stop admitted",
                               evidence_request_id=issued_request_id)

    def wait_terminal(self, handles: Iterable[str]) -> None:
        """Bounded terminal-state observation; expiry never reports PASS."""
        self.validate_scope(list(handles))
        rows = [self._row(handle) for handle in handles]
        terminal = {"COMPLETED", "CANCELLED", "FAILED", "RETAINED", "UNKNOWN"}
        for row in rows:
            if row["state"] not in WAITABLE_STATES and row["state"] != "CANCELLING" \
                    and row["state"] not in terminal:
                raise EffectControlError("UNSUPPORTED_SCOPE",
                    f"effect state {row['state']} is not waitable through this port")
        deadline = time.monotonic() + self.observation_seconds
        pending = {row["handle"] for row in rows}
        while pending and time.monotonic() < deadline:
            pending = {
                handle for handle in pending
                if self._row(handle)["state"] not in
                {"COMPLETED", "CANCELLED", "FAILED"}
            }
            if not pending:
                return
            time.sleep(POLL_INTERVAL_SECONDS)
        for handle in pending:
            self.ledger.unknown(handle,
                reason="bounded terminal observation window expired without terminal evidence")
        raise EffectControlError("UNSUPPORTED_SCOPE",
            "execution effects did not reach terminal within the bounded observation window")
