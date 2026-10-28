"""Shared product-CLI contract for the first Session-file core slice."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .file_transport import RemoteFileClient
from .write_transport import WriteClient
from .run_state import RunStateError
from .local_session_contract import PARENT, session_source_binding
from .workspace_cli_runner import SESSION_CLI_CREATE_GET_EFFECT
from .workspace_cli_profile import (
    SESSION_FILE_CORE_PROFILE_CONTRACT,
    validate_session_file_core_profile,
)
from .session_cli_profile import (
    SESSION_CLI_BOT_GROUPS_OPERATION,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    validate_session_cli_create_get_profile,
)

SESSION_FILE_PARENT = "story_session_file_workspace"
CASE_ID = "DUAL-SESSION-FILE-CORE-01"
REQUIREMENT_IDS = (
    "CLI-FILE.setup", "CLI-FILE.upload", "CLI-FILE.list", "CLI-FILE.capabilities",
    "CLI-FILE.metadata", "CLI-FILE.download", "CLI-FILE.share",
    "CLI-FILE.shared-metadata",
    "CLI-FILE.shared-content", "CLI-FILE.delete",
)
CASE_METHODS = {
    "CLI-FILE.setup": "setup_session_file_workspace",
    "CLI-FILE.upload": "upload_session_file",
    "CLI-FILE.list": "list_session_files",
    "CLI-FILE.capabilities": "read_session_file_capabilities",
    "CLI-FILE.metadata": "read_session_file_metadata",
    "CLI-FILE.download": "download_session_file",
    "CLI-FILE.share": "share_session_file",
    "CLI-FILE.shared-metadata": "read_shared_session_file_metadata",
    "CLI-FILE.shared-content": "download_shared_session_file",
    "CLI-FILE.delete": "delete_session_file",
}
FIXTURE_EFFECT = "RETAINED_DYNAMIC_GROUP_SESSIONS_AND_FILE"
DELETE_FILE_EFFECT = "RETAINED_DYNAMIC_GROUP_SESSIONS_DELETED_FILE"
SOURCE_FUNCTIONS = ("story_session_file_workspace",)
FILE_SIZE = 29
FILE_BYTES = b"bcs-e2e-session-file-payload\n"
FILE_NAME = "bcs-e2e-session-file-source.txt"


@dataclass(frozen=True)
class SessionFileCoreCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != SESSION_FILE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("Session-file core case is invalid")


def session_file_core_case_plan() -> SessionFileCoreCasePlan:
    from pathlib import Path
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = read(root / "migration-contracts.json")
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == SESSION_FILE_PARENT), None)
    if story is None or story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("Session-file core selected the wrong parent")
    rows = [row for row in story["requirements"] if row["id"] in REQUIREMENT_IDS]
    if ([row["id"] for row in rows] != list(REQUIREMENT_IDS)
            or any(tuple(row["functions"]) != SOURCE_FUNCTIONS for row in rows)):
        raise RunStateError("Session-file core source obligations changed")
    plan = SessionFileCoreCasePlan(
        case_id=CASE_ID, parent_id=SESSION_FILE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def session_file_core_adaptability() -> dict:
    plan = session_file_core_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["setup", "upload", "list", "capabilities",
                               "metadata", "download", "share", "shared-metadata",
                               "shared-content", "delete"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session-file",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The first Session-file core contract is implemented and "
            "mock-verified, but real Singlebox/preprod effect admission and "
            "execution remain independent gates."
        ),
    }


def _session_file_metadata_profile(profile: dict) -> dict:
    if profile["environment"]["tier"] == "local":
        auth = {"header_env": {
            "Authorization": profile["auth"]["driver_token_env"],
        }}
    else:
        auth = {"header_env": {
            "Cookie": profile["auth"]["cookie_env"],
        }}
    return {
        "environment": copy.deepcopy(profile["environment"]),
        "entrypoint": {"kind": profile["entrypoint"]["kind"],
                      "origin": profile["entrypoint"]["origin"]},
        "auth": auth,
        "file_transfer": copy.deepcopy(profile.get("file_transfer", {})),
        "policy": copy.deepcopy(profile["policy"]),
    }


def validate_session_file_core_adapter(adapter: object) -> None:
    for name in CASE_METHODS.values():
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"Session-file core adapter is missing {name}")


def _validated_request(result: AdapterCall, *, operation: str,
                        credential_kind: str, transport: str = "PRODUCT_CLI",
                        caller_role: str = "driver",
                        fixture_effect: str = FIXTURE_EFFECT) -> dict:
    evidence = result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != transport
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != caller_role
            or evidence.get("credential_kind") != credential_kind
            or evidence.get("fixture_effect") != fixture_effect
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"Session-file core evidence for {operation} is invalid")
    return evidence


class RemoteSessionFileCoreAdapter:
    """Create the retained Session and begin its bounded CLI-file lifecycle."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_file_core_profile(profile)
        if (self.profile["contract"] != SESSION_FILE_CORE_PROFILE_CONTRACT
                or not callable(getattr(runner, "execute", None))):
            raise RunStateError("Session-file core requires its product-CLI runner")
        self.runner = runner
        self.file_client = RemoteFileClient(
            _session_file_metadata_profile(self.profile))

        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.initial_session_id = ""
        self.session_id: str | None = None
        self.file_id: str | None = None
        self.share_token: str | None = None
        self.share_url: str | None = None
        self.share_expires_at: int | None = None

    def _call(self, operation: str, *, group_id: str | None = None,
              session_id: str | None = None,
              file_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id,
                                    session_id=session_id, file_id=file_id)
        if not isinstance(result, dict) or set(result) != {"data", "evidence"}:
            raise RunStateError("Session-file product-CLI result is invalid")
        if not isinstance(result["evidence"], dict):
            raise RunStateError("Session-file product-CLI evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["operation"] = operation
        return AdapterCall(data=result["data"], evidence=evidence)

    def setup_session_file_workspace(self) -> AdapterCall:
        if self.group_id is not None or self.session_id is not None:
            raise RunStateError("Session-file setup is a one-shot operation")
        created = self._call("session-file.group.create")
        created.validate()
        data = created.data
        if (not isinstance(data, dict)
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("group_id"), str)
                or not data["group_id"]
                or not isinstance(data.get("session_id"), str)):
            raise RunStateError("Session-file Group setup returned an inexact binding")
        self.group_id = data["group_id"]
        self.initial_session_id = data["session_id"]

        session = self._call("session-file.session.create", group_id=self.group_id)
        session.validate()
        value = session.data
        if (not isinstance(value, dict)
                or not isinstance(value.get("session_id"), str)
                or not value["session_id"].startswith(self.group_id + ":")
                or value.get("session_kind") != "chat"
                or value.get("status") not in ("active", "running")):
            raise RunStateError("Session-file explicit Session create is not exact")
        self.session_id = value["session_id"]
        evidence = {
            "transport": "PRODUCT_CLI",
            "fixture_effect": FIXTURE_EFFECT,
            "requests": [created.evidence, session.evidence],
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "group_id": self.group_id,
            "session_id": self.session_id,
        }
        return AdapterCall(data={**data, "explicit_session": value}, evidence=evidence)

    def upload_session_file(self) -> AdapterCall:
        if self.session_id is None or self.file_id is not None:
            raise RunStateError("Session-file upload must follow setup exactly once")
        result = self._call("session-file.upload", session_id=self.session_id)
        result.validate()
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("file_id"), str) or not data["file_id"]
                or data.get("file_name") != FILE_NAME
                or data.get("size") != FILE_SIZE
                or data.get("status") not in ("ready", "Ready")):
            raise RunStateError("Session-file upload result is not exact")
        self.file_id = data["file_id"]
        return result

    def list_session_files(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file list must follow upload")
        result = self._call("session-file.list", session_id=self.session_id)
        result.validate()
        data = result.data
        items = data.get("items") if isinstance(data, dict) else None
        item = items[0] if isinstance(items, list) and len(items) == 1 else None
        if (not isinstance(data, dict) or data.get("total") != 1
                or not isinstance(item, dict)
                or item.get("file_id") != self.file_id
                or item.get("file_name") != FILE_NAME
                or item.get("size") != FILE_SIZE
                or item.get("status") not in ("ready", "Ready")):
            raise RunStateError("Session-file list result is not exact")
        return result

    def read_session_file_capabilities(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file capabilities must follow upload")
        result = self._call("session-file.capabilities", session_id=self.session_id)
        result.validate()
        data = result.data
        expected_storage = "local" if self.profile["environment"]["tier"] == "local" else "baas"
        if (not isinstance(data, dict)
                or data.get("storage") != expected_storage
                or not isinstance((max_size := data.get("max_file_size_bytes",
                                                        data.get("max_size", 0))), int)
                or max_size <= 0
                or data.get("file_uploads", data.get("presign_upload")) is not True):
            raise RunStateError("Session-file capabilities are not environment-exact")
        return result

    def read_session_file_metadata(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file metadata must follow upload")
        data, record = self.file_client.metadata(self.session_id, self.file_id)
        if (not isinstance(data, dict) or data.get("file_id") != self.file_id
                or not isinstance(record, dict)
                or record.get("operation") != "file.metadata"
                or record.get("http_status") != 200
                or not isinstance(record.get("path"), str)):
            raise RunStateError("Session-file metadata result is not exact")
        credential_kind = ("owned_local_bot_token"
                           if self.profile["environment"]["tier"] == "local"
                           else "human_cookie_with_owned_driver_bot_token")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": credential_kind,
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def read_shared_session_file_metadata(self) -> AdapterCall:
        if self.share_token is None or self.file_id is None:
            raise RunStateError("Session-file shared metadata must follow share")
        data, record = self.file_client.shared_metadata(self.share_token)
        if (not isinstance(data, dict)
                or data.get("file_id") != self.file_id
                or data.get("file_name") != FILE_NAME
                or data.get("size") != FILE_SIZE
                or data.get("status") != "Ready"
                or "session_id" in data
                or not isinstance(record, dict)
                or record.get("operation") != "file.shared_metadata"
                or record.get("http_status") != 200
                or not isinstance(record.get("path"), str)):
            raise RunStateError("Session-file shared metadata result is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "public_share",
            "credential_kind": "share_token",
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def download_shared_session_file(self) -> AdapterCall:
        if self.share_token is None or self.file_id is None:
            raise RunStateError("Session-file shared-content must follow share")
        body, record = self.file_client.shared_download(self.share_token)
        if (body != FILE_BYTES
                or not isinstance(record, dict)
                or record.get("operation") != "file.shared_download"
                or record.get("http_status") != 200
                or not isinstance(record.get("path"), str)):
            raise RunStateError("Session-file shared-content result is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "public_share",
            "credential_kind": "share_token",
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data={
            "size": FILE_SIZE,
            "sha256": hashlib.sha256(FILE_BYTES).hexdigest(),
        }, evidence=evidence)

    def download_session_file(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file download must follow upload")
        result = self._call("session-file.download", session_id=self.session_id,
                            file_id=self.file_id)
        result.validate()
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("out"), str)
                or data["out"] != str(self.runner.session_file_download)):
            raise RunStateError("Session-file download target is not exact")
        if self.runner.session_file_download.read_bytes() != FILE_BYTES:
            raise RunStateError("Session-file download bytes differ from upload")
        return AdapterCall(data={
            "out": data["out"], "size": FILE_SIZE,
            "sha256": hashlib.sha256(FILE_BYTES).hexdigest(),
        }, evidence=result.evidence)

    def share_session_file(self) -> AdapterCall:
        if self.session_id is None or self.file_id is None:
            raise RunStateError("Session-file share must follow upload")
        result = self._call("session-file.share", session_id=self.session_id,
                            file_id=self.file_id)
        result.validate()
        data = result.data
        token = data.get("share_token") if isinstance(data, dict) else None
        url = data.get("share_url") if isinstance(data, dict) else None
        expires = data.get("expires_at") if isinstance(data, dict) else None
        if (not isinstance(token, str) or not token
                or not isinstance(url, str) or not url
                or not isinstance(expires, int) or expires <= 0):
            raise RunStateError("Session-file share result is not exact")
        self.share_token = token
        self.share_url = url
        self.share_expires_at = expires
        self.file_client.register_share_target(token, url)
        return AdapterCall(data={
            "share_granted": True, "share_token_present": True,
            "expires_at": expires,
        }, evidence=result.evidence)

    def delete_session_file(self) -> AdapterCall:
        if (self.session_id is None or self.file_id is None
                or self.share_token is None):
            raise RunStateError("Session-file delete must follow shared content")
        result = self._call("session-file.delete", session_id=self.session_id,
                            file_id=self.file_id)
        result.validate()
        if result.data not in (None, {"deleted": True}):
            raise RunStateError("Session-file delete acknowledgement is not exact")
        invalidations = []
        _, file_absence = self.file_client.verify_deleted(self.session_id, self.file_id)
        invalidations.append({
            "transport": "PRODUCT_HTTP", "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie_with_owned_driver_bot_token"),
            "fixture_effect": DELETE_FILE_EFFECT, **file_absence,
        })
        _, shared_metadata_absence = self.file_client.shared_metadata_verify_deleted(self.share_token)
        invalidations.append({
            "transport": "PRODUCT_HTTP", "caller_role": "public_share",
            "credential_kind": "share_token", "fixture_effect": DELETE_FILE_EFFECT,
            **shared_metadata_absence,
        })
        _, shared_content_absence = self.file_client.verify_shared_deleted(self.share_token)
        invalidations.append({
            "transport": "PRODUCT_HTTP", "caller_role": "public_share",
            "credential_kind": "share_token", "fixture_effect": DELETE_FILE_EFFECT,
            **shared_content_absence,
        })
        evidence = dict(result.evidence)
        evidence["fixture_effect"] = DELETE_FILE_EFFECT
        evidence["invalidations"] = invalidations
        return AdapterCall(data={"file_deleted": True, "invalidation_count": 3},
                           evidence=evidence)


def _validate_step(requirement_id: str, adapter: object,
                   result: AdapterCall) -> None:
    if not isinstance(adapter, RemoteSessionFileCoreAdapter):
        raise RunStateError("Session-file core adapter is invalid")
    credential_kind = ("owned_local_bot_token"
                       if adapter.profile["environment"]["tier"] == "local"
                       else "human_cookie_with_owned_driver_bot_token")
    if requirement_id == "CLI-FILE.setup":
        evidence = result.evidence
        requests = evidence.get("requests") if isinstance(evidence, dict) else None
        if (not isinstance(evidence, dict)
                or evidence.get("transport") != "PRODUCT_CLI"
                or evidence.get("fixture_effect") != FIXTURE_EFFECT
                or evidence.get("remote_validation") != "NOT_RUN"
                or evidence.get("grants_release_credit") is not False
                or not isinstance(requests, list) or len(requests) != 2
                or requests[0].get("operation") != "session-file.group.create"
                or requests[1].get("operation") != "session-file.session.create"
                or requests[0].get("credential_kind") != credential_kind
                or requests[1].get("credential_kind") != credential_kind):
            raise RunStateError("Session-file setup evidence is not exact")
    else:
        operations = {
            "CLI-FILE.upload": "session-file.upload",
            "CLI-FILE.list": "session-file.list",
            "CLI-FILE.capabilities": "session-file.capabilities",
            "CLI-FILE.metadata": "file.metadata",
            "CLI-FILE.download": "session-file.download",
            "CLI-FILE.share": "session-file.share",
            "CLI-FILE.shared-metadata": "file.shared_metadata",
            "CLI-FILE.shared-content": "file.shared_download",
            "CLI-FILE.delete": "session-file.delete",
        }
        if requirement_id == "CLI-FILE.metadata":
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind=credential_kind,
                               transport="PRODUCT_HTTP")
        elif requirement_id == "CLI-FILE.shared-metadata":
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind="share_token",
                               transport="PRODUCT_HTTP", caller_role="public_share")
        elif requirement_id == "CLI-FILE.shared-content":
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind="share_token",
                               transport="PRODUCT_HTTP", caller_role="public_share")
        elif requirement_id == "CLI-FILE.delete":
            evidence = _validated_request(result, operation=operations[requirement_id],
                                          credential_kind=credential_kind,
                                          fixture_effect=DELETE_FILE_EFFECT)
            invalidations = evidence.get("invalidations")
            if not isinstance(invalidations, list) or len(invalidations) != 3:
                raise RunStateError("Session-file deletion invalidations are not exact")
            expected = (
                ("file.verify_deleted", "driver", credential_kind),
                ("file.shared_metadata_verify_deleted", "public_share", "share_token"),
                ("file.shared_verify_deleted", "public_share", "share_token"),
            )
            for record, (operation, caller_role, cred_kind) in zip(invalidations, expected):
                if (not isinstance(record, dict)
                        or record.get("transport") != "PRODUCT_HTTP"
                        or record.get("operation") != operation
                        or record.get("http_status") != 404
                        or record.get("caller_role") != caller_role
                        or record.get("credential_kind") != cred_kind
                        or record.get("fixture_effect") != DELETE_FILE_EFFECT):
                    raise RunStateError("Session-file deletion invalidation is not exact")
        else:
            _validated_request(result, operation=operations[requirement_id],
                               credential_kind=credential_kind)


