"""Disposable, frozen Local/Server datasets for direct Qdrant benchmarks only."""

import logging
import threading
from contextlib import nullcontext
from uuid import uuid4

from qdrant_client import QdrantClient, models

from src.domain.models.vector_search_hit import VectorSearchHit
from src.infra.public.vectorstores.qdrant_keyword_index import normalize, sparse
from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)


class QdrantBenchmarkStores:
    """Never registered as the application's vector store; preparation is explicit.

    A dataset is immutable for this process lifetime. Local queries retain a mutex
    for Qdrant Local safety; Server queries run concurrently without that mutex.
    """

    def __init__(
        self, source: QdrantMySQLVectorStore, *, server: QdrantClient | None = None
    ):
        self.source = source
        self.server = server
        self.local = QdrantClient(location=":memory:")
        self._prepare_lock = threading.Lock()
        self._local_lock = threading.Lock()
        self.collection = "bcsfuse_bench_" + uuid4().hex
        self._closed = False
        self.server_index_at_prepare = {}
        self.ready = False
        self.server_ready = False
        self.count = 0
        self.filter_fields: dict[str, str] = {}

    @staticmethod
    def sparse_query(query: str) -> models.SparseVector:
        return sparse(query)

    def prepare(self, *, include_server: bool, filter_fields: dict[str, str]) -> dict:
        if not self._prepare_lock.acquire(blocking=False):
            raise RuntimeError("dataset preparation already running")
        created = []
        try:
            if self.ready:
                raise RuntimeError("dataset already prepared; restart to replace it")
            if include_server and self.server is None:
                raise RuntimeError("server is not configured")
            # Hold the existing store lock only while copying; no network or model
            # calls under this lock. A bounded snapshot avoids unbounded TC memory.
            records = []
            with self.source._index_lock:
                if self.source.size() > 5000:
                    raise ValueError("benchmark snapshot exceeds 5000 records")
                offset = None
                while True:
                    batch, offset = self.source._client.scroll(
                        self.source.collection_name,
                        limit=64,
                        offset=offset,
                        with_vectors=True,
                        with_payload=True,
                    )
                    records.extend(batch)
                    if offset is None:
                        break
            if not records:
                raise ValueError("cannot benchmark an empty dataset")
            clients = [self.local] + ([self.server] if include_server else [])
            for client in clients:
                client.create_collection(
                    self.collection,
                    vectors_config={
                        "dense": models.VectorParams(
                            size=self.source.dimension,
                            distance=models.Distance(self.source.distance),
                        )
                    },
                    sparse_vectors_config={
                        "text": models.SparseVectorParams(modifier=models.Modifier.IDF)
                    },
                )
                created.append(client)
                if client is self.server:
                    for field, schema in filter_fields.items():
                        client.create_payload_index(
                            self.collection,
                            field,
                            models.PayloadSchemaType(schema),
                            wait=True,
                        )
            for offset in range(0, len(records), 64):
                points = []
                for record in records[offset : offset + 64]:
                    payload = dict(record.payload or {})
                    payload["_bench_id"] = str(payload.get("_external_id", record.id))
                    content = (
                        payload.get("content")
                        or payload.get("searchable_text")
                        or payload.get("content_preview")
                        or ""
                    )
                    if normalize(str(content)) in {"无", "none", "null", "n/a"}:
                        content = ""
                    text = " ".join(
                        str(value or "")
                        for value in (
                            payload.get("worker_id"),
                            payload.get("identity_name"),
                            payload.get("name"),
                            content,
                        )
                    )
                    points.append(
                        models.PointStruct(
                            id=record.id,
                            payload=payload,
                            vector={"dense": record.vector, "text": sparse(text)},
                        )
                    )
                for client in clients:
                    client.upsert(self.collection, points, wait=True)
            for client in clients:
                if client.count(self.collection, exact=True).count != len(records):
                    raise RuntimeError("dataset count mismatch")
            if include_server:
                state = self.server.get_collection(self.collection)
                self.server_index_at_prepare = {
                    "version": self.server.info().version,
                    "status": str(state.status),
                    "indexed_vectors_count": state.indexed_vectors_count,
                    "points_count": state.points_count,
                }
            self.count = len(records)
            self.filter_fields = dict(filter_fields)
            self.server_ready = include_server
            self.ready = True
            return self.info()
        except Exception:
            # These collections were created by this attempt, never TC collections.
            for client in created:
                try:
                    client.delete_collection(self.collection)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Failed to clean benchmark collection %s", self.collection
                    )
            if created:
                self.collection = "bcsfuse_bench_" + uuid4().hex
            raise
        finally:
            self._prepare_lock.release()

    def info(self) -> dict:
        return {
            "dataset_ready": self.ready,
            "dataset_id": self.collection if self.ready else "",
            "vector_count": self.count,
            "server_ready": self.server_ready,
            "filter_fields": self.filter_fields,
            "local_serialized": True,
            "server_index_at_prepare": self.server_index_at_prepare,
            "search_scope": "direct_qdrant",
            "exact_worker_id": False,
        }

    def server_info(self) -> dict:
        if not self.server_ready:
            raise RuntimeError("server dataset is not prepared")
        state = self.server.get_collection(self.collection)
        return {
            "dataset_id": self.collection,
            "status": str(state.status),
            "indexed_vectors_count": state.indexed_vectors_count,
            "points_count": state.points_count,
            "optimizer_status": str(state.optimizer_status),
        }

    def query(self, backend: str, mode: str, vector, top_k: int, filters: dict):
        if not self.ready:
            raise RuntimeError("dataset is not prepared")
        if backend not in {"local", "server"} or mode not in {"vector", "keyword"}:
            raise ValueError("unsupported benchmark operation")
        if backend == "server" and not self.server_ready:
            raise RuntimeError("server dataset is not prepared")
        client = self.local if backend == "local" else self.server
        conditions = [
            models.FieldCondition(
                key=key,
                match=models.MatchAny(any=value)
                if isinstance(value, list)
                else models.MatchValue(value=value),
            )
            for key, value in filters.items()
        ]
        if mode == "keyword" and not vector.indices:
            return []
        with self._local_lock if backend == "local" else nullcontext():
            points = client.query_points(
                self.collection,
                query=vector,
                using="dense" if mode == "vector" else "text",
                limit=top_k,
                query_filter=models.Filter(must=conditions) if conditions else None,
                with_payload=["_bench_id"],
                with_vectors=False,
            ).points
        return [
            VectorSearchHit(id=p.payload["_bench_id"], score=p.score, payload={})
            for p in points
        ]

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.server_ready:
            try:
                self.server.delete_collection(self.collection)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to clean benchmark collection %s", self.collection
                )
        self.local.close()
        if self.server is not None:
            self.server.close()
