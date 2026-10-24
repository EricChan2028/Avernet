"""Benchmark HTTP contract against the real durable/local Qdrant composition."""

import math
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.models.vector_point import VectorPoint
from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


class QueryEmbeddings:
    """Only the external model is replaced; retrieval uses real Qdrant."""

    def __init__(self):
        self.calls = []
        self.vector = [1.0, 0.0, 0.0]

    def embed(self, text):
        self.calls.append(text)
        return self.vector


@contextmanager
def bench(tmp_path, **options):
    from src.interfaces.api.retrieval_benchmark import (
        benchmark_bearer_auth,
        create_retrieval_benchmark_router,
    )

    backend = SQLiteVectorPersistenceBackend(str(tmp_path / "vectors.sqlite"))
    store = QdrantMySQLVectorStore(
        qdrant_path=str(tmp_path / "qdrant"),
        dimension=3,
        persistence_backend=backend,
    )
    store.upsert(
        [
            VectorPoint(
                id="account:default:description",
                vector=[1.0, 0.0, 0.0],
                payload={"worker_id": "account", "name": "查账", "category": "finance"},
            ),
            VectorPoint(
                id="travel:default:description",
                vector=[0.0, 1.0, 0.0],
                payload={"worker_id": "travel", "name": "订票", "category": "travel"},
            ),
        ]
    )
    embedding = QueryEmbeddings()
    from src.infra.public.vectorstores.qdrant_benchmark_stores import (
        QdrantBenchmarkStores,
    )

    stores = QdrantBenchmarkStores(store)
    app = FastAPI()
    app.state.benchmark = stores
    app.include_router(
        create_retrieval_benchmark_router(
            store=store,
            stores=stores,
            embedding=embedding,
            model_name="test-model",
            dimension=3,
            authenticate=benchmark_bearer_auth("unit-test"),
            **options,
        )
    )
    try:
        with TestClient(app, headers={"Authorization": "Bearer unit-test"}) as client:
            assert (
                client.post(
                    "/internal/bench/dataset",
                    json={
                        "include_server": False,
                        "filter_fields": {"category": "keyword"},
                    },
                ).status_code
                == 200
            )
            yield client, store, backend, embedding
    finally:
        store.close()


def prepare(client, **options):
    response = client.post(
        "/internal/bench/samples", json={"queries": ["查账"], **options}
    )
    assert response.status_code == 200, response.text
    return response.json()["sample_set_id"]


def search(client, sample_set_id, **options):
    return client.post(
        "/internal/bench/search",
        json={
            "sample_set_id": sample_set_id,
            "sample_index": 0,
            "mode": "both",
            "top_k": 1,
            **options,
        },
    )


def test_real_queries_are_embedded_once_and_search_never_touches_database(
    tmp_path, monkeypatch
):
    with bench(tmp_path) as (client, store, backend, embedding):
        sample_set = prepare(client)

        def forbidden(*args, **kwargs):
            raise AssertionError("benchmark search accessed durable storage")

        monkeypatch.setattr(backend, "load_all", forbidden)
        monkeypatch.setattr(backend, "save_batch", forbidden)
        for mode in ("vector", "keyword", "both"):
            response = search(client, sample_set, mode=mode)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["instance_id"]
            assert data["sample_source"] == "configured_embedding"
            for name in ("vector", "keyword"):
                hits = data[f"{name}_hits"]
                assert len(hits) == (1 if mode in (name, "both") else 0)
                if hits:
                    assert hits[0]["id"] == "account:default:description"
                    assert set(hits[0]) == {"id", "score"}
            assert data["timing_ms"]["store"] >= 0
        assert embedding.calls == ["查账"]
        assert store.size() == 2


def test_filters_apply_to_both_searches(tmp_path):
    with bench(tmp_path) as (client, *_):
        response = search(client, prepare(client), filters={"category": "travel"})
        assert response.status_code == 200
        assert response.json()["vector_hits"][0]["id"] == "travel:default:description"
        assert response.json()["keyword_hits"] == []


