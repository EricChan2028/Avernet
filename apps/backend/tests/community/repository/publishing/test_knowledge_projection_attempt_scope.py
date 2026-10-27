"""Attempt, historical-tenant and native-capacity edge regressions."""

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    publication as _publication,
)
from tests.community.repository.publishing.test_knowledge_projection_recovery import (
    finish,
    intercept,
    journal,
)


publication = _publication


def test_failed_recapture_rotates_attempt_and_rejects_old_build_result(publication):
    from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
        PublishExtState,
    )
    from agentclaw.community.core.service_bot.services.publish_flow.errors import (
        PublishFlowServiceError,
    )

    db, repo, record = publication
    first = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    first_token = first.ext["knowledge_selection_snapshot"]
    repo.update_status(record.id, PublishStatus.DRAFT, PublishStatus.BUILDING)

    def fail_binding(_c, _cu, sql, *_):
        if "ac_bot_knowledge_binding" in sql:
            raise RuntimeError("recapture unavailable")

    with intercept(db, fail_binding):
        second = repo.update_status(
            record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
    assert second.status == PublishStatus.BUILDING
    assert (
        second.ext["knowledge_selection_snapshot"]["generation"]
        != first_token["generation"]
    )
    with pytest.raises(PublishFlowServiceError, match="superseded"):
        PublishExtState.require_knowledge_snapshot(first_token, second.ext)


def test_external_legacy_online_completion_uses_native_tenant_not_background_default(
    publication,
):
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    db, repo, _record = publication
    with avernet_tenant_scope("external-legacy"), db.orm_session() as session:
        session.add(
            BotModel(
                id=2,
                bot_id="external",
                bot_name="External",
                entity_id="owner",
                entity_type="staff",
                owner_id="owner",
                creator_id="owner",
                bot_type="service",
                space_id=10,
                env="dev",
                is_delete=0,
            )
        )
        session.add(
            Binding(
                bot_pk=2,
                space_id=10,
                env="dev",
                kb_id="external-kb",
                enabled=True,
                online_enabled=True,
                revision=1,
                updated_by="owner",
            )
        )
    with avernet_tenant_scope("external-legacy"):
        legacy = repo.insert(
            dict(
                source_bot_pk=2,
                source_bot_id="external",
                publish_bot_id="external.pub",
                name="External",
                owner_id="owner",
                permission_owner="owner",
                status=PublishStatus.UPGRADED,
                version=1,
                env="dev",
            )
        )
    # Historical version has no capture. Progress completion runs outside the
    # request context, just like the persisted native publish-progress task.
    online = repo.update_status_with_ext(
        legacy.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 123}},
        source_status=PublishStatus.UPGRADED,
    )
    finish(repo, online, deployment=123)
    assert repo.reconcile_knowledge_projection() == 1
    assert journal(db, legacy.id)["state"] == "unknown"
    with avernet_tenant_scope("external-legacy"), db.orm_session() as session:
        assert not session.query(Binding).filter_by(bot_pk=2).one().online_enabled


def test_missing_publication_keeps_native_online_cas_result(publication):
    _db, repo, _record = publication
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=99999,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=None,
            ext={"publish": {"online": 999}},
        )
        is None
    )


def test_optional_token_does_not_overflow_valid_native_text(publication):
    db, repo, record = publication
    payload = {"large": "x" * 65490}
    repo.update_status_with_ext(
        record.id, PublishStatus.DRAFT, payload, source_status=PublishStatus.DRAFT
    )

    def enforce_mysql_text_cap(_c, _cu, sql, parameters, *_):
        if sql.startswith("UPDATE ac_bot_publish"):
            for value in parameters:
                if isinstance(value, str) and value.startswith('{"large":'):
                    if len(value.encode("utf-8")) > 65535:
                        raise RuntimeError("simulated MySQL TEXT overflow")

    with intercept(db, enforce_mysql_text_cap):
        built = repo.update_status(
            record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
    assert built.status == PublishStatus.BUILDING
    assert built.ext == payload
    assert journal(db, record.id, repo.Model.__table__.c.knowledge_snapshot)[
        "capture_error"
    ]
