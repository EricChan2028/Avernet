"""Durable local surrogate implementation of the pilot adapter contract."""
from __future__ import annotations

import base64
import copy
from pathlib import Path

from .adapters import LocalMockAdapter
from .evidence import LocalSemanticEvidenceProvider
from .mock_cleanup import verify_snapshot_scope
from .mock_state import DELETION_NAMES, RESOURCE_NAMES, MockStateError, MockStateStore
from .run_state import read, require_profile_binding


_MUTATIONS = {
    "group.create", "group.update", "group.delete", "session.create", "session.update", "session.delete",
    "session.ws.connect", "session.ws.close", "file.prepare", "file.upload", "file.complete", "file.share", "file.delete",
}
_CREATIONS = {"group.create": "groups", "session.create": "sessions", "file.prepare": "files"}
_STATE_FIELDS = (*RESOURCE_NAMES, *DELETION_NAMES.values(), "connected_sessions", "shared_files")


class PersistentLocalMockAdapter(LocalMockAdapter):
    def __init__(self, profile: dict, state_dir: Path, *, case_id: str | None = None):
        # None is intentionally read/cleanup-only; it cannot create resources.
        super().__init__(profile)
        state = read(state_dir / "run.json")
        require_profile_binding(state, profile)
        self.store = MockStateStore(state_dir, state["run_id"], profile)
        self.case_id = case_id
        self._poisoned = False
        snapshot = self.store.load()
        self.generation = snapshot["generation"]
        self.provenance = snapshot["provenance"]
        self._restore(snapshot)

    def _restore(self, snapshot: dict) -> None:
        for name in _STATE_FIELDS:
            setattr(self, name, snapshot[name])

    def _snapshot(self) -> dict:
        result = {"schema_version": 1, "run_id": self.store.run_id, "profile_digest": self.store.digest,
                  "generation": self.generation + 1, "provenance": copy.deepcopy(self.provenance)}
        for name in _STATE_FIELDS:
            value = getattr(self, name)
            result[name] = sorted(value) if isinstance(value, set) else copy.deepcopy(value)
        for value in result["files"].values():
            value["content_base64"] = base64.b64encode(value.pop("content")).decode("ascii")
        return result

    def _record(self, operation: str, method: str, path: str, status: int,
                *, request_id: str | None = None) -> dict:
        if self._poisoned:
            raise MockStateError("local mock persistence failed; reload and reconcile before further operations")
        if operation in _MUTATIONS:
            try:
                if operation in _CREATIONS:
                    if self.case_id is None:
                        raise MockStateError("recovery adapter cannot create resources")
                    names = ("groups", "sessions") if operation == "group.create" else (_CREATIONS[operation],)
                    for name in names:
                        new_ids = set(getattr(self, name)) - set(self.provenance[name])
                        if len(new_ids) != 1:
                            raise MockStateError("mock creation provenance is ambiguous")
                        resource_id = next(iter(new_ids))
                        value = getattr(self, name)[resource_id]
                        parent = None if name == "groups" else value["group_id" if name == "sessions" else "session_id"]
                        self.provenance[name][resource_id] = {
                            "case_id": self.case_id, "attempt": 1, "origin": "created", "parent_id": parent,
                        }
                self.store.save(self._snapshot(), self.generation)
                self.generation += 1
            except BaseException:
                self._poisoned = True
                raise
        return super()._record(operation, method, path, status, request_id=request_id)

    def semantic_observer(self):
        def query(plan):
            if self._poisoned:
                raise MockStateError("local mock persistence requires reconciliation")
            snapshot = self.store.load()
            view = LocalMockAdapter(self.profile)
            for name in _STATE_FIELDS:
                setattr(view, name, snapshot[name])
            return view.semantic_observer().query(plan)
        return LocalSemanticEvidenceProvider(database="local", environment="local", query=query)


    def verify_cleanup_scope(self, run_id: str, records: list[dict]) -> dict:
        return verify_snapshot_scope(self._current_snapshot(), run_id, records)

    def _current_snapshot(self) -> dict:
        if self._poisoned:
            raise MockStateError("local mock persistence requires reconciliation")
        snapshot = self.store.load()
        if snapshot["generation"] != self.generation:
            raise MockStateError("local mock state changed; reload before cleanup")
        self._restore(snapshot)
        self.provenance = snapshot["provenance"]
        return snapshot

    def _owned(self, name: str, resource_id: str, parent_id: str | None = None) -> dict:
        snapshot = self._current_snapshot()
        proof = snapshot["provenance"][name].get(resource_id)
        if proof is None or proof["origin"] != "created":
            raise MockStateError("resource has no independent current-Run creation proof")
        if self.case_id is not None and proof["case_id"] != self.case_id:
            raise MockStateError("resource was created by another case")
        if parent_id is not None and proof["parent_id"] != parent_id:
            raise MockStateError("file belongs to a different session")
        return snapshot

    @staticmethod
    def _children_absent(snapshot: dict, name: str, resource_id: str) -> None:
        if name == "groups":
            if any(s["group_id"] == resource_id and sid not in snapshot["deleted_sessions"]
                   for sid, s in snapshot["sessions"].items()):
                raise MockStateError("group still has live sessions")
        elif name == "sessions":
            if resource_id in snapshot["connected_sessions"]:
                raise MockStateError("session still has a live subscription")
            if any(f["session_id"] == resource_id and fid not in snapshot["deleted_files"]
                   for fid, f in snapshot["files"].items()):
                raise MockStateError("session still has live files")
        elif resource_id in snapshot["shared_files"].values():
            raise MockStateError("deleted file still has a share mapping")

    def group_delete(self, group_id: str):
        snapshot = self._owned("groups", group_id)
        self._children_absent(snapshot, "groups", group_id)
        if group_id in snapshot["deleted"]:
            return {"deleted": True}, LocalMockAdapter._record(self, "group.delete", "DELETE", f"/mock/groups/{group_id}", 200)
        return super().group_delete(group_id)

    def session_delete(self, session_id: str):
        snapshot = self._owned("sessions", session_id)
        self._children_absent(snapshot, "sessions", session_id)
        if session_id in snapshot["deleted_sessions"]:
            return {"deleted": True}, LocalMockAdapter._record(self, "session.delete", "DELETE", f"/mock/sessions/{session_id}", 200)
        return super().session_delete(session_id)

    def file_delete(self, session_id: str, file_id: str):
        snapshot = self._owned("files", file_id, session_id)
        if file_id in snapshot["deleted_files"]:
            self._children_absent(snapshot, "files", file_id)
            return {"deleted": True}, LocalMockAdapter._record(self, "file.delete", "DELETE", f"/mock/sessions/{session_id}/files/{file_id}", 200)
        return super().file_delete(session_id, file_id)

    def group_verify_deleted(self, group_id: str):
        snapshot = self._owned("groups", group_id)
        self._children_absent(snapshot, "groups", group_id)
        if group_id not in snapshot["deleted"]:
            raise MockStateError("group deletion is not independently recorded")
        return super().group_verify_deleted(group_id)

    def session_verify_deleted(self, session_id: str):
        snapshot = self._owned("sessions", session_id)
        self._children_absent(snapshot, "sessions", session_id)
        if session_id not in snapshot["deleted_sessions"]:
            raise MockStateError("session deletion is not independently recorded")
        return super().session_verify_deleted(session_id)

    def file_verify_deleted(self, session_id: str, file_id: str):
        snapshot = self._owned("files", file_id, session_id)
        self._children_absent(snapshot, "files", file_id)
        if file_id not in snapshot["deleted_files"]:
            raise MockStateError("file deletion is not independently recorded")
        return None, LocalMockAdapter._record(self, "file.verify_deleted", "GET", f"/mock/sessions/{session_id}/files/{file_id}", 404)
