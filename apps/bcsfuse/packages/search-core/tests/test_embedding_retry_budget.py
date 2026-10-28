"""Bounded retry semantics for the real embedding provider."""

from __future__ import annotations

import httpx
import pytest

from search_core.config.embedding import EmbeddingSettings
from search_core.providers.embedding import EmbeddingAPIError, RealEmbeddingProvider, time


def settings() -> EmbeddingSettings:
    return EmbeddingSettings(
        base_url="http://127.0.0.1:1/v1",
        auth_token="test-only",
        model="controlled-retry-embedding",
        dimension=64,
    )


def test_transient_server_error_retries_once_within_budget(monkeypatch):
    provider = RealEmbeddingProvider(
        settings=settings(),
        max_retries=1,
        base_delay_ms=1,
        max_delay_ms=2,
        retry_budget_ms=1000,
    )
    attempts = []
    delays = []

    def call(texts):
        attempts.append(texts)
        if len(attempts) == 1:
            raise EmbeddingAPIError("service unavailable", status_code=503)
        return [[0.25] * 64 for _ in texts]

    monkeypatch.setattr(provider, "_call_embedding_api", call)
    monkeypatch.setattr(time, "sleep", delays.append)

    result = provider.embed("支付")

    assert result == [0.25] * 64
    assert len(attempts) == 2
    assert len(delays) == 1
    assert 0.001 <= delays[0] <= 0.002


def test_large_retry_after_is_not_retried_when_it_exceeds_budget(monkeypatch):
    provider = RealEmbeddingProvider(
        settings=settings(),
        max_retries=4,
        base_delay_ms=1,
        max_delay_ms=60000,
        retry_budget_ms=10,
    )
    attempts = []

    def call(texts):
        attempts.append(texts)
        raise EmbeddingAPIError(
            "rate limited",
            status_code=429,
            response=httpx.Response(429, headers={"Retry-After": "30"}),
        )

    monkeypatch.setattr(provider, "_call_embedding_api", call)

    def reject_sleep(_):
        raise AssertionError("provider slept despite an exhausted retry budget")

    failures = []
    monkeypatch.setattr(time, "sleep", reject_sleep)
    monkeypatch.setattr(provider, "_record_failure", failures.append)

    with pytest.raises(EmbeddingAPIError):
        provider.embed("支付")

    assert len(attempts) == 1
    assert len(failures) == 1


def test_retry_budget_must_be_positive_when_configured():
    with pytest.raises(ValueError, match="positive"):
        RealEmbeddingProvider(settings=settings(), retry_budget_ms=0)


def test_non_retryable_client_error_records_one_final_failure(monkeypatch):
    provider = RealEmbeddingProvider(
        settings=settings(),
        max_retries=4,
        base_delay_ms=1,
        max_delay_ms=2,
        retry_budget_ms=1000,
    )
    failures = []

    def call(texts):
        raise EmbeddingAPIError("invalid credentials", status_code=401)

    monkeypatch.setattr(provider, "_call_embedding_api", call)
    monkeypatch.setattr(provider, "_record_failure", failures.append)

    def reject_sleep(_):
        raise AssertionError("401 must not be retried")

    monkeypatch.setattr(time, "sleep", reject_sleep)

    with pytest.raises(EmbeddingAPIError):
        provider.embed("支付")

    assert len(failures) == 1
