"""Shared Human Session-file business steps, independent of retention/cleanup.

No delete, stop, storage sweep or environment lifecycle occurs here. Mutable
progress records intents before operations so both finalization policies retain
exact ownership, including when creation or observation fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time

from .control_steps import ControlProgress, PilotError, _group_id, _session_id, _observe_initial_session
from .file_assertions import FileExpectation
from .profile import ConfigError


@dataclass
class FileProgress(ControlProgress):
    file_handle: str = ""
    file_id: str = ""
    share_token: str = field(default="", repr=False)  # In-memory capability; never evidence.


def file_control_steps(profile: dict, run_id: str, case_id: str, *, ledger, adapter,
                       observer, control: FileProgress, evidence, assertions: list,
                       semantic_evidence: list, assertion, expected_backend: str) -> None:
    if expected_backend not in ('local', 'baas'):
        raise PilotError('file Story requires an explicit supported storage backend')
    payload = f"bcs-e2e-pre03:{run_id}:small-file\n".encode()
    payload_hash = hashlib.sha256(payload).hexdigest()
    discovery = adapter.preflight()
    evidence.extend(discovery.get("requests", []))
    if discovery.get("status") != "DISCOVERY_PASSED":
        raise PilotError("read-only preflight did not pass")
    assertion("preflight", True)

    control.group_handle, control.initial_session_handle = ledger.group_creation_intent(case_id, 1, f"e2e-pre03-group-{run_id}")
    driver = profile["actors"]["driver"]["bot_id"]
    member = profile["actors"]["member"]["bot_id"]
    data, record = adapter.group_create({
        "group_kind": "normal", "name": f"E2E {run_id} file",
        "context": f"BCS E2E pilot {case_id}", "driver_bot_uuid": driver,
        "participants": [{"actor_id": driver, "role": "driver"},
                         {"actor_id": member, "role": "consultant"}],
        "collaboration": {"strategy": "chat"},
    })
    evidence.append(record)
    control.group_id = _group_id(data)
    ledger.observed(control.group_handle, control.group_id, origin="created",
                     actual_request_id=record.get("submitted_request_id"))
    _observe_initial_session(ledger, control.initial_session_handle, data)
    assertion("group_created", True)

    control.session_handle = ledger.intent(case_id, 1, "session", f"e2e-pre03-session-{run_id}", control.group_handle)
    data, record = adapter.session_create(control.group_id, {"title": f"E2E {run_id} file session",
                                                    "acting_bot_id": "human_" + profile["human"]["user_id"]})
    evidence.append(record)
    control.session_id = _session_id(data)
    ledger.observed(control.session_handle, control.session_id, origin="created",
                     actual_request_id=record.get("submitted_request_id"))
    assertion("session_created", True)

    control.file_handle = ledger.intent(case_id, 1, "file", f"e2e-pre03-file-{run_id}", control.session_handle)
    data, record = adapter.file_prepare(control.session_id, {
        "file_name": f"e2e-{run_id}.txt", "size": len(payload), "mime_type": "text/plain",
    })
    evidence.append(record)
    if not isinstance(data, dict) or not isinstance(data.get("file_id"), str):
        raise PilotError("file prepare returned no file ID")
    candidate_id = data["file_id"]
    try:
        ledger.observed(control.file_handle, candidate_id, origin="created",
                         actual_request_id=record.get("submitted_request_id"))
    except ConfigError:
        raise PilotError("file prepare returned an unjournalable resource ID") from None
    control.file_id = candidate_id
    expected_file = FileExpectation(
        file_id=control.file_id, session_id=control.session_id, file_name=f"e2e-{run_id}.txt", mime_type="text/plain",
        size=len(payload), sha256=payload_hash, owner_id="human_" + profile["human"]["user_id"],
        storage_backend=expected_backend,
    )
    assertion("file_prepare_id", True)

    data, record = adapter.file_upload(control.session_id, control.file_id, payload)
    evidence.append(record)
    if not isinstance(data, dict) or data.get("file_id") != control.file_id or data.get("upload_accepted") is not True:
        raise PilotError("file upload did not confirm byte acceptance")
    assertion("upload_accepted", True)

    metadata, record = adapter.file_complete(control.session_id, control.file_id)
    evidence.append(record)
    assertion("file_completed", expected_file.matches_metadata(metadata),
              "completion must return the exact Ready file metadata")

    listed, record = adapter.file_list(control.session_id)
    evidence.append(record)
    assertion("file_listed", expected_file.matches_page(listed),
              "file page must contain exactly one matching Ready file")

    metadata, record = adapter.file_metadata(control.session_id, control.file_id)
    evidence.append(record)
    assertion("file_metadata_matches", expected_file.matches_metadata(metadata))
    assertion("file_and_session_match", metadata["file_id"] == control.file_id
              and metadata["session_id"] == control.session_id)
    owner = metadata.get("owner")
    assertion("file_owner_matches", isinstance(owner, dict)
              and owner.get("actor_kind") == "human"
              and owner.get("actor_id") == "human_" + profile["human"]["user_id"])
    assertion("storage_backend_matches", metadata.get("storage_backend") == expected_backend,
              f"expected storage backend {expected_backend}")
    control.evidence_status, observation = observer.observe("file.persisted", control.file_id)
    if control.evidence_status in {"PASS", "LOCAL_SURROGATE"}:
        rows = observation.get("rows", [])
        if len(rows) != 1 or not expected_file.matches_persisted(rows[0]):
            control.evidence_status = "BLOCKED"
            observation = {**observation, "status": "MISMATCH"}
    semantic_evidence.append(observation)
    assertions.append({"id": f"{case_id}.ob_file_matches",
                       "status": "PASS" if control.evidence_status in {"PASS", "LOCAL_SURROGATE"} else "BLOCKED",
                       "detail": "persisted file projection checked through the admitted observer"})

    downloaded, record = adapter.file_download(control.session_id, control.file_id)
    evidence.append(record)
    assertion("download_bytes_match", downloaded == payload)

    share, record = adapter.file_share(control.session_id, control.file_id)
    evidence.append(record)
    token = (share or {}).get("share_token") if isinstance(share, dict) else None
    control.share_token = token if isinstance(token, str) else ""
    expires_at = (share or {}).get("expires_at") if isinstance(share, dict) else None
    share_url = (share or {}).get("share_url") if isinstance(share, dict) else None
    if not isinstance(token, str) or not token or not isinstance(share_url, str) or not share_url:
        raise PilotError("file share returned an incomplete share target")
    assertion("file_shared", type(expires_at) is int and expires_at > int(time.time()))

    shared_bytes, record = adapter.shared_file_download(token)
    evidence.append(record)
    assertion("share_bytes_match", shared_bytes == payload)
