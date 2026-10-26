"""Fail-closed boundary pending a genuinely run-owned local lifecycle.

The previous singlebox shell delegation could kill processes by port/path and
could not prove per-run ownership. Do not reinstate it behind a force flag.
Legacy markers are preserved for explicit recovery, never trusted as PID proof.
"""
from __future__ import annotations

from pathlib import Path


class RuntimeContractError(RuntimeError):
    """The requested local runtime cannot be controlled safely."""


DEFAULT_LOCAL_PORTS = (21000, 8765, 30001, 30011, 30021, 30031, 30041)
DISABLED_REASON = (
    "runner-managed LocalSinglebox lifecycle is disabled: legacy scripts do not "
    "enforce per-run process ownership; existing runtime state requires explicit recovery"
)


def require_no_legacy_runtime(state_dir: Path) -> None:
    """No cleanup/release while old producers may still be running."""
    marker = state_dir / "runtime.json"
    if marker.exists() or marker.is_symlink():
        raise RuntimeContractError(
            "unverified legacy runtime state requires explicit process recovery; "
            "preserve the marker, ledger and lease")


class LocalSingleboxRuntime:
    """Reserved lifecycle API; currently no process or network capabilities."""

    def __init__(
        self,
        profile: dict,
        state_dir: Path,
        *,
        runtime_dir: Path | None = None,
        repo_root: Path | None = None,
        ports: tuple[int, ...] = DEFAULT_LOCAL_PORTS,
    ):
        if profile["entrypoint"]["kind"] != "singlebox":
            raise RuntimeContractError("local runtime requires entrypoint.kind=singlebox")
        self.state_dir = state_dir
        self.runtime_dir = runtime_dir or state_dir / "singlebox-runtime"
        # Keep call compatibility for the reserved API, not an execution bypass.
        if state_dir.is_symlink() or self.runtime_dir.is_symlink():
            raise RuntimeContractError("state-dir/runtime-dir cannot be a symlink")
        state_root, runtime_root = state_dir.resolve(), self.runtime_dir.resolve()
        if runtime_root == state_root or state_root not in runtime_root.parents:
            raise RuntimeContractError("runtime-dir must be a private child of state-dir")

    @property
    def state_path(self) -> Path:
        return self.state_dir / "runtime.json"

    def check(self) -> dict:
        return {
            "status": "BLOCKED", "ready_to_execute": False,
            "reason": DISABLED_REASON,
            "legacy_state_present": self.state_path.exists() or self.state_path.is_symlink(),
            "process_ownership": "UNVERIFIED",
        }

    def start(self) -> dict:
        raise RuntimeContractError(DISABLED_REASON)

    def stop(self) -> dict:
        raise RuntimeContractError(DISABLED_REASON)

    def status(self) -> dict:
        # Even a saved RUNNING/STOPPED value is not a live process observation.
        return self.check()