def execute_session_file_core_case(adapter: object) -> CaseExecution:
    plan = session_file_core_case_plan()
    validate_session_file_core_adapter(adapter)
    if not isinstance(adapter, RemoteSessionFileCoreAdapter):
        raise RunStateError("Session-file core requires its shared adapter")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for requirement_id in plan.requirement_ids:
        method = getattr(adapter, CASE_METHODS[requirement_id])
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=CASE_METHODS[requirement_id],
                                     status="NOT_RUN", data=None, evidence={},
                                     error_type=None))
            continue
        try:
            result = method()
            if not isinstance(result, AdapterCall):
                raise RunStateError("Session-file core method did not return AdapterCall")
            result.validate()
            _validate_step(requirement_id, adapter, result)
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=CASE_METHODS[requirement_id], status="PASS",
                                     data=result.data, evidence=dict(result.evidence),
                                     error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(requirement_id=requirement_id,
                                     method=CASE_METHODS[requirement_id],
                                     status="NOT_RUN", data=None, evidence={},
                                     error_type=error_type))
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=SESSION_FILE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed, total_count=len(steps),
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Dual product-CLI Session create/list/get case
CASE_CLASSES = {
    "session-cli.group.create": "setup",
    "session-cli.session.create": "create",
    "session-cli.session.list": "list",
    "session-cli.session.get": "get",
}
FINAL_EFFECT = "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET"


