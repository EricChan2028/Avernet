"""Bounded build admission; immutable artifact PUTs never hold Bot/DB locks."""

from uuid import uuid4

from agentclaw.community.core.repository.implementations.publishing import knowledge_selection as selection
from agentclaw.community.core.service_bot.repository.config_artifact_offload import ARTIFACT_KEY
from agentclaw.community.core.service_bot.repository.models import BotPublishModel as Publish
from agentclaw.community.utils.env_utils import get_current_env


def freeze_build(session_factory, offload, publish_id, *, incoming_ext=None,
                 expected_json=None, compare_ext=False):
    # Re-capture only when a remote upload races an edit. Persistent contention
    # returns the native CAS miss; the caller may retry without a deployment.
    for _attempt in range(3):
        generation = uuid4().hex
        with session_factory(publish_id) as session:
            candidate = selection.prepare_build(session, publish_id, generation, incoming_ext)
            if not candidate:
                return False
            publish, ext = candidate
            before = publish.ext
            if compare_ext and before != expected_json:
                return False
            if ARTIFACT_KEY in ext:
                encoded, pending = offload.prepare(ext, publish_id, get_current_env())
                selection.validate_encoded_ext(encoded)
            else:
                # Stored markers must survive without fetching their objects.
                encoded, pending = selection.encode_ext(ext), None
            if pending is None:
                selection.commit_build(session, publish, encoded)
                return True
        offload.upload(pending)
        missing = False
        with session_factory(publish_id) as session:
            current = selection.prepare_build(session, publish_id, generation, incoming_ext)
            if current:
                publish, current_ext = current
                if publish.ext == before and current_ext == ext:
                    selection.commit_build(session, publish, encoded)
                    return True
                if compare_ext and publish.ext != expected_json:
                    return False
            else:
                missing = session.query(Publish.id).filter(Publish.id == publish_id).first() is None
        if missing:
            # Delete may have swept its prefix while the PUT was still pending.
            offload.cleanup(offload.prefix(get_current_env(), publish_id))
        if not current:
            return False
    return False
