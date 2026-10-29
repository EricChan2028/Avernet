"""Pinned repository collaboration templates for one owned local BCS run."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .runtime import RuntimeContractError

CONTRACT = "bcs-local-template-asset-v1"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_FILES = 256
_RECEIPT_NAME = "template-assets.json"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LANGUAGE = re.compile(r"[a-z]{2}-[A-Z]{2}")
_TEMPLATE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.yaml")
_READ_SIZE = 64 * 1024


def _error(message: str) -> RuntimeContractError:
    return RuntimeContractError(message)


def _open_regular(path: Path, label: str) -> tuple[int, os.stat_result]:
    try:
        observed = path.lstat()
    except OSError:
        raise _error(f"{label} must be a readable regular non-symlink file") from None
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
        raise _error(f"{label} must be a regular non-symlink single-link file")
    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        raise _error(f"{label} must be a readable regular non-symlink file") from None
    metadata = os.fstat(fd)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino)):
        os.close(fd)
        raise _error(f"{label} link or identity changed during open")
    return fd, metadata


def _read_pinned(path: Path, *, label: str, maximum: int) -> tuple[bytes, os.stat_result]:
    fd, before = _open_regular(path, label)
    try:
        if before.st_size > maximum:
            raise _error(f"{label} exceeds its byte limit")
        value = bytearray()
        while True:
            block = os.read(fd, min(_READ_SIZE, maximum + 1 - len(value)))
            if not block:
                break
            value.extend(block)
            if len(value) > maximum:
                raise _error(f"{label} exceeds its byte limit")
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(value) != before.st_size):
        raise _error(f"{label} changed while it was being read")
    return bytes(value), before


def _write_all(fd: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("local template asset write made no progress")
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


def _relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise _error("local template manifest paths must be bounded POSIX relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise _error("local template manifest path traversal is forbidden")
    if path.parts == ("registry.yaml",):
        return path
    if (len(path.parts) == 2 and _LANGUAGE.fullmatch(path.parts[0])
            and _TEMPLATE_NAME.fullmatch(path.parts[1])):
        return path
    raise _error("local template manifest must use the registry/language catalog layout")


@dataclass(frozen=True)
class _ApprovedFile:
    path: PurePosixPath
    sha256: str
    size: int


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _error("local template manifest contains duplicate JSON keys")
        result[key] = value
    return result


def _bounded_entries(path: Path, limit: int):
    entries = []
    with os.scandir(path) as scan:
        for entry in scan:
            if len(entries) >= limit:
                raise _error("local template source exceeds the directory-entry limit")
            entries.append(entry)
    return sorted(entries, key=lambda entry: entry.name)


def _catalog_inventory(root: Path) -> tuple[PurePosixPath, ...]:
    try:
        metadata = root.lstat()
    except OSError:
        raise _error("local template source must be a readable non-symlink directory") from None
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise _error("local template source must be a non-symlink directory")

    files: list[PurePosixPath] = []
    languages: set[str] = set()
    try:
        root_entries = _bounded_entries(root, 4)
        for entry in root_entries:
            entry_metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(entry_metadata.st_mode):
                raise _error("local template source symlinks are forbidden")
            if stat.S_ISREG(entry_metadata.st_mode):
                if entry.name == "README.md":
                    continue
                if entry.name != "registry.yaml" or entry_metadata.st_nlink != 1:
                    raise _error("local template source contains an unsupported catalog file")
                files.append(_relative_path(entry.name))
                continue
            if (not stat.S_ISDIR(entry_metadata.st_mode)
                    or entry.name not in {"en-US", "zh-CN"}):
                raise _error("local template source contains an unsupported catalog entry")
            languages.add(entry.name)
            for child in _bounded_entries(root / entry.name, MAX_FILES - len(files)):
                child_metadata = child.stat(follow_symlinks=False)
                if stat.S_ISLNK(child_metadata.st_mode):
                    raise _error("local template source symlinks are forbidden")
                if not stat.S_ISREG(child_metadata.st_mode) or child_metadata.st_nlink != 1:
                    raise _error("local template files must be regular non-symlink single-link files")
                files.append(_relative_path(f"{entry.name}/{child.name}"))
                if len(files) > MAX_FILES:
                    raise _error("local template source exceeds the file-count limit")
    except OSError:
        raise _error("local template source could not be scanned safely") from None
    required = {
        PurePosixPath("registry.yaml"),
        PurePosixPath("en-US/single-bot-guided-answer.yaml"),
        PurePosixPath("zh-CN/single-bot-guided-answer.yaml"),
    }
    if languages != {"en-US", "zh-CN"} or not required.issubset(files):
        raise _error("local template source requires registry, both languages and guided templates")
    return tuple(sorted(files, key=lambda path: path.as_posix()))


@dataclass(frozen=True)
class LocalTemplateAsset:
    """An actual catalog directory bound by a separately approved JSON manifest.

    Callers provide ``directory``, an explicit manifest file, and the manifest's
    independently approved SHA256. The manifest is schema version 1 with this
    contract and ``files`` entries containing ``path``, ``sha256`` and ``size``.
    """

    directory: Path
    manifest_path: Path
    manifest_sha256: str

    def _approved_files(self) -> tuple[_ApprovedFile, ...]:
        if not isinstance(self.directory, Path) or not isinstance(self.manifest_path, Path):
            raise _error("local template asset paths must be pathlib.Path values")
        if not isinstance(self.manifest_sha256, str) or _DIGEST.fullmatch(self.manifest_sha256) is None:
            raise _error("local template asset requires an approved lowercase 64-character manifest SHA256")
        manifest_bytes, _ = _read_pinned(
            self.manifest_path, label="local template manifest", maximum=MAX_MANIFEST_BYTES)
        if hashlib.sha256(manifest_bytes).hexdigest() != self.manifest_sha256:
            raise _error("local template manifest SHA256 does not match the approved digest")
        try:
            document = json.loads(manifest_bytes, object_pairs_hook=_object_without_duplicates)
        except RuntimeContractError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _error("local template manifest must be valid UTF-8 JSON") from None
        if (not isinstance(document, dict) or set(document) != {"schema_version", "contract", "files"}
                or document.get("schema_version") != 1 or document.get("contract") != CONTRACT
                or not isinstance(document.get("files"), list)):
            raise _error("local template manifest contract is invalid")
        raw_files = document["files"]
        if not 1 <= len(raw_files) <= MAX_FILES:
            raise _error("local template manifest file count is outside the supported bounds")

        approved: list[_ApprovedFile] = []
        collision_keys: set[str] = set()
        for raw in raw_files:
            if not isinstance(raw, dict) or set(raw) != {"path", "sha256", "size"}:
                raise _error("local template manifest file entries are invalid")
            relative = _relative_path(raw["path"])
            digest = raw["sha256"]
            size = raw["size"]
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise _error("local template file requires a lowercase 64-character SHA256")
            if type(size) is not int or not 1 <= size <= MAX_FILE_BYTES:
                raise _error("local template file size is outside the supported bounds")
            collision = relative.as_posix().casefold()
            if collision in collision_keys:
                raise _error("local template manifest paths collide")
            collision_keys.add(collision)
            approved.append(_ApprovedFile(relative, digest, size))
        approved.sort(key=lambda item: item.path.as_posix())
        if sum(item.size for item in approved) > MAX_TOTAL_BYTES:
            raise _error("local template manifest exceeds the total byte limit")
        inventory = _catalog_inventory(self.directory)
        if inventory != tuple(item.path for item in approved):
            raise _error("local template manifest does not exactly bind the source catalog")
        return tuple(approved)

    def validate(self) -> "LocalTemplateAsset":
        approved = self._approved_files()
        for item in approved:
            value, _ = _read_pinned(
                self.directory.joinpath(*item.path.parts),
                label=f"local template file {item.path.as_posix()}", maximum=MAX_FILE_BYTES)
            if len(value) != item.size or hashlib.sha256(value).hexdigest() != item.sha256:
                raise _error(f"local template file {item.path.as_posix()} drifted from its approved manifest")
        return self

    def install(self, root: Path, *, run_id: str) -> dict:
        """Copy the exact approved catalog into the fresh run's templates directory."""
        if not isinstance(root, Path):
            raise _error("local template asset root must be a pathlib.Path")
        if (not isinstance(run_id, str) or not run_id or len(run_id) > 256
                or any(ord(char) < 32 for char in run_id)):
            raise _error("local template asset requires a bounded run_id")
        approved = self._approved_files()
        self.validate()

        templates = root / "templates"
        receipt_path = root / _RECEIPT_NAME
        try:
            root_metadata = root.lstat()
            templates_metadata = templates.lstat()
        except OSError:
            raise _error("local template destination must be a fresh owned runtime") from None
        if (root.is_symlink() or templates.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode)
                or not stat.S_ISDIR(templates_metadata.st_mode)):
            raise _error("local template destination must use non-symlink directories")
        if receipt_path.exists() or receipt_path.is_symlink() or any(templates.iterdir()):
            raise _error("local template owned paths already exist; never adopt or overwrite them")

        for parent in sorted({item.path.parts[0] for item in approved if len(item.path.parts) == 2}):
            path = templates / parent
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700, follow_symlinks=False)

        receipt_files: list[dict[str, object]] = []
        for item in approved:
            source = self.directory.joinpath(*item.path.parts)
            value, _ = _read_pinned(
                source, label=f"local template file {item.path.as_posix()}", maximum=MAX_FILE_BYTES)
            if len(value) != item.size or hashlib.sha256(value).hexdigest() != item.sha256:
                raise _error(f"local template file {item.path.as_posix()} changed before installation")
            destination = templates.joinpath(*item.path.parts)
            fd = _exclusive_file(destination)
            try:
                os.fchmod(fd, 0o600)
                _write_all(fd, value)
                os.fsync(fd)
            finally:
                os.close(fd)
            copied, copied_metadata = _read_pinned(
                destination, label=f"owned local template file {item.path.as_posix()}", maximum=MAX_FILE_BYTES)
            if ((copied_metadata.st_mode & 0o777) != 0o600 or len(copied) != item.size
                    or hashlib.sha256(copied).hexdigest() != item.sha256):
                raise _error(f"owned local template file {item.path.as_posix()} verification failed")
            receipt_files.append({"path": item.path.as_posix(), "sha256": item.sha256, "size": item.size})

        # Detect source additions, removals or byte drift that raced the copy.
        self.validate()
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "contract": CONTRACT,
            "manifest_sha256": self.manifest_sha256,
            "files": receipt_files,
        }
        payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd = _exclusive_file(receipt_path)
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        for directory in sorted({templates / item.path.parts[0] for item in approved if len(item.path.parts) == 2}):
            _fsync_directory(directory)
        _fsync_directory(templates)
        _fsync_directory(root)
        return receipt