class RemoteSessionCliCreateGetAdapter:
    """Execute one owned CLI Session lifecycle and exact parent cleanup."""

    def __init__(self, profile: dict, runner: object):
        self.profile = validate_session_cli_create_get_profile(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Session CLI create/list/get requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        self.initial_session_id = ""
        self.session_id: str | None = None
        self.cleanup_client = WriteClient(self._cleanup_profile(self.profile))

    @staticmethod
    def _cleanup_profile(profile: dict) -> dict:
        """Use the reviewed framework transport only for the two cleanup routes."""
        value = copy.deepcopy(profile)
        if profile["environment"]["tier"] == "local":
            value["auth"] = {"kind": "authenticated-product",
                             "header_env": {
                                 "Authorization": profile["auth"]["driver_token_env"]}}
        else:
            value["auth"] = {"kind": "human-cookie",
                             "header_env": {"Cookie": profile["auth"]["cookie_env"]}}
        value["entrypoint"] = {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return json.loads(json.dumps(value, allow_nan=False))

    def _call(self, operation: str, *, group_id: str | None = None,
              session_id: str | None = None) -> AdapterCall:
        result = self.runner.execute(operation, group_id=group_id, session_id=session_id)
        if (not isinstance(result, dict)
                or set(result) != {"data", "evidence"}
                or not isinstance(result["evidence"], dict)):
            raise RunStateError("Session product-CLI result or evidence is invalid")
        evidence = dict(result["evidence"])
        evidence["case_class"] = CASE_CLASSES[operation]
        return AdapterCall(data=result["data"], evidence=evidence)

    def create_owned_cli_group(self) -> AdapterCall:
        if self.group_id is not None:
            raise RunStateError("Session CLI Group creation is a one-shot operation")
        result = self._call("session-cli.group.create")
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("group_id"), str) or not data["group_id"]
                or data.get("driver_bot") != self.driver_id
                or data.get("participants") != [self.driver_id, self.member_id]
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(data["group_id"] + ":")):
            raise RunStateError("Session CLI group setup is not exact")
        self.group_id = data["group_id"]
        self.initial_session_id = data["session_id"]
        return result

    def create_listed_cli_session(self) -> AdapterCall:
        if self.group_id is None or self.session_id is not None:
            raise RunStateError("Session CLI explicit create must follow Group setup once")
        result = self._call("session-cli.session.create", group_id=self.group_id)
        data = result.data
        if (not isinstance(data, dict)
                or not isinstance(data.get("session_id"), str)
                or not data["session_id"].startswith(self.group_id + ":")
                or data["session_id"] == self.initial_session_id
                or data.get("id") != data["session_id"]
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "cli-e2e-sess"):
            raise RunStateError("Session CLI explicit create is not exact")
        self.session_id = data["session_id"]
        return result

    def read_session_list(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session CLI list must follow explicit Session create")
        result = self._call("session-cli.session.list", group_id=self.group_id)
        data = result.data
        items = data.get("items") if isinstance(data, dict) else None
        exact = [row for row in items if isinstance(row, dict)
                 and (row.get("session_id") or row.get("id")) == self.session_id] \
            if isinstance(items, list) else []
        if (not isinstance(data, dict) or set(data) != {"group_id", "items"}
                or data.get("group_id") != self.group_id
                or not isinstance(items, list) or not items or len(items) > 50
                or len(exact) != 1 or exact[0].get("group_id") != self.group_id):
            raise RunStateError("Session CLI list did not contain the exact created Session")
        return result

    def read_session_exact(self) -> AdapterCall:
        if self.group_id is None or self.session_id is None:
            raise RunStateError("Session CLI get must follow list")
        result = self._call("session-cli.session.get", session_id=self.session_id)
        data = result.data
        if (not isinstance(data, dict)
                or data.get("id") != self.session_id
                or data.get("session_id") != self.session_id
                or data.get("group_id") != self.group_id
                or data.get("session_title") != "cli-e2e-sess"):
            raise RunStateError("Session CLI get did not return the exact Session")
        return result

    def _cleanup(self, operation: str, *, expected: tuple[int, ...],
                 parse_error: bool = False) -> tuple[object, dict]:
        if self.group_id is None:
            raise RunStateError("Session CLI cleanup requires the exact owned Group ID")
        return self.cleanup_client.call(
            operation, group_id=self.group_id, expected=expected,
            parse_error=parse_error, request_id=f"e2e-session-cli-{operation}-{os.urandom(8).hex()}")

    def delete_owned_group(self) -> AdapterCall:
        if self.session_id is None:
            raise RunStateError("Session CLI Group cleanup must follow list/get")
        data, record = self._cleanup(SESSION_CLI_GROUP_DELETE_OPERATION, expected=(200,))
        if (not isinstance(data, dict)
                or data != {"deleted": True, "id": self.group_id}
                or record.get("http_status") != 200):
            raise RunStateError("Session CLI Group delete acknowledgement is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": "cleanup-delete",
            "fixture_effect": FINAL_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)

    def verify_group_deleted(self) -> AdapterCall:
        data, record = self._cleanup(
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION, expected=(404,), parse_error=True)
        if (not isinstance(data, dict)
                or data.get("status") != 404
                or data.get("code") != "group_not_found"
                or data.get("params") != {"group_id": self.group_id}
                or not isinstance(data.get("message"), str) or not data["message"]
                or record.get("http_status") != 404):
            raise RunStateError("Session CLI deleted Group was not returned as an exact 404")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": "cleanup-404-proof",
            "fixture_effect": FINAL_EFFECT,
        })
        return AdapterCall(data=data, evidence=evidence)