def test_all_routes_require_auth_and_info_contains_no_query_vectors(tmp_path):
    with bench(tmp_path) as (client, *_):
        for method, path, body in [
            ("GET", "/internal/bench/info", None),
            ("GET", "/internal/bench/server-info", None),
            ("POST", "/internal/bench/dataset", {"include_server": False}),
            ("POST", "/internal/bench/samples", {"queries": ["查账"]}),
            (
                "POST",
                "/internal/bench/search",
                {"sample_set_id": "missing", "sample_index": 0},
            ),
        ]:
            response = client.request(
                method, path, json=body, headers={"Authorization": "Bearer invalid"}
            )
            assert response.status_code == 401
        prepare(client)
        info = client.get("/internal/bench/info").json()
        assert info["vector_count"] == 2
        assert info["sample_count"] == 1
        assert info["dimension"] == 3
        assert "查账" not in str(info)
        assert "unit-test" not in str(info)
        assert info["logging"]


def test_wrong_instance_or_replaced_sample_set_is_rejected(tmp_path):
    with bench(tmp_path) as (client, *_):
        old = prepare(client)
        prepare(client)
        assert search(client, old).status_code == 409
        assert search(client, "another-process").status_code == 409


def test_keyword_only_preparation_does_not_require_model(tmp_path):
    with bench(tmp_path) as (client, _, _, embedding):
        samples = prepare(client, with_vectors=False)
        assert search(client, samples, mode="keyword").status_code == 200
        assert search(client, samples, mode="vector").status_code == 409
        assert embedding.calls == []


@pytest.mark.parametrize("bad_vector", [[1.0], [math.nan, 0.0, 0.0], [0.0, 0.0, 0.0]])
def test_invalid_model_output_does_not_replace_good_samples(tmp_path, bad_vector):
    with bench(tmp_path) as (client, _, _, embedding):
        old = prepare(client)
        embedding.vector = bad_vector
        assert (
            client.post(
                "/internal/bench/samples", json={"queries": ["订票"]}
            ).status_code
            == 503
        )
        assert search(client, old).status_code == 200


def test_query_bounds_and_unknown_samples_are_rejected(tmp_path):
    with bench(tmp_path) as (client, *_):
        samples = prepare(client)
        for body in ({"queries": []}, {"queries": [" "]}, {"queries": ["x"] * 65}):
            assert client.post("/internal/bench/samples", json=body).status_code == 422
        assert search(client, samples, top_k=1001).status_code == 422
        assert search(client, samples, sample_index=2).status_code == 404


def test_storage_error_is_not_reported_as_successful_empty_result(
    tmp_path, monkeypatch
):
    with bench(tmp_path) as (client, _store, *_):
        samples = prepare(client)

        def unavailable(*args, **kwargs):
            raise RuntimeError("internal details must not be returned")

        monkeypatch.setattr(client.app.state.benchmark, "query", unavailable)
        response = search(client, samples, mode="keyword")
        assert response.status_code == 503
        assert "internal details" not in response.text


def test_inflight_limit_rejects_overload_without_blocking_event_loop(
    tmp_path, monkeypatch
):
    with bench(tmp_path, max_inflight=1) as (client, _store, *_):
        samples = prepare(client)
        entered, release = Event(), Event()
        original = client.app.state.benchmark.query

        def slow_search(*args, **kwargs):
            entered.set()
            assert release.wait(5), "test search was not released"
            return original(*args, **kwargs)

        monkeypatch.setattr(client.app.state.benchmark, "query", slow_search)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(search, client, samples, mode="keyword")
            try:
                assert entered.wait(2)
                overloaded = pool.submit(search, client, samples, mode="keyword")
                assert overloaded.result(timeout=2).status_code == 429
            finally:
                release.set()
            assert pending.result(timeout=2).status_code == 200
        assert search(client, samples, mode="keyword").status_code == 200
