"""Command entry for the reviewed dual Session Service product-CLI cases.

The four reviewed CLI-SESSION-SERVICE obligations already own exact
Singlebox adapters (``local_session_service_cli`` / ``local_session_service_completion``).
Those adapters normalise both credential contexts per tier, so this module
only adds the dual-environment receipts: DUAL case ids, per-step transport
labelling, dual adaptability rows, and the ``dual-session-service-run``
command plumbing mirrored after ``dual-session-run``.
"""

from __future__ import annotations

from pathlib import Path
import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .inventory import executor_digest
from .local_operator_cli_contract import SESSIONS_SERVICES_PARENT
from .local_session_service_cli import (
    FIXTURE_EFFECT,
    SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
    SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
    SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
    SESSION_SERVICE_MEMBER_CHAT_FIXTURE_EFFECT,
    SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
    RemoteLocalSessionServiceLifecycleAdapter,
    RemoteLocalSessionServiceMemberChatAdapter,
    local_session_service_lifecycle_case_plan,
    local_session_service_member_chat_case_plan,
)
from .local_session_service_completion import (
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_COMPLETE_EFFECT,
    SESSION_SERVICE_COMPLETE_REQUIREMENT,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_EFFECT,
    SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
    RemoteLocalSessionServiceCompleteAdapter,
    RemoteLocalSessionServiceRoundtripAdapter,
    local_session_service_complete_case_plan,
    local_session_service_roundtrip_case_plan,
)
from .profile import ConfigError, fingerprint, identifier, require_resolved, unique_keys
from .run_state import RunStateError, write
from .session_cli_profile import (
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_lifecycle_profile,
)
from .workspace_cli_commands import WorkspaceCliRunnerError
from .workspace_cli_runner import WorkspaceCliRunner

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-session-service-case-run-v1"
SESSION_SERVICE_PARENT = SESSIONS_SERVICES_PARENT
CASE_KEYS = (
    "service-lifecycle",
    "service-member-chat",
    "service-complete",
    "service-roundtrip",
)

DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID = "DUAL-SESSION-SERVICE-LIFECYCLE-01"
DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID = "DUAL-SESSION-SERVICE-MEMBER-CHAT-01"
DUAL_SESSION_SERVICE_COMPLETE_CASE_ID = "DUAL-SESSION-SERVICE-COMPLETE-01"
DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID = "DUAL-SESSION-SERVICE-ROUNDTRIP-01"

DUAL_SESSION_SERVICE_LIFECYCLE_CASE_METHOD = "execute_session_service_lifecycle_case"
DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD = "execute_session_service_member_chat_case"
DUAL_SESSION_SERVICE_COMPLETE_CASE_METHOD = "execute_session_service_complete_case"
DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_METHOD = "execute_session_service_roundtrip_case"

# Operations the owned adapters drive through the cleanup WriteClient instead
# of the product CLI.  Every other session-cli leaf is a PRODUCT_CLI step.
_HTTP_PRODUCT_OPERATIONS = frozenset({
    SESSION_CLI_GROUP_DELETE_OPERATION,
    SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
    SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
    SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
    SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
})


def session_service_credential_kind(profile: dict) -> str:
    """Label the preprod credential family without echoing any secret."""
    if profile["environment"]["tier"] == "local":
        return "owned_local_bot_token"
    if (profile["contract"] == SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT
            and profile["auth"]["kind"] == "human-cookie-with-owned-bot-tokens"):
        return "human_cookie_with_owned_bot_tokens"
    return "human_cookie"


def expected_step_transport(operation: str) -> str:
    return ("PRODUCT_HTTP" if operation in _HTTP_PRODUCT_OPERATIONS
            else "PRODUCT_CLI")


# Dual adaptability rows for the reviewed inventory (never execution admission).


