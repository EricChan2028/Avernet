"""Single-machine fixture-pool lease.

The lease is fail-closed: an existing lock is never reclaimed automatically,
even when its PID is gone. A human/operator must inspect the saved run ledger
and explicitly release the lock. This is intentionally only a local mutual
exclusion primitive; it is not server-side authorization or a cross-machine
lease.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import time
import uuid

from .profile import identifier


class PoolBusyError(RuntimeError):
    pass


class PoolLeaseError(RuntimeError):
    pass


def pool_root() -> Path:
    """One fixed registry per OS account, shared across checkouts and Run dirs."""
    return Path.home() / ".local" / "state" / "bcs-e2e" / "pools"


def pool_scope(profile: dict) -> dict[str, str]:
    # Credentials, timeouts and entrypoint routes do not change pool ownership.
    return {"environment_tier": profile["environment"]["tier"],
            "environment_id": profile["environment"]["id"],
            "fixture_pool": profile["policy"]["fixture_pool"]}


def pool_path(scope: dict) -> Path:
    if not isinstance(scope, dict) or set(scope) != {
        "environment_tier", "environment_id", "fixture_pool"
    }:
        raise PoolLeaseError("invalid fixture-pool scope")
    for key, value in scope.items():
        identifier(value, key)
    key = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return pool_root() / (key + ".lock")


def inspect_pool(path: Path) -> dict:
    """Read-only lease inspection; never reclaims a reservation automatically."""
    if path.is_symlink():
        return {"status": "BLOCKED", "lock_path": str(path),
                "reason": "fixture-pool lock is a symlink"}
    if not path.exists():
        return {"status": "AVAILABLE", "lock_path": str(path), "process_alive": False}
    if not path.is_file():
        return {"status": "BLOCKED", "lock_path": str(path),
                "reason": "fixture-pool lock is not a regular file"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "BLOCKED", "lock_path": str(path),
                "reason": "fixture-pool lock is unreadable"}
    required = {"schema_version", "run_id", "profile_digest", "lease_token",
                "acquired_at", "pid", "host"}
    if (not isinstance(payload, dict) or set(payload) != required
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("run_id"), str)
            or not isinstance(payload.get("profile_digest"), str)
            or not isinstance(payload.get("pid"), int)
            or not isinstance(payload.get("host"), str)
            or not isinstance(payload.get("lease_token"), str)):
        return {"status": "BLOCKED", "lock_path": str(path),
                "reason": "fixture-pool lock has an invalid schema"}
    if len(payload["profile_digest"]) != 64 or len(payload["lease_token"]) != 32:
        return {"status": "BLOCKED", "lock_path": str(path),
                "reason": "fixture-pool lock has invalid binding metadata"}
    if payload["pid"] <= 0:
        process_alive = False
    else:
        try:
            os.kill(payload["pid"], 0)
            process_alive = True
        except ProcessLookupError:
            process_alive = False
        except PermissionError:
            process_alive = None
        except OSError:
            process_alive = False
    return {
        "status": "LEASED", "lock_path": str(path), "run_id": payload["run_id"],
        "profile_digest": payload["profile_digest"], "pid": payload["pid"],
        "host": payload["host"], "acquired_at": payload.get("acquired_at"),
        "process_alive": process_alive,
        "guard_present": path.with_suffix(".guard").is_file(),
        "recovery": "inspect the matching Run ledger and use release; never unlink this file directly",
    }


def bound_pool_path(state: dict) -> Path:
    """Reject older per-Run locks rather than silently bypass a shared lease."""
    path = pool_path(state.get("pool_scope"))
    if state.get("lock_path") != str(path):
        raise PoolLeaseError("run does not use the shared fixture-pool registry; explicit recovery required")
    return path


class PoolLease:
    def __init__(self, path: Path, run_id: str, profile_digest: str, token: str,
                 acquired_at: int, owner: str, guard_fd: int):
        self.path = path
        self.run_id = run_id
        self.profile_digest = profile_digest
        self.token = token
        self.acquired_at = acquired_at
        self.owner = owner
        self._guard_fd = guard_fd

    @staticmethod
    def _guard(path: Path, *, existing_only: bool = False) -> int:
        # Persistent guard inode: unlinking it would allow two separate flocks.
        if path.parent.is_symlink():
            raise PoolLeaseError("fixture-pool directory cannot be a symlink")
        if not existing_only:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_NOFOLLOW | (0 if existing_only else os.O_CREAT)
        fd = os.open(path.with_suffix(".guard"), flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PoolLeaseError("fixture-pool guard must be a regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PoolBusyError("fixture-pool command is already active") from None
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _read_payload(path: Path, run_id: str, profile_digest: str) -> dict:
        if not path.is_file() or path.is_symlink():
            raise PoolLeaseError("fixture-pool lock is missing or not a regular file")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PoolLeaseError("fixture-pool lock is unreadable") from None
        required = {"schema_version", "run_id", "profile_digest", "lease_token",
                    "acquired_at", "pid", "host"}
        if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != 1:
            raise PoolLeaseError("fixture-pool lock has an invalid schema")
        if payload["run_id"] != run_id or payload["profile_digest"] != profile_digest:
            raise PoolLeaseError("fixture-pool lock does not match the run/profile")
        token = payload["lease_token"]
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            raise PoolLeaseError("fixture-pool lock has an invalid token")
        if payload["host"] != socket.gethostname():
            raise PoolLeaseError("fixture-pool reservation belongs to another host")
        return payload

    @classmethod
    def acquire(cls, path: Path, run_id: str, profile_digest: str) -> "PoolLease":
        identifier(run_id, "run_id")
        if len(profile_digest) != 64 or any(c not in "0123456789abcdef" for c in profile_digest):
            raise PoolLeaseError("invalid profile digest")
        guard = cls._guard(path)
        try:
            if path.is_symlink():
                raise PoolLeaseError("fixture-pool lock cannot be a symlink")
            payload = {
                "schema_version": 1, "run_id": run_id, "profile_digest": profile_digest,
                "lease_token": uuid.uuid4().hex, "acquired_at": int(time.time()),
                "pid": os.getpid(), "host": socket.gethostname(),
            }
            encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                raise PoolBusyError("fixture-pool is already leased; inspect and explicitly release it") from None
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                # A partial reservation remains fail-closed for recovery.
                raise
            return cls(path, run_id, profile_digest, payload["lease_token"],
                       payload["acquired_at"], payload["host"], guard)
        except BaseException:
            os.close(guard)
            raise

    @classmethod
    def from_file(cls, path: Path, run_id: str, profile_digest: str, *, existing_guard_only: bool = False) -> "PoolLease":
        guard = cls._guard(path, existing_only=existing_guard_only)
        try:
            payload = cls._read_payload(path, run_id, profile_digest)
            return cls(path, run_id, profile_digest, payload["lease_token"],
                       payload["acquired_at"], payload["host"], guard)
        except BaseException:
            os.close(guard)
            raise

    def public_view(self) -> dict:
        return {
            "status": "ACQUIRED", "run_id": self.run_id,
            "profile_digest": self.profile_digest, "acquired_at": self.acquired_at,
            "owner": self.owner, "lock_path": str(self.path),
        }

    def require_held(self) -> None:
        """Revalidate local command ownership without reacquiring or renewing it."""
        if self._guard_fd < 0:
            raise PoolLeaseError("fixture-pool command guard is no longer held")
        try:
            held = os.fstat(self._guard_fd)
            current_guard = self.path.with_suffix(".guard").lstat()
            if (not stat.S_ISREG(current_guard.st_mode)
                    or (held.st_dev, held.st_ino) != (current_guard.st_dev, current_guard.st_ino)):
                raise PoolLeaseError("fixture-pool command guard was replaced")
            current = self._read_payload(self.path, self.run_id, self.profile_digest)
        except OSError:
            raise PoolLeaseError("fixture-pool authority is unavailable") from None
        if current["lease_token"] != self.token:
            raise PoolLeaseError("fixture-pool lock token mismatch")

    def release(self) -> None:
        self.require_held()
        self.path.unlink()
        self.close()

    def close(self) -> None:
        """End this command only; keep the durable pool reservation in place."""
        if self._guard_fd >= 0:
            os.close(self._guard_fd)
            self._guard_fd = -1

    def __enter__(self) -> "PoolLease":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
