"""Shared semantic file expectations; wire contracts normalize explicitly."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileExpectation:
    file_id: str
    session_id: str
    file_name: str
    mime_type: str
    size: int
    sha256: str
    owner_id: str
    storage_backend: str
    owner_kind: str = "human"

    def __post_init__(self):
        if self.owner_kind not in ("human", "bot"):
            raise ValueError("file expectation requires an explicit human or bot owner kind")

    def matches_metadata(self, value: object) -> bool:
        if not isinstance(value, dict):
            return False
        required = {"file_id", "session_id", "file_name", "mime_type", "size", "sha256",
                    "owner", "storage_backend", "status", "created_at", "updated_at"}
        return (required <= value.keys()
                and self.matches_persisted(value)
                and value["file_name"] == self.file_name and value["mime_type"] == self.mime_type
                and value["owner"] == {"actor_kind": self.owner_kind, "actor_id": self.owner_id}
                and all(type(value[name]) is int and value[name] >= 0 for name in ("created_at", "updated_at")))

    def matches_page(self, value: object) -> bool:
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            return False
        items, total = value["items"], value.get("total")
        if type(total) is not int or total < len(items) or not all(isinstance(item, dict) for item in items):
            return False
        matches = [item for item in items if item.get("file_id") == self.file_id]
        return len(matches) == 1 and self.matches_metadata(matches[0])

    def matches_persisted(self, value: object) -> bool:
        return (isinstance(value, dict)
                and value.get("file_id") == self.file_id and value.get("session_id") == self.session_id
                and value.get("status") == "ready" and value.get("storage_backend") == self.storage_backend
                and type(value.get("size")) is int and value["size"] == self.size
                and "sha256" in value and value["sha256"] in (None, self.sha256))
