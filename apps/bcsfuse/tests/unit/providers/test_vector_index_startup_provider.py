import pytest
from app.providers.public.vector_index_startup_provider import (
    VectorIndexStartupProvider,
)


class StartupSpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def initialize(self) -> None:
        self.events.append("delegate.initialize")

    async def shutdown(self) -> None:
        self.events.append("delegate.shutdown")


class VectorStoreSpy:
    def __init__(self, events: list[str], *, fail_rebuild: bool = False) -> None:
        self.events = events
        self.fail_rebuild = fail_rebuild

    def rebuild_from_backend(self) -> None:
        self.events.append("vector.rebuild")
        if self.fail_rebuild:
            raise RuntimeError("rebuild failed")

    def close(self) -> None:
        self.events.append("vector.close")


@pytest.mark.asyncio
async def test_rebuild_runs_after_delegate_initialization() -> None:
    events: list[str] = []
    provider = VectorIndexStartupProvider(
        StartupSpy(events),
        VectorStoreSpy(events),
    )

    await provider.initialize()
    await provider.shutdown()

    assert events == [
        "delegate.initialize",
        "vector.rebuild",
        "vector.close",
        "delegate.shutdown",
    ]


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_delegate_and_blocks_startup() -> None:
    events: list[str] = []
    provider = VectorIndexStartupProvider(
        StartupSpy(events),
        VectorStoreSpy(events, fail_rebuild=True),
    )

    with pytest.raises(RuntimeError, match="rebuild failed"):
        await provider.initialize()

    assert events == [
        "delegate.initialize",
        "vector.rebuild",
        "delegate.shutdown",
    ]


class LeaseAwareVectorStoreSpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @property
    def lease_heartbeat_seconds(self) -> float:
        return 0.002

    def renew_owner_lease(self) -> bool:
        self.events.append("vector.renew")
        return True

    def rebuild_from_backend(self) -> None:
        self.events.append("vector.rebuild")
        import time

        time.sleep(0.02)

    def close(self) -> None:
        self.events.append("vector.close")


@pytest.mark.asyncio
async def test_owner_lease_heartbeat_runs_during_rebuild_and_stops_on_shutdown() -> None:
    import time

    events: list[str] = []
    vector_store = LeaseAwareVectorStoreSpy(events)
    provider = VectorIndexStartupProvider(StartupSpy(events), vector_store)

    await provider.initialize()

    assert events[0] == "delegate.initialize"
    assert "vector.rebuild" in events
    assert events.count("vector.renew") > 0

    await provider.shutdown()

    renew_count_after_shutdown = events.count("vector.renew")
    time.sleep(0.02)
    assert events.count("vector.renew") == renew_count_after_shutdown
    assert events[-2:] == ["vector.close", "delegate.shutdown"]
