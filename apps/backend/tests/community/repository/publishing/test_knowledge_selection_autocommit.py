"""Knowledge publication honors the production database transaction contract."""

from contextlib import contextmanager

from agentclaw.community.core.repository.implementations.publishing import knowledge_projection as projection
from datetime import datetime, timedelta

import pytest
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
    OrmPublishOperationRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishStatus,
)
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.community.repository.publishing import (
    test_knowledge_selection_lifecycle as lifecycle,
)

# Register the existing fixture locally; its file-backed DB lives in tmp_path.
publication = lifecycle.publication


class ProductionContractDatabase:
    """Real SQLite autocommit for ordinary sessions, disabled for write units."""

    def __init__(self, engine):
        self.engine = engine
        self.calls = []
        self.writes = []

    @contextmanager
    def _session(self, interface, isolation):
        with self.engine.connect().execution_options(
            isolation_level=isolation
        ) as connection:
            autocommit = connection.connection.driver_connection.isolation_level is None
            assert autocommit == (interface == "orm_session")
            self.calls.append((interface, autocommit))
            connection.info["knowledge_test_interface"] = interface
            try:
                with Session(bind=connection, expire_on_commit=False) as session:
                    with session.begin():
                        yield session
            finally:
                connection.info.pop("knowledge_test_interface", None)

    def orm_session(self):
        return self._session("orm_session", "AUTOCOMMIT")

    def transactional_orm_session(self):
        return self._session("transactional_orm_session", "SERIALIZABLE")


@pytest.fixture
def autocommit_publication(publication):
    original_db, repo, record = publication
    db = ProductionContractDatabase(original_db.factory.kw["bind"])
    repo._db = db

    def track_write(connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(("UPDATE ", "INSERT ", "DELETE ")):
            db.writes.append(
                (
                    connection.info.get("knowledge_test_interface"),
                    connection.connection.driver_connection.isolation_level is None,
                )
            )

    event.listen(db.engine, "before_cursor_execute", track_write)
    try:
        yield db, repo, record
    finally:
        event.remove(db.engine, "before_cursor_execute", track_write)


def online_selection(db):
    with db.orm_session() as session:
        return {
            kb_id
            for (kb_id,) in session.query(Binding.kb_id)
            .filter(
                Binding.bot_pk == 1,
                Binding.env == "dev",
                Binding.online_enabled.is_(True),
            )
            .all()
        }


def test_projection_failure_rolls_back_only_effect_while_native_success_survives(autocommit_publication):
    db, repo, record = autocommit_publication
    online = lifecycle._start_online(repo, record)
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id="A").update({"online_enabled": True})
    succeeded = repo.compare_and_set_status_with_ext(publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB, target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext, ext=online.ext)
    assert succeeded.status == PublishStatus.SUCCESS
    assert online_selection(db) == {"A"}
    writes = []
    def fail_second_binding_update(_c, _cu, statement, *_):
        if statement.startswith("UPDATE ac_bot_knowledge_binding"):
            writes.append(statement)
            if len(writes) == 2:
                raise RuntimeError("injected second binding UPDATE failure")
    event.listen(db.engine, "before_cursor_execute", fail_second_binding_update)
    try:
        with pytest.raises(RuntimeError, match="second binding UPDATE"):
            projection.apply(db, record.id)
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_second_binding_update)
    assert len(writes) == 2
    assert repo.get_by_id(record.id).status == PublishStatus.SUCCESS
    assert online_selection(db) == {"A"}
    with db.orm_session() as session:
        raw = session.query(repo.Model.__table__.c.knowledge_projection).filter_by(id=record.id).scalar()
    assert projection.decode(raw)["state"] == "pending"
    assert projection.apply(db, record.id)
    assert online_selection(db) == {"B"}
    assert repo.compare_and_set_status_with_ext(publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB, target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext, ext=online.ext) is None
    assert online_selection(db) == {"B"}


def test_capture_write_failure_rolls_back_capture_but_native_admission_can_continue(autocommit_publication):
    db, repo, record = autocommit_publication
    writes = []
    def fail_once_after_publish_update(_c, _cu, statement, *_):
        if statement.startswith("UPDATE ac_bot_publish SET"):
            writes.append(statement)
            if len(writes) == 1:
                raise RuntimeError("injected after capture UPDATE")
    event.listen(db.engine, "after_cursor_execute", fail_once_after_publish_update)
    try:
        admitted = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    finally:
        event.remove(db.engine, "after_cursor_execute", fail_once_after_publish_update)
    assert admitted.status == PublishStatus.BUILDING
    assert admitted.ext["other"] == record.ext["other"]
    assert "enabled_kb_ids" not in admitted.ext["knowledge_selection_snapshot"]
    with db.orm_session() as session:
        raw = session.query(repo.Model.__table__.c.knowledge_snapshot).filter_by(id=record.id).scalar()
    assert projection.decode(raw)["capture_error"] == "RuntimeError"
    assert repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT) is None
    repo.update_status(record.id, PublishStatus.DRAFT, PublishStatus.BUILDING)
    frozen = repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
    assert frozen.ext["knowledge_selection_snapshot"]["enabled_kb_ids"] == ["B"]
    assert online_selection(db) == set()


