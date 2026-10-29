"""Environment adapters used by pilot Stories.

The Story executor depends on this small contract instead of deciding whether
it is talking to a deployed Gateway/BCS or a local deterministic fake. The
local mock never opens a socket or pretends to validate a deployment.
"""
from __future__ import annotations

from typing import Protocol
from pathlib import Path
import hashlib
import time
import uuid

from .alisddbquery import AlisdDbQueryObserver
from .case_adapters import AdapterCall
from .edge_read_case import (
    FRIEND_CONNECTIONS_CASE_ID,
    FRIEND_CONNECTIONS_PUBLIC_CASE_ID,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_REQUESTS_PUBLIC_CASE_ID,
    edge_read_case_plans,
    edge_read_public_aliases,
)

from .remote_edge_search_adapter import RemoteEdgeBotSearchCandidatesAdapter
from .remote_network_adapters import (
    RemoteNetworkActorListAdapter,
    RemoteNetworkBotsMineAdapter,
    RemoteNetworkBotsPagedAdapter,
    RemoteNetworkActorSearchAdapter,
    RemoteNetworkActorStatusAdapter,
    RemoteNetworkBotsQueryAdapter,
    RemoteNetworkPlatformAssetAdapter,
)
from .remote_network_register_adapter import RemoteNetworkRegisterAdapter
from .remote_channel_cli_adapter import RemoteChannelCliAdapter
from .remote_webhook_subscription_adapter import RemoteWebhookSubscriptionAdapter
from .evidence import EvidenceError, LocalSemanticEvidenceProvider
from .execution_ledger import ExecutionLedger
from .file_transport import RemoteFileClient
from .preflight import check
from .profile import ConfigError
from .remote_effects import remote_effect_admission, require_group_effect_admission
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient
from .ws_transport import SessionWebSocketClient


class AdapterCapabilityError(RuntimeError):
    pass


class PilotAdapter(Protocol):
    def preflight(self) -> dict: ...
    def semantic_observer(self): ...
    def group_create(self, body: dict) -> tuple[dict | None, dict]: ...
    def group_read(self, group_id: str) -> tuple[dict | None, dict]: ...
    def group_update(self, group_id: str, body: dict) -> tuple[dict | None, dict]: ...
    def group_delete(self, group_id: str) -> tuple[dict | None, dict]: ...
    def group_verify_deleted(self, group_id: str) -> tuple[dict | None, dict]: ...
    def session_create(self, group_id: str, body: dict) -> tuple[dict | None, dict]: ...
    def session_read(self, session_id: str) -> tuple[dict | None, dict]: ...
    def session_update(self, session_id: str, body: dict) -> tuple[dict | None, dict]: ...
    def session_messages(self, session_id: str) -> tuple[dict | None, dict]: ...
    def session_token(self, session_id: str) -> tuple[dict | None, dict]: ...
    def session_ws_connect(self, group_id: str, session_id: str, token: str) -> tuple[dict | None, dict]: ...
    def session_ws_close(self, session_id: str) -> tuple[dict | None, dict]: ...
    def session_delete(self, session_id: str) -> tuple[dict | None, dict]: ...
    def session_verify_deleted(self, session_id: str) -> tuple[dict | None, dict]: ...
    def file_list(self, session_id: str) -> tuple[dict | None, dict]: ...
    def file_prepare(self, session_id: str, body: dict) -> tuple[dict | None, dict]: ...
    def file_upload(self, session_id: str, file_id: str, content: bytes) -> tuple[dict | None, dict]: ...
    def file_complete(self, session_id: str, file_id: str) -> tuple[dict | None, dict]: ...
    def file_metadata(self, session_id: str, file_id: str) -> tuple[dict | None, dict]: ...
    def file_download(self, session_id: str, file_id: str) -> tuple[bytes, dict]: ...
    def file_share(self, session_id: str, file_id: str) -> tuple[dict | None, dict]: ...
    def shared_file_download(self, token: str) -> tuple[bytes, dict]: ...
    def file_delete(self, session_id: str, file_id: str) -> tuple[dict | None, dict]: ...
    def file_verify_deleted(self, session_id: str, file_id: str) -> tuple[dict | None, dict]: ...
    def shared_file_verify_deleted(self, token: str) -> tuple[dict | None, dict]: ...


