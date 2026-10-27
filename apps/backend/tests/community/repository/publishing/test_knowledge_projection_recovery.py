"""Real-SQLite native success, durable recovery, fencing and fail-closed reads."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, func

from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.repository.implementations.publishing import (
    knowledge_projection as projection,
)
from agentclaw.community.core.repository.implementations.publishing.bot_publish import (
    BotPublishRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publish,
    PublishStatus,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgePublicationPolicy,
)
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    _online,
    _start_online,
    publication as _publication,
)
from tests.community.repository.publishing.test_knowledge_selection_restore import (
    restore_case as _restore_case,
)

publication = _publication
restore_case = _restore_case
columns = Publish.__table__.c


def finish(repo, record, deployment=77):
    if (record.ext.get("publish") or {}).get("online") != deployment:
        record = repo.update_status_with_ext(
            record.id,
            PublishStatus.ONLINE_PUB,
            {**record.ext, "publish": {"online": deployment}},
            source_status=PublishStatus.ONLINE_PUB,
        )
    return repo.compare_and_set_status_with_ext(
        publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS,
        expected_ext=record.ext,
        ext=record.ext,
    )


def journal(db, publish_id, column=columns.knowledge_projection):
    with db.orm_session() as session:
        return projection.decode(
            session.query(column).filter(Publish.id == publish_id).scalar()
        )


def allow_reads(db):
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=9,
                space_code="personal-owner",
                space_type="PERSONAL",
                name="Owner",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
    return BotKnowledgeRepository(db, KnowledgePublicationPolicy(enabled=True))


@contextmanager
def intercept(db, callback):
    engine = db.factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", callback)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", callback)


def test_native_success_has_no_knowledge_io_and_pending_read_is_closed(publication):
    db, repo, record = publication
    online = _start_online(repo, record)
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    statements = []
    with intercept(db, lambda _c, _cu, sql, *_: statements.append(sql)):
        assert finish(repo, online).status == PublishStatus.SUCCESS
    assert not any("ac_bot_knowledge_binding" in sql for sql in statements)
    assert not any("ac_bots" in sql for sql in statements)
    reader = allow_reads(db)
    scope = BotKnowledgeScope("service", "owner", "dev")
    assert reader.list_enabled(scope, selection_stage="online") == ()
    assert (
        reader.list_bindings(scope=scope, actor_id="owner", selection_stage="online")[
            "projection_state"
        ]
        == "pending"
    )
    assert repo.reconcile_knowledge_projection() == 1
    assert _online(db) == {"B"}
    assert reader.list_enabled(scope, selection_stage="online") == ("B",)
    statements.clear()
    with intercept(db, lambda _c, _cu, sql, *_: statements.append(sql)):
        assert repo.reconcile_knowledge_projection() == 0
    assert not any(sql.startswith("UPDATE") for sql in statements)


def test_failed_projection_survives_new_repository_and_retries_without_redeploy(
    publication,
):
    db, repo, record = publication
    finish(repo, _start_online(repo, record))

    def fail_binding_write(_c, _cu, sql, *_):
        if sql.startswith("UPDATE ac_bot_knowledge_binding"):
            raise RuntimeError("injected unavailable knowledge store")

    with intercept(db, fail_binding_write):
        assert repo.reconcile_knowledge_projection() == 0
    assert repo.get_by_id(record.id).status == PublishStatus.SUCCESS
    assert journal(db, record.id)["state"] == "pending"
    assert journal(db, record.id)["attempts"] == 1
    assert _online(db) == set()
    with db.orm_session() as session:
        session.query(Publish).filter_by(id=record.id).update(
            {columns.knowledge_due: func.now()}, synchronize_session=False
        )
    restarted = BotPublishRepository(
        db, repo._offload, KnowledgePublicationPolicy(enabled=True)
    )
    assert restarted.reconcile_knowledge_projection() == 1
    assert journal(db, record.id)["state"] == "applied"
    assert _online(db) == {"B"}


def test_capture_failure_never_recaptures_a_later_draft(publication):
    db, repo, record = publication

    def fail_capture(_c, _cu, sql, *_):
        if sql.startswith("SELECT") and "ac_bot_knowledge_binding" in sql:
            raise RuntimeError("injected capture failure")

    with intercept(db, fail_capture):
        building = repo.update_status(
            record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
    assert building.status == PublishStatus.BUILDING
    with db.orm_session() as session:
        session.query(Binding).update({"enabled": False, "online_enabled": True})
    online = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {**building.ext, "publish": {"online": 77}},
        source_status=PublishStatus.BUILDING,
    )
    assert finish(repo, online).status == PublishStatus.SUCCESS
    assert repo.reconcile_knowledge_projection() == 1
    assert journal(db, record.id)["state"] == "unknown"
    assert _online(db) == set()


def test_new_deployment_supersedes_older_delayed_intent(publication):
    db, repo, first = publication
    first = finish(repo, _start_online(repo, first))
    second = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=first.id,
        )
    )
    with db.orm_session() as session:
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    second = finish(repo, _start_online(repo, second), deployment=88)
    assert projection.apply(db, second.id)
    assert _online(db) == {"A"}
    assert projection.apply(db, first.id)
    assert journal(db, first.id)["state"] == "superseded"
    assert _online(db) == {"A"}
    assert not projection.apply(db, first.id)


def test_old_version_rollback_gets_new_deployment_order(publication):
    db, repo, first = publication
    first = finish(repo, _start_online(repo, first))
    projection.apply(db, first.id)
    second = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=first.id,
        )
    )
    with db.orm_session() as session:
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})
    second = finish(repo, _start_online(repo, second), deployment=88)
    projection.apply(db, second.id)
    first = repo.update_status(first.id, PublishStatus.UPGRADED, PublishStatus.SUCCESS)
    assert repo.rollback_flip(
        demoted_publish_id=second.id,
        demoted_ext=second.ext,
        demoted_from_status=PublishStatus.SUCCESS,
        demoted_to_status=PublishStatus.DRAFT,
        restored_publish_id=first.id,
        restored_ext={**first.ext, "rollback_restored_from": second.id},
        restored_from_status=PublishStatus.UPGRADED,
        restored_to_status=PublishStatus.SUCCESS,
    ) == (True, True)
    assert _online(db) == {"A"}  # admission alone is not actual deployment success
    rolling = repo.update_status_with_ext(
        first.id,
        PublishStatus.ONLINE_PUB,
        {**repo.get_by_id(first.id).ext, "publish": {"online": 99}},
        source_status=PublishStatus.SUCCESS,
    )
    finish(repo, rolling, deployment=99)
    projection.apply(db, first.id)
    assert _online(db) == {"B"}


def test_unknown_historical_snapshot_does_not_block_native_success(publication):
    db, repo, record = publication
    with db.orm_session() as session:
        session.query(Binding).update({"online_enabled": True})
    record = repo.update_status_with_ext(
        record.id,
        PublishStatus.ONLINE_PUB,
        {"publish": {"online": 77}},
        source_status=PublishStatus.DRAFT,
    )
    assert finish(repo, record).status == PublishStatus.SUCCESS
    projection.apply(db, record.id)
    assert journal(db, record.id)["state"] == "unknown"
    assert _online(db) == set()


def test_draft_restore_commits_native_result_before_optional_projection(restore_case):
    case = restore_case
    with case.db.orm_session() as session:
        session.query(Binding).update({"enabled": False})
        session.query(Binding).filter_by(kb_id="A").update({"enabled": True})

    def unavailable(_c, _cu, sql, *_):
        if "ac_bot_knowledge_binding" in sql:
            raise RuntimeError("knowledge unavailable")

    with intercept(case.db, unavailable):
        assert case.repo.restore_draft_knowledge_selection(
            draft_publish_id=case.draft.id,
            source_publish_id=case.source.id,
            operation_id=case.operation.id,
            expected_attempt=case.operation.attempt,
            final_result=case.final_result,
        )
    assert case.ledger.get_by_id(case.operation.id).state == "completed"
    assert (
        journal(case.db, case.draft.id, columns.knowledge_restore)["state"] == "pending"
    )
    projection.apply(case.db, case.draft.id)
    assert (
        journal(case.db, case.draft.id, columns.knowledge_restore)["state"] == "applied"
    )
    with case.db.orm_session() as session:
        assert {
            r.kb_id for r in session.query(Binding).filter(Binding.enabled.is_(True))
        } == {"B"}


def test_delayed_restore_cannot_overwrite_new_draft_edit(restore_case):
    case = restore_case
    reader = allow_reads(case.db)
    assert case.repo.restore_draft_knowledge_selection(
        draft_publish_id=case.draft.id,
        source_publish_id=case.source.id,
        operation_id=case.operation.id,
        expected_attempt=case.operation.attempt,
        final_result=case.final_result,
    )
    reader.mutate(
        scope=BotKnowledgeScope("service", "owner", "dev"),
        actor_id="owner",
        kb_id="A",
        value=True,
        space_id=9,
        expected_revision=1,
    )
    projection.apply(case.db, case.draft.id)
    assert (
        journal(case.db, case.draft.id, columns.knowledge_restore)["state"]
        == "superseded"
    )
    with case.db.orm_session() as session:
        assert session.query(Binding).filter_by(kb_id="A").one().enabled


def test_optional_columns_are_not_in_native_disabled_select_or_insert(publication):
    db, repo, record = publication
    repo._knowledge_policy = KnowledgePublicationPolicy()
    sql = []
    with intercept(db, lambda _c, _cu, statement, *_: sql.append(statement)):
        repo.insert(
            dict(
                source_bot_pk=1,
                source_bot_id="service",
                publish_bot_id="service.pub",
                name="Service",
                owner_id="owner",
                permission_owner="owner",
                status=PublishStatus.DRAFT,
                version=2,
                env="dev",
            )
        )
        repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
        repo.get_by_id(record.id)
    assert not any("knowledge_" in statement for statement in sql)


def test_retry_uses_db_clock_stops_at_limit_and_explicit_repair_requeues(publication):
    db, repo, record = publication
    finish(repo, _start_online(repo, record))

    def fail_binding(_c, _cu, sql, *_):
        if sql.startswith("UPDATE ac_bot_knowledge_binding"):
            raise RuntimeError("injected persistent failure")

    with intercept(db, fail_binding):
        for attempt in range(projection.MAX_ATTEMPTS):
            with db.orm_session() as session:
                session.query(Publish).filter_by(id=record.id).update(
                    {columns.knowledge_due: func.now()}, synchronize_session=False
                )
            assert repo.reconcile_knowledge_projection() == 0
            with db.orm_session() as session:
                due, now = (
                    session.query(columns.knowledge_due, func.now())
                    .filter(Publish.id == record.id)
                    .one()
                )
            if attempt + 1 < projection.MAX_ATTEMPTS:
                assert 29 <= (due - now).total_seconds() <= 30
            else:
                assert due is None
    assert journal(db, record.id)["state"] == "failed"
    assert journal(db, record.id)["attempts"] == projection.MAX_ATTEMPTS
    assert repo.get_by_id(record.id).status == PublishStatus.SUCCESS
    assert repo.reconcile_knowledge_projection() == 0
    assert repo.retry_knowledge_projection(record.id)
    assert repo.reconcile_knowledge_projection() == 1
    assert journal(db, record.id)["state"] == "applied"
    assert _online(db) == {"B"}


def test_disabled_works_against_actual_pre_journal_schema(publication):
    db, repo, record = publication
    repo._knowledge_policy = KnowledgePublicationPolicy()
    engine = db.factory.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX idx_publish_knowledge_due")
        connection.exec_driver_sql("DROP INDEX idx_publish_knowledge_deploy")
        for name in (
            "knowledge_tenant",
            "knowledge_snapshot",
            "knowledge_projection",
            "knowledge_due",
            "knowledge_deployment_id",
            "knowledge_restore",
            "knowledge_revision",
        ):
            connection.exec_driver_sql(f"ALTER TABLE ac_bot_publish DROP COLUMN {name}")
    assert repo.get_by_id(record.id).id == record.id
    online = _start_online(repo, record)
    assert finish(repo, online).status == PublishStatus.SUCCESS
    assert repo.reconcile_knowledge_projection() == 0
    assert not repo.retry_knowledge_projection(record.id)


def test_missing_native_deployment_id_never_exposes_older_applied_flags(publication):
    db, repo, first = publication
    first = finish(repo, _start_online(repo, first))
    repo.reconcile_knowledge_projection()
    second = repo.insert(
        dict(
            source_bot_pk=1,
            source_bot_id="service",
            publish_bot_id="service.pub",
            name="Service",
            owner_id="owner",
            permission_owner="owner",
            status=PublishStatus.DRAFT,
            version=2,
            env="dev",
            last_pub_id=first.id,
        )
    )
    building = repo.update_status(
        second.id, PublishStatus.BUILDING, PublishStatus.DRAFT
    )
    online = repo.update_status_with_ext(
        second.id,
        PublishStatus.ONLINE_PUB,
        building.ext,
        source_status=PublishStatus.BUILDING,
    )
    assert (
        repo.compare_and_set_status_with_ext(
            publish_id=second.id,
            source_status=PublishStatus.ONLINE_PUB,
            target_status=PublishStatus.SUCCESS,
            expected_ext=online.ext,
            ext=online.ext,
        ).status
        == PublishStatus.SUCCESS
    )
    reader = allow_reads(db)
    scope = BotKnowledgeScope("service", "owner", "dev")
    assert reader.list_enabled(scope, selection_stage="online") == ()
    repo.reconcile_knowledge_projection()
    assert journal(db, second.id)["state"] == "unknown"
    assert reader.list_enabled(scope, selection_stage="online") == ()


def test_due_scan_has_index_plan_and_no_unbounded_historical_scan(publication):
    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        from sqlalchemy import text

        plan = session.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT id FROM ac_bot_publish WHERE env = 'dev' AND knowledge_due <= CURRENT_TIMESTAMP ORDER BY knowledge_due, id LIMIT 50"
            )
        ).all()
    assert any("idx_publish_knowledge_due" in str(row) for row in plan)
    assert projection.run_batch(db, limit=1) == 1
    assert projection.run_batch(db, limit=1) == 0


def test_draft_page_checks_projection_before_reading_enabled_flags(publication):
    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    reader = allow_reads(db)
    sql = []
    with intercept(db, lambda _c, _cu, statement, *_: sql.append(statement)):
        page = reader.list_bindings(
            scope=BotKnowledgeScope("service", "owner", "dev"),
            actor_id="owner",
            selection_stage="online",
        )
    assert page["projection_state"] == "pending"
    assert not any(row["enabled"] for row in page["items"])
    journal_read = next(
        i for i, statement in enumerate(sql) if "knowledge_projection" in statement
    )
    binding_read = next(
        i
        for i, statement in enumerate(sql)
        if "FROM ac_bot_knowledge_binding" in statement
    )
    assert journal_read < binding_read


def test_completed_restore_is_superseded_by_a_later_restore_attempt(restore_case):
    case = restore_case
    assert case.repo.restore_draft_knowledge_selection(
        draft_publish_id=case.draft.id,
        source_publish_id=case.source.id,
        operation_id=case.operation.id,
        expected_attempt=case.operation.attempt,
        final_result=case.final_result,
    )
    case.ledger.insert(
        dict(
            publish_id=case.draft.id,
            operation_kind="draft_restore",
            stage="draft",
            attempt=2,
            request_id="new-restore-attempt",
            env="dev",
            operator="owner",
            params=case.operation.params,
        )
    )
    projection.apply(case.db, case.draft.id)
    assert (
        journal(case.db, case.draft.id, columns.knowledge_restore)["state"]
        == "superseded"
    )


def test_background_discovery_uses_persisted_tenant_without_bypassing_guards(
    publication,
):
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.utils.avernet_tenant import (
        avernet_tenant_scope,
        get_current_avernet_tenant,
    )

    db, repo, _record = publication
    with avernet_tenant_scope("external-tenant"), db.orm_session() as session:
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
                revision=1,
                updated_by="owner",
            )
        )
    with avernet_tenant_scope("external-tenant"):
        external = repo.insert(
            dict(
                source_bot_pk=2,
                source_bot_id="external",
                publish_bot_id="external.pub",
                name="External",
                owner_id="owner",
                permission_owner="owner",
                status=PublishStatus.DRAFT,
                version=1,
                env="dev",
            )
        )
        finish(repo, _start_online(repo, external), deployment=123)
    assert get_current_avernet_tenant() == "teamclaw"
    assert repo.reconcile_knowledge_projection() == 1
    assert get_current_avernet_tenant() == "teamclaw"
    assert journal(db, external.id)["state"] == "applied"
    with avernet_tenant_scope("external-tenant"), db.orm_session() as session:
        assert session.query(Binding).filter_by(bot_pk=2).one().online_enabled
    with db.orm_session() as session:
        assert not session.query(Binding).filter_by(bot_pk=2).all()


def test_two_concurrent_projection_deliveries_have_one_effect(publication):
    from threading import Event, Thread, current_thread

    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    reserved, contending, release = Event(), Event(), Event()
    outcomes = []
    writes = []

    def before(_c, _cu, statement, *_):
        if (
            statement.startswith("UPDATE ac_bots SET id=")
            and current_thread().name == "second"
        ):
            contending.set()
        if statement.startswith("UPDATE ac_bot_knowledge_binding"):
            writes.append(statement)

    def after(_c, _cu, statement, *_):
        if (
            statement.startswith("UPDATE ac_bots SET id=")
            and current_thread().name == "first"
        ):
            reserved.set()
            assert release.wait(5)

    engine = db.factory.kw["bind"]

    def work():
        try:
            outcomes.append(projection.apply(db, record.id))
        except Exception as exc:
            outcomes.append(exc)

    threads = [Thread(target=work, name=name) for name in ("first", "second")]
    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        threads[0].start()
        assert reserved.wait(5)
        threads[1].start()
        assert contending.wait(5)
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(10)
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == [False, True]
    assert len(writes) == 2  # set-based disable and enable, never per-KB SQL
    assert _online(db) == {"B"}


@pytest.mark.asyncio
async def test_projection_lifecycle_disabled_has_no_io_and_shutdown_drains():
    import asyncio
    from threading import Event
    from agentclaw.community.core.service_bot.services.knowledge_projection_lifecycle import (
        KnowledgeProjectionLifecycle,
    )

    repo = MagicMock()
    disabled = KnowledgeProjectionLifecycle(repo, KnowledgePublicationPolicy())
    await disabled.startup()
    await disabled.shutdown()
    repo.reconcile_knowledge_projection.assert_not_called()
    started, release = Event(), Event()

    def batch():
        started.set()
        assert release.wait(5)

    repo.reconcile_knowledge_projection.side_effect = batch
    enabled = KnowledgeProjectionLifecycle(
        repo, KnowledgePublicationPolicy(enabled=True)
    )
    await enabled.startup()
    assert await asyncio.to_thread(started.wait, 5)
    closing = asyncio.create_task(enabled.shutdown())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await asyncio.wait_for(closing, timeout=5)
    assert enabled._task is None
    assert repo.reconcile_knowledge_projection.call_count == 1


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        "[]",
        '{"state":"invalid"}',
        '{"state":["pending"]}',
        '{"state":"pending","attempts":"bad"}',
    ],
)
def test_malformed_journal_is_durable_unknown_not_silently_dropped(publication, raw):
    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        session.query(Publish).filter_by(id=record.id).update(
            {
                columns.knowledge_projection: raw,
            },
            synchronize_session=False,
        )
    assert repo.reconcile_knowledge_projection() == 0
    assert journal(db, record.id) == {
        "state": "unknown",
        "error": "KNOWLEDGE_JOURNAL_INVALID",
    }
    with db.orm_session() as session:
        assert (
            session.query(columns.knowledge_due)
            .filter(Publish.id == record.id)
            .scalar()
            is None
        )
    assert repo.get_by_id(record.id).status == PublishStatus.SUCCESS
    reader = allow_reads(db)
    assert (
        reader.list_enabled(
            BotKnowledgeScope("service", "owner", "dev"), selection_stage="online"
        )
        == ()
    )


def test_latest_deployment_fence_is_current_read_not_repeatable_read_snapshot(
    publication,
):
    from sqlalchemy.orm import Session
    from sqlalchemy.dialects import mysql

    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    fences = []

    def inspect_query(state):
        statement = state.statement
        if state.is_select and "knowledge_deployment_id" in str(statement):
            fences.append(str(statement.compile(dialect=mysql.dialect())))

    event.listen(Session, "do_orm_execute", inspect_query)
    try:
        assert projection.apply(db, record.id)
    finally:
        event.remove(Session, "do_orm_execute", inspect_query)
    assert len(fences) == 1
    assert "FOR UPDATE" in fences[0]


def test_two_workers_claim_once_before_bot_lock(publication, monkeypatch):
    from threading import Barrier, Thread

    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    both_discovered = Barrier(2)
    original_claim = projection._claim
    writes, outcomes = [], []

    def claim(*args):
        both_discovered.wait(timeout=5)
        return original_claim(*args)

    def work():
        try:
            outcomes.append(repo.reconcile_knowledge_projection())
        except Exception as exc:
            outcomes.append(exc)

    monkeypatch.setattr(projection, "_claim", claim)
    threads = [Thread(target=work) for _ in range(2)]
    with intercept(db, lambda _c, _cu, sql, *_: writes.append(sql)):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == [0, 1]
    assert sum(sql.startswith("UPDATE ac_bots SET id=") for sql in writes) == 1
    assert journal(db, record.id)["state"] == "applied"


def test_abandoned_claim_is_rediscovered_after_lease_expiry(publication):
    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    with db.orm_session() as session:
        raw = (
            session.query(columns.knowledge_projection)
            .filter(Publish.id == record.id)
            .scalar()
        )
    lease = projection._claim(db, record.id, raw, None)
    assert lease is not None
    assert projection._claim(db, record.id, raw, None) is None
    assert repo.reconcile_knowledge_projection() == 0
    # Simulate crash after claim, then DB lease expiry, without touching journal.
    with db.orm_session() as session:
        session.query(Publish).filter_by(id=record.id).update(
            {
                columns.knowledge_due: func.now(),
            },
            synchronize_session=False,
        )
    assert not projection.apply(db, record.id, lease_until=lease)
    assert repo.reconcile_knowledge_projection() == 1
    assert journal(db, record.id)["state"] == "applied"


def test_operator_entrypoint_requeues_only_same_scoped_failed_intent(
    publication, capsys, monkeypatch
):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "retry_knowledge_projection_cli",
        Path(__file__).resolve().parents[4] / "scripts/retry_knowledge_projection.py",
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    db, repo, record = publication
    finish(repo, _start_online(repo, record))
    failed = {**journal(db, record.id), "state": "failed", "attempts": 10}
    import json

    with db.orm_session() as session:
        session.query(Publish).filter_by(id=record.id).update(
            {
                columns.knowledge_projection: json.dumps(failed),
                columns.knowledge_due: None,
            },
            synchronize_session=False,
        )
    args = SimpleNamespace(publish_id=record.id, tenant="teamclaw", env="dev")
    with pytest.raises(ValueError, match="enabled"):
        cli.retry(args, repo, enabled=False, current_env="dev")
    with pytest.raises(ValueError, match="env"):
        cli.retry(args, repo, enabled=True, current_env="prod")
    args.tenant = "other-tenant"
    from agentclaw.community.kernel.knowledge import KnowledgeError

    with pytest.raises(KnowledgeError):
        cli.retry(args, repo, enabled=True, current_env="dev")
    assert journal(db, record.id)["state"] == "failed"
    args.tenant = "teamclaw"
    # Exercise actual CLI parsing and its composition-root handoff without
    # connecting to any deployed profile or changing the global config registry.
    from agentclaw.community import di
    from agentclaw.community.di import config_bootstrap
    from agentclaw.community.core.repository.protocols.publishing import (
        BotPublishRepositoryProtocol,
    )

    bootstrap = MagicMock()
    injector = MagicMock()
    injector.get.side_effect = lambda cls: (
        repo
        if cls is BotPublishRepositoryProtocol
        else KnowledgePublicationPolicy(enabled=True)
    )
    monkeypatch.setattr(config_bootstrap, "register_config_provider", bootstrap)
    monkeypatch.setattr(di, "build_injector", lambda **_: injector)
    argv = ["--publish-id", str(record.id), "--tenant", "teamclaw", "--env", "dev"]
    with pytest.raises(SystemExit) as missing_confirmation:
        cli.main(argv)
    assert missing_confirmation.value.code == 2
    bootstrap.assert_not_called()
    assert cli.main([*argv, "--confirm-retry"]) == 0
    bootstrap.assert_called_once()
    assert '"requeued": true' in capsys.readouterr().out
    pending = journal(db, record.id)
    assert pending["snapshot"] == failed["snapshot"]
    assert pending["generation"] == failed["generation"]
    assert repo.reconcile_knowledge_projection() == 1
    assert journal(db, record.id)["state"] == "applied"
    assert repo.get_by_id(record.id).status == PublishStatus.SUCCESS