@pytest.mark.parametrize(
    "interface,persists_after_failure",
    [("orm_session", True), ("transactional_orm_session", False)],
)
def test_database_contract_is_real_autocommit_or_rollback(
    autocommit_publication,
    interface,
    persists_after_failure,
):
    db, _repo, _record = autocommit_publication
    with pytest.raises(RuntimeError, match="contract rollback probe"):
        with getattr(db, interface)() as session:
            session.query(Binding).filter(Binding.kb_id == "A").update(
                {Binding.enabled: True}
            )
            raise RuntimeError("contract rollback probe")
    with db.orm_session() as session:
        enabled = session.query(Binding.enabled).filter(Binding.kb_id == "A").scalar()
    assert enabled is persists_after_failure


@pytest.mark.parametrize(
    "boundary",
    [
        "freeze",
        "status",
        "status_ext",
        "ext_cas",
        "status_ext_cas",
        "built",
        "rollback",
        "restore",
    ],
)
def test_native_and_effect_transactions_follow_their_own_boundaries(
    autocommit_publication, boundary
):
    db, repo, record = autocommit_publication
    current = record
    if boundary in ("built", "rollback", "restore"):
        current = repo.update_status(
            record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
    if boundary in ("rollback", "restore"):
        successor = repo.insert(
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
                last_pub_id=record.id,
            )
        )
    if boundary == "rollback":
        current = repo.update_status(
            record.id, PublishStatus.UPGRADED, PublishStatus.BUILDING
        )
        successor = repo.update_status(
            successor.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
        successor = repo.update_status(
            successor.id, PublishStatus.SUCCESS, PublishStatus.BUILDING
        )

    if boundary == "restore":
        operation = OrmPublishOperationRepository(db).insert(
            dict(
                publish_id=successor.id,
                operation_kind=str(PublishOperationKind.DRAFT_RESTORE),
                stage="draft",
                attempt=1,
                request_id="autocommit_restore_boundary",
                operator="owner",
                env="dev",
                params={
                    "source_publish_id": record.id,
                    "source_version": record.version,
                    "deadline_at": (datetime.now() + timedelta(minutes=30)).isoformat(),
                },
            )
        )

    db.calls.clear()
    db.writes.clear()
    if boundary == "freeze":
        result = repo.update_status(
            record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
    elif boundary == "status":
        result = repo.update_status(
            record.id, PublishStatus.FAILED, PublishStatus.DRAFT
        )
    elif boundary == "status_ext":
        result = repo.update_status_with_ext(
            record.id,
            PublishStatus.FAILED,
            {"other": 2},
            source_status=PublishStatus.DRAFT,
        )
    elif boundary == "ext_cas":
        result = repo.compare_and_set_ext(
            publish_id=record.id,
            expected_ext=record.ext,
            ext={"other": 2},
        )
    elif boundary == "status_ext_cas":
        result = repo.compare_and_set_status_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.DRAFT,
            target_status=PublishStatus.FAILED,
            expected_ext=record.ext,
            ext={"other": 2},
        )
    elif boundary == "built":
        result = repo.compare_and_set_built_with_ext(
            publish_id=record.id,
            source_status=PublishStatus.BUILDING,
            target_status=PublishStatus.BUILT,
            expected_ext=current.ext,
            ext=current.ext,
            center_skill_uuids=(),
            env="dev",
        )
    elif boundary == "rollback":
        result = repo.rollback_flip(
            demoted_publish_id=successor.id,
            demoted_ext=successor.ext,
            demoted_from_status=PublishStatus.SUCCESS,
            demoted_to_status=PublishStatus.DRAFT,
            restored_publish_id=record.id,
            restored_ext=current.ext,
            restored_from_status=PublishStatus.UPGRADED,
            restored_to_status=PublishStatus.SUCCESS,
        )
        assert result == (True, True)
    else:
        result = repo.restore_draft_knowledge_selection(
            draft_publish_id=successor.id,
            source_publish_id=record.id,
            operation_id=operation.id,
            expected_attempt=operation.attempt,
            final_result={"status": "success"},
        )
        assert result is True
    assert result is not None
    assert db.writes, "the boundary must execute real writes, not just open a session"
    expected = ("transactional_orm_session", False) if boundary in ("freeze", "rollback", "restore") else ("orm_session", True)
    assert expected in db.calls
    assert set(db.writes) == {expected}