from dataclasses import dataclass

from .case_executor import CaseExecution, StepOutcome
from .local_session_contract import PARENT, session_source_binding
from .workspace_cli_runner import SESSION_CLI_CREATE_GET_EFFECT

SESSION_CLI_CREATE_GET_CASE_ID = "DUAL-SESSION-CLI-CREATE-LIST-GET-01"
SESSION_CLI_CREATE_GET_REQUIREMENT_ID = "SESSION.cli-create-list-get"
SOURCE_FUNCTION = "test_group_session_via_cli"
CASE_METHOD = "execute_session_cli_create_list_get_case"


@dataclass(frozen=True)
class SessionCliCreateGetCasePlan:
    case_id: str
    parent_id: str
    requirement_id: str
    source_function: str
    source_expectation: str

    def validate(self) -> None:
        if (self.case_id != SESSION_CLI_CREATE_GET_CASE_ID or self.parent_id != PARENT
                or self.requirement_id != SESSION_CLI_CREATE_GET_REQUIREMENT_ID
                or self.source_function != SOURCE_FUNCTION
                or not self.source_expectation):
            raise RunStateError("Session CLI create/list/get case plan changed without review")


def session_cli_create_get_case_plan() -> SessionCliCreateGetCasePlan:
    binding = session_source_binding()
    row = next((item for item in binding["requirements"]
                if item["id"] == SESSION_CLI_CREATE_GET_REQUIREMENT_ID
                and item["function"] == SOURCE_FUNCTION), None)
    if row is None:
        raise RunStateError("Session CLI create/list/get source obligation changed")
    plan = SessionCliCreateGetCasePlan(
        case_id=SESSION_CLI_CREATE_GET_CASE_ID, parent_id=PARENT,
        requirement_id=SESSION_CLI_CREATE_GET_REQUIREMENT_ID,
        source_function=SOURCE_FUNCTION,
        source_expectation=row["expectation"])
    plan.validate()
    return plan