def dual_session_service_lifecycle_adaptability() -> dict:
    return {
        "case_id": DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID,
        "legacy_id": SESSION_SERVICE_PARENT,
        "requirement_ids": [SESSION_SERVICE_LIFECYCLE_REQUIREMENT],
        "selected_obligations": [
            "cli-group-create", "explicit-session-create", "session-list",
            "session-get", "session-patch", "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-verified Singlebox and preprod product-CLI contexts; "
            "live effect admission remains a separate gate."
        ),
    }


def dual_session_service_member_chat_adaptability() -> dict:
    return {
        "case_id": DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
        "legacy_id": SESSION_SERVICE_PARENT,
        "requirement_ids": [SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT],
        "selected_obligations": [
            "cli-group-create", "explicit-session-create", "session-member-add",
            "session-member-mute", "session-get", "session-chat",
            "session-message-history", "session-invite-link", "session-member-remove",
            "session-get", "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-verified Singlebox and preprod product-CLI contexts; "
            "live effect admission remains a separate gate."
        ),
    }


def dual_session_service_complete_adaptability() -> dict:
    return {
        "case_id": DUAL_SESSION_SERVICE_COMPLETE_CASE_ID,
        "legacy_id": SESSION_SERVICE_PARENT,
        "requirement_ids": [SESSION_SERVICE_COMPLETE_REQUIREMENT],
        "selected_obligations": [
            "cli-group-create", "explicit-session-create", "session-completion",
            "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-session",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-verified Singlebox and preprod product-CLI contexts; "
            "live effect admission remains a separate gate."
        ),
    }


def dual_session_service_roundtrip_adaptability() -> dict:
    return {
        "case_id": DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        "legacy_id": SESSION_SERVICE_PARENT,
        "requirement_ids": [SESSION_SERVICE_ROUNDTRIP_REQUIREMENT],
        "selected_obligations": [
            "cli-service-group-create", "explicit-session-create",
            "session-completion", "service-concurrency-config",
            "detached-service-invocation", "service-status-readback",
            "bounded-service-wait", "creator-scoped-session-delete",
            "bot-scoped-service-session-delete", "exact-group-delete",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-service",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-verified Singlebox and preprod product-CLI contexts; "
            "live service effect admission remains a separate gate."
        ),
    }


# Dual adapters: the reviewed Singlebox steps are reused unchanged; only the
# completion family needs the tier-aware ACE-trio credential label.


class DualSessionServiceLifecycleAdapter(RemoteLocalSessionServiceLifecycleAdapter):
    """Dual-environment Session Service lifecycle adapter."""


class DualSessionServiceMemberChatAdapter(RemoteLocalSessionServiceMemberChatAdapter):
    """Dual-environment Session Service member/chat lifecycle adapter."""


class DualSessionServiceCompleteAdapter(RemoteLocalSessionServiceCompleteAdapter):
    """Dual-environment Session completion adapter with tier-aware labels."""

    def _credential_kind(self) -> str:
        return session_service_credential_kind(self.profile)


class DualSessionServiceRoundtripAdapter(RemoteLocalSessionServiceRoundtripAdapter):
    """Dual-environment service round-trip adapter with tier-aware labels."""

    def _credential_kind(self) -> str:
        return session_service_credential_kind(self.profile)


def _require_step_evidence(adapter: object, call: object, operation: str) -> dict:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"Session Service {operation} did not return AdapterCall")
    call.validate()
    if (call.evidence.get("transport") != expected_step_transport(operation)
            or not isinstance(call.evidence.get("caller_role"), str)
            or not call.evidence["caller_role"]
            or call.evidence.get("credential_kind") != adapter._credential_kind()
            or call.evidence.get("remote_validation") != "NOT_RUN"
            or call.evidence.get("grants_release_credit") is not False
            or call.evidence.get("fixture_effect") != adapter.fixture_effect):
        raise RunStateError(f"Session Service evidence for {operation} is invalid")
    return dict(call.evidence)


