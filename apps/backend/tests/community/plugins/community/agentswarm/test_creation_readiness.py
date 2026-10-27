"""Creation phase/status and subagent lifecycle regressions through the real host.

Only platform persistence and I/O ports are hand fakes. In particular, status
mapping and the subagent's ready poll/teardown are the production methods, not
scripted BotPlatform answers. These are in-process tests, not simulator proof.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import agentclaw.community.core.bot_management.create_flow as create_flow
from agentclaw.community.core.agentswarm.domain.models import AgentId, BotState
from agentclaw.community.core.apply_engine.delivery import CreationSequence
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER, CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.plugins.community.agentswarm.bot_subagent import AvernetSubagent
from tests.community.plugins.community.agentswarm.test_agentswarm_platform import (
    _Apply, _Repo, _Seam, _job, _mk,
)
from tests.community.plugins.community.agentswarm.test_bot_subagent import _mk_run, _mk_spec


@pytest.mark.parametrize("sequence", list(CreationSequence))
@pytest.mark.parametrize("trigger", [CREATE_PRE_CONTAINER_TRIGGER, CREATE_ON_CONTAINER_TRIGGER])
@pytest.mark.parametrize("status", list(ApplyStatus))
@pytest.mark.parametrize("bot_status", ["ACTIVE", "PENDING"])
@pytest.mark.parametrize("job_status", [
    TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMED_OUT,
])
def test_creation_phase_status_matrix(sequence, trigger, status, bot_status, job_status):
    seam = _Seam()
    seam.jobs["persona"] = _job(job_status)
    repo = _Repo(bot={"status": bot_status, "engine_type": "openclaw"})
    report = SimpleNamespace(trigger=trigger, status=status)
    apply = _Apply(report, sequence=sequence)
    terminal = (CREATE_PRE_CONTAINER_TRIGGER
                if sequence is CreationSequence.RECORD_APPLY_PROVISION
                else CREATE_ON_CONTAINER_TRIGGER)
    if status is ApplyStatus.RUNNING:
        expected = BotState.PROVISIONING
    elif status is ApplyStatus.SUCCEEDED:
        if bot_status == "ACTIVE" and trigger == terminal:
            expected = BotState.READY
        elif job_status in (TaskStatus.FAILED, TaskStatus.TIMED_OUT):
            expected = BotState.APPLY_FAILED if bot_status == "ACTIVE" else BotState.CREATE_FAILED
        else:
            expected = BotState.PROVISIONING
    else:
        expected = BotState.APPLY_FAILED
    assert _mk(seam=seam, repo=repo, apply_=apply).bot_status(
        "persona", owner_id="owner") == expected.value


@pytest.mark.parametrize("bot_status", ["FAILED", "DELETED", "INACTIVE"])
@pytest.mark.parametrize("apply_status", list(ApplyStatus))
def test_dead_bot_outranks_creation_report(bot_status, apply_status):
    seam = _Seam()
    seam.jobs["persona"] = _job(TaskStatus.SUCCEEDED)
    apply = _Apply(SimpleNamespace(trigger=CREATE_ON_CONTAINER_TRIGGER, status=apply_status))
    assert _mk(seam=seam, repo=_Repo({"status": bot_status}), apply_=apply).bot_status(
        "persona", owner_id="owner") == BotState.CREATE_FAILED.value


@pytest.mark.parametrize("trigger", ["explicit", "put", None])
@pytest.mark.parametrize("status", list(ApplyStatus))
@pytest.mark.parametrize("job_status,expected", [
    (TaskStatus.RUNNING, BotState.PROVISIONING),
    (TaskStatus.SUCCEEDED, BotState.READY),
    (TaskStatus.FAILED, BotState.APPLY_FAILED),
])
def test_later_apply_is_not_a_creation_verdict(trigger, status, job_status, expected):
    seam = _Seam()
    seam.jobs["persona"] = _job(job_status)
    report = None if trigger is None else SimpleNamespace(trigger=trigger, status=status)
    assert _mk(seam=seam, repo=_Repo({"status": "ACTIVE"}), apply_=_Apply(report)).bot_status(
        "persona", owner_id="owner") == expected.value


@pytest.mark.parametrize("status", [ApplyStatus.FAILED, ApplyStatus.PARTIAL])
@pytest.mark.parametrize("sequence", list(CreationSequence))
def test_mid_boot_live_device_keeps_existing_recovery_contract(status, sequence):
    seam = _Seam()
    seam.jobs["persona"] = _job(TaskStatus.RUNNING)
    trigger = (CREATE_PRE_CONTAINER_TRIGGER
               if sequence is CreationSequence.RECORD_APPLY_PROVISION
               else CREATE_ON_CONTAINER_TRIGGER)
    apply = _Apply(SimpleNamespace(trigger=trigger, status=status), sequence=sequence)
    assert _mk(seam=seam, repo=_Repo({"status": "PENDING", "device_id": "device"}),
               apply_=apply).bot_status("persona", owner_id="owner") == BotState.PROVISIONING.value


class _World:
    """Owner-scoped durable-port fake with controllable asynchronous apply."""

    def __init__(self):
        self.bots = {}
        self.jobs = {}
        self.reports = {}
        self.polls = {}
        self.deleted = []
        self.reads = []
        self.platform = _mk(seam=self, repo=self, apply_=self, bot_svc=self)

    def submit(self, **kwargs):
        key = (kwargs["user_id"], kwargs["bot_id"])
        self.bots[key] = {"status": "ACTIVE", "engine_type": "openclaw"}
        # The real creation job completes after start_apply, not after its I/O.
        self.jobs[key] = _job(TaskStatus.SUCCEEDED)
        self.reports[key] = SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.RUNNING)
        self.polls[key] = 0
        return SimpleNamespace(bot_id=key[1])

    def get_by_id_and_entity(self, bot_id, entity_id):
        self.reads.append(("bot", entity_id, bot_id))
        return self.bots.get((entity_id, bot_id))

    def find_job(self, *, entity_id, bot_id):
        self.reads.append(("job", entity_id, bot_id))
        return self.jobs.get((entity_id, bot_id))

    def last_apply(self, *, entity_id, bot_id):
        key = (entity_id, bot_id)
        self.reads.append(("apply", *key))
        self.polls[key] += 1
        return self.reports.get(key)

    def delivery_for_bot(self, bot):
        return SimpleNamespace(creation_sequence=CreationSequence.CREATE_BETWEEN_PHASES)

    def delete_bot(self, bot_id, user_id, nick_name=None):
        self.deleted.append((user_id, bot_id))
        return True


def _subagent(world, agent_id="agent-1"):
    run = _mk_run()
    run.owner_id = "owner"
    return AvernetSubagent(_mk_spec(name=agent_id), run, world.platform, AgentId(agent_id))


async def _wait_for_polls(world, count, expected_personas=1):
    # Keep the production polling cadence. A bounded wait also fails if setup
    # exits on RUNNING and no longer polls, which is the original defect.
    async with asyncio.timeout(5):
        while len(world.polls) < expected_personas or min(world.polls.values()) < count:
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("terminal", [ApplyStatus.SUCCEEDED, ApplyStatus.FAILED, ApplyStatus.PARTIAL])
def test_running_apply_is_not_torn_down_before_its_terminal_result(monkeypatch, terminal):
    async def main():
        world = _World()
        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest", world.submit)
        sub = _subagent(world)

        async def lifecycle():
            try:
                await sub.setup()
            finally:
                await sub.teardown()

        task = asyncio.create_task(lifecycle())
        try:
            await _wait_for_polls(world, 2)
            assert not task.done()
            assert world.deleted == []
            assert sub.executor_id == ""
            key, = world.reports
            world.reports[key].status = terminal
            if terminal is ApplyStatus.SUCCEEDED:
                await asyncio.wait_for(task, timeout=3)
                assert sub.executor_id == key[1]
            else:
                with pytest.raises(RuntimeError, match="APPLY_FAILED"):
                    await asyncio.wait_for(task, timeout=3)
            assert world.deleted == [key]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(main())


@pytest.mark.parametrize("reverse_submit", [False, True])
def test_concurrent_personas_are_owner_scoped_and_not_cross_deleted(monkeypatch, reverse_submit):
    async def main():
        world = _World()
        second_submitted = threading.Event()

        def submit(**kwargs):
            if reverse_submit and kwargs["spec"].bot_name.startswith("first-"):
                assert second_submitted.wait(timeout=3)
            result = world.submit(**kwargs)
            if kwargs["spec"].bot_name.startswith("second-"):
                second_submitted.set()
            return result

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest", submit)
        first, second = _subagent(world, "first"), _subagent(world, "second")
        tasks = [asyncio.create_task(sub.setup()) for sub in (first, second)]
        try:
            await _wait_for_polls(world, 2, expected_personas=2)
            assert len(world.bots) == 2
            assert world.deleted == []
            keys = [("owner", first._bot_id), ("owner", second._bot_id)]
            if reverse_submit:
                assert list(world.reports) == list(reversed(keys))
            assert world.platform.bot_status(keys[0][1], owner_id="foreign") == "PROVISIONING"
            assert not any(owner == "foreign" for kind, owner, _ in world.reads if kind == "apply")
            world.reports[keys[0]].status = ApplyStatus.FAILED
            with pytest.raises(RuntimeError, match="APPLY_FAILED"):
                await asyncio.wait_for(tasks[0], timeout=3)
            await first.teardown()
            assert world.deleted == [keys[0]]
            assert not tasks[1].done()
            world.reports[keys[1]].status = ApplyStatus.SUCCEEDED
            await asyncio.wait_for(tasks[1], timeout=3)
            assert world.deleted == [keys[0]]
            await second.teardown()
            assert world.deleted == keys
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(main())


@pytest.mark.parametrize("exit_mode", ["cancel", "deadline"])
def test_pending_creation_keeps_bounded_cancel_and_deadline_cleanup(monkeypatch, exit_mode):
    async def main():
        world = _World()
        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest", world.submit)
        sub = _subagent(world)
        if exit_mode == "deadline":
            poll_ready = sub._poll_ready

            async def bounded_poll(bot_id):
                return await poll_ready(bot_id, timeout=0.05, interval=0.01)

            monkeypatch.setattr(sub, "_poll_ready", bounded_poll)

        async def lifecycle():
            try:
                await sub.setup()
            finally:
                await sub.teardown()

        task = asyncio.create_task(lifecycle())
        try:
            if exit_mode == "cancel":
                await _wait_for_polls(world, 2)
                assert world.deleted == []
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(RuntimeError, match="last state='PROVISIONING'"):
                    await asyncio.wait_for(task, timeout=3)
            assert world.deleted == list(world.bots)
            assert sub.executor_id == ""
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(main())


@pytest.mark.parametrize("read_fails", [False, True])
def test_identity_apply_is_released_before_readiness_and_502_remains_a_failure(
        monkeypatch, read_fails):
    from datetime import datetime
    from hashlib import sha256

    import httpx

    from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
    from agentclaw.community.core.apply_engine.materialisers.identity import IdentityMaterialiser
    from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
    from agentclaw.community.core.apply_engine.order import ApplyPhase
    from agentclaw.community.core.apply_engine.capabilities import Category
    from agentclaw.community.core.apply_engine.desired_state import (
        ContentRef, Declaration, DesiredIdentityFile, DesiredState,
    )
    from tests.community.core.apply_engine._fakes import (
        FakeIdentityService, FakeManifestContent, make_context,
    )

    async def main():
        release = asyncio.Event()

        class Identity(FakeIdentityService):
            async def read_identity_file(self, *args, **kwargs):
                await release.wait()
                if read_fails:
                    self.reads.append(args[3])
                    raise httpx.HTTPStatusError(
                        "file/read returned 502", request=httpx.Request("GET", "http://runtime/identity"),
                        response=httpx.Response(502))
                return await super().read_identity_file(*args, **kwargs)

        world = _World()
        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest", world.submit)
        sub = _subagent(world)
        setup = asyncio.create_task(sub.setup())
        apply_task = None
        try:
            await _wait_for_polls(world, 1)
            key, = world.reports
            identity = Identity({"SOUL.md": "old identity"})
            # A compiled identity's bytes are already filed. All phase selection,
            # identity planning/writing and aggregate reporting are product code.
            content = FakeManifestContent()
            body = b"complete persona identity"
            ref = ContentRef("sha256:" + sha256(body).hexdigest())
            content.blobs[ref.digest] = body
            materialiser = IdentityMaterialiser(identity, content)
            orchestrator = ApplyOrchestrator(
                {Category.IDENTITY: materialiser}, steps=ArcaDelivery(lambda: None).steps_for)

            async def apply_identity():
                world.reports[key] = await orchestrator.apply(
                    make_context(bot_id=key[1], owner_id=key[0], engine_type="openclaw"),
                    DesiredState(identity=Declaration((DesiredIdentityFile("SOUL.md", ref),))),
                    apply_id="creation-apply", trigger=CREATE_ON_CONTAINER_TRIGGER,
                    phase=ApplyPhase.ON_CONTAINER, started_at=datetime.now())

            apply_task = asyncio.create_task(apply_identity())
            await _wait_for_polls(world, 2)
            assert not setup.done()
            assert world.deleted == []
            assert identity.writes == []
            release.set()
            await asyncio.wait_for(apply_task, timeout=3)
            if read_fails:
                assert world.reports[key].status is ApplyStatus.FAILED
                with pytest.raises(RuntimeError, match="APPLY_FAILED"):
                    await asyncio.wait_for(setup, timeout=3)
                assert identity.files == {"SOUL.md": "old identity"}
                assert identity.writes == []
            else:
                await asyncio.wait_for(setup, timeout=3)
                assert world.reports[key].status is ApplyStatus.SUCCEEDED
                assert identity.files == {"SOUL.md": "complete persona identity"}
                assert len(identity.writes) == 1
            assert identity.reads == ["SOUL.md"]
            assert world.deleted == []
            await sub.teardown()
            assert world.deleted == [key]
        finally:
            release.set()
            for task in (setup, apply_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (setup, apply_task) if task is not None),
                                 return_exceptions=True)
    asyncio.run(main())