def session_cli_create_get_adaptability() -> dict:
    return {
        "case_id": SESSION_CLI_CREATE_GET_CASE_ID,
        "legacy_id": PARENT,
        "requirement_ids": [SESSION_CLI_CREATE_GET_REQUIREMENT_ID],
        "source_function": SOURCE_FUNCTION,
        "selected_obligations": ["cli-create", "cli-list", "cli-get", "exact-group-delete"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The case is mock-verified against both product-CLI credential contexts, "
            "but real Singlebox/preprod effect admission and execution remain separate gates."
        ),
    }


def validate_session_cli_create_get_adapter(adapter: object) -> None:
    if not isinstance(adapter, RemoteSessionCliCreateGetAdapter):
        raise RunStateError("Session CLI create/list/get requires its shared adapter")
    for name in (
        "create_owned_cli_group", "create_listed_cli_session",
        "read_session_list", "read_session_exact",
        "delete_owned_group", "verify_group_deleted",
    ):
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(f"Session CLI adapter is missing {name}")


def _validate_cli_evidence(result: AdapterCall, operation: str,
                            credential_kind: str) -> None:
    evidence = result.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_CLI"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "driver"
            or evidence.get("credential_kind") != credential_kind
            or evidence.get("fixture_effect") not in {
                "OWNED_GROUP_SESSIONS_READ_THEN_GENERIC_GROUP_DELETE",
                "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET",
            }
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"Session CLI evidence for {operation} is outside the contract")


