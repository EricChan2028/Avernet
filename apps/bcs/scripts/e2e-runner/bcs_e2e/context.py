"""Ephemeral, non-home execution contexts for the E2E runner.

A context contains only a validated profile copy and a small ownership marker.
It is deliberately separate from a user's permanent CLI/configuration home and
is removable only with a matching path-bound marker and the exact two-file
inventory. This is an accidental-deletion guard, not proof against tampering
by another process running as the same OS user.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os
import stat
import uuid

from .profile import ConfigError, fingerprint, unique_keys, validate


_CONTEXT_SCHEMA = 1
_CONTEXT_MARKER = "context.json"
_PROFILE_FILE = "profile.json"
_MAX_METADATA_BYTES = 2_097_152
_CONTEXT_FILES = {_CONTEXT_MARKER, _PROFILE_FILE}


def _resolved_external_root(path: Path) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    # Check the lexical root before resolving it.  A system temporary
    # directory such as macOS /tmp may itself be a symlink, but the context
    # path supplied by the caller must not be a symlink to an unrelated tree.
    if candidate.is_symlink():
        raise ConfigError("context: symlinked directories are not accepted")
    root = candidate.resolve()
    home = Path.home().resolve()
    if root == home or home in root.parents:
        raise ConfigError("context: directory must not be inside the user home")
    if root == Path(root.anchor):
        raise ConfigError("context: filesystem root is not an acceptable directory")
    return root


def _write_private(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ConfigError("context: metadata exceeds the size limit")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise ConfigError("context: destination already exists") from None
    except OSError:
        raise ConfigError("context: cannot write private metadata") from None


def create_ephemeral_context(profile: dict, context_dir: Path) -> dict:
    """Create an owned context root without resolving credentials or contacting services."""
    profile = validate(profile)
    root = _resolved_external_root(context_dir)
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise ConfigError("context: destination must be a regular directory")
        try:
            if any(root.iterdir()):
                raise ConfigError("context: destination must be empty")
        except OSError:
            raise ConfigError("context: destination cannot be inspected") from None
    else:
        try:
            root.mkdir(mode=0o700, parents=True)
        except OSError:
            raise ConfigError("context: cannot create destination") from None
    try:
        root.chmod(0o700)
        context_id = "ctx-" + uuid.uuid4().hex
        digest = fingerprint(profile)
        metadata = {
            "schema_version": _CONTEXT_SCHEMA,
            "kind": "bcs-e2e-ephemeral-context",
            "status": "ACTIVE",
            "context_id": context_id,
            "context_dir": str(root),
            "profile_digest": digest,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_private(root / _PROFILE_FILE, profile)
        try:
            _write_private(root / _CONTEXT_MARKER, metadata)
        except Exception:
            (root / _PROFILE_FILE).unlink(missing_ok=True)
            raise
    except ConfigError:
        raise
    except OSError:
        raise ConfigError("context: failed to initialize private files") from None
    return {"schema_version": _CONTEXT_SCHEMA, "status": "CREATED", "context_id": context_id,
            "context_dir": str(root), "profile_digest": digest}


def _read_owned_json(directory_fd: int, name: str) -> tuple[dict, os.stat_result]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ConfigError("context: metadata must be a regular file with no additional links")
        raw = stream.read(_MAX_METADATA_BYTES + 1)
    if len(raw) > _MAX_METADATA_BYTES:
        raise ConfigError("context: metadata exceeds the size limit")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ConfigError("context: metadata must be a JSON object")
    return value, info


def remove_ephemeral_context(context_dir: Path) -> dict:
    """Remove only two validated context files; never recursively delete data.

    Unexpected content (including Run state) blocks before either unlink. A
    file added concurrently also prevents rmdir; it is never swept away.
    """
    root = _resolved_external_root(context_dir)
    if not root.is_dir() or root.is_symlink():
        raise ConfigError("context: root is not an owned regular directory")
    try:
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ConfigError("context: directory cannot be opened") from None
    try:
        marker, marker_info = _read_owned_json(directory_fd, _CONTEXT_MARKER)
        profile, profile_info = _read_owned_json(directory_fd, _PROFILE_FILE)
        context_id = marker.get("context_id")
        if (type(marker.get("schema_version")) is not int
                or marker["schema_version"] != _CONTEXT_SCHEMA
                or marker.get("kind") != "bcs-e2e-ephemeral-context"
                or marker.get("status") != "ACTIVE"
                or marker.get("context_dir") != str(root)
                or not isinstance(context_id, str) or not context_id.startswith("ctx-")
                or len(context_id) != 36
                or any(c not in "0123456789abcdef" for c in context_id[4:])):
            raise ConfigError("context: ownership marker does not match this context; preserve legacy contexts")
        if fingerprint(validate(profile)) != marker.get("profile_digest"):
            raise ConfigError("context: profile copy does not match ownership marker")
        if set(os.listdir(directory_fd)) != _CONTEXT_FILES:
            raise ConfigError("context: unexpected files present; preserve Run state and remove nothing")
        # Recheck both entries before deleting either one. Relative operations
        # stay anchored to the opened directory instead of following a new path.
        for name, original in ((_PROFILE_FILE, profile_info), (_CONTEXT_MARKER, marker_info)):
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)):
                raise ConfigError("context: metadata changed during removal")
        os.unlink(_PROFILE_FILE, dir_fd=directory_fd)
        os.unlink(_CONTEXT_MARKER, dir_fd=directory_fd)
        os.fsync(directory_fd)
        os.rmdir(root)  # Nonempty directories fail here; no recursive fallback.
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("context: marker/profile unreadable or removal incomplete; preserve remaining files") from None
    finally:
        os.close(directory_fd)
    return {"schema_version": _CONTEXT_SCHEMA, "status": "REMOVED",
            "context_id": context_id, "context_dir": str(root)}
