"""The composed TC embedding adapter retains the shared binary wire upgrade."""

from __future__ import annotations

import httpx
import struct
from prometheus_client import REGISTRY

from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider


def test_tc_embedding_adapter_uses_negotiated_binary_response():
    vector = [0.25, -0.5] + [0.125] * 62
    body_prefix = struct.pack("<4sBBII", b"BCFE", 1, 1, 64, 1)
    binary_body = body_prefix + b"".join(
        struct.pack("<f", value) for value in vector
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/x-bcsfuse-embedding-v1"},
            content=binary_body,
        )

    settings = EmbeddingSettings(
        base_url="http://models.example.test/v1",
        auth_token="test-only",
        model="binary-composition",
        dimension=64,
    )
    instance = RealEmbeddingProvider(
        settings=settings,
        max_retries=0,
        retry_budget_ms=1000,
    )
    instance._client = httpx.Client(
        transport=httpx.MockTransport(respond),
        timeout=httpx.Timeout(5.0),
    )
    try:
        assert instance.embed("composition query") == vector
        before = REGISTRY.get_sample_value(
            "bcsfuse_embedding_wire_responses_total",
            {"model": "binary-composition", "protocol": "binary"},
        )
        assert instance.embed("composition query") == vector
        after = REGISTRY.get_sample_value(
            "bcsfuse_embedding_wire_responses_total",
            {"model": "binary-composition", "protocol": "binary"},
        )
    finally:
        instance.close()

    assert after is not None
    assert after - (before or 0.0) == 1.0
