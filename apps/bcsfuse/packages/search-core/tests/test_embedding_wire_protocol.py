"""TDD for the high-density embedding response protocol.

The provider still speaks the OpenAI-compatible JSON wire by default. A model
server may negotiate a little-endian float32 response, which avoids expanding
each 4096-dimensional vector through a large JSON parser path.
"""

from __future__ import annotations

import json as jsonlib
import math
import struct

import httpx
import pytest

from search_core.config.embedding import EmbeddingSettings
from search_core.providers.embedding import (
    EmbeddingAPIError,
    RealEmbeddingProvider,
)


def encode_binary(vectors: list[list[float]], dimension: int) -> bytes:
    from search_core.providers.embedding_wire import (
        BINARY_CONTENT_TYPE,
        encode_embedding_vectors,
    )

    assert BINARY_CONTENT_TYPE == "application/x-bcsfuse-embedding-v1"
    return encode_embedding_vectors(vectors, dimension=dimension)


def provider(transport: httpx.MockTransport) -> RealEmbeddingProvider:
    settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-token",
        model="example-embedding",
        dimension=64,
    )
    instance = RealEmbeddingProvider(
        settings=settings,
        request_interval_ms=0,
        max_retries=0,
        retry_budget_ms=1000,
    )
    instance._client = httpx.Client(transport=transport)
    return instance


def binary_response(payload: bytes) -> httpx.Response:
    from search_core.providers.embedding_wire import BINARY_CONTENT_TYPE

    return httpx.Response(
        200,
        headers={"Content-Type": f"{BINARY_CONTENT_TYPE}; charset=binary"},
        content=payload,
    )


def test_embedding_request_negotiates_binary_and_stays_json_compatible():
    requests: list[httpx.Request] = []

    vector = [0.25] * 64
    json_payload = jsonlib.dumps({"data": [{"embedding": vector}]}).encode()

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json_payload,
        )

    instance = provider(httpx.MockTransport(respond))
    try:
        assert instance.embed("支付查询") == vector
    finally:
        instance.close()

    accept = requests[0].headers["Accept"]
    assert accept.startswith("application/x-bcsfuse-embedding-v1")
    assert "application/json" in accept
    assert requests[0].headers["Content-Type"] == "application/json"


def test_embedding_provider_parses_negotiated_binary_response():
    vectors = [
        [1.0, -2.5, 3.25] + [0.0] * 61,
        [-1.0, 2.5, -3.25] + [1.0] * 61,
    ]
    payload = encode_binary(vectors, dimension=64)

    def respond(request: httpx.Request) -> httpx.Response:
        return binary_response(payload)

    instance = provider(httpx.MockTransport(respond))
    try:
        assert instance.embed_batch(["第一个", "第二个"]) == vectors
    finally:
        instance.close()


def test_binary_wire_payload_is_fixed_and_dense_for_4096_dimensions():
    payload = encode_binary([[0.5] * 4096], dimension=4096)
    assert struct.unpack_from("<4sBBII", payload) == (
        b"BCFE",
        1,
        1,
        4096,
        1,
    )
    assert len(payload) == struct.calcsize("<4sBBII") + 4096 * 4
    assert len(payload) == 16_398
    assert len(payload) < len(jsonlib.dumps({"embedding": [0.5] * 4096}).encode())


def test_invalid_binary_header_fails_closed_without_relying_on_json_fallback():
    payload = b"NOTBCFE" + b"\x00" * 128

    def respond(request: httpx.Request) -> httpx.Response:
        return binary_response(payload)

    instance = provider(httpx.MockTransport(respond))
    try:
        with pytest.raises(EmbeddingAPIError) as raised:
            instance.embed("invalid wire")
    finally:
        instance.close()
    assert raised.value.status_code == 415
    assert "Embedding binary response" in str(raised.value)


def test_binary_response_dimension_and_count_must_match_request_contract():
    payload = encode_binary([[1.0] * 32], dimension=32)

    def respond(request: httpx.Request) -> httpx.Response:
        return binary_response(payload)

    instance = provider(httpx.MockTransport(respond))
    try:
        with pytest.raises(EmbeddingAPIError) as raised:
            instance.embed("wrong dimension")
    finally:
        instance.close()
    assert raised.value.status_code == 415
    assert "dimension" in str(raised.value)


def test_binary_response_rejects_non_finite_and_declared_but_missing_bytes():
    invalid_finite = struct.pack("<4sBBII", b"BCFE", 1, 1, 64, 1) + b"".join(
        struct.pack("<f", value) for value in [math.nan] + [0.0] * 63
    )
    short_payload = encode_binary([[1.0] * 64, [2.0] * 64], dimension=64)[:-4]

    def respond_nan(request: httpx.Request) -> httpx.Response:
        return binary_response(invalid_finite)

    instance = provider(httpx.MockTransport(respond_nan))
    try:
        with pytest.raises(EmbeddingAPIError) as raised:
            instance.embed("non-finite")
    finally:
        instance.close()
    assert "finite" in str(raised.value)
    assert raised.value.status_code == 415

    def respond_short(request: httpx.Request) -> httpx.Response:
        return binary_response(short_payload)

    instance = provider(httpx.MockTransport(respond_short))
    try:
        with pytest.raises(EmbeddingAPIError) as raised:
            instance.embed_batch(["first", "missing second"])
    finally:
        instance.close()
    assert raised.value.status_code == 415
    assert "payload length" in str(raised.value)
