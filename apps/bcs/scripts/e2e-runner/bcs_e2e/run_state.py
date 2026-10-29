"""Private atomic state files; never follow a state-file symlink."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import uuid

from .profile import ConfigError, fingerprint, unique_keys


class RunStateError(RuntimeError):
    pass


MAX_STATE_BYTES = 8 * 1024 * 1024


def _regular_target(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RunStateError("state target must be a regular file, not a symlink")
    if path.parent.is_symlink():
        raise RunStateError("state directory cannot be a symlink")


def write(path: Path, value: dict) -> None:
    # Encode first: serialization errors must not create an empty state file.
    encoded = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise RunStateError("state exceeds the supported size limit")
    _regular_target(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _regular_target(path)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        # Only our own temporary path is eligible, never another writer's files.
        if temporary.exists():
            temporary.unlink()


def read(path: Path) -> dict:
    _regular_target(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RunStateError("state is not a regular file")
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise RunStateError("state exceeds the supported size limit")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ConfigError):
        raise RunStateError("run state is missing or unreadable") from None
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise RunStateError("run state has an invalid schema")
    return value


def require_profile_binding(state: dict, profile: dict) -> str:
    """Validate the caller's target before opening a lease, ledger or adapter."""
    digest = fingerprint(profile)
    if state.get("profile_digest") != digest:
        raise RunStateError("profile does not match the prepared run")
    return digest
