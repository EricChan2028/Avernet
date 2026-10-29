"""Bounded, immutable input for one run-owned local BCS panel bundle."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .runtime import RuntimeContractError

MIN_ASSET_BYTES = 1001
MAX_ASSET_BYTES = 2 * 1024 * 1024
CONTRACT = "bcs-local-panel-asset-v1"
_OWNED_RELATIVE_PATH = Path("panel/index.umd.js")
_RECEIPT_NAME = "panel-asset.json"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_READ_SIZE = 64 * 1024


def _error(message: str) -> RuntimeContractError:
    return RuntimeContractError(message)


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    try:
        observed = path.lstat()
    except OSError:
        raise _error("local panel asset must be a readable regular non-symlink file") from None
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise _error("local panel asset must be a regular non-symlink single-link file")

    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        raise _error("local panel asset must be a readable regular non-symlink file") from None
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino)):
            raise _error("local panel asset link or identity changed during open")
        return fd, metadata
    except BaseException:
        os.close(fd)
        raise


def _digest_fd(fd: int, *, maximum: int = MAX_ASSET_BYTES) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, min(_READ_SIZE, maximum + 1 - size))
        if not block:
            return digest.hexdigest(), size
        size += len(block)
        if size > maximum:
            raise _error("local panel asset exceeds the 2 MiB byte limit")
        digest.update(block)


def _write_all(fd: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("local panel asset write made no progress")
        view = view[written:]


def _exclusive_file(path: Path) -> int:
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    return os.open(path, flags, 0o600)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class LocalPanelAsset:
    path: Path
    sha256: str

    def validate(self) -> "LocalPanelAsset":
        if not isinstance(self.path, Path):
            raise _error("local panel asset path must be a pathlib.Path")
        if not isinstance(self.sha256, str) or _DIGEST.fullmatch(self.sha256) is None:
            raise _error("local panel asset requires a lowercase 64-character SHA256")
        if self.path.suffix not in {".js", ".mjs"}:
            raise _error("local panel asset filename must end in .js or .mjs")
        if self.path.is_symlink():
            raise _error("local panel asset must not be a symlink")

        fd, before = _open_regular(self.path)
        try:
            if not MIN_ASSET_BYTES <= before.st_size <= MAX_ASSET_BYTES:
                raise _error("local panel asset size must be 1001 bytes through 2 MiB")
            actual, size = _digest_fd(fd)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or size != before.st_size):
            raise _error("local panel asset changed while it was being validated")
        if actual != self.sha256:
            raise _error("local panel asset SHA256 does not match the declared digest")
        return self

    def install(self, root: Path, *, run_id: str) -> dict:
        """Copy into a fresh run root without adopting or replacing any path."""
        if not isinstance(root, Path):
            raise _error("local panel asset root must be a pathlib.Path")
        if (not isinstance(run_id, str) or not run_id or len(run_id) > 256
                or any(ord(char) < 32 for char in run_id)):
            raise _error("local panel asset requires a bounded run_id")

        self.validate()
        try:
            root_metadata = root.lstat()
        except OSError:
            raise _error("local panel asset root must already be a fresh directory") from None
        if root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
            raise _error("local panel asset root must be a non-symlink directory")

        panel_dir = root / _OWNED_RELATIVE_PATH.parent
        owned_path = root / _OWNED_RELATIVE_PATH
        receipt_path = root / _RECEIPT_NAME
        if (panel_dir.exists() or panel_dir.is_symlink()
                or receipt_path.exists() or receipt_path.is_symlink()):
            raise _error("local panel asset owned paths already exist; never adopt or overwrite them")

        # Re-open only after all destination admission checks. The descriptor,
        # identity checks and digest bind the exact bytes copied below.
        source_fd, source_before = _open_regular(self.path)
        try:
            if not MIN_ASSET_BYTES <= source_before.st_size <= MAX_ASSET_BYTES:
                raise _error("local panel asset size changed outside the supported bounds")
            os.mkdir(panel_dir, 0o700)
            os.chmod(panel_dir, 0o700, follow_symlinks=False)

            destination_fd = _exclusive_file(owned_path)
            copied_digest = hashlib.sha256()
            copied_size = 0
            try:
                os.fchmod(destination_fd, 0o600)
                while True:
                    block = os.read(source_fd, _READ_SIZE)
                    if not block:
                        break
                    copied_size += len(block)
                    if copied_size > MAX_ASSET_BYTES:
                        raise _error("local panel asset changed beyond the 2 MiB byte limit")
                    _write_all(destination_fd, block)
                    copied_digest.update(block)
                os.fsync(destination_fd)
            finally:
                os.close(destination_fd)

            source_after = os.fstat(source_fd)
            if ((source_before.st_dev, source_before.st_ino, source_before.st_size,
                 source_before.st_mtime_ns)
                    != (source_after.st_dev, source_after.st_ino, source_after.st_size,
                        source_after.st_mtime_ns)
                    or copied_size != source_before.st_size
                    or copied_digest.hexdigest() != self.sha256):
                raise _error("local panel asset changed before or during the owned copy")
        finally:
            os.close(source_fd)

        copied_fd, copied_metadata = _open_regular(owned_path)
        try:
            verified_digest, verified_size = _digest_fd(copied_fd)
        finally:
            os.close(copied_fd)
        if (copied_metadata.st_mode & 0o777) != 0o600:
            raise _error("owned local panel asset mode is not private")
        if verified_digest != self.sha256 or verified_size != copied_size:
            raise _error("owned local panel asset verification failed")

        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "contract": CONTRACT,
            "sha256": self.sha256,
            "size": verified_size,
            "owned_path": _OWNED_RELATIVE_PATH.as_posix(),
        }
        payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        receipt_fd = _exclusive_file(receipt_path)
        try:
            os.fchmod(receipt_fd, 0o600)
            _write_all(receipt_fd, payload)
            os.fsync(receipt_fd)
        finally:
            os.close(receipt_fd)

        _fsync_directory(panel_dir)
        _fsync_directory(root)
        return {"name": "bcsPanel", "type": "file", "file": str(owned_path.resolve(strict=True))}