def execute_session_cli_create_get_case(adapter: object) -> CaseExecution:
    session_cli_create_get_case_plan()
    validate_session_cli_create_get_adapter(adapter)
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_listed_cli_session(),
            adapter.read_session_list(),
            adapter.read_session_exact(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create", "session-cli.session.create",
            "session-cli.session.list", "session-cli.session.get",
            "session_cli.group.delete", "session_cli.group.verify_deleted",
        )
        credential_kind = ("owned_local_bot_token"
                           if adapter.profile["environment"]["tier"] == "local"
                           else "human_cookie")
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(f"Session CLI {operation} did not return AdapterCall")
            call.validate()
            if operation.startswith("session-cli."):
                _validate_cli_evidence(call, operation, credential_kind)
            else:
                evidence = call.evidence
                if (evidence.get("transport") != "PRODUCT_HTTP"
                        or evidence.get("operation") != operation
                        or evidence.get("caller_role") != "driver"
                        or evidence.get("credential_kind") != credential_kind
                        or evidence.get("http_status") not in {200, 404}
                        or evidence.get("fixture_effect") != "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET"):
                    raise RunStateError(f"Session cleanup evidence for {operation} is invalid")
            records.append(dict(call.evidence))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__

    step = StepOutcome(
        requirement_id=SESSION_CLI_CREATE_GET_REQUIREMENT_ID, method=CASE_METHOD,
        status="PASS" if error_type is None else "NOT_RUN",
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "explicit_session_listed": True,
            "exact_session_read": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": "OWNED_GROUP_AND_SESSIONS_DELETED_AFTER_CREATE_LIST_GET",
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    completed = 1 if error_type is None else 0
    execution = CaseExecution(
        case_id=SESSION_CLI_CREATE_GET_CASE_ID, parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,), completed_count=completed, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


# Dual product-CLI Session Bot-groups case
SESSION_CLI_BOT_GROUPS_CASE_ID = "DUAL-SESSION-CLI-BOT-GROUPS-01"
SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID = "SESSION.bot-groups"
SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION = "test_bot_groups_of_bot"
SESSION_CLI_BOT_GROUPS_CASE_METHOD = "execute_session_cli_bot_groups_case"

def session_cli_bot_groups_case_plan() -> dict:
    binding = session_source_binding()
    row = next((x for x in binding["requirements"]
                if x["id"] == SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID
                and x["function"] == SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION), None)
    if row is None:
        raise RunStateError("Session CLI Bot-groups source obligation changed")
    return {"case_id": SESSION_CLI_BOT_GROUPS_CASE_ID, "parent_id": PARENT,
            "requirement_id": SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID,
            "source_function": SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION,
            "source_expectation": row["expectation"]}

def session_cli_bot_groups_adaptability() -> dict:
    return {"case_id": SESSION_CLI_BOT_GROUPS_CASE_ID, "legacy_id": PARENT,
            "requirement_ids": [SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID],
            "source_function": SESSION_CLI_BOT_GROUPS_SOURCE_FUNCTION,
            "selected_obligations": ["cli-group-create", "bot-groups-list", "exact-group-delete"],
            "environments": ["singlebox-product-cli", "preprod-product-cli"],
            "local_executor_status": "LOCAL_FULL_EXECUTOR",
            "cli_surface": "product-bcs-cli-session", "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "blocker": "Mock-only coverage; real Singlebox/preprod execution remain separate gates."}

class RemoteSessionCliBotGroupsAdapter(RemoteSessionCliCreateGetAdapter):
    def create_owned_cli_group(self) -> AdapterCall:
        result = super().create_owned_cli_group()
        self.session_id = self.initial_session_id
        return result

    def read_driver_bot_groups(self) -> AdapterCall:
        if self.group_id is None:
            raise RunStateError("Session CLI Bot-groups must follow an exact Group create")
        data, record = self.cleanup_client.call(
            SESSION_CLI_BOT_GROUPS_OPERATION, expected=(200,),
            request_id=f"e2e-session-cli-bot-groups-{os.urandom(8).hex()}")
        items = data.get("items") if isinstance(data, dict) else None
        exact = [x for x in items if isinstance(x, dict)
                 and (x.get("group_id") or x.get("id")) == self.group_id] \
            if isinstance(items, list) else []
        if not (isinstance(data, dict) and data.get("bot_uuid") == self.driver_id
                and data.get("offset") == 0 and data.get("limit") == 50
                and isinstance(items, list) and len(items) <= 50
                and type(data.get("total")) is int and data["total"] >= len(items)
                and len(exact) == 1):
            raise RunStateError("Session CLI Bot-groups listing is not exact")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP", "caller_role": "driver",
            "credential_kind": ("owned_local_bot_token"
                                if self.profile["environment"]["tier"] == "local"
                                else "human_cookie"),
            "case_class": "bot-groups", "fixture_effect": FINAL_EFFECT,
            "remote_validation": "NOT_RUN", "grants_release_credit": False,
        })
        return AdapterCall(data=data, evidence=evidence)

    def delete_owned_group(self) -> AdapterCall:
        result = super().delete_owned_group()
        result.evidence.update({"remote_validation": "NOT_RUN", "grants_release_credit": False})
        return result

    def verify_group_deleted(self) -> AdapterCall:
        result = super().verify_group_deleted()
        result.evidence.update({"remote_validation": "NOT_RUN", "grants_release_credit": False})
        return result

