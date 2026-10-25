"""Opt-in real binary integration: QDRANT_BENCH_BINARY=/path/to/qdrant pytest ..."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from src.domain.models.vector_point import VectorPoint
from src.infra.public.vectorstores.qdrant_benchmark_stores import QdrantBenchmarkStores
from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)
from src.interfaces.api.retrieval_benchmark import (
    benchmark_bearer_auth,
    create_retrieval_benchmark_router,
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(
    not os.environ.get("QDRANT_BENCH_BINARY"), reason="real Qdrant binary not supplied"
)
def test_real_server_equivalence_and_failure_isolation(tmp_path, monkeypatch):
    port, grpc = free_port(), free_port()
    config = tmp_path / "qdrant.yaml"
    config.write_text(
        json.dumps(
            {
                "telemetry_disabled": True,
                "log_level": "WARN",
                "service": {"host": "127.0.0.1", "http_port": port, "grpc_port": grpc},
                "storage": {
                    "storage_path": str(tmp_path / "server-data"),
                    "snapshots_path": str(tmp_path / "snapshots"),
                },
            }
        )
    )
    source = QdrantMySQLVectorStore(
        qdrant_path=str(tmp_path / "tc"),
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
            VectorPoint(
                id="c",
                vector=[0.0, 1.0, 0.0],
                payload={"worker_id": "c", "name": "target", "tag": ["b", "c"]},
            ),
        ]
    )
    log = (tmp_path / "server.log").open("w")
    process = subprocess.Popen(
        [
            str(Path(os.environ["QDRANT_BENCH_BINARY"]).resolve()),
            "--config-path",
            str(config),
        ],
        cwd=tmp_path,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    remote = QdrantClient(
        url=f"http://127.0.0.1:{port}",
        timeout=2,
        trust_env=False,
        check_compatibility=False,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
    )
    grpc_remote = QdrantClient(
        url=f"http://127.0.0.1:{port}",
        grpc_port=grpc,
        prefer_grpc=True,
        timeout=2,
        trust_env=False,
        check_compatibility=False,
    )
    stores = QdrantBenchmarkStores(source, server=remote, server_grpc=grpc_remote)
    try:
        deadline = time.monotonic() + 20
        while True:
            assert process.poll() is None, (tmp_path / "server.log").read_text()
            try:
                assert remote.info().version == "1.20.0"
                break
            except httpx.TransportError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        stores.prepare(include_server=True, filter_fields={"tag": "keyword"})
        for mode, vector in [
            ("keyword", stores.sparse_query("target")),
            ("vector", [1.0, 0.0, 0.0]),
        ]:
            for filters in [{}, {"tag": "b"}, {"tag": ["b", "c"]}, {"tag": "missing"}]:
                local = stores.query("local", mode, vector, 3, filters)
                server = stores.query("server", mode, vector, 3, filters)
                grpc_hits = stores.query("server_grpc", mode, vector, 3, filters)
                assert {h.id for h in local} == {h.id for h in server}
                assert {h.id for h in grpc_hits} == {h.id for h in server}
                scores = {h.id: h.score for h in server}
                for hit in local + grpc_hits:
                    assert hit.score == pytest.approx(scores[hit.id], rel=1e-5)
        assert (
            stores.query("local", "keyword", stores.sparse_query("target"), 1, {})[0].id
            == "b"
        )
        assert (
            stores.query("server", "keyword", stores.sparse_query("target"), 1, {})[
                0
            ].id
            == "b"
        )

        class Embedding:
            def embed(self, query):
                return [1.0, 0.0, 0.0]

        app = FastAPI()
        app.include_router(
            create_retrieval_benchmark_router(
                store=source,
                stores=stores,
                embedding=Embedding(),
                model_name="test",
                dimension=3,
                authenticate=benchmark_bearer_auth("unit-test"),
            )
        )
        with TestClient(app, headers={"Authorization": "Bearer unit-test"}) as client:
            samples = client.post(
                "/internal/bench/samples", json={"queries": ["target"]}
            ).json()
            assert client.get("/internal/bench/info").json()["backends"] == [
                "local",
                "server",
                "server_grpc",
            ]
            for backend in ["local", "server", "server_grpc"]:
                for mode in ["keyword", "vector"]:
                    response = client.post(
                        "/internal/bench/search",
                        json={
                            "sample_set_id": samples["sample_set_id"],
                            "sample_index": 0,
                            "backend": backend,
                            "mode": mode,
                            "filters": {"tag": "b"},
                            "top_k": 1,
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()[mode + "_hits"][0]["id"] == "b"
                    assert response.json()["backend"] == backend
            # A broken gRPC channel must not silently use the healthy HTTP client.
            with monkeypatch.context() as patch:

                def grpc_unavailable(*args, **kwargs):
                    raise ConnectionError("gRPC channel unavailable")

                patch.setattr(grpc_remote, "query_points", grpc_unavailable)
                body = {
                    "sample_set_id": samples["sample_set_id"],
                    "sample_index": 0,
                    "mode": "keyword",
                }
                assert (
                    client.post(
                        "/internal/bench/search",
                        json={**body, "backend": "server_grpc"},
                    ).status_code
                    == 503
                )
                assert (
                    client.post(
                        "/internal/bench/search", json={**body, "backend": "server"}
                    ).status_code
                    == 200
                )
            process.terminate()
            process.wait(timeout=10)
            body = {
                "sample_set_id": samples["sample_set_id"],
                "sample_index": 0,
                "mode": "keyword",
            }
            assert (
                client.post(
                    "/internal/bench/search", json={**body, "backend": "server"}
                ).status_code
                == 503
            )
            assert (
                client.post(
                    "/internal/bench/search", json={**body, "backend": "server_grpc"}
                ).status_code
                == 503
            )
            assert (
                client.post(
                    "/internal/bench/search", json={**body, "backend": "local"}
                ).status_code
                == 200
            )
            assert (
                source.text_search("target", 1)[0].id == "a"
            )  # TC still retains ID priority.
    finally:
        stores.close()
        source.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        log.close()
