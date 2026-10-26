"""Shared Group and Session business steps, independent of finalization policy.

Callers retain mutable progress on every exception, including socket handoff and
unknown creates. These steps never delete resources, quiesce executions, release
leases or qualify a Run. Legacy and retained executors own those decisions.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ledger import Ledger
from .observation_gate import aggregate_evidence
from .profile import ConfigError


@dataclass
class ControlProgress:
    group_handle: str = ""
    initial_session_handle: str = ""
    group_id: str = ""
    session_handle: str = ""
    session_id: str = ""
    ws_open: bool = False
    ws_attempted: bool = False
    evidence_status: str = "INCOMPLETE"


class PilotError(RuntimeError):
    pass

def _group_id(data: dict | None) -> str:
    if not isinstance(data, dict):
        raise PilotError("group create returned no object")
    value = data.get("group_id") or data.get("id")
    if not isinstance(value, str) or not value:
        raise PilotError("group create returned no group ID")
    return value

def _assert_group(data: dict | None, group_id: str) -> None:
    if not isinstance(data, dict):
        raise PilotError("group read returned no object")
    returned = data.get("group_id") or data.get("id")
    if returned != group_id:
        raise PilotError("group response returned a different group ID")

def _observe_initial_session(ledger: Ledger, handle: str, data: dict) -> None:
    initial_id = data.get("initial_session_id")
    try:
        ledger.observed(handle, initial_id, origin="created")
    except ConfigError:
        raise PilotError("normal group creation did not return a valid initial Session ID") from None

def _session_id(data: dict | None) -> str:
    if not isinstance(data, dict):
        raise PilotError("session create returned no object")
    value = data.get("session_id") or data.get("id")
    if not isinstance(value, str) or not value:
        raise PilotError("session create returned no session ID")
    return value

def _assert_session(data: dict | None, session_id: str) -> None:
    if not isinstance(data, dict):
        raise PilotError("session response returned no object")
    returned = data.get("session_id") or data.get("id")
    if returned != session_id:
        raise PilotError("session response returned a different session ID")


def group_control_steps(profile: dict, run_id: str, case_id: str, *, ledger: Ledger, adapter,
                        observer, control: ControlProgress, evidence, assertions: list[dict],
                        semantic_evidence: list[dict], assertion) -> None:
    discovery = adapter.preflight()
    evidence.extend(discovery.get("requests", []))
    if discovery.get("status") != "DISCOVERY_PASSED":
        raise PilotError("read-only preflight did not pass")
    assertion("preflight", True)

    group_request_id = f"e2e-create-{run_id}"
    control.group_handle, control.initial_session_handle = ledger.group_creation_intent(case_id, 1, group_request_id)
    driver = profile["actors"]["driver"]["bot_id"]
    member = profile["actors"]["member"]["bot_id"]
    data, record = adapter.group_create({
        "group_kind": "normal", "name": f"E2E {run_id}",
        "context": f"BCS E2E pilot {case_id}", "driver_bot_uuid": driver,
        "participants": [
            {"actor_id": driver, "role": "driver"},
            {"actor_id": member, "role": "consultant"},
        ],
        "collaboration": {"strategy": "chat"},
    })
    evidence.append(record)
    control.group_id = _group_id(data)
    ledger.observed(control.group_handle, control.group_id, origin="created",
                     actual_request_id=record.get("submitted_request_id"))
    _observe_initial_session(ledger, control.initial_session_handle, data)
    assertion("owner_matches", data.get("originator_actor_id") == "human_" + profile["human"]["user_id"])
    participants = data.get("participants", []) if isinstance(data, dict) else []
    participant_ids = {item.get("actor_id") for item in participants if isinstance(item, dict)}
    roles = {item.get("actor_id"): item.get("role") for item in participants if isinstance(item, dict)}
    assertion("driver_and_members_match", {driver, member} <= participant_ids
              and data.get("driver_bot_uuid") == driver
              and roles.get(driver) == "driver" and roles.get(member) == "consultant")

    data, record = adapter.group_read(control.group_id)
    evidence.append(record)
    _assert_group(data, control.group_id)
    assertion("group_read", True)

    data, record = adapter.group_update(control.group_id, {"name": f"E2E {run_id} updated"})
    evidence.append(record)
    _assert_group(data, control.group_id)
    assertion("group_updated", True)

    data, record = adapter.group_read(control.group_id)
    evidence.append(record)
    _assert_group(data, control.group_id)
    assertion("update_persisted", data.get("name") == f"E2E {run_id} updated")
    control.evidence_status, observation = observer.observe("group.persisted", control.group_id)
    semantic_evidence.append(observation)
    ob_rows = observation.get("rows", []) if control.evidence_status in {"PASS", "LOCAL_SURROGATE"} else []
    assertions.append({"id": f"{case_id}.ob_group_matches",
                       "status": "PASS" if len(ob_rows) == 1 and ob_rows[0].get("group_id") == control.group_id
                       and ob_rows[0].get("driver_bot") == driver
                       and ob_rows[0].get("originator") == "human_" + profile["human"]["user_id"]
                       and ob_rows[0].get("status") == "active" else "BLOCKED",
                       "detail": "persisted group projection checked through the admitted observer"})


def session_control_steps(profile: dict, run_id: str, case_id: str, *, ledger: Ledger, adapter,
                        observer, control: ControlProgress, evidence, assertions: list[dict],
                        semantic_evidence: list[dict], assertion) -> None:
    discovery = adapter.preflight()
    evidence.extend(discovery.get("requests", []))
    if discovery.get("status") != "DISCOVERY_PASSED":
        raise PilotError("read-only preflight did not pass")
    assertion("preflight", True)

    control.group_handle, control.initial_session_handle = ledger.group_creation_intent(case_id, 1, f"e2e-pre02-group-{run_id}")
    driver = profile["actors"]["driver"]["bot_id"]
    member = profile["actors"]["member"]["bot_id"]
    data, record = adapter.group_create({
        "group_kind": "normal", "name": f"E2E {run_id} session",
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

    control.session_handle = ledger.intent(case_id, 1, "session", f"e2e-pre02-session-{run_id}", control.group_handle)
    data, record = adapter.session_create(control.group_id, {
        "title": f"E2E {run_id} session",
        "acting_bot_id": "human_" + profile["human"]["user_id"],
        "input": {"query": "Validate the Session connection lifecycle"},
    })
    evidence.append(record)
    control.session_id = _session_id(data)
    ledger.observed(control.session_handle, control.session_id, origin="created",
                     actual_request_id=record.get("submitted_request_id"))
    assertion("session_created", True)

    data, record = adapter.session_read(control.session_id)
    evidence.append(record)
    _assert_session(data, control.session_id)
    assertion("session_binding_matches", data.get("group_id") == control.group_id)

    data, record = adapter.session_update(control.session_id, {"title": f"E2E {run_id} session updated"})
    evidence.append(record)
    _assert_session(data, control.session_id)
    assertion("title_updated", data.get("title") == f"E2E {run_id} session updated")

    data, record = adapter.session_read(control.session_id)
    evidence.append(record)
    _assert_session(data, control.session_id)
    assertion("session_parent_matches", data.get("group_id") == control.group_id)
    assertion("title_persisted", data.get("title") == f"E2E {run_id} session updated")
    group_evidence_status, group_observation = observer.observe("group.persisted", control.group_id)
    session_evidence_status, session_observation = observer.observe("session.persisted", control.session_id)
    semantic_evidence.extend([group_observation, session_observation])
    control.evidence_status = aggregate_evidence((group_evidence_status, session_evidence_status))
    session_rows = session_observation.get("rows", []) if session_evidence_status in {"PASS", "LOCAL_SURROGATE"} else []
    assertions.append({"id": f"{case_id}.ob_session_matches",
                       "status": "PASS" if len(session_rows) == 1
                       and session_rows[0].get("session_id") == control.session_id
                       and session_rows[0].get("group_id") == control.group_id
                       and session_rows[0].get("session_title") == f"E2E {run_id} session updated"
                       and session_rows[0].get("status") == "running" else "BLOCKED",
                       "detail": "persisted session projection checked through the admitted observer"})

    data, record = adapter.session_messages(control.session_id)
    evidence.append(record)
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        raise PilotError("session messages response has invalid shape")
    assertion("messages_read", True)

    data, record = adapter.session_token(control.session_id)
    evidence.append(record)
    token = (data or {}).get("token") or (data or {}).get("connection_token")
    if not isinstance(token, str) or not token:
        raise PilotError("session token response returned no token")
    assertion("connection_token_issued", True)

    control.ws_attempted = True
    data, record = adapter.session_ws_connect(control.group_id, control.session_id, token)
    # The adapter has handed us a socket even if evidence/shape validation
    # fails next. Record ownership before either can raise.
    control.ws_open = True
    evidence.append(record)
    if (not isinstance(data, dict) or data.get("upgraded") is not True
            or data.get("connected") is not True
            or data.get("group_id") != control.group_id
            or data.get("session_id") != control.session_id):
        raise PilotError("session WebSocket did not complete the bound connect handshake")
    assertion("websocket_upgrade", data.get("upgraded") is True and data.get("connected") is True)