def execute_session_cli_bot_groups_case(adapter: object) -> CaseExecution:
    session_cli_bot_groups_case_plan()
    if not isinstance(adapter, RemoteSessionCliBotGroupsAdapter):
        raise RunStateError("Session CLI Bot-groups requires its shared adapter")
    records, error_type, group_id = [], None, ""
    try:
        calls = (adapter.create_owned_cli_group(), adapter.read_driver_bot_groups(),
                 adapter.delete_owned_group(), adapter.verify_group_deleted())
        operations = ("session-cli.group.create", SESSION_CLI_BOT_GROUPS_OPERATION,
                     SESSION_CLI_GROUP_DELETE_OPERATION, SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION)
        credential_kind = ("owned_local_bot_token"
                           if adapter.profile["environment"]["tier"] == "local"
                           else "human_cookie")
        for call, operation in zip(calls, operations):
            if not isinstance(call, AdapterCall):
                raise RunStateError(f"Session CLI Bot-groups {operation} did not return AdapterCall")
            call.validate()
            evidence = call.evidence
            if not (evidence.get("caller_role") == "driver"
                    and evidence.get("credential_kind") == credential_kind
                    and evidence.get("fixture_effect") in {
                        SESSION_CLI_CREATE_GET_EFFECT, FINAL_EFFECT}
                    and evidence.get("remote_validation") == "NOT_RUN"
                    and evidence.get("grants_release_credit") is False):
                raise RunStateError(f"Session CLI Bot-groups evidence for {operation} is invalid")
            records.append(dict(evidence))
        group_id = adapter.group_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_CLI_BOT_GROUPS_REQUIREMENT_ID,
        method=SESSION_CLI_BOT_GROUPS_CASE_METHOD, status=status,
        data=None if error_type is not None else {
            "group_id": group_id, "bot_groups_exact": True,
            "group_deleted": True, "group_absence_verified": True},
        evidence={} if error_type is not None else {
            "requests": records, "fixture_effect": FINAL_EFFECT,
            "remote_validation": "NOT_RUN", "grants_release_credit": False},
        error_type=error_type)
    execution = CaseExecution(
        case_id=SESSION_CLI_BOT_GROUPS_CASE_ID, parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED", steps=(step,),
        completed_count=1 if error_type is None else 0, total_count=1,
        error_type=error_type, ready_to_execute=error_type is None)
    execution.validate()
    return execution
