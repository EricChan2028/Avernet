"""Per-Run isolated Provider/Judge sandbox and receipt cursor for fixed-Loop.

``FIXED_LOOP.sandbox-isolation`` requires that a fresh per-Run Provider and
a deterministic Judge sandbox *replace* the shared ``/control/reset`` and
``/control/provider/clear`` control-plane resets, and that no existing
endpoint or service state is reset.  This module is the owned-local fixture
that satisfies that invariant:

* :class:`FixedLoopRunFixture` provisions a brand-new
  :class:`~bcs_e2e.local_judge_sandbox.OwnedLocalJudgeSandbox` scoped to one
  Run ID, bound to an ephemeral loopback port.  The fixture manages only its
  own sandbox lifecycle (start/close); it NEVER issues a shared control-plane
  reset, and :meth:`FixedLoopRunFixture.forbid_shared_control_reset` fails
  closed on any attempt to route a shared reset through it.
* :class:`FixedLoopReceiptCursor` is an append-only, strictly-sequential
  ledger of per-iteration Provider/Judge receipts.  It preserves the prior
  iteration's ``output_ref`` so the next iteration can verify it and reject a
  stale or drifted ``response_ref``.

The fixture is local-only: it starts no engine, calls no BCS, opens no
callback, and grants no release credit.  It is the safety core that the
full fixed-Loop case executor (still gated on the owned-local runtime's
state-machine-runs routes) will compose.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib

from .local_judge_sandbox import OwnedLocalJudgeSandbox
from .runtime import RuntimeContractError

# The shared control-plane resets that a fixed-Loop Run must not issue.
# The per-Run sandbox owns its own lifecycle instead, so these become
# unnecessary; admitting them would reset every other Run's shared state.
SHARED_CONTROL_RESETS = frozenset({"/control/reset", "/control/provider/clear"})


class FixedLoopSandboxError(RuntimeContractError):
    """Sanitized per-Run fixture, lifecycle, or receipt-cursor failure."""


def _run_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise FixedLoopSandboxError(f"invalid {label}")
    if any(character.isspace() for character in value):
        raise FixedLoopSandboxError(f"invalid {label}: whitespace")
    return value


def _receipt_body(value: object) -> str:
    """Return a finite receipt body hash without reading the payload."""
    if not isinstance(value, dict):
        raise FixedLoopSandboxError("receipt evidence must be a mapping")
    for key in ("body_sha256", "delivery_request_id", "request_id"):
        if key in value and not isinstance(value[key], str):
            raise FixedLoopSandboxError("receipt evidence field is not a string")
    return value.get("body_sha256", "") or ""


@dataclass(frozen=True)
class IterationReceipt:
    """One fixed-Loop iteration's append-only, sanitized receipt record."""

    iteration: int
    output_ref: str
    provider_receipt: dict
    judge_receipt: dict

    def to_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "output_ref": self.output_ref,
            "provider_receipt": dict(self.provider_receipt),
            "judge_receipt": dict(self.judge_receipt),
        }


