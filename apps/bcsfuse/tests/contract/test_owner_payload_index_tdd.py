"""Payload-index schema changes belong to the single durable index owner."""

from __future__ import annotations

from app.application.services.index_state_vector_store import (
    IndexStateMaintainingVectorStore,
)
from app.domain.models.index_state import IndexStateRecord


class FixedOwnerStore:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id

    def claim_owner(self, _collection_pair, _owner_id, *, lease_seconds):
        return self._record()

    def read_owner(self, _collection_pair):
        return self._record()

    def _record(self) -> IndexStateRecord:
        return IndexStateRecord(
            collection_pair="profiles:dense+keyword",
            owner_id=self.owner_id,
            owner_expires_at=103.0,
            index_version=1,
            index_updated_at=100.0,
            observed_at=102.0,
        )


class ReadyBackend:
    def __init__(self) -> None:
        self.payload_index_calls = 0

    def check_ready(self) -> None:
        return None

    def ensure_payload_indexes(self) -> None:
        self.payload_index_calls += 1


def instantiate(record_owner: str, owner_id: str, backend) -> (
    IndexStateMaintainingVectorStore
):
    return IndexStateMaintainingVectorStore(
        backend,
        store=FixedOwnerStore(record_owner),
        owner_id=owner_id,
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=20.0,
        lease_seconds=30.0,
    )


def test_owner_initializes_server_payload_indexes_exactly_once():
    backend = ReadyBackend()
    owner = instantiate("owner-a", "owner-a", backend)
    assert not owner.read_only
    assert backend.payload_index_calls == 1


def test_read_only_replica_does_not_mutate_server_payload_indexes():
    backend = ReadyBackend()
    reader = instantiate("other-owner", "reader", backend)
    assert reader.read_only
    assert backend.payload_index_calls == 0


class LegacyBackend:
    def check_ready(self) -> None:
        return None


def test_backends_without_server_schema_support_are_compatible():
    owner = instantiate("owner-c", "owner-c", LegacyBackend())
    assert not owner.read_only
