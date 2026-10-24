"""Environment scoping for task state and durable relay delivery intents."""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.models import TaskDeliveryIntentModel
from agentclaw.community.core.task.repository.types import TaskInfoRecord


def _set_env(monkeypatch, env: str) -> None:
    monkeypatch.setenv("SERVER_ENV", env)


def _seed_task_info(db, task_id: str) -> None:
    TaskInfoRepository(db).insert(TaskInfoRecord(
        id=0,
        task_id=task_id,
        source_type="bot",
        owner_user_id="U-1",
        owner_bot_id="B-1",
        execution_config={"orchestration_mode": "relay"},
        task_spec={"metadata": {"task_id": task_id}},
        status=Status.RUNNING,
    ))


def _minimal_graph(task_id: str) -> TaskExecutionGraph:
    graph = TaskExecutionGraph(
        task_id=task_id,
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[],
        relations=[],
        extend_props={},
    )
    root = TaskNode(
        node_id=task_id,
        task_id=task_id,
        status=Status.RUNNING,
        task_spec=TaskSpec(Context(title="t"), Goal(objective="o", acceptances=[])),
        run_info=RuntimeInfo(),
        node_run_graph=graph,
    )
    graph.tasks.append(root)
    return graph


def test_delivery_intent_is_scoped_to_task_environment(db, monkeypatch) -> None:
    task_id = "T-ENV-DELIVERY"
    relay_token = "0" * 16
    _set_env(monkeypatch, "pre")
    _seed_task_info(db, task_id)
    with db.orm_session() as session:
        session.add(TaskDeliveryIntentModel(
            task_id=task_id,
            node_id="node-2",
            relay_token=relay_token,
            state="PENDING",
            attempt=0,
        ))

    graph_repo = TaskGraphRepository(db)
    info_repo = TaskInfoRepository(db)
    assert graph_repo.list_pending_delivery_intents() == [(task_id, "node-2")]

    _set_env(monkeypatch, "prod")
    assert graph_repo.list_pending_delivery_intents() == []
    with pytest.raises(TaskStateError, match="environment mismatch"):
        graph_repo.claim_delivery_intent(task_id, "node-2", relay_token)
    with pytest.raises(TaskStateError, match="environment mismatch"):
        info_repo.get(task_id)

    _set_env(monkeypatch, "pre")
    assert graph_repo.claim_delivery_intent(task_id, "node-2", relay_token)[0] == "CLAIMED"


def test_graph_mutation_is_rejected_across_environments(db, monkeypatch) -> None:
    task_id = "T-ENV-GRAPH"
    _set_env(monkeypatch, "pre")
    _seed_task_info(db, task_id)
    graph = _minimal_graph(task_id)
    repo = TaskGraphRepository(db)

    _set_env(monkeypatch, "prod")
    with pytest.raises(TaskStateError, match="environment mismatch"):
        repo.save_graph(
            graph,
            expected_version=0,
            runtime_status=Status.RUNNING,
            action_events=[],
        )
    with pytest.raises(TaskStateError, match="environment mismatch"):
        repo.get_version(task_id)
    with pytest.raises(TaskStateError, match="environment mismatch"):
        repo.acquire_lease(task_id, instance_id="prod-pod", lease_seconds=60)

    _set_env(monkeypatch, "pre")
    assert repo.get_version(task_id) == 0
    assert repo.save_graph(
        graph,
        expected_version=0,
        runtime_status=Status.RUNNING,
        action_events=[],
    ) == 1
    assert repo.get_version(task_id) == 1
    assert repo.acquire_lease(task_id, instance_id="pre-pod", lease_seconds=60) is True


def test_task_info_gateway_reads_and_writes_share_environment(db, monkeypatch) -> None:
    task_id = "T-ENV-INFO"
    _set_env(monkeypatch, "pre")
    repo = TaskInfoRepository(db)
    record = TaskInfoRecord(
        id=0,
        task_id=task_id,
        source_type="bot",
        owner_user_id="U-1",
        owner_bot_id="B-1",
        execution_config={},
        task_spec={"metadata": {"task_id": task_id}},
        status=Status.PENDING,
    )
    stored = repo.insert(record)
    assert stored.env == "pre"

    _set_env(monkeypatch, "prod")
    with pytest.raises(TaskStateError, match="environment mismatch"):
        repo.get(task_id)
    with pytest.raises(TaskStateError, match="environment mismatch"):
        repo.update_status(task_id, Status.RUNNING)
    assert repo.list_records() == []

    _set_env(monkeypatch, "pre")
    assert repo.get(task_id).env == "pre"
    assert repo.update_status(task_id, Status.RUNNING) is True
