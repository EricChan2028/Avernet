"""Durable, retryable knowledge effects, independent of native deployment success.

The intent lives on the publication row and is written by its native CAS. There
is no enqueue handoff to lose. Only the projector reads/writes knowledge tables.
All effects and acknowledgement commit together under Bot -> publication locks;
there is no remote I/O or independent commit inside an effect transaction.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, text

from agentclaw.community.core.repository.implementations.publishing import (
    knowledge_selection as selection,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publish,
    PublishOperationModel as Operation,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
)

logger = get_logger()
columns = Publish.__table__.c
BATCH_SIZE = 50
RETRY_SECONDS = 30
MAX_ATTEMPTS = 10
CLAIM_SECONDS = 30
_STATES = frozenset(("pending", "applied", "unknown", "superseded", "failed"))


def decode(value):
    if value is None:
        return None
    try:
        result = json.loads(value)
    except (ValueError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def _invalid_journal(raw):
    journal = decode(raw)
    return raw is not None and (
        journal is None
        or not isinstance(journal.get("state"), str)
        or journal["state"] not in _STATES
        or type(journal.get("attempts", 0)) is not int
        or journal.get("attempts", 0) < 0
    )


def online_intent(session, publish_id, ext):
    """Values for the SAME UPDATE that commits native ONLINE_PUB -> SUCCESS.

    Only native publication data is accessed, never the knowledge table. These
    optional journal columns require the opt-in migration; ordinary publishing
    neither selects nor writes them.
    """
    row = (
        session.query(columns.knowledge_snapshot, columns.knowledge_tenant)
        .filter(Publish.id == publish_id)
        .one_or_none()
    )
    if row is None:
        return {}  # The native CAS still returns its original missing-row result.
    raw, tenant = row
    deployment_id = (ext.get("publish") or {}).get("online")
    if type(deployment_id) is not int or deployment_id <= 0:
        deployment_id = 0
    snapshot = decode(raw)
    token = ext.get(selection.SNAPSHOT_KEY)
    if (
        not isinstance(snapshot, dict)
        or not isinstance(token, dict)
        or snapshot.get("generation") != token.get("generation")
    ):
        snapshot = None
    return {
        columns.knowledge_projection: json.dumps(
            {
                "state": "pending",
                "generation": uuid4().hex,
                "snapshot": snapshot,
                "deployment_id": deployment_id,
                "avernet_tenant": tenant,
            }
        ),
        columns.knowledge_due: func.now(),
        columns.knowledge_deployment_id: deployment_id,
    }


def complete_restore(
    db,
    *,
    draft_publish_id,
    source_publish_id,
    operation_id,
    expected_attempt,
    final_result,
):
    """Complete native restore plus durable intent, without knowledge I/O.

    Revision is sampled under the publication lock. Subsequent draft edits bump
    it in their own transaction, so delayed delivery cannot overwrite them.
    """
    with db.transactional_orm_session() as session:
        draft = (
            session.query(Publish)
            .filter(Publish.id == draft_publish_id)
            .with_for_update()
            .one_or_none()
        )
        if draft is None:
            return False
        operation, params = selection._locked_restore_operation(
            session,
            draft,
            source_publish_id=source_publish_id,
            operation_id=operation_id,
            expected_attempt=expected_attempt,
        )
        if operation.state == PublishOperationState.COMPLETED:
            return True
        selection._require_restore_deadline(params)
        previous = (
            session.query(Publish)
            .filter(
                Publish.id == source_publish_id,
                Publish.source_bot_pk == draft.source_bot_pk,
                Publish.env == draft.env,
                Publish.version == params["source_version"],
            )
            .one_or_none()
        )
        if previous is None or draft.last_pub_id != previous.id:
            raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
        raw = (
            session.query(columns.knowledge_snapshot)
            .filter(Publish.id == previous.id)
            .scalar()
        )
        revision, tenant = (
            session.query(columns.knowledge_revision, columns.knowledge_tenant)
            .filter(Publish.id == draft.id)
            .with_for_update()
            .one()
        )
        revision = revision or 0
        session.query(Publish).filter(Publish.id == draft.id).update(
            {
                columns.knowledge_restore: json.dumps(
                    {
                        "state": "pending",
                        "generation": uuid4().hex,
                        "snapshot": decode(raw),
                        "source_publish_id": previous.id,
                        "operation_id": operation.id,
                        "revision": revision,
                        "avernet_tenant": tenant,
                    }
                ),
                columns.knowledge_due: func.now(),
            },
            synchronize_session=False,
        )
        operation.result = json.dumps(final_result, ensure_ascii=False)
        operation.state = PublishOperationState.COMPLETED.value
        operation.gmt_modified = func.now()
        session.flush()
        return True


def _validated_snapshot(publish_id, snapshot, source):
    return selection.validate_snapshot(
        SimpleNamespace(
            id=publish_id, ext=json.dumps({selection.SNAPSHOT_KEY: snapshot})
        ),
        source,
    )


def _online(session, publish, source, journal):
    if journal.get("deployment_id", 0) <= 0:
        return {**journal, "state": "unknown", "error": "KNOWLEDGE_DEPLOYMENT_UNKNOWN"}
    newest = (
        session.query(Publish.id)
        .filter(
            Publish.source_bot_pk == source.id,
            Publish.env == source.env,
            columns.knowledge_deployment_id.is_not(None),
        )
        .order_by(columns.knowledge_deployment_id.desc(), Publish.id.desc())
        # A locking read observes current committed intents even if an earlier
        # SELECT established a MySQL REPEATABLE READ snapshot before Bot lock.
        .with_for_update()
        .first()
    )
    if newest is None or newest[0] != publish.id:
        return {**journal, "state": "superseded"}
    try:
        snapshot = _validated_snapshot(publish.id, journal.get("snapshot"), source)
        selection.replace_online(session, source, snapshot)
    except KnowledgeError as exc:
        logger.warning(
            "Knowledge online projection unknown: publish=%s code=%s",
            publish.id,
            exc.code,
        )
        # An unknown current version must not inherit the preceding version's
        # access. Clearing and recording UNKNOWN are one transaction. Database
        # failures escape to retry with the pending intent still intact.
        selection._bindings(session, source).filter(
            selection.Binding.online_enabled.is_(True)
        ).update({selection.Binding.online_enabled: False}, synchronize_session=False)
        return {**journal, "state": "unknown", "error": exc.code}
    return {**journal, "state": "applied"}


def _restore(session, publish, source, journal, revision):
    latest = (
        session.query(Publish.id)
        .filter(
            Publish.source_bot_pk == source.id,
            Publish.env == source.env,
        )
        .order_by(Publish.version.desc(), Publish.id.desc())
        .with_for_update()
        .first()
    )
    if (
        publish.status != PublishStatus.DRAFT
        or latest[0] != publish.id
        or revision != journal["revision"]
    ):
        return {**journal, "state": "superseded"}
    operation = (
        session.query(Operation)
        .filter(Operation.id == journal["operation_id"])
        .with_for_update()
        .one_or_none()
    )
    if operation is None or operation.state != PublishOperationState.COMPLETED:
        return {**journal, "state": "superseded"}
    latest_operation = (
        session.query(Operation.id)
        .filter(
            Operation.publish_id == publish.id,
            Operation.operation_kind == "draft_restore",
            Operation.stage == "draft",
            Operation.env == publish.env,
        )
        .order_by(Operation.attempt.desc(), Operation.id.desc())
        .with_for_update()
        .first()
    )
    if latest_operation is None or latest_operation[0] != operation.id:
        return {**journal, "state": "superseded"}
    try:
        snapshot = _validated_snapshot(
            journal["source_publish_id"], journal.get("snapshot"), source
        )
        ids = set(snapshot["enabled_kb_ids"])
        rows = (
            selection._bindings(session, source)
            .filter(
                (selection.Binding.enabled.is_(True))
                | (selection.Binding.kb_id.in_(ids))
            )
            .with_for_update()
            .limit(201)
            .all()
        )
        if len(rows) > 200 or not ids.issubset({r.kb_id for r in rows}):
            raise KnowledgeError("KNOWLEDGE_SNAPSHOT_BINDING_MISSING")
    except KnowledgeError as exc:
        logger.warning(
            "Knowledge restore projection unknown: publish=%s code=%s",
            publish.id,
            exc.code,
        )
        return {**journal, "state": "unknown", "error": exc.code}
    for row in rows:
        value = row.kb_id in ids
        if row.enabled != value:
            row.enabled = value
            row.revision += 1
            row.updated_by = "publish_restore"
    session.query(Publish).filter(Publish.id == publish.id).update(
        {
            columns.knowledge_revision: revision + 1,
        },
        synchronize_session=False,
    )
    return {**journal, "state": "applied"}


def apply(db, publish_id, *, lease_until=None):
    """One atomic attempt. Failed persistence raises; it is never acknowledged."""
    with db.transactional_orm_session() as session:
        publish = session.query(Publish).filter(Publish.id == publish_id).one_or_none()
        if publish is None:
            return False
        source = selection._locked_source(session, publish)
        raw_online, raw_restore, due, revision = (
            session.query(
                columns.knowledge_projection,
                columns.knowledge_restore,
                columns.knowledge_due,
                columns.knowledge_revision,
            )
            .filter(Publish.id == publish.id)
            .with_for_update()
            .one()
        )
        if lease_until is not None:
            if due != lease_until:
                return False
        elif due is None or due > session.query(func.now()).scalar():
            return False
        online, restore = decode(raw_online), decode(raw_restore)
        values = {columns.knowledge_due: None}
        for column, raw in (
            (columns.knowledge_projection, raw_online),
            (columns.knowledge_restore, raw_restore),
        ):
            if _invalid_journal(raw):
                values[column] = json.dumps(
                    {"state": "unknown", "error": "KNOWLEDGE_JOURNAL_INVALID"}
                )
        if (
            online
            and not _invalid_journal(raw_online)
            and online.get("state") == "pending"
        ):
            values[columns.knowledge_projection] = json.dumps(
                _online(session, publish, source, online)
            )
        if (
            restore
            and not _invalid_journal(raw_restore)
            and restore.get("state") == "pending"
        ):
            values[columns.knowledge_restore] = json.dumps(
                _restore(session, publish, source, restore, revision or 0)
            )
        session.query(Publish).filter(Publish.id == publish.id).update(
            values, synchronize_session=False
        )
        session.flush()
        return True


def _claim(db, publish_id, online, restore):
    """One-statement lease before the Bot lock; crashes reappear when due.

    Reuse the indexed due time, with database time as the clock authority. The
    journal and lease CAS prevents a delayed candidate from claiming new work.
    No database lock is retained between claiming and applying.
    """
    with db.orm_session() as session:
        until = session.query(func.now()).scalar() + timedelta(seconds=CLAIM_SECONDS)
        query = session.query(Publish).filter(
            Publish.id == publish_id,
            Publish.env == get_current_env(),
            columns.knowledge_due <= func.now(),
            columns.knowledge_projection == online
            if online is not None
            else columns.knowledge_projection.is_(None),
            columns.knowledge_restore == restore
            if restore is not None
            else columns.knowledge_restore.is_(None),
        )
        affected = query.update(
            {columns.knowledge_due: until}, synchronize_session=False
        )
    return until if affected else None


def run_batch(db, limit=BATCH_SIZE):
    """Indexed due-only scan, bounded per tick; DB locks provide single winner.

    No historical/terminal rescan and no external I/O. A failed row is deferred
    so it cannot starve the remaining candidates. The next process rediscovers
    every unacknowledged intent without needing an enqueue callback.
    """
    with db.orm_session() as session:
        rows = (
            session.query(
                Publish.id, columns.knowledge_projection, columns.knowledge_restore
            )
            .filter(
                Publish.env == get_current_env(),
                columns.knowledge_due <= func.now(),
            )
            .order_by(columns.knowledge_due, Publish.id)
            .limit(limit)
            .all()
        )
    applied = 0
    for publish_id, online, restore in rows:
        lease_until = _claim(db, publish_id, online, restore)
        if lease_until is None:
            continue
        try:
            pending = next(
                (
                    j
                    for raw in (online, restore)
                    if (j := decode(raw))
                    and not _invalid_journal(raw)
                    and j.get("state") == "pending"
                ),
                {},
            )
            tenant = pending.get("avernet_tenant")
            if not isinstance(tenant, str) or not tenant:
                raise KnowledgeError("KNOWLEDGE_TENANT_UNKNOWN")
            # Same carrier as existing durable tenant-aware tasks. The intent
            # was persisted by the authenticated native transition; the Bot
            # guard and captured scope are still checked inside apply().
            with avernet_tenant_scope(tenant):
                applied += bool(apply(db, publish_id, lease_until=lease_until))
        except Exception as exc:
            logger.warning(
                "Knowledge projection retry: publish=%s error=%s",
                publish_id,
                type(exc).__name__,
            )
            with db.orm_session() as session:
                # A newer native completion may have replaced either journal.
                # Never delay that new work based on an older failed delivery.
                query = session.query(Publish).filter(
                    Publish.id == publish_id,
                    columns.knowledge_due == lease_until,
                )
                query = query.filter(
                    columns.knowledge_projection == online
                    if online is not None
                    else columns.knowledge_projection.is_(None)
                )
                query = query.filter(
                    columns.knowledge_restore == restore
                    if restore is not None
                    else columns.knowledge_restore.is_(None)
                )
                retry_at = (
                    func.datetime(func.now(), text(f"'+{RETRY_SECONDS} seconds'"))
                    if session.get_bind().dialect.name == "sqlite"
                    else func.date_add(
                        func.now(), text(f"INTERVAL {RETRY_SECONDS} SECOND")
                    )
                )
                values = {columns.knowledge_due: None}
                for column, raw in (
                    (columns.knowledge_projection, online),
                    (columns.knowledge_restore, restore),
                ):
                    journal = decode(raw)
                    if _invalid_journal(raw):
                        values[column] = json.dumps(
                            {"state": "unknown", "error": "KNOWLEDGE_JOURNAL_INVALID"}
                        )
                    elif journal and journal.get("state") == "pending":
                        attempts = journal.get("attempts", 0) + 1
                        state = "failed" if attempts >= MAX_ATTEMPTS else "pending"
                        if state == "pending":
                            values[columns.knowledge_due] = retry_at
                        values[column] = json.dumps(
                            {
                                **journal,
                                "state": state,
                                "last_error": type(exc).__name__,
                                "attempts": attempts,
                            }
                        )
                query.update(values, synchronize_session=False)
    return applied


def retry_failed(db, publish_id):
    """Explicit operator recovery after repairing the cause; never recaptures.

    Scope is the configured tenant/environment. Unknown snapshots cannot be
    repaired by retry: publish again with a new capture instead.
    """
    with db.transactional_orm_session() as session:
        row = (
            session.query(Publish)
            .filter(
                Publish.id == publish_id,
                Publish.env == get_current_env(),
            )
            .one_or_none()
        )
        if row is None:
            return False
        selection._locked_source(session, row)
        raw_online, raw_restore = (
            session.query(columns.knowledge_projection, columns.knowledge_restore)
            .filter(Publish.id == row.id)
            .with_for_update()
            .one()
        )
        values = {}
        for column, raw in (
            (columns.knowledge_projection, raw_online),
            (columns.knowledge_restore, raw_restore),
        ):
            journal = decode(raw)
            if journal and journal.get("state") == "failed":
                values[column] = json.dumps(
                    {**journal, "state": "pending", "attempts": 0}
                )
        if not values:
            return False
        values[columns.knowledge_due] = func.now()
        session.query(Publish).filter(Publish.id == row.id).update(
            values, synchronize_session=False
        )
        return True
