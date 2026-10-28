"""The reranker circuit breaker protects workers and leaves fallback intact."""

from __future__ import annotations

import threading

import pytest
from prometheus_client import REGISTRY


class RerankBackend:
    model = "test-reranker"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def rerank(self, query, candidates, top_k=5):
        self.calls.append((query, candidates, top_k))
        if self.fail:
            self.entered.set()
            self.release.wait(timeout=2)
            raise RuntimeError("reranker unavailable")
        return [{"id": "agent-1", "score": 1.0}]


def breaker(backend, **kwargs):
    from app.infra.public.reranker.circuit_breaker_reranker import (
        CircuitBreakerReranker,
    )

    return CircuitBreakerReranker(backend, **kwargs)


def metric(name):
    value = REGISTRY.get_sample_value(name, {"model": "test-reranker"})
    return 0.0 if value is None else float(value)


def test_consecutive_failures_open_and_short_circuit_backend_calls():
    from app.infra.public.reranker.circuit_breaker_reranker import (
        RerankCircuitOpenError,
    )

    backend = RerankBackend(fail=True)
    provider = breaker(backend, failure_threshold=2, recovery_timeout_seconds=10)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="reranker unavailable"):
            provider.rerank("支付", [{"id": "a", "text": "x"}], 1)

    with pytest.raises(RerankCircuitOpenError):
        provider.rerank("支付", [{"id": "a", "text": "x"}], 1)
    assert len(backend.calls) == 2
    assert provider.state == "open"
    assert metric("bcsfuse_rerank_circuit_rejected_total") >= 1
    assert metric("bcsfuse_rerank_circuit_open") == 1


def test_empty_candidate_batch_bypasses_backend_and_circuit():
    backend = RerankBackend(fail=True)
    provider = breaker(backend, failure_threshold=1, recovery_timeout_seconds=10)

    assert provider.rerank("支付", [], 1) == []
    assert backend.calls == []
    assert provider.state == "closed"


def test_recovery_probe_success_closes_circuit():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    backend = RerankBackend(fail=True)
    provider = breaker(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=1,
        clock=Clock(),
    )

    with pytest.raises(RuntimeError):
        provider.rerank("支付", [{"id": "a", "text": "x"}], 1)
    backend.fail = False
    provider.clock.now = 1

    assert provider.rerank("recovered", [{"id": "a", "text": "x"}], 1)
    assert provider.rerank("healthy", [{"id": "a", "text": "x"}], 1)
    assert provider.state == "closed"
    assert metric("bcsfuse_rerank_circuit_open") == 0


def test_failed_recovery_probe_resets_timer_and_remains_open():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    from app.infra.public.reranker.circuit_breaker_reranker import (
        RerankCircuitOpenError,
    )

    backend = RerankBackend(fail=True)
    provider = breaker(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=1,
        clock=Clock(),
    )

    with pytest.raises(RuntimeError):
        provider.rerank("first", [{"id": "a", "text": "x"}], 1)
    provider.clock.now = 1
    with pytest.raises(RuntimeError):
        provider.rerank("failed probe", [{"id": "a", "text": "x"}], 1)
    backend.fail = False
    provider.clock.now = 1.5
    with pytest.raises(RerankCircuitOpenError):
        provider.rerank("still open", [{"id": "a", "text": "x"}], 1)
    provider.clock.now = 2.1
    assert provider.rerank("recovered", [{"id": "a", "text": "x"}], 1)


def test_half_open_probe_is_single_flight():
    class Gate:
        model = "test-reranker"

        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()

        def rerank(self, *args):
            self.entered.set()
            self.release.wait(timeout=2)
            return [{"id": "agent-1", "score": 1.0}]

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    gate = Gate()
    provider = breaker(
        gate,
        failure_threshold=1,
        recovery_timeout_seconds=1,
        clock=Clock(),
    )
    provider.state = "open"
    provider._state_changed_at = 0
    provider.clock.now = 1

    from app.infra.public.reranker.circuit_breaker_reranker import (
        RerankCircuitOpenError,
    )

    probe = threading.Thread(target=lambda: provider.rerank("probe", [{}], 1))
    probe.start()
    assert gate.entered.wait(timeout=2)
    with pytest.raises(RerankCircuitOpenError):
        provider.rerank("duplicate", [{}], 1)
    gate.release.set()
    probe.join(timeout=2)
    assert not probe.is_alive()


def test_reranker_fail_action_degrades_on_circuit_open_without_hiding_error():
    from app.domain.services.fragment_reranker_service import (
        FragmentRerankerService,
        RerankFailAction,
        RerankRequest,
    )
    from app.infra.public.reranker.circuit_breaker_reranker import (
        RerankCircuitOpenError,
    )

    backend = RerankBackend(fail=True)
    provider = breaker(backend, failure_threshold=1, recovery_timeout_seconds=10)
    with pytest.raises(RuntimeError):
        provider.rerank("失败打开", [{"id": "a", "text": "x"}], 1)

    service = FragmentRerankerService(
        reranker=provider,
        fail_action=RerankFailAction.DEGRADE,
    )
    request = RerankRequest(
        query="策略",
        candidates=[
            type("Candidate", (), {
                "profile_key": "agent:default",
                "aggregated_score": 0.7,
                "fragments": [],
                "metadata": {},
            })()
        ],
        top_k=1,
    )

    result = service.rerank(request)

    assert result[0].rerank_metadata["degraded"] is True
    assert result[0].rerank_metadata["error_type"] == RerankCircuitOpenError.__name__


def test_default_factory_reuses_one_process_local_breaker_per_model():
    from app.infra.public.reranker.circuit_breaker_reranker import (
        CircuitBreakerReranker,
        get_circuit_breaker_reranker,
    )
    from app.infra.public.reranker.http_reranker import HttpReranker

    try:
        first = get_circuit_breaker_reranker("circuit-factory-model")
        second = get_circuit_breaker_reranker("circuit-factory-model")

        assert isinstance(first, CircuitBreakerReranker)
        assert first is second
        assert isinstance(first.backend, HttpReranker)
        assert first.model == "circuit-factory-model"
    finally:
        get_circuit_breaker_reranker.cache_clear()


def test_domain_reranker_factory_uses_circuit_breaker_by_default():
    """The runtime model-override path must not bypass the process breaker."""
    from app.domain.services.fragment_reranker_service import FragmentRerankerService
    from app.infra.public.reranker.circuit_breaker_reranker import (
        CircuitBreakerReranker,
        get_circuit_breaker_reranker,
    )
    from app.infra.public.reranker.http_reranker import HttpReranker

    model = "circuit-domain-factory-model"
    try:
        reranker = FragmentRerankerService(reranker_model=model)._reranker

        assert isinstance(reranker, CircuitBreakerReranker)
        assert isinstance(reranker.backend, HttpReranker)
        assert reranker.model == model
    finally:
        get_circuit_breaker_reranker.cache_clear()
