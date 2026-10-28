"""TC composition must configure finite embedding retry defaults."""

from __future__ import annotations

import pytest

from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider


def provider():
    return RealEmbeddingProvider(
        settings=EmbeddingSettings(
            base_url="http://127.0.0.1:1/v1",
            auth_token="test-only",
            model="tc-retry-embedding",
            dimension=64,
        ),
    )


@pytest.fixture(autouse=True)
def clear_embedding_retry_environment(monkeypatch):
    for name in [
        "EMBEDDING_MAX_RETRIES",
        "EMBEDDING_RETRY_BACKOFF_SECONDS",
        "EMBEDDING_RETRY_BUDGET_MS",
        "EMBEDDING_REQUEST_INTERVAL_MS",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_tc_embedding_provider_has_a_short_default_retry_budget():
    instance = provider()
    try:
        assert instance._max_retries == 1
        assert instance._base_delay_ms == 250
        assert instance._retry_budget_ms == 2000
    finally:
        instance.close()


def test_tc_embedding_retry_environment_overrides_composition(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MAX_RETRIES", "3")
    monkeypatch.setenv("EMBEDDING_RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("EMBEDDING_RETRY_BUDGET_MS", "1500")

    instance = provider()
    try:
        assert instance._max_retries == 3
        assert instance._base_delay_ms == 500
        assert instance._retry_budget_ms == 1500
    finally:
        instance.close()