def execute_session_service_lifecycle_case(adapter: object) -> CaseExecution:
    local_session_service_lifecycle_case_plan()
    if not isinstance(adapter, DualSessionServiceLifecycleAdapter):
        raise RunStateError("Session Service lifecycle requires its dual adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.read_session_list(),
            adapter.read_session_exact(),
            adapter.rename_session_title(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.list",
            "session-cli.session.get",
            "session-cli.session.patch",
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            records.append(_require_step_evidence(adapter, call, operation))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
        method=DUAL_SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "session_created": True,
            "session_listed": True,
            "session_read": True,
            "session_renamed": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": adapter.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID,
        parent_id=SESSION_SERVICE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,),
        completed_count=1 if error_type is None else 0,
        total_count=1,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


def execute_session_service_member_chat_case(adapter: object) -> CaseExecution:
    local_session_service_member_chat_case_plan()
    if not isinstance(adapter, DualSessionServiceMemberChatAdapter):
        raise RunStateError("Session Service member-chat requires its dual adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.add_session_member(),
            adapter.mute_session_member(),
            adapter.read_muted_session(),
            adapter.send_session_chat(),
            adapter.read_session_history(),
            adapter.create_session_invite(),
            adapter.remove_session_member(),
            adapter.read_removed_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.member.add",
            "session-cli.session.member.patch_mode",
            "session-cli.session.get.muted",
            "session-cli.session.chat",
            "session-cli.session.messages",
            "session-cli.session.invite",
            "session-cli.session.member.remove",
            "session-cli.session.get.removed",
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            records.append(_require_step_evidence(adapter, call, operation))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
        method=DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "member_admitted": True,
            "member_muted": True,
            "chat_delivered": True,
            "history_observable": True,
            "invite_minted": True,
            "member_removed": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": adapter.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
        parent_id=SESSION_SERVICE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,),
        completed_count=1 if error_type is None else 0,
        total_count=1,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


def execute_session_service_complete_case(adapter: object) -> CaseExecution:
    local_session_service_complete_case_plan()
    if not isinstance(adapter, DualSessionServiceCompleteAdapter):
        raise RunStateError("Session Service completion requires its dual adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    try:
        calls = (
            adapter.create_owned_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.complete_owned_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.group.create",
            "session-cli.session.create",
            "session-cli.session.complete",
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            records.append(_require_step_evidence(adapter, call, operation))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_COMPLETE_REQUIREMENT,
        method=DUAL_SESSION_SERVICE_COMPLETE_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "session_completed": True,
            "completion_output_preserved": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": adapter.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=DUAL_SESSION_SERVICE_COMPLETE_CASE_ID,
        parent_id=SESSION_SERVICE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,),
        completed_count=1 if error_type is None else 0,
        total_count=1,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


def execute_session_service_roundtrip_case(adapter: object) -> CaseExecution:
    local_session_service_roundtrip_case_plan()
    if not isinstance(adapter, DualSessionServiceRoundtripAdapter):
        raise RunStateError("Session Service round trip requires its dual adapter")
    records: list[dict] = []
    error_type: str | None = None
    group_id = ""
    session_id = ""
    service_session_id = ""
    try:
        calls = (
            adapter.create_service_cli_group(),
            adapter.create_owned_cli_session(),
            adapter.complete_owned_session(),
            adapter.configure_service_concurrency(),
            adapter.invoke_detached_service(),
            adapter.read_service_status(),
            adapter.wait_for_service_terminal_contract(),
            adapter.delete_owned_chat_session(),
            adapter.delete_owned_service_session(),
            adapter.delete_owned_group(),
            adapter.verify_group_deleted(),
        )
        operations = (
            "session-cli.service.group.create",
            "session-cli.session.create",
            "session-cli.session.complete",
            SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION,
            "session-cli.service.invoke",
            "session-cli.service.status",
            "session-cli.service.wait",
            SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION,
            SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION,
            SESSION_CLI_GROUP_DELETE_OPERATION,
            SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION,
        )
        for call, operation in zip(calls, operations):
            records.append(_require_step_evidence(adapter, call, operation))
        group_id = adapter.group_id or ""
        session_id = adapter.session_id or ""
        service_session_id = adapter.service_session_id or ""
    except Exception as error:
        error_type = type(error).__name__
    status = "PASS" if error_type is None else "NOT_RUN"
    step = StepOutcome(
        requirement_id=SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
        method=DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
        status=status,
        data=None if error_type is not None else {
            "group_id": group_id,
            "session_id": session_id,
            "service_session_id": service_session_id,
            "service_identity_preserved": True,
            "bounded_wait_contract_exact": True,
            "settings_form": adapter.settings_form,
            "chat_session_deleted": True,
            "service_session_deleted": True,
            "group_deleted": True,
            "group_absence_verified": True,
        },
        evidence={} if error_type is not None else {
            "requests": records,
            "fixture_effect": adapter.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        },
        error_type=error_type,
    )
    execution = CaseExecution(
        case_id=DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        parent_id=SESSION_SERVICE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=(step,),
        completed_count=1 if error_type is None else 0,
        total_count=1,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


_CASES = {
    "service-lifecycle": {
        "case_id": DUAL_SESSION_SERVICE_LIFECYCLE_CASE_ID,
        "validator": validate_session_cli_lifecycle_profile,
        "adapter": DualSessionServiceLifecycleAdapter,
        "execute": execute_session_service_lifecycle_case,
        "fixture_effect": FIXTURE_EFFECT,
    },
    "service-member-chat": {
        "case_id": DUAL_SESSION_SERVICE_MEMBER_CHAT_CASE_ID,
        "validator": validate_session_cli_lifecycle_profile,
        "adapter": DualSessionServiceMemberChatAdapter,
        "execute": execute_session_service_member_chat_case,
        "fixture_effect": SESSION_SERVICE_MEMBER_CHAT_FIXTURE_EFFECT,
    },
    "service-complete": {
        "case_id": DUAL_SESSION_SERVICE_COMPLETE_CASE_ID,
        "validator": validate_session_cli_complete_invoke_profile,
        "adapter": DualSessionServiceCompleteAdapter,
        "execute": execute_session_service_complete_case,
        "fixture_effect": SESSION_SERVICE_COMPLETE_EFFECT,
    },
    "service-roundtrip": {
        "case_id": DUAL_SESSION_SERVICE_ROUNDTRIP_CASE_ID,
        "validator": validate_session_cli_complete_invoke_profile,
        "adapter": DualSessionServiceRoundtripAdapter,
        "execute": execute_session_service_roundtrip_case,
        "fixture_effect": SESSION_SERVICE_ROUNDTRIP_EFFECT,
    },
}


def load_session_service_cli_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "session_service case must be service-lifecycle, service-member-chat, "
            "service-complete or service-roundtrip")
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"session service {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError(f"session service {case_key} profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def dual_session_service_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError(
            "session_service case must be service-lifecycle, service-member-chat, "
            "service-complete or service-roundtrip")
    run_id = identifier(run_id, f"session service {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"session service {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_session_service_cli_case_profile(profile_path, case_key)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError(f"session service {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"session service {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    profile_file = state_dir / f"session-service-{case_key}-profile.json"
    binding_file = state_dir / f"session-service-{case_key}-binding.json"
    report_file = state_dir / f"session-service-{case_key}-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": _CASES[case_key]["case_id"],
        "parent_id": SESSION_SERVICE_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        runner = WorkspaceCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
        adapter = _CASES[case_key]["adapter"](profile, runner)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, OSError) as error:
        raise ConfigError(f"session service {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": SESSION_SERVICE_PARENT,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "case": execution.snapshot(),
        "execution": {
            "status": execution.status,
            "completed_count": execution.completed_count,
            "total_count": execution.total_count,
            "error_type": execution.error_type,
            "error_detail": execution.error_detail,
        },
        "resource_disposition": _CASES[case_key]["fixture_effect"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_kind": session_service_credential_kind(profile),
    }
    write(report_file, report)
    return report
