"""Knowledge state follows observed deployment success, never admission status."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishOperationModel,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PublishProgressPollHandler,
)
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    PublishNotFoundError,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    BotModel,
    _online,
    _real_flow,
    _start_online,
    publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import (
    finish,
)

publication = publication_fixture


def space(db):
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=9,
                space_code="personal-knowledge",
                space_type="PERSONAL",
                name="Personal",
                personal_owner_id="owner",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )


def selected(db, owner="owner"):
    return BotKnowledgeRepository(db).list_enabled(
        BotKnowledgeScope("service", owner, "dev"),
        selection_stage="online",
    )


def version(repo, previous, *, owner="owner", status=PublishStatus.DRAFT, **extra):
    return repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id=owner,
            permission_owner=owner,
            status=status,
            version=previous.version + 1,
            env="dev",
            last_pub_id=previous.id,
            **extra,
        )
    )


def deploying(repo, record, workflow_id):
    online = _start_online(repo, record)
    return repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {**online.ext, "publish": {"online": workflow_id}, "binding": {"online": 11}},
        PublishStatus.ONLINE_PUB,
    )


def poll(repo, publish_id):
    flow, queue = _real_flow(repo)
    flow.get_baas_publish_progress = MagicMock(return_value={"status": "SUCCESS"})
    return PublishProgressPollHandler(flow=flow, task_queue_service=queue).handle(
        {"publish_id": publish_id},
    )


@pytest.mark.asyncio
async def test_unknown_survives_real_rollback_failure_and_redraft(publication):
    db, repo, current = publication
    space(db)
    legacy = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.UPGRADED,
            version=0,
            env="dev",
            ext={"migration_path": "/legacy"},
        )
    )
    with db.orm_session() as session:
        session.query(BotPublishModel).filter_by(id=current.id).update(
            {"last_pub_id": legacy.id}
        )
        session.query(Binding).update({"avernet_tenant": "external"})
        session.query(BotModel).filter_by(id=1).update({"avernet_tenant": "external"})
    # Real default-tenant poll cannot infer the legacy tenant or clear its flags.
    online = repo.update_status_with_ext(
        current.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 200}, "binding": {"online": 11}},
        PublishStatus.DRAFT,
    )
    with avernet_tenant_scope("external"), db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    poll(repo, online.id)
    unknown = repo.get_by_id(current.id).ext["knowledge_selection_result"]
    assert unknown["status"] == "unknown" and unknown["deployment_id"] == 200
    with avernet_tenant_scope("external"):
        assert _online(db) == {"A"}  # no tenant-guard bypass
        assert selected(db) == ()
    flow, _queue = _real_flow(repo)
    flow._publish_service._publish_flow_service_provider = lambda: flow
    flow.execute_rollback = AsyncMock(
        side_effect=RuntimeError("deployment submission failed")
    )
    with pytest.raises(RuntimeError, match="deployment submission failed"):
        await flow._publish_service.rollback_publish(current.id, operator="owner")
    assert repo.get_by_id(current.id).status == PublishStatus.DRAFT
    with avernet_tenant_scope("external"):
        assert selected(db) == ()
        rebuilt = repo.update_status(
            current.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
        assert rebuilt.ext["knowledge_selection_result"] == unknown
        assert selected(db) == ()  # capture is not successful deployment


@pytest.mark.asyncio
async def test_rollback_admission_does_not_reactivate_old_unknown_result(publication):
    db, repo, old = publication
    space(db)
    old = repo.update_status_with_ext(
        old.id,
        PublishStatus.ONLINE_PUB,
        {
            "publish": {"online": 100},
            "binding": {"online": 11},
            "migration_path": "/old",
        },
        PublishStatus.DRAFT,
    )
    old = finish(repo, old)
    assert old.ext["knowledge_selection_result"]["status"] == "unknown"
    current = version(repo, old)
    current = finish(repo, deploying(repo, current, 200))
    repo.update_status(old.id, PublishStatus.UPGRADED, PublishStatus.SUCCESS)
    assert selected(db) == ("B",)
    flow, _queue = _real_flow(repo)
    flow._publish_service._publish_flow_service_provider = lambda: flow
    flow.execute_rollback = AsyncMock(
        side_effect=RuntimeError("deployment submission failed")
    )
    with pytest.raises(RuntimeError, match="deployment submission failed"):
        await flow._publish_service.rollback_publish(current.id, operator="owner")
    # Native admission flips current→DRAFT and old→SUCCESS, but the actual live
    # deployment is still workflow200 and its knowledge selection stays B.
    assert repo.get_by_id(current.id).status == PublishStatus.DRAFT
    assert repo.get_by_id(old.id).status == PublishStatus.SUCCESS
    assert selected(db) == ("B",)
    restored = repo.update_status_with_ext(
        old.id,
        PublishStatus.ONLINE_PUB,
        {**repo.get_by_id(old.id).ext, "publish": {"online": 300}},
        PublishStatus.SUCCESS,
    )
    finish(repo, restored)
    assert selected(db) == ()  # only actual target success activates UNKNOWN300


@pytest.mark.parametrize("owner_changed", [False, True])
def test_late_callback_across_uuid_or_owner_replacement_cannot_touch_new_flags(
    publication,
    owner_changed,
):
    db, repo, first = publication
    space(db)
    old = deploying(repo, first, 100)
    owner = "new-owner" if owner_changed else "owner"
    with db.orm_session() as session:
        if owner_changed:
            session.query(BotModel).filter_by(id=1).update({"owner_id": owner})
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    current = version(repo, first, owner=owner)
    current = deploying(repo, current, 200)
    with db.orm_session() as session:
        session.add_all(
            [
                PublishOperationModel(
                    publish_id=row.id,
                    stage="online",
                    operation_kind="upgrade",
                    attempt=1,
                    state="completed",
                    request_id=f"release-{workflow}",
                    bot_uuid=uuid,
                    baas_publish_id=workflow,
                    env="dev",
                )
                for row, workflow, uuid in (
                    (old, 100, "old-device"),
                    (current, 200, "replacement-device"),
                )
            ]
        )
    poll(repo, current.id)
    assert selected(db, owner) == ("A",)
    if owner_changed:
        poll(repo, old.id)  # native may complete; no effects on new Owner
        assert (
            repo.get_by_id(old.id).ext["knowledge_selection_result"]["status"]
            == "unknown"
        )
    else:
        with pytest.raises(PublishNotFoundError):
            poll(repo, old.id)  # older observed workflow cannot win the local CAS
    assert _online(db) == {"A"}
    assert selected(db, owner) == ("A",)