class RemotePilotAdapter:
    # Remote termination is intentionally unavailable until a reviewed product
    # execution contract is wired in; remote writes remain admission-blocked.
    termination_mode = "REMOTE_UNSUPPORTED"

    def __init__(self, profile: dict, *, observer_client=None, physical_binding_probe=None):
        self.profile = profile
        self.resource_review = remote_effect_admission(profile)["status"] == "ADMITTED"
        # Leave the class default untouched for unreviewed remote Profiles so
        # synthetic tests can explicitly force the older LOCAL_CONTROLLED path.
        if self.resource_review:
            self.termination_mode = "REVIEWED_REMOTE"
        self.observer_client = observer_client
        self.physical_binding_probe = physical_binding_probe
        self.read = ReadClient(profile)
        self.write = WriteClient(profile)
        self.websocket = SessionWebSocketClient(
            profile["entrypoint"]["origin"],
            timeout_seconds=profile["policy"]["timeout_seconds"],
            request_headers=({k: v for k, v in self.read.headers.items()
                              if k in {'Cookie', 'Origin', 'Referer'}}
                             if profile['entrypoint']['kind'] == 'gateway' else {}),
        )
        self.ws_connections: dict[str, object] = {}
        self.files = RemoteFileClient(profile)

    def verify_cleanup_scope(self, run_id: str, records: list[dict]) -> dict:
        """Reject remote cleanup unless the ledger and product agree exactly.

        This verifier is deliberately conservative: only live Subjects whose handle
        appears in the current run are considered owned. Any unknown file IDs or
        broken Group->Session->File chains must be reconciled in the ledger before
        cleanup is allowed.
        """
        if not self.resource_review:
            return {"status": "BLOCKED", "reason": "reviewed remote effect admission is required"}
        if not isinstance(run_id, str) or not run_id:
            return {"status": "BLOCKED", "reason": "run_id is missing"}
        if not isinstance(records, list):
            return {"status": "BLOCKED", "reason": "cleanup records are invalid"}
        required = {"handle", "case_id", "attempt", "kind", "parent", "resource_id", "origin", "state"}
        if any(not isinstance(item, dict) or not required.issubset(item) for item in records):
            return {"status": "BLOCKED", "reason": "cleanup records have missing fields"}
        resources = {item["handle"]: item for item in records if isinstance(item, dict)}
        live = [item for item in records if item.get("state") == "live"]
        # Validate the resource kind and parent chain before making product calls.
        for item in live:
            kind = item.get("kind")
            if kind not in {"group", "session", "file"} or item.get("origin") != "created":
                return {"status": "BLOCKED", "reason": "cleanup resource is not an owned live resource"}
            if not item.get("resource_id") or not item.get("handle"):
                return {"status": "BLOCKED", "reason": "cleanup resource ID is missing"}
            parent = item.get("parent")
            if kind == "group":
                if parent is not None:
                    return {"status": "BLOCKED", "reason": "group cleanup has an unexpected parent"}
            elif kind == "session":
                if parent not in resources or resources[parent].get("kind") != "group" or resources[parent].get("state") != "live":
                    return {"status": "BLOCKED", "reason": "session cleanup parent group is not live or bound"}
            elif kind == "file":
                if parent not in resources or resources[parent].get("kind") != "session" or resources[parent].get("state") != "live":
                    return {"status": "BLOCKED", "reason": "file cleanup parent session is not live or bound"}
        # Verify every live resource with its exact API record, and prove there are
        # no unrecorded live files under any live Session.
        for item in live:
            kind, resource_id = item["kind"], item["resource_id"]
            try:
                if kind == "group":
                    data, _ = self.group_read(resource_id)
                    if data.get("group_id") != resource_id:
                        return {"status": "BLOCKED", "reason": "group API identity mismatch"}
                elif kind == "session":
                    data, _ = self.session_read(resource_id)
                    parent = resources[item["parent"]]["resource_id"]
                    if data.get("session_id") != resource_id or data.get("group_id") != parent:
                        return {"status": "BLOCKED", "reason": "session API identity or parent mismatch"}
                    listed, _ = self.file_list(resource_id)
                    items = listed.get("items") if isinstance(listed, dict) else None
                    if not isinstance(items, list):
                        return {"status": "BLOCKED", "reason": "session file list is unreadable"}
                    projections: dict[str, dict] = {}
                    for value in items:
                        if not isinstance(value, dict) or not isinstance(value.get("file_id"), str) or not value["file_id"]:
                            return {"status": "BLOCKED", "reason": "session file identity is invalid"}
                        if value["file_id"] in projections:
                            return {"status": "BLOCKED", "reason": "session file identity is duplicated"}
                        projections[value["file_id"]] = value
                    expected_files = {r["resource_id"] for r in live
                                      if r["kind"] == "file" and r["parent"] == item["handle"] and r.get("resource_id")}
                    if set(projections) != expected_files:
                        return {"status": "BLOCKED", "reason": "session has unrecorded or missing live files",
                                "expected_file_ids": sorted(expected_files),
                                "actual_file_ids": sorted(projections)}
                    owners = {actor["bot_id"] for actor in self.profile["actors"].values()}
                    for file_id in expected_files:
                        projection = projections[file_id]
                        owner = projection.get("owner")
                        if (projection.get("session_id") != resource_id
                                or projection.get("status") != "ready"
                                or projection.get("storage_backend") != "baas"
                                or not isinstance(projection.get("file_name"), str)
                                or not projection["file_name"]
                                or type(projection.get("size")) is not int or projection["size"] <= 0
                                or not isinstance(owner, dict) or owner.get("actor_kind") != "bot"
                                or owner.get("actor_id") not in owners):
                            return {"status": "BLOCKED", "reason": "session file projection is invalid"}
                elif kind == "file":
                    # The exact Session file list is the authoritative identity
                    # projection; this branch intentionally avoids requiring a
                    # separate human-readable metadata route for Bot-owned files.
                    continue
            except Exception as error:
                return {"status": "BLOCKED", "reason": "cleanup scope product read failed",
                        "error": type(error).__name__}
        return {"status": "VERIFIED"}

    def preflight(self) -> dict:
        return check(self.read.profile, self.read)

    def semantic_observer(self):
        # The composition root must inject an approved client.  Do not spawn
        # mcporter implicitly from a business Story or from a profile string.
        if self.observer_client is None:
            return None
        if self.profile["observer"]["kind"] != "alisddbquery":
            return None
        return AlisdDbQueryObserver(
            client=self.observer_client,
            database=self.profile["observer"]["database"],
            environment=self.profile["environment"]["expected_bcs_env"],
            environment_binding_confirmed=self.profile["observer"]["environment_binding_confirmed"],
            physical_binding_probe=self.physical_binding_probe,
        )

    def group_create(self, body: dict):
        require_group_effect_admission(self.profile)
        return self.write.call("group.create", body=body, expected=(200, 201))

    def group_read(self, group_id: str):
        return self.write.call("group.read", group_id=group_id)

    def group_update(self, group_id: str, body: dict):
        return self.write.call("group.update", group_id=group_id, body=body)

    def group_delete(self, group_id: str):
        return self.write.call("group.delete", group_id=group_id, expected=(200,))

    def group_verify_deleted(self, group_id: str):
        return self.write.call("group.verify_deleted", group_id=group_id, expected=(404,))

    def session_create(self, group_id: str, body: dict):
        return self.write.call("session.create", group_id=group_id, body=body, expected=(200, 201))

    def session_read(self, session_id: str):
        return self.write.call("session.read", session_id=session_id)

    def session_update(self, session_id: str, body: dict):
        return self.write.call("session.update", session_id=session_id, body=body)

    def session_messages(self, session_id: str):
        return self.write.call("session.messages", session_id=session_id)

    def session_token(self, session_id: str):
        return self.write.call("session.token", session_id=session_id)

    def session_ws_connect(self, group_id: str, session_id: str, token: str):
        connection, record = self.websocket.connect(token, group_id, session_id)
        self.ws_connections[session_id] = connection
        record = {"operation": "session.ws.connect", "source": "remote-websocket", **record}
        return {"group_id": group_id, "session_id": session_id, "upgraded": True, "connected": True}, record

    def session_ws_close(self, session_id: str):
        connection = self.ws_connections.pop(session_id, None)
        if connection is None:
            raise AdapterCapabilityError("remote WebSocket connection is not open")
        try:
            connection.close()
        except OSError as error:
            raise AdapterCapabilityError(f"WebSocket close failed: {type(error).__name__}") from None
        return {"closed": True}, {
            "operation": "session.ws.close", "source": "remote-websocket",
            "method": "WS", "path": "/openapi/v1/collaboration/messages/ws",
            "protocol": "websocket", "close_code": 1000,
            "local_socket_closed": True, "request_id_echoed": False, "elapsed_ms": 0.0,
        }

    def _require_reviewed_remote_effect(self) -> None:
        if not self.resource_review:
            raise AdapterCapabilityError("remote execution review is not admitted")

    def close_production_channels(self, session_id: str) -> None:
        if self.termination_mode == "LOCAL_CONTROLLED":
            return
        self._require_reviewed_remote_effect()

    def quiesce_execution(self, session_id: str, *, reason: str = "reviewed remote completion") -> None:
        """Record that the reviewed remote effect-set is allowed to settle.

        The product exposes no universal cross-bot stop API. This callback is a
        one-shot boundary check for an already reviewed remote Session, not an
        effect-observer. The reconciliation command can independently persist
        exact session metadata, but this seam deliberately imposes no retry and
        never broadens the session scope.
        """
        if self.termination_mode == "LOCAL_CONTROLLED":
            return
        self._require_reviewed_remote_effect()

    def close_termination_channel(self, session_id: str) -> None:
        if self.termination_mode == "LOCAL_CONTROLLED":
            return
        self._require_reviewed_remote_effect()

    def session_delete(self, session_id: str):
        return self.write.call("session.delete", session_id=session_id, expected=(200,))

    def session_verify_deleted(self, session_id: str):
        return self.write.call("session.verify_deleted", session_id=session_id, expected=(404,))

    def file_list(self, session_id: str):
        return self.files.list(session_id)

    def file_prepare(self, session_id: str, body: dict):
        return self.files.prepare(session_id, body)

    def file_upload(self, session_id: str, file_id: str, content: bytes):
        return self.files.upload(session_id, file_id, content)

    def file_complete(self, session_id: str, file_id: str):
        return self.files.complete(session_id, file_id)

    def file_metadata(self, session_id: str, file_id: str):
        return self.files.metadata(session_id, file_id)

    def file_download(self, session_id: str, file_id: str):
        return self.files.download(session_id, file_id)

    def file_share(self, session_id: str, file_id: str):
        return self.files.share(session_id, file_id)

    def shared_file_download(self, token: str):
        return self.files.shared_download(token)

    def file_delete(self, session_id: str, file_id: str):
        return self.files.delete(session_id, file_id)

    def file_verify_deleted(self, session_id: str, file_id: str):
        return self.files.verify_deleted(session_id, file_id)

    def shared_file_verify_deleted(self, token: str):
        return self.files.verify_shared_deleted(token)