class FixedLoopReceiptCursor:
    """Append-only, strictly-sequential per-Run receipt ledger.

    Each iteration's ``output_ref`` is a deterministic hash of the iteration
    index and the Provider/Judge receipt body hashes.  The next iteration's
    request must echo the prior ``output_ref`` as its ``response_ref``; a
    mismatch is a stale or drifted reference and is rejected.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = _run_identifier(run_id, "run ID")
        self._receipts: list[IterationReceipt] = []

    @property
    def last_iteration(self) -> int:
        return len(self._receipts)

    def _output_ref(self, iteration: int, provider: str, judge: str) -> str:
        digest = hashlib.sha256()
        digest.update(self.run_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(iteration).encode("ascii"))
        digest.update(b"\x00")
        digest.update(provider.encode("ascii"))
        digest.update(b"\x00")
        digest.update(judge.encode("ascii"))
        return digest.hexdigest()

    def record(
        self,
        iteration: int,
        *,
        provider_receipt: dict,
        judge_receipt: dict,
    ) -> IterationReceipt:
        """Append one iteration receipt; ordering and uniqueness are enforced."""
        if not isinstance(iteration, int) or isinstance(iteration, bool):
            raise FixedLoopSandboxError("iteration must be an integer")
        if iteration != len(self._receipts) + 1:
            raise FixedLoopSandboxError(
                "receipt cursor is append-only and strictly sequential")
        provider_body = _receipt_body(provider_receipt)
        judge_body = _receipt_body(judge_receipt)
        output_ref = self._output_ref(iteration, provider_body, judge_body)
        receipt = IterationReceipt(
            iteration=iteration,
            output_ref=output_ref,
            provider_receipt=dict(provider_receipt),
            judge_receipt=dict(judge_receipt),
        )
        self._receipts.append(receipt)
        return receipt

    def prior(self, iteration: int) -> IterationReceipt:
        """Return the iteration immediately before ``iteration``."""
        if not isinstance(iteration, int) or isinstance(iteration, bool):
            raise FixedLoopSandboxError("iteration must be an integer")
        if iteration < 2 or iteration - 1 > len(self._receipts):
            raise FixedLoopSandboxError("iteration has no prior receipt")
        return self._receipts[iteration - 2]

    def expected_response_ref(self, iteration: int) -> str | None:
        """The ``response_ref`` the next iteration must echo, or None first."""
        if not isinstance(iteration, int) or isinstance(iteration, bool):
            raise FixedLoopSandboxError("iteration must be an integer")
        if iteration < 1:
            raise FixedLoopSandboxError("iteration must be positive")
        if iteration == 1:
            return None
        return self.prior(iteration).output_ref

    def verify_response_ref(self, iteration: int, response_ref: object) -> bool:
        """Reject a stale or drifted ``response_ref`` for the next iteration."""
        if not isinstance(iteration, int) or isinstance(iteration, bool):
            raise FixedLoopSandboxError("iteration must be an integer")
        expected = self.expected_response_ref(iteration)
        if expected is None:
            if response_ref not in (None, ""):
                raise FixedLoopSandboxError(
                    "first iteration has no prior output to reference")
            return True
        if response_ref != expected:
            raise FixedLoopSandboxError("stale or drifted response_ref")
        return True

    def snapshot(self) -> list[dict]:
        """Return the append-only receipt ledger without payloads."""
        return [receipt.to_dict() for receipt in self._receipts]


class FixedLoopRunFixture:
    """Fresh per-Run Provider/Judge sandbox that replaces shared control reset."""

    def __init__(self, run_id: str) -> None:
        self.run_id = _run_identifier(run_id, "run ID")
        self._sandbox: OwnedLocalJudgeSandbox | None = None
        self._cursor = FixedLoopReceiptCursor(run_id)
        self._state = "NEW"
        self._forbidden_reset_attempts = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def fresh_per_run(self) -> bool:
        """True: each Run owns a brand-new sandbox; none is ever reused."""
        return self._sandbox is not None and self._sandbox.run_id == self.run_id

    def start(self) -> "FixedLoopRunFixture":
        """Provision the fresh per-Run sandbox (ephemeral loopback port)."""
        if self._state != "NEW":
            raise FixedLoopSandboxError("fixture lifecycle cannot start twice")
        self._sandbox = OwnedLocalJudgeSandbox(self.run_id)
        self._sandbox.start()
        self._state = "LIVE"
        return self

    def __enter__(self) -> "FixedLoopRunFixture":
        return self.start()

    def __exit__(self, *_args) -> None:
        self.close()

    def _require_live(self) -> None:
        if self._state != "LIVE" or self._sandbox is None:
            raise FixedLoopSandboxError("fixture is not live")
        self._sandbox.validate_live(self.run_id)

    @property
    def provider_url(self) -> str:
        self._require_live()
        return self._sandbox.provider_url

    @property
    def judge_base_url(self) -> str:
        self._require_live()
        return self._sandbox.judge_base_url

    def record_iteration(
        self,
        iteration: int,
        *,
        provider_receipt: dict,
        judge_receipt: dict,
    ) -> IterationReceipt:
        """Record one iteration receipt against the live per-Run sandbox."""
        self._require_live()
        return self._cursor.record(
            iteration, provider_receipt=provider_receipt, judge_receipt=judge_receipt)

    def expected_response_ref(self, iteration: int) -> str | None:
        self._require_live()
        return self._cursor.expected_response_ref(iteration)

    def verify_response_ref(self, iteration: int, response_ref: object) -> bool:
        self._require_live()
        return self._cursor.verify_response_ref(iteration, response_ref)

    def forbid_shared_control_reset(self, path: object) -> bool:
        """Fail closed on any attempt to route a shared control reset here."""
        if not isinstance(path, str):
            raise FixedLoopSandboxError("control path must be a string")
        if path in SHARED_CONTROL_RESETS:
            self._forbidden_reset_attempts += 1
            raise FixedLoopSandboxError(
                "shared control-plane reset is forbidden; the per-Run sandbox "
                "owns its own lifecycle")
        return False

    def assert_no_shared_reset(self, attempted_paths: object) -> bool:
        """Prove a Run touched no shared control-plane reset path."""
        if isinstance(attempted_paths, str):
            attempted_paths = [attempted_paths]
        if not isinstance(attempted_paths, (list, tuple, frozenset, set)):
            raise FixedLoopSandboxError("attempted paths must be a sequence")
        for path in attempted_paths:
            if path in SHARED_CONTROL_RESETS:
                self._forbidden_reset_attempts += 1
                raise FixedLoopSandboxError(
                    "Run issued a shared control-plane reset: " + str(path))
        return True

    def snapshot(self) -> dict:
        """Finite sanitized evidence; no URLs, payloads, or credentials."""
        return {
            "run_id": self.run_id,
            "state": self._state,
            "fresh_per_run": self.fresh_per_run,
            "forbidden_reset_attempts": self._forbidden_reset_attempts,
            "receipts": self._cursor.snapshot(),
            "sandbox": (
                self._sandbox.snapshot() if self._sandbox is not None else None
            ),
        }

    def close(self) -> None:
        """Release this Run's own sandbox resources; never a shared reset."""
        if self._state == "CLOSED":
            return
        if self._state == "NEW":
            self._state = "CLOSED"
            return
        sandbox, self._sandbox = self._sandbox, None
        if sandbox is not None:
            sandbox.close()
        self._state = "CLOSED"
