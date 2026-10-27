"""Bounded capture and selection primitives for the independent projector.

Only admission capture runs synchronously, with explicit UNKNOWN fallback owned
by the publication repository. All binding promotion/restoration runs in the
projector's transaction; none runs in native deployment completion.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.bot_knowledge.repository.store import resolve_bot
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ARTIFACT_KEY,
    ARTIFACT_OSS_MARKER,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publish,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationModel as Operation,
)
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgeError,
    require_knowledge_id,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import func

SNAPSHOT_KEY = "knowledge_selection_snapshot"
_ROLLBACK_BASE_KEY = "knowledge_selection_rollback_base"
_MAX_SELECTION = 100
_MAX_EXT_BYTES = 60000


def _locked_source(session, publish):
    source = (
        session.query(BotModel)
        .filter(
            BotModel.id == publish.source_bot_pk,
            BotModel.bot_id == publish.source_bot_id,
            BotModel.env == publish.env,
            BotModel.bot_type == "service",
            BotModel.is_delete == 0,
        )
        .one_or_none()
    )
    if source is None or publish.env != get_current_env():
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    source = resolve_bot(
        session,
        BotKnowledgeScope(source.bot_id, source.owner_id, source.env),
        lock=True,
    )
    session.refresh(publish, with_for_update=True)
    if (
        source.id != publish.source_bot_pk
        or source.bot_id != publish.source_bot_id
        or source.env != publish.env
        or source.bot_type != "service"
        or source.is_delete
    ):
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    return source


def _bindings(session, bot):
    return session.query(Binding).filter(
        Binding.bot_pk == bot.id,
        Binding.env == bot.env,
        Binding.space_id == bot.space_id,
        Binding.avernet_tenant == bot.avernet_tenant,
    )


def _ext(row):
    return json.loads(row.ext) if row.ext else {}


def encode_ext(ext):
    # Match offloader.prepare's persisted ordering without resolving or dropping
    # an existing OSS marker. Otherwise the very next exact ext CAS would miss.
    artifact_keys = (ARTIFACT_KEY, ARTIFACT_OSS_MARKER)
    canonical = {key: value for key, value in ext.items() if key not in artifact_keys}
    for key in artifact_keys:
        if key in ext:
            canonical[key] = ext[key]
    encoded = json.dumps(canonical, ensure_ascii=False)
    return encoded


def validate_encoded_ext(encoded):
    if encoded is None or len(encoded.encode("utf-8")) <= _MAX_EXT_BYTES:
        return
    ext = json.loads(encoded)
    if SNAPSHOT_KEY in ext:
        raise KnowledgeError("KNOWLEDGE_PUBLISH_EXT_TOO_LARGE")


def validate_snapshot(publish, source):
    snapshot = _ext(publish).get(SNAPSHOT_KEY)
    if snapshot is None:
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_MISSING")
    expected = {
        "schema_version": 1,
        "source_bot_pk": source.id,
        "owner_id": source.owner_id,
        "space_id": source.space_id,
        "env": source.env,
        "avernet_tenant": source.avernet_tenant,
        "publish_id": publish.id,
    }
    if not isinstance(snapshot, dict) or any(
        type(snapshot.get(k)) is not type(v) or snapshot.get(k) != v
        for k, v in expected.items()
    ):
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_SCOPE_MISMATCH")
    ids = snapshot.get("enabled_kb_ids")
    generation = snapshot.get("generation")
    if (
        not isinstance(generation, str)
        or len(generation) != 32
        or not isinstance(ids, list)
        or len(ids) > _MAX_SELECTION
        or any(not isinstance(kb, str) for kb in ids)
        or len(set(ids)) != len(ids)
        or (source.space_id is None and ids)
    ):
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_INVALID")
    try:
        for kb in ids:
            require_knowledge_id(kb, "kb_id")
        int(generation, 16)
    except ValueError as exc:
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_INVALID") from exc
    return snapshot


def freeze_build(session, publish_id):
    publish = session.query(Publish).filter(Publish.id == publish_id).one_or_none()
    if publish is None or publish.status != PublishStatus.DRAFT:
        return False
    source = _locked_source(session, publish)
    if publish.status != PublishStatus.DRAFT:
        return False
    ids = [
        r[0]
        for r in _bindings(session, source)
        .with_entities(Binding.kb_id)
        .filter(Binding.enabled.is_(True))
        .order_by(Binding.kb_id)
        .limit(_MAX_SELECTION + 1)
        .all()
    ]
    if len(ids) > _MAX_SELECTION:
        raise KnowledgeError("KNOWLEDGE_LIMIT_EXCEEDED")
    ext = _ext(publish)
    ext.pop(_ROLLBACK_BASE_KEY, None)
    ext[SNAPSHOT_KEY] = {
        "schema_version": 1,
        "source_bot_pk": source.id,
        "owner_id": source.owner_id,
        "space_id": source.space_id,
        "env": source.env,
        "avernet_tenant": source.avernet_tenant,
        "publish_id": publish.id,
        "generation": uuid4().hex,
        "enabled_kb_ids": ids,
    }
    publish.ext = encode_ext(ext)
    validate_encoded_ext(publish.ext)
    publish.status = PublishStatus.BUILDING
    publish.gmt_modified = func.now()
    session.flush()
    session.query(Publish).filter(Publish.id == publish.id).update(
        {
            Publish.__table__.c.knowledge_snapshot: json.dumps(ext[SNAPSHOT_KEY]),
            Publish.__table__.c.knowledge_tenant: source.avernet_tenant,
        },
        synchronize_session=False,
    )
    return True


def freeze_unknown(session, publish_id, error):
    """Admit a fresh native attempt after capture rollback, without binding I/O.

    Rotate the small ext token even when capture failed, so a previous build
    cannot adopt the new BUILDING state. The full failed capture lives only in
    the journal; it never borrows enabled IDs or tenant from the later request.
    """
    publish = (
        session.query(Publish)
        .filter(Publish.id == publish_id)
        .with_for_update()
        .one_or_none()
    )
    if publish is None or publish.status != PublishStatus.DRAFT:
        return False
    tenant = (
        session.query(Publish.__table__.c.knowledge_tenant)
        .filter(Publish.id == publish_id)
        .with_for_update()
        .scalar()
    )
    generation = uuid4().hex
    ext = _ext(publish)
    had_token = SNAPSHOT_KEY in ext
    ext[SNAPSHOT_KEY] = {"generation": generation}
    encoded = encode_ext(ext)
    if not had_token and len(encoded.encode("utf-8")) > 65535:
        # Optional metadata cannot overflow a previously valid native TEXT.
        # There is no preceding admitted token to rotate; the immutable failed
        # capture still fences all knowledge effects as UNKNOWN.
        encoded = publish.ext
    query = session.query(Publish).filter(
        Publish.id == publish_id, Publish.status == PublishStatus.DRAFT
    )
    affected = query.update(
        {
            Publish.status: PublishStatus.BUILDING,
            Publish.ext: encoded,
            Publish.gmt_modified: func.now(),
            Publish.__table__.c.knowledge_snapshot: json.dumps(
                {
                    "generation": generation,
                    "capture_error": error.code
                    if isinstance(error, KnowledgeError)
                    else type(error).__name__,
                    "avernet_tenant": tenant,
                }
            ),
        },
        synchronize_session=False,
    )
    return bool(affected)


def replace_online(session, source, snapshot):
    """Replace flags inside the independent projector/ack transaction."""
    if snapshot is None:
        return
    query = _bindings(session, source)
    # A disabled row remains durable, so historical frozen ids must still exist.
    ids = snapshot["enabled_kb_ids"]
    present = (
        {
            r[0]
            for r in query.with_entities(Binding.kb_id)
            .filter(Binding.kb_id.in_(ids))
            .with_for_update()
            .all()
        }
        if ids
        else set()
    )
    if not set(snapshot["enabled_kb_ids"]).issubset(present):
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_BINDING_MISSING")
    query.filter(Binding.online_enabled.is_(True), Binding.kb_id.not_in(ids)).update(
        {Binding.online_enabled: False},
        synchronize_session=False,
    )
    if ids:
        query.filter(Binding.online_enabled.is_(False), Binding.kb_id.in_(ids)).update(
            {Binding.online_enabled: True},
            synchronize_session=False,
        )


def _locked_restore_operation(
    session,
    draft,
    *,
    source_publish_id,
    operation_id,
    expected_attempt,
):
    operation = (
        session.query(Operation)
        .filter(Operation.id == operation_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        operation is None
        or operation.publish_id != draft.id
        or operation.operation_kind != str(PublishOperationKind.DRAFT_RESTORE)
        or operation.stage != "draft"
        or operation.env != draft.env
        or type(expected_attempt) is not int
        or expected_attempt <= 0
        or operation.attempt != expected_attempt
    ):
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_MISMATCH")
    params = json.loads(operation.params) if operation.params else {}
    if (
        not isinstance(params, dict)
        or type(params.get("source_publish_id")) is not int
        or params["source_publish_id"] != source_publish_id
        or type(params.get("source_version")) is not int
        or params["source_version"] <= 0
    ):
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_MISMATCH")
    # The ledger, not a marker left by a previous partial implementation, is
    # authoritative. Completed delivery must never replay the selection write.
    if operation.state == PublishOperationState.COMPLETED:
        return operation, params
    if operation.state not in (
        PublishOperationState.PENDING,
        PublishOperationState.ID_RECORDED,
    ):
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_TERMINAL")
    latest = (
        session.query(Operation)
        .filter(
            Operation.publish_id == draft.id,
            Operation.operation_kind == str(PublishOperationKind.DRAFT_RESTORE),
            Operation.stage == "draft",
            Operation.env == draft.env,
        )
        .order_by(Operation.attempt.desc(), Operation.id.desc())
        .with_for_update()
        .first()
    )
    if latest.id != operation.id:
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_SUPERSEDED")
    return operation, params


def _require_restore_deadline(params):
    raw = params.get("deadline_at")
    try:
        if not isinstance(raw, str) or not raw:
            raise ValueError("deadline_at is missing")
        deadline = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise KnowledgeError("KNOWLEDGE_RESTORE_DEADLINE_INVALID") from exc
    if datetime.now(tz=deadline.tzinfo) >= deadline:
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_EXPIRED")