class RemoteEdgeReadAdapter:
    """Execute only declared Edge reads on public-v1 Singlebox/gateway adapters."""

    def __init__(self, profile: dict, case_id: str = FRIEND_REQUESTS_CASE_ID):
        aliases = edge_read_public_aliases()
        if (case_id not in {FRIEND_REQUESTS_CASE_ID, FRIEND_CONNECTIONS_CASE_ID}
                and case_id not in aliases):
            raise RunStateError("Remote Edge read adapter supports only declared Edge list Cases")
        if not isinstance(profile, dict):
            raise RunStateError("Remote Edge read adapter requires an admitted profile")
        execution_case_id = aliases.get(case_id, case_id)
        self.plan = next(row for row in edge_read_case_plans()
                         if row.case_id == execution_case_id)
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("Remote Edge read requires an admitted public-v1 Singlebox or gateway profile")
        self.operation = ("edge.friend_requests.read"
                           if execution_case_id == FRIEND_REQUESTS_CASE_ID
                           else "edge.friend_connections.read")
        binding_name = ("friend_requests.read"
                        if execution_case_id == FRIEND_REQUESTS_CASE_ID
                        else "friend_connections.read")
        try:
            self.binding = require_edge_case_binding(profile, binding_name)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = case_id
        self.execution_case_id = execution_case_id
        self.profile = profile
        self.caller_actor_id = self.binding["caller_actor_id"]
        self._attempted: set[str] = set()
        self.client = ReadClient(profile)

    def _expected_path(self, direction: str = "") -> str:
        if self.execution_case_id == FRIEND_CONNECTIONS_CASE_ID:
            return (self.binding["path"]
                    + "?actor_type=bot&actor_id="
                    + self.caller_actor_id
                    + "&page=1&page_size=100")
        if self.profile["environment"].get("tier") == "preprod":
            return (self.binding["path"] + "?actor_type=bot&actor_id="
                    + self.caller_actor_id + "&direction="
                    + direction + "&page=1&page_size=100")
        return self.binding["path"] + "?direction=" + direction

    def _read(self, requirement_id: str) -> AdapterCall:
        requirement = next((row for row in self.plan.requirements
                            if row.requirement_id == requirement_id), None)
        method = None if requirement is None else requirement.adapter_method
        if method is None or method in self._attempted:
            raise RunStateError("Remote Edge read requirement contract changed or method was repeated")
        direction = ("sent" if requirement.path.endswith("?direction=sent") else
                     "all" if requirement.path.endswith("?direction=all") else "received")
        expected_path = self._expected_path(
            "" if self.execution_case_id == FRIEND_CONNECTIONS_CASE_ID else direction)
        self._attempted.add(method)
        observation = (self.client.read(self.operation)
                       if self.execution_case_id == FRIEND_CONNECTIONS_CASE_ID
                       else self.client.read(self.operation, direction=direction))
        body, record = observation.data, observation.evidence
        if (record.get("path") != expected_path or record.get("method") != "GET"
                or record.get("http_status") != 200):
            raise RunStateError("Remote Edge read transport evidence differs from the fixed contract")
        if not isinstance(body, dict):
            raise RunStateError("Edge list response is not a public object envelope")
        items = body.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise RunStateError("Edge list response exceeds its bounded public contract")
        if self.execution_case_id == FRIEND_REQUESTS_CASE_ID:
            if (type(body.get("page")) is not int or type(body.get("page_size")) is not int
                    or type(body.get("total")) is not int or body["page"] < 0
                    or not 0 <= body["page_size"] <= 100 or body["total"] < 0):
                raise RunStateError("Friend-request list response does not satisfy the bounded public contract")
            data = {
                "direction": direction,
                "item_count": len(items),
                "page": body["page"],
                "page_size": body["page_size"],
                "total": body["total"],
            }
        else:
            if (type(body.get("page")) is not int or body.get("page") != 1
                    or type(body.get("page_size")) is not int or body.get("page_size") != 100
                    or type(body.get("total")) is not int or body["total"] < 0):
                raise RunStateError("Friend-connection list response does not satisfy the page contract")
            data = {
                "item_count": len(items),
                "page": body["page"],
                "page_size": body["page_size"],
                "total": body["total"],
            }
        evidence = {
            "transport": "public-v1-http",
            "operation": self.operation,
            "path": record["path"],
            "http_method": "GET",
            "http_status": record["http_status"],
            "identity": ("HUMAN_COOKIE_DRIVER_VIEW"
                          if self.binding["auth"] == "human_cookie"
                          else "AUTHENTICATED_DRIVER_BOT"),
            "caller_actor_id": self.caller_actor_id,
            "fixture_effect": "NONE",
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("Remote Edge read evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def read_friend_requests_received(self) -> AdapterCall:
        return self._read("EDGE-FRIEND-REQUESTS.received")

    def read_friend_requests_sent(self) -> AdapterCall:
        return self._read("EDGE-FRIEND-REQUESTS.sent")

    def read_friend_requests_all(self) -> AdapterCall:
        return self._read("EDGE-FRIEND-REQUESTS.all")

    def read_friend_connections_list(self) -> AdapterCall:
        return self._read("EDGE-FRIEND-CONNECTIONS.list")

    def read_friend_connections_list_repeat(self) -> AdapterCall:
        return self._read("EDGE-FRIEND-CONNECTIONS.list-repeat")


class LocalMockAdapter:
    """Deterministic local adapter; no HTTP, subprocess or product state."""

    termination_mode = "LOCAL_CONTROLLED"

    def __init__(self, profile: dict):
        self.profile = profile
        self._execution_ledger: ExecutionLedger | None = None
        self._effect_case_id: str | None = None
        self._effect_attempt: int | None = None
        self._session_effects: dict[str, list[str]] = {}
        self.effect_mode = "AUTO_COMPLETE"
        self._production_closed: set[str] = set()
        self._control_sessions: set[str] = set()
        self.groups: dict[str, dict] = {}
        self.deleted: set[str] = set()
        self.sessions: dict[str, dict] = {}
        self.deleted_sessions: set[str] = set()
        self.connected_sessions: set[str] = set()
        self.files: dict[str, dict] = {}
        self.deleted_files: set[str] = set()
        self.shared_files: dict[str, str] = {}

    def bind_execution_ledger(self, execution_ledger: ExecutionLedger, case_id: str, attempt: int) -> None:
        self._execution_ledger = execution_ledger
        self._effect_case_id = case_id
        self._effect_attempt = attempt

    def _record(self, operation: str, method: str, path: str, status: int,
                *, request_id: str | None = None) -> dict:
        return {
            "operation": operation, "method": method, "path": path,
            "submitted_request_id": request_id or f"local-mock-{uuid.uuid4().hex}",
            "request_id_echoed": True, "http_status": status, "elapsed_ms": 0.0,
            "source": "local-mock-adapter",
        }

    def _record_request(self, operation: str, method: str, path: str, status: int,
                       request_id: str) -> dict:
        """Record a request while remaining compatible with shaped test adapters.

        Some composition fakes override ``_record`` to model a remote boundary
        and intentionally keep the older four-argument signature.  The request
        ID is still the authoritative ID in the returned evidence; it is added
        after the boundary-specific recorder runs rather than forcing every
        adapter override to know the local optional keyword.
        """
        record = self._record(operation, method, path, status)
        record["submitted_request_id"] = request_id
        return record

    def _effect_request(self, request_id: str, suffix: str) -> str:
        return f"{request_id}.{suffix}"

    def _begin_session_effects(self, session_id: str, request_id: str, bot_ids: list[str]) -> None:
        if self._execution_ledger is None:
            return
        if self._effect_case_id is None or self._effect_attempt is None:
            raise RuntimeError("local execution effects require a bound Case")

        def admit(kind: str, effect_request: str, *, parent: str | None = None,
                  actor_id: str | None = None, effect_id: str) -> str:
            handle = self._execution_ledger.intent(
                self._effect_case_id, self._effect_attempt, kind, effect_request,
                parent=parent, actor_id=actor_id, session_id=session_id)
            self._execution_ledger.observe(handle, effect_id, state="ADMITTED",
                                           evidence_request_id=effect_request)
            self._execution_ledger.transition(handle, "QUEUED", reason=f"local {kind} admitted")
            return handle

        context = admit("context", request_id, effect_id=f"local-context-{session_id}")
        run = admit("run", self._effect_request(request_id, "run"), parent=context,
                     effect_id=f"local-run-{session_id}")
        handles = [context, run]
        for index, bot_id in enumerate(bot_ids):
            delivery_request = self._effect_request(request_id, f"delivery-{index}")
            handles.append(admit("delivery", delivery_request, parent=run, actor_id=bot_id,
                                 effect_id=f"local-delivery-{session_id}-{index}"))
        self._session_effects[session_id] = handles

    def _begin_subscription_effect(self, session_id: str, request_id: str) -> None:
        if self._execution_ledger is None:
            return
        handles = self._session_effects.get(session_id)
        if not handles:
            raise RuntimeError("WebSocket subscription has no exact Session effect binding")
        context = next((row["handle"] for row in self._execution_ledger.snapshot()
                        if row["handle"] == handles[0]), None)
        if context is None:
            raise RuntimeError("WebSocket subscription context is not bound")
        subscription = self._execution_ledger.intent(
            self._effect_case_id, self._effect_attempt, "subscription", request_id,
            parent=context, session_id=session_id)
        self._execution_ledger.observe(subscription, f"local-subscription-{session_id}",
                                       state="ADMITTED", evidence_request_id=request_id)
        self._execution_ledger.transition(subscription, "QUEUED", reason="local subscription admitted")
        handles.append(subscription)

    def close_production_channels(self, session_id: str) -> None:
        """Stop the controlled producer subscription, not its observation socket."""
        if session_id not in self.sessions:
            raise RuntimeError("local producer Session is unknown")
        self._production_closed.add(session_id)
        if self._execution_ledger is not None:
            for row in self._execution_ledger.ledger.execution_effects(session_id=session_id):
                if row["kind"] == "subscription" and row["state"] == "QUEUED":
                    self._execution_ledger.transition(row["handle"], "COMPLETED",
                        reason="local production subscription closed",
                        evidence_request_id=f"local-production-close-{session_id}")

    def close_termination_channel(self, session_id: str) -> None:
        if session_id in self.connected_sessions:
            raise RuntimeError("owned observation socket must be closed before resource cleanup")
        if self._execution_ledger is not None:
            self._execution_ledger.require_session_quiescent(session_id)

    def quiesce_execution(self, session_id: str, *, reason: str = "local controlled completion") -> None:
        if self._execution_ledger is None:
            return
        if session_id not in self._production_closed:
            raise RuntimeError("local producers must be stopped before effect observation")
        if session_id in self._control_sessions and session_id not in self.connected_sessions:
            raise RuntimeError("local observation channel was closed before terminal evidence")
        handles = self._session_effects.get(session_id, [])
        mode = self.effect_mode
        if mode not in {"AUTO_COMPLETE", "CANCEL_QUEUED", "CANCEL_ACKED_RUNNING",
                        "UNKNOWN_EFFECT", "LATE_CALLBACK"}:
            raise RuntimeError("unsupported local effect mode")
        for handle in handles:
            row = next(item for item in self._execution_ledger.snapshot() if item["handle"] == handle)
            if row["state"] in {"COMPLETED", "CANCELLED", "FAILED", "RETAINED", "UNKNOWN"}:
                continue
            if mode == "CANCEL_ACKED_RUNNING":
                # The product acknowledged the cancellation request but
                # the effect is still executing: CANCELLING must not be
                # mistaken for a terminal state, so no completion is
                # claimed and quiescence keeps waiting or retains.
                self._execution_ledger.transition(handle, "CANCELLING",
                    reason="cancel admitted; product execution still in flight")
            elif mode == "UNKNOWN_EFFECT":
                self._execution_ledger.unknown(handle, reason="controlled unknown effect")
            elif mode == "CANCEL_QUEUED":
                self._execution_ledger.transition(handle, "CANCELLING", reason="controlled cancel requested")
                self._execution_ledger.transition(handle, "CANCELLED", reason="controlled cancel terminal",
                                                  evidence_request_id=f"local-cancel-{session_id}")
            else:
                self._execution_ledger.transition(handle, "COMPLETED", reason=reason,
                                                  evidence_request_id=f"local-quiesce-{session_id}")
        if mode == "LATE_CALLBACK":
            late_request = f"local-late-{session_id}-{uuid.uuid4().hex}"
            late = self._execution_ledger.intent(
                self._effect_case_id, self._effect_attempt, "context", late_request,
                session_id=session_id)
            self._execution_ledger.observe(late, f"local-late-context-{session_id}", state="ADMITTED",
                                           evidence_request_id=late_request)
            self._execution_ledger.transition(late, "QUEUED", reason="controlled late callback")
        self._execution_ledger.require_session_quiescent(session_id)

    def preflight(self) -> dict:
        human = self.profile["human"]["user_id"]
        actors = self.profile["actors"]
        checks = [
            {"id": "target.environment", "status": "PASS", "reason": "local mock profile"},
            {"id": "identity.human", "status": "PASS", "reason": human},
            *[
                {"id": f"actor.{role}", "status": "PASS",
                 "reason": f"dedicated local mock actor {binding['bot_id']}"}
                for role, binding in actors.items()
            ],
        ]
        return {
            "schema_version": 1, "status": "DISCOVERY_PASSED", "ready_to_execute": False,
            "checks": checks, "requests": [],
            "not_verified": ["deployed service", "Gateway authentication", "OB", "BaaS"],
            "note": "Local mock adapter only; no network or product process was contacted.",
        }

    def semantic_observer(self):
        def query(plan):
            resource_id = next(value for key, value in plan.filters.items() if key != "env")
            if plan.assertion_id == "group.persisted":
                value = self.groups.get(resource_id)
                row = None if value is None or resource_id in self.deleted else {
                    "group_id": resource_id, "env": "local",
                    "driver_bot": value.get("driver_bot_uuid"),
                    "originator": value.get("originator_actor_id"),
                    "status": value.get("status"),
                }
            elif plan.assertion_id == "session.persisted":
                value = self.sessions.get(resource_id)
                row = None if value is None or resource_id in self.deleted_sessions else {
                    "session_id": resource_id, "env": "local",
                    "group_id": value.get("group_id"), "status": value.get("status"),
                    "session_title": value.get("title"),
                }
            elif plan.assertion_id == "file.persisted":
                value = self.files.get(resource_id)
                row = None if value is None or resource_id in self.deleted_files else {
                    "file_id": resource_id, "env": "local",
                    "session_id": value.get("session_id"), "status": value.get("status"),
                    "size": value.get("size"), "sha256": value.get("sha256"),
                    "storage_backend": value.get("storage_backend"),
                }
            else:
                return {"success": False, "message": "unsupported local assertion"}
            return {"success": True, "data": [] if row is None else [row]}

        return LocalSemanticEvidenceProvider(database="local", environment="local", query=query)

    def group_create(self, body: dict):
        group_id = f"local-mock-group-{uuid.uuid4().hex}"
        session_id = f"local-mock-session-{uuid.uuid4().hex}"
        request_id = f"local-mock-{uuid.uuid4().hex}"
        participants = body.get("participants", [])
        bot_ids = [item.get("actor_id") for item in participants
                   if isinstance(item, dict) and isinstance(item.get("actor_id"), str)]
        self._begin_session_effects(session_id, request_id, bot_ids)
        value = {**body, "group_id": group_id, "status": "active", "initial_session_id": session_id,
                 "originator_actor_id": "human_" + self.profile["human"]["user_id"]}
        self.sessions[session_id] = {"session_id": session_id, "group_id": group_id,
                                     "status": "running", "title": None}
        self.groups[group_id] = value
        return value, self._record_request("group.create", "POST", "/mock/groups", 201, request_id)

    def group_read(self, group_id: str):
        value = self.groups.get(group_id)
        if value is None or group_id in self.deleted:
            raise RuntimeError("local mock group not found")
        return value, self._record("group.read", "GET", f"/mock/groups/{group_id}", 200)

    def group_update(self, group_id: str, body: dict):
        value, _ = self.group_read(group_id)
        value.update(body)
        return value, self._record("group.update", "PATCH", f"/mock/groups/{group_id}", 200)

    def group_delete(self, group_id: str):
        self.group_read(group_id)
        self.deleted.add(group_id)
        return {"group_id": group_id, "deleted": True}, self._record(
            "group.delete", "DELETE", f"/mock/groups/{group_id}", 200)

    def group_verify_deleted(self, group_id: str):
        if group_id not in self.deleted:
            raise RuntimeError("local mock deletion was not recorded")
        return None, self._record("group.verify_deleted", "GET", f"/mock/groups/{group_id}", 404)

    def session_create(self, group_id: str, body: dict):
        group = self.group_read(group_id)[0]
        session_id = f"local-mock-session-{uuid.uuid4().hex}"
        request_id = f"local-mock-{uuid.uuid4().hex}"
        participants = group.get("participants", [])
        bot_ids = [item.get("actor_id") for item in participants
                   if isinstance(item, dict) and isinstance(item.get("actor_id"), str)]
        self._begin_session_effects(session_id, request_id, bot_ids)
        value = {**body, "session_id": session_id, "group_id": group_id, "status": "running"}
        self.sessions[session_id] = value
        return value, self._record_request("session.create", "POST",
                                          f"/mock/groups/{group_id}/sessions", 201, request_id)

    def session_read(self, session_id: str):
        value = self.sessions.get(session_id)
        if value is None or session_id in self.deleted_sessions:
            raise RuntimeError("local mock session not found")
        return value, self._record("session.read", "GET", f"/mock/sessions/{session_id}", 200)

    def session_update(self, session_id: str, body: dict):
        value, _ = self.session_read(session_id)
        value.update(body)
        return value, self._record("session.update", "PATCH", f"/mock/sessions/{session_id}", 200)

    def session_messages(self, session_id: str):
        self.session_read(session_id)
        return {"items": []}, self._record("session.messages", "GET", f"/mock/sessions/{session_id}/messages", 200)

    def session_token(self, session_id: str):
        self.session_read(session_id)
        return {"token": f"local-mock-token-{session_id}"}, self._record("session.token", "POST", f"/mock/sessions/{session_id}/token", 200)

    def session_ws_connect(self, group_id: str, session_id: str, token: str):
        data, _ = self.session_read(session_id)
        if data.get("group_id") != group_id:
            raise RuntimeError("local mock session group binding mismatch")
        if token != f"local-mock-token-{session_id}":
            raise RuntimeError("local mock session token mismatch")
        if session_id in self._production_closed:
            raise RuntimeError("local Session producers are already fenced")
        request_id = f"local-ws-{uuid.uuid4().hex}"
        self._begin_subscription_effect(session_id, request_id)
        self.connected_sessions.add(session_id)
        self._control_sessions.add(session_id)
        return {"group_id": group_id, "session_id": data["session_id"], "upgraded": True, "connected": True}, self._record_request("session.ws.connect", "WS", f"/mock/sessions/{session_id}/ws", 101, request_id)

    def session_ws_close(self, session_id: str):
        if session_id not in self.connected_sessions:
            raise RuntimeError("local mock WebSocket was not connected")
        self.connected_sessions.remove(session_id)
        if self._execution_ledger is not None:
            for handle in self._session_effects.get(session_id, []):
                row = next(item for item in self._execution_ledger.snapshot() if item["handle"] == handle)
                if row["kind"] == "subscription" and row["state"] not in {"COMPLETED", "CANCELLED", "FAILED", "RETAINED", "UNKNOWN"}:
                    self._execution_ledger.transition(handle, "COMPLETED", reason="local subscription closed",
                                                      evidence_request_id=f"local-ws-close-{session_id}")
        return {"closed": True}, self._record("session.ws.close", "WS", f"/mock/sessions/{session_id}/ws", 1000)

    def session_delete(self, session_id: str):
        self.session_read(session_id)
        if session_id in self.connected_sessions:
            raise RuntimeError("local mock session WebSocket is still open")
        self.deleted_sessions.add(session_id)
        return {"session_id": session_id, "deleted": True}, self._record("session.delete", "DELETE", f"/mock/sessions/{session_id}", 200)

    def session_verify_deleted(self, session_id: str):
        if session_id not in self.deleted_sessions:
            raise RuntimeError("local mock session deletion was not recorded")
        return None, self._record("session.verify_deleted", "GET", f"/mock/sessions/{session_id}", 404)

    def _file(self, session_id: str, file_id: str) -> dict:
        self.session_read(session_id)
        value = self.files.get(file_id)
        if value is None or file_id in self.deleted_files or value["session_id"] != session_id:
            raise RuntimeError("local mock file not found")
        return value

    def _public_file(self, value: dict) -> dict:
        return {key: value[key] for key in (
            "file_id", "session_id", "file_name", "mime_type", "size", "sha256",
            "owner", "storage_backend", "status", "created_at", "updated_at",
        )}

    def file_list(self, session_id: str):
        self.session_read(session_id)
        items = [self._public_file(value) for value in self.files.values()
                 if value["session_id"] == session_id and value["file_id"] not in self.deleted_files]
        return {"items": items, "total": len(items)}, self._record(
            "file.list", "GET", f"/mock/sessions/{session_id}/files", 200)

    def file_prepare(self, session_id: str, body: dict):
        self.session_read(session_id)
        if body.get("size", -1) < 0 or body.get("size", 0) > 4096:
            raise RuntimeError("local mock file exceeds the 4KiB pilot limit")
        file_id = f"local-mock-file-{uuid.uuid4().hex}"
        now = int(time.time())
        value = {
            "file_id": file_id, "session_id": session_id,
            "file_name": body["file_name"], "mime_type": body["mime_type"],
            "size": body["size"], "sha256": None, "storage_backend": "local",
            "owner": {"actor_kind": "human", "actor_id": "human_" + self.profile["human"]["user_id"]},
            "status": "pending", "created_at": now, "updated_at": now,
            "content": b"",
        }
        self.files[file_id] = value
        return {"file_id": file_id}, self._record(
            "file.prepare", "POST", f"/mock/sessions/{session_id}/files", 201)

    def file_upload(self, session_id: str, file_id: str, content: bytes):
        value = self._file(session_id, file_id)
        if value["status"] != "pending" or len(content) != value["size"]:
            raise RuntimeError("local mock upload does not match pending file")
        value["content"] = bytes(content)
        value["updated_at"] = int(time.time())
        return {"file_id": file_id, "status": "pending", "upload_accepted": True}, self._record(
            "file.upload", "PUT", f"/mock/sessions/{session_id}/files/{file_id}/content", 202)

    def file_complete(self, session_id: str, file_id: str):
        value = self._file(session_id, file_id)
        if value["status"] != "pending" or len(value["content"]) != value["size"]:
            raise RuntimeError("local mock file is not ready to complete")
        value["sha256"] = hashlib.sha256(value["content"]).hexdigest()
        value["status"] = "ready"
        value["updated_at"] = int(time.time())
        return self._public_file(value), self._record(
            "file.complete", "POST", f"/mock/sessions/{session_id}/files/{file_id}/complete", 200)

    def file_metadata(self, session_id: str, file_id: str):
        value = self._file(session_id, file_id)
        return self._public_file(value), self._record(
            "file.metadata", "GET", f"/mock/sessions/{session_id}/files/{file_id}", 200)

    def file_download(self, session_id: str, file_id: str):
        value = self._file(session_id, file_id)
        if value["status"] != "ready":
            raise RuntimeError("local mock file is not ready for download")
        return value["content"], self._record(
            "file.download", "GET", f"/mock/sessions/{session_id}/files/{file_id}/content", 200)

    def file_share(self, session_id: str, file_id: str):
        value = self._file(session_id, file_id)
        if value["status"] != "ready":
            raise RuntimeError("local mock file is not ready for sharing")
        token = f"local-mock-share-{uuid.uuid4().hex}"
        self.shared_files[token] = file_id
        expires_at = int(time.time()) + 3600
        return {"share_token": token, "share_url": f"mock://local/shared-file?token={token}",
                "expires_at": expires_at}, self._record(
            "file.share", "POST", f"/mock/sessions/{session_id}/files/{file_id}/share", 201)

    def shared_file_download(self, token: str):
        file_id = self.shared_files.get(token)
        if not file_id or file_id in self.deleted_files:
            raise RuntimeError("local mock share token not found")
        value = self.files[file_id]
        if value["status"] != "ready":
            raise RuntimeError("local mock shared file is not ready")
        return value["content"], self._record(
            "file.shared_download", "GET", "/mock/sessions/shared-file/content", 200)

    def file_delete(self, session_id: str, file_id: str):
        value = self._file(session_id, file_id)
        self.deleted_files.add(file_id)
        for token, shared_file_id in list(self.shared_files.items()):
            if shared_file_id == file_id:
                del self.shared_files[token]
        value["status"] = "deleting"
        value["updated_at"] = int(time.time())
        return {"deleted": True}, self._record(
            "file.delete", "DELETE", f"/mock/sessions/{session_id}/files/{file_id}", 200)

    def file_verify_deleted(self, session_id: str, file_id: str):
        self.session_read(session_id)
        if file_id not in self.deleted_files:
            raise RuntimeError("local mock file deletion was not recorded")
        return None, self._record(
            "file.verify_deleted", "GET", f"/mock/sessions/{session_id}/files/{file_id}", 404)

    def shared_file_verify_deleted(self, token: str):
        if token in self.shared_files:
            raise RuntimeError("local mock share token remains usable after file deletion")
        return None, self._record(
            "file.shared_verify_deleted", "GET", "/mock/sessions/shared-file/content", 404)


def adapter_for(profile: dict, *, state_dir: Path | None = None, case_id: str | None = None,
                observer_client=None, physical_binding_probe=None) -> PilotAdapter:
    # Read-only discovery has no Run directory. Execution/recovery always supplies it.
    if profile["entrypoint"]["kind"] == "mock":
        if state_dir is not None:
            from .persistent_mock import PersistentLocalMockAdapter
            return PersistentLocalMockAdapter(profile, state_dir, case_id=case_id)
        return LocalMockAdapter(profile)
    if profile["entrypoint"]["kind"] == "singlebox":
        raise AdapterCapabilityError("LocalSinglebox adapter requires verified run-owned provisioning")
    if state_dir is not None:
        require_group_effect_admission(profile)
    return RemotePilotAdapter(profile, observer_client=observer_client,
                               physical_binding_probe=physical_binding_probe)
