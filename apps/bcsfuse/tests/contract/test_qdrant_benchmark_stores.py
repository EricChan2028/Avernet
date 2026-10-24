"""Pure Qdrant operations must not invoke business retrieval or persistence."""

import pytest
from qdrant_client import QdrantClient

from src.domain.models.vector_point import VectorPoint
from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


def test_snapshot_queries_skip_business_search_and_filter_before_top_k(
    tmp_path, monkeypatch
):
    from src.infra.public.vectorstores.qdrant_benchmark_stores import (
        QdrantBenchmarkStores,
    )

    source = QdrantMySQLVectorStore(
        qdrant_path=str(tmp_path / "source"),
        dimension=3,
        persistence_backend=SQLiteVectorPersistenceBackend(str(tmp_path / "db.sqlite")),
    )
    source.upsert(
        [
            VectorPoint(
                id="a",
                vector=[1.0, 0.0, 0.0],
                payload={"worker_id": "target", "name": "其他", "tag": "a"},
            ),
            VectorPoint(
                id="b",
                vector=[0.9, 0.1, 0.0],
                payload={"worker_id": "b", "name": "target target", "tag": "b"},
            ),
        ]
    )
    server = QdrantClient(location=":memory:")
    stores = QdrantBenchmarkStores(source, server=server)
    try:
        info = stores.prepare(include_server=True, filter_fields={"tag": "keyword"})
        assert info["vector_count"] == 2

        def forbidden(*args, **kwargs):
            raise AssertionError("business retrieval / durable storage was called")

        monkeypatch.setattr(source, "search", forbidden)
        monkeypatch.setattr(source, "text_search", forbidden)
        monkeypatch.setattr(source._persistence, "load_all", forbidden)
        monkeypatch.setattr(stores.local, "scroll", forbidden)
        monkeypatch.setattr(server, "scroll", forbidden)
        for backend in ["local", "server"]:
            for mode, vector in [
                ("vector", [1.0, 0.0, 0.0]),
                ("keyword", stores.sparse_query("target")),
            ]:
                hits = stores.query(backend, mode, vector, 1, {"tag": "b"})
                assert [h.id for h in hits] == ["b"]
        source.delete(["b"])
        assert (
            stores.query("local", "vector", [1.0, 0.0, 0.0], 1, {"tag": "b"})[0].id
            == "b"
        )
        assert source.size() == 1
        with pytest.raises(RuntimeError, match="already"):
            stores.prepare(include_server=True, filter_fields={})
        assert stores.query("local", "vector", [1.0, 0.0, 0.0], 1, {})
    finally:
        stores.close()
        source.close()


def test_server_failure_never_falls_back_to_local(tmp_path):
    from src.infra.public.vectorstores.qdrant_benchmark_stores import (
        QdrantBenchmarkStores,
    )

    source = QdrantMySQLVectorStore(
        qdrant_path=str(tmp_path / "source"),
        dimension=3,
        persistence_backend=SQLiteVectorPersistenceBackend(str(tmp_path / "db.sqlite")),
    )
    source.upsert(
        [VectorPoint(id="a", vector=[1.0, 0.0, 0.0], payload={"name": "告警"})]
    )
    stores = QdrantBenchmarkStores(source)
    try:
        stores.prepare(include_server=False, filter_fields={})
        with pytest.raises(RuntimeError, match="server"):
            stores.query("server", "vector", [1.0, 0.0, 0.0], 1, {})
        assert stores.query("local", "vector", [1.0, 0.0, 0.0], 1, {})
    finally:
        stores.close()
        source.close()


def test_failed_import_can_retry_without_modifying_tc(tmp_path, monkeypatch):
    from src.infra.public.vectorstores.qdrant_benchmark_stores import (
        QdrantBenchmarkStores,
    )

    source = QdrantMySQLVectorStore(
        qdrant_path=str(tmp_path / "source"),
        dimension=3,
        persistence_backend=SQLiteVectorPersistenceBackend(str(tmp_path / "db.sqlite")),
    )
    source.upsert(
        [VectorPoint(id="a", vector=[1.0, 0.0, 0.0], payload={"name": "告警"})]
    )
    remote = QdrantClient(location=":memory:")
    stores = QdrantBenchmarkStores(source, server=remote)
    original = remote.upsert
    try:

        def unavailable(*args, **kwargs):
            raise RuntimeError("temporary server failure")

        monkeypatch.setattr(remote, "upsert", unavailable)
        with pytest.raises(RuntimeError, match="temporary server failure"):
            stores.prepare(include_server=True, filter_fields={})
        assert not stores.ready
        assert source.size() == 1
        assert source.text_search("告警", 1)[0].id == "a"
        monkeypatch.setattr(remote, "upsert", original)
        assert (
            stores.prepare(include_server=True, filter_fields={})["vector_count"] == 1
        )
        assert stores.query("server", "vector", [1.0, 0.0, 0.0], 1, {})[0].id == "a"
    finally:
        stores.close()
        source.close()
