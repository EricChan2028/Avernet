"""Payload fields used by bounded pre-retrieval authorization and filters."""

from __future__ import annotations

from collections.abc import Sequence

from qdrant_client.models import PayloadSchemaType


DENSE_PAYLOAD_INDEX_FIELDS = (
    "_external_id",
    "availability",
    "runtime_state",
    "domains",
    "trust_level",
    "worker_id",
    "profile_key",
)
KEYWORD_PAYLOAD_INDEX_FIELDS = (
    "_keyword_external_id",
    "_keyword_worker_id",
    "availability",
    "runtime_state",
    "domains",
    "trust_level",
    "worker_id",
    "profile_key",
)


def ensure_payload_indexes(
    client,
    *,
    collection_name: str,
    fields: Sequence[str],
) -> None:
    """Register keyword indexes for a Qdrant Server collection.

    Qdrant Server evaluates payload filters against metadata indexes. Without
    these indexes, a default visibility filter applies to almost every vector
    and introduces a large vector scan on each cold query. Local Qdrant does
    not use payload indexes for these filters, so this initialization is
    intentionally a Server-mode operation.
    """

    for field in fields:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )


__all__ = [
    "DENSE_PAYLOAD_INDEX_FIELDS",
    "KEYWORD_PAYLOAD_INDEX_FIELDS",
    "ensure_payload_indexes",
]
