"""Run-bound LocalMock snapshots, not product persistence or remote DB evidence."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import copy
import fcntl
import os
import stat
from pathlib import Path

from .profile import ConfigError, fingerprint, identifier
from .run_state import RunStateError, read, write


class MockStateError(RuntimeError):
    pass


RESOURCE_NAMES = ("groups", "sessions", "files")
ID_FIELDS = {"groups": "group_id", "sessions": "session_id", "files": "file_id"}
DELETION_NAMES = {"groups": "deleted", "sessions": "deleted_sessions", "files": "deleted_files"}


def empty_snapshot(run_id: str, digest: str) -> dict:
    return {"schema_version": 1, "run_id": run_id, "profile_digest": digest, "generation": 0,
            "groups": {}, "sessions": {}, "files": {}, "deleted": [], "deleted_sessions": [],
            "deleted_files": [], "connected_sessions": [], "shared_files": {},
            "provenance": {name: {} for name in RESOURCE_NAMES}}


def validate_snapshot(value: dict, run_id: str, digest: str) -> dict:
    try:
        if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError
        if set(value) != set(empty_snapshot(run_id, digest)):
            raise ValueError
        if value["run_id"] != run_id or value["profile_digest"] != digest:
            raise ValueError
        if type(value["generation"]) is not int or value["generation"] < 0:
            raise ValueError
        if not isinstance(value["provenance"], dict) or set(value["provenance"]) != set(RESOURCE_NAMES):
            raise ValueError
        result = copy.deepcopy(value)
        for name in RESOURCE_NAMES:
            objects, provenance = result[name], result["provenance"][name]
            if not isinstance(objects, dict) or len(objects) > 1000 or not isinstance(provenance, dict) or set(objects) != set(provenance):
                raise ValueError
            for resource_id, resource in objects.items():
                identifier(resource_id, "mock resource ID")
                if not isinstance(resource, dict) or resource.get(ID_FIELDS[name]) != resource_id:
                    raise ValueError
                proof = provenance[resource_id]
                if not isinstance(proof, dict) or set(proof) != {"case_id", "attempt", "origin", "parent_id"}:
                    raise ValueError
                identifier(proof["case_id"], "mock case ID")
                if type(proof["attempt"]) is not int or proof["attempt"] < 1 or proof["origin"] not in {"created", "borrowed", "reused"}:
                    raise ValueError
                parent = None if name == "groups" else resource.get("group_id" if name == "sessions" else "session_id")
                if proof["parent_id"] != parent:
                    raise ValueError
                if name != "groups" and parent not in result["groups" if name == "sessions" else "sessions"]:
                    raise ValueError
                if name == "files":
                    encoded = resource.pop("content_base64")
                    if not isinstance(encoded, str):
                        raise ValueError
                    content = base64.b64decode(encoded, validate=True)
                    if len(content) > 4096 or type(resource.get("size")) is not int or not 0 <= resource["size"] <= 4096:
                        raise ValueError
                    if len(content) > resource["size"]:
                        raise ValueError
                    resource["content"] = content
            deleted = result[DELETION_NAMES[name]]
            if (not isinstance(deleted, list) or not all(isinstance(item, str) for item in deleted)
                    or len(set(deleted)) != len(deleted) or not set(deleted) <= set(objects)):
                raise ValueError
            result[DELETION_NAMES[name]] = set(deleted)
        connections = result["connected_sessions"]
        if (not isinstance(connections, list) or not all(isinstance(item, str) for item in connections)
                or len(set(connections)) != len(connections)
                or not set(connections) <= set(result["sessions"]) - result["deleted_sessions"]):
            raise ValueError
        result["connected_sessions"] = set(connections)
        shares = result["shared_files"]
        if not isinstance(shares, dict) or len(shares) > 1000:
            raise ValueError
        for token, file_id in shares.items():
            identifier(token, "mock share token")
            if not isinstance(file_id, str) or file_id not in result["files"] or file_id in result["deleted_files"]:
                raise ValueError
        return result
    except (ValueError, TypeError, KeyError, ConfigError):
        raise MockStateError("local mock snapshot has invalid data or run/profile binding") from None


class MockStateStore:
    def __init__(self, state_dir: Path, run_id: str, profile: dict):
        identifier(run_id, "run_id")
        if profile["entrypoint"]["kind"] != "mock" or profile["environment"]["tier"] != "local":
            raise MockStateError("mock persistence is restricted to the local mock profile")
        self.path = state_dir / "local-mock.json"
        self.run_id, self.digest = run_id, fingerprint(profile)

    @contextmanager
    def _guard(self):
        if self.path.parent.is_symlink():
            raise MockStateError("local mock directory cannot be a symlink")
        fd = os.open(self.path.with_suffix(".guard"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise MockStateError("local mock guard must be a regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MockStateError("local mock snapshot has an active writer") from None
            yield
        finally:
            os.close(fd)

    def create(self) -> None:
        with self._guard():
            if self.path.exists() or self.path.is_symlink():
                raise MockStateError("local mock snapshot already exists; never reset it implicitly")
            write(self.path, empty_snapshot(self.run_id, self.digest))

    def load(self) -> dict:
        try:
            value = read(self.path)
        except RunStateError:
            raise MockStateError("local mock snapshot missing or unreadable; explicit recovery required") from None
        return validate_snapshot(value, self.run_id, self.digest)

    def save(self, value: dict, expected_generation: int) -> None:
        # The snapshot guard makes compare + replace atomic for stale adapter instances.
        with self._guard():
            current = self.load()
            if current["generation"] != expected_generation:
                raise MockStateError("local mock snapshot changed; stale adapter write refused")
            if value["generation"] != expected_generation + 1:
                raise MockStateError("invalid local mock generation advance")
            validate_snapshot(value, self.run_id, self.digest)
            write(self.path, value)
