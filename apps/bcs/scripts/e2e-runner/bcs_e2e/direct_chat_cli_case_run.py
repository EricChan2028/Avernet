"""Command entry for the reviewed dual Direct-chat product-CLI case run.

The five reviewed DM obligations chain the frozen legacy product bcs-cli
Direct-chat leaves only; every step rides PRODUCT_CLI because the DM run
lifecycle routes are not expressible through the cleanup WriteClient
templates (no {run_id} or bot-target placeholder), the main --timeout-ms
budget is a client dispatch header, and the pinned preprod browser_origin
is validated but unconsumed by this slice.  The frozen operation
vocabulary, the reviewed route bindings, the fail-closed profile validator
and the one-shot launcher envelope live in the focused leaf module; this
module owns the frozen DM plan, the dual-environment adapters, the
in-order execution projection and the command entry.
"""

from __future__ import annotations

from pathlib import Path
import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .direct_chat_cli_case_leaf import (
    DIRECT_CHAT_ADAPTER_METHODS,
    DIRECT_CHAT_CLI_EFFECT,
    DIRECT_CHAT_CLI_OPERATIONS,
    DIRECT_CHAT_CLI_PROFILE_CONTRACT,
    DIRECT_CHAT_CLI_RELATIONSHIPS,
    DIRECT_CHAT_DECLARED_ROUTES,
    DIRECT_CHAT_MAIN_CANCEL_OPERATION,
    DIRECT_CHAT_MAIN_READ_OPERATION,
    DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
    DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
    DIRECT_CHAT_PING_CANCEL_OPERATION,
    DIRECT_CHAT_PING_SUBMIT_OPERATION,
    DIRECT_CHAT_PING_TERMINAL_OPERATION,
    DIRECT_CHAT_REQUIREMENTS,
    DIRECT_CHAT_REQUIREMENT_OPERATIONS,
    _HTTP_PRODUCT_OPERATIONS,
    DirectChatCliRunner,
    DirectChatCliRunnerError,
    PING_MESSAGE,
    MAIN_MESSAGE,
    MAIN_TAGS,
    MAIN_TIMEOUT_MS,
    direct_chat_cli_credential_kind,
    expected_step_transport,
    validate_direct_chat_cli_profile,
)
from .inventory import executor_digest
from .local_direct_chat_contract import PARENT, direct_chat_source_binding
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    require_resolved,
    unique_keys,
)
from .run_state import RunStateError, write
from .workspace_cli_commands import WorkspaceCliRunnerError

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-direct-chat-case-run-v1"
PROFILE_FILE = "direct-chat-profile.json"
BINDING_FILE = "direct-chat-binding.json"
REPORT_FILE = "direct-chat-run.json"

CASE_KEYS = ("direct-chat",)
CASE_COMMAND = "dual-direct-chat-run"

DUAL_DIRECT_CHAT_CASE_ID = "DUAL-DIRECT-CHAT-01"
DUAL_DIRECT_CHAT_CASE_METHOD = "execute_direct_chat_case"


def direct_chat_cli_case_plan() -> dict:
    """Derive the frozen DM plan from its reviewed migration contract."""
    binding = direct_chat_source_binding()
    if (binding["parent"] != PARENT
            or [row["id"] for row in binding["requirements"]] != list(DIRECT_CHAT_REQUIREMENTS)
            or [(row["method"], row["path"]) for row in binding["requirements"]] != [
                ("POST", "/bots/${BOT_PM_UUID}/chat-async"),
                ("POST", "/bots/${BOT_PM_UUID}/chat-async"),
                ("GET", "/chat/runs/${run_id}"),
                ("POST", "/chat/runs/${run_id}/cancel"),
                ("GET", "/chat/runs/${run_id}"),
            ]
            or tuple(DIRECT_CHAT_REQUIREMENT_OPERATIONS) != tuple(DIRECT_CHAT_REQUIREMENTS)
            or any(DIRECT_CHAT_REQUIREMENT_OPERATIONS[row["id"]] is None
                   for row in binding["requirements"])):
        raise RunStateError("Direct chat DM obligation map changed")
    return {
        "case_id": DUAL_DIRECT_CHAT_CASE_ID,
        "parent_id": PARENT,
        "requirement_ids": list(DIRECT_CHAT_REQUIREMENTS),
        "requirement_operations": {
            key: list(operations)
            for key, operations in DIRECT_CHAT_REQUIREMENT_OPERATIONS.items()
        },
        "cli_operations": list(DIRECT_CHAT_CLI_OPERATIONS),
        "adapter_methods": list(DIRECT_CHAT_ADAPTER_METHODS),
        "source_expectations": [row["expectation"] for row in binding["requirements"]],
    }


def dual_direct_chat_adaptability() -> dict:
    plan = direct_chat_cli_case_plan()
    return {
        "case_id": DUAL_DIRECT_CHAT_CASE_ID,
        "legacy_id": plan["parent_id"],
        "requirement_ids": plan["requirement_ids"],
        "selected_obligations": [
            "cli-chat-ping-submit-detach", "cli-chat-main-submit-detach",
            "cli-chat-run-status", "cli-chat-run-cancel",
            "cli-chat-run-terminal-read", "supplementary-ping-lifecycle"],
        "cli_operations": plan["cli_operations"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-cli-chat",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Mock-verified Singlebox and preprod product-CLI contexts; the DM run "
            "lifecycle stays on PRODUCT_CLI because the cleanup WriteClient "
            "templates cannot express {run_id} routes. Live effect admission "
            "remains a separate gate."
        ),
    }


class RemoteLocalDirectChatAdapter:
    """Chain the seven DM leaves and project exact DM lifecycle evidence."""

    _profile_validator = staticmethod(validate_direct_chat_cli_profile)

    def __init__(self, profile: dict, runner: object):
        self.profile = self._profile_validator(profile)
        if not callable(getattr(runner, "execute", None)):
            raise RunStateError("Direct chat requires a product-CLI runner")
        self.runner = runner
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.ping_run_id: str | None = None
        self.ping_session_id: str | None = None
        self.main_run_id: str | None = None
        self.main_session_id: str | None = None
        self.main_cancel_version = 0
        self.ping_cancel_version = 0
        self.fixture_effect = DIRECT_CHAT_CLI_EFFECT

    def _credential_kind(self) -> str:
        return ("owned_local_bot_token"
                if self.profile["environment"]["tier"] == "local"
                else "human_cookie")

    def _run(self, operation: str, *, case_class: str, **kwargs) -> AdapterCall:
        try:
            result = self.runner.execute(operation, **kwargs)
        except Exception as error:
            raise RunStateError(
                f"Direct chat product-CLI execution for {operation} failed"
            ) from error
        if (not isinstance(result, dict) or set(result) != {"data", "evidence"}
                or not isinstance(result["evidence"], dict)):
            raise RunStateError(f"Direct chat CLI result for {operation} is invalid")
        evidence = dict(result["evidence"])
        evidence.update({
            "case_class": case_class,
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=result["data"], evidence=evidence)

    def _submit_projection(self, data: object, operation: str) -> dict:
        if (not isinstance(data, dict)
                or set(data) != {"delivered", "submitted", "bot_uuid", "run_id",
                                 "session_id", "state", "delivery_status",
                                 "wait_reason", "error_message"}):
            raise RunStateError(
                f"Direct chat {operation} admission projection is not exact")
        if data["submitted"] is not True or data.get("error_message") is not None:
            raise RunStateError(f"Direct chat {operation} admission was not accepted")
        if (not isinstance(data["run_id"], str) or not data["run_id"]
                or not isinstance(data["session_id"], str) or not data["session_id"]
                or data["bot_uuid"] != self.member_id):
            raise RunStateError(f"Direct chat {operation} identity differs from the exact PM")
        identifier(data["run_id"], f"direct chat {operation} Run ID")
        identifier(data["session_id"], f"direct chat {operation} Session ID")
        if data["state"] not in {"pending", "submitted"}:
            raise RunStateError(f"Direct chat {operation} admission state differs")
        return dict(data)

    def _status_projection(self, data: object, operation: str) -> dict:
        required = {"run_id", "bot_uuid", "from_bot_id", "session_id", "state",
                    "response", "error_message", "version", "content_truncated",
                    "is_terminal"}
        if not isinstance(data, dict) or not required <= set(data):
            raise RunStateError(f"Direct chat {operation} status projection is not exact")
        return dict(data)

    def _cancel_projection(self, data: object, operation: str) -> dict:
        required = {"run_id", "cancelled", "state", "response", "error_message",
                    "version", "content_truncated"}
        if not isinstance(data, dict) or not required <= set(data):
            raise RunStateError(f"Direct chat {operation} cancel projection is not exact")
        return dict(data)

    def submit_ping_run(self) -> AdapterCall:
        if self.ping_run_id is not None:
            raise RunStateError("Direct chat ping submit is a one-shot operation")
        call = self._run(
            DIRECT_CHAT_PING_SUBMIT_OPERATION, case_class="ping-submit",
            message=PING_MESSAGE, bot_uuid=self.member_id)
        data = self._submit_projection(call.data, DIRECT_CHAT_PING_SUBMIT_OPERATION)
        self.ping_run_id = data["run_id"]
        self.ping_session_id = data["session_id"]
        return AdapterCall(data=data, evidence=call.evidence)

    def submit_main_run(self) -> AdapterCall:
        if self.ping_run_id is None or self.main_run_id is not None:
            raise RunStateError("Direct chat main submit must follow the ping submit")
        call = self._run(
            DIRECT_CHAT_MAIN_SUBMIT_OPERATION, case_class="main-submit",
            message=MAIN_MESSAGE, tags=list(MAIN_TAGS),
            timeout_ms=MAIN_TIMEOUT_MS, bot_uuid=self.member_id)
        data = self._submit_projection(call.data, DIRECT_CHAT_MAIN_SUBMIT_OPERATION)
        if (data["run_id"] == self.ping_run_id
                or data["session_id"] == self.ping_session_id):
            raise RunStateError(
                "Direct chat main submit returned the ping Run or Session identity")
        self.main_run_id = data["run_id"]
        self.main_session_id = data["session_id"]
        projected = dict(data)
        projected.update({
            "message": MAIN_MESSAGE,
            "timeout_ms": MAIN_TIMEOUT_MS,
            "tags": list(MAIN_TAGS),
            "ids_distinct_from_ping": True,
        })
        return AdapterCall(data=projected, evidence=call.evidence)

    def read_main_run(self) -> AdapterCall:
        if self.main_run_id is None:
            raise RunStateError("Direct chat main read requires its exact Run")
        call = self._run(
            DIRECT_CHAT_MAIN_READ_OPERATION, case_class="main-read",
            run_id=self.main_run_id)
        data = self._status_projection(call.data, DIRECT_CHAT_MAIN_READ_OPERATION)
        if (data["run_id"] != self.main_run_id
                or data["session_id"] != self.main_session_id
                or data["bot_uuid"] != self.member_id
                or data["from_bot_id"] != self.driver_id
                or data["state"] != "running"
                or data["is_terminal"] is not False
                or data.get("error_message") is not None
                or data.get("response", {}).get("content") != ""
                or data.get("content_truncated") is not False):
            raise RunStateError(
                "Direct chat main read differs from the exact running Run")
        return AdapterCall(data=data, evidence=call.evidence)

    def cancel_main_run(self) -> AdapterCall:
        if self.main_run_id is None:
            raise RunStateError("Direct chat main cancel requires its exact Run")
        call = self._run(
            DIRECT_CHAT_MAIN_CANCEL_OPERATION, case_class="main-cancel",
            run_id=self.main_run_id)
        data = self._cancel_projection(call.data, DIRECT_CHAT_MAIN_CANCEL_OPERATION)
        if (data["run_id"] != self.main_run_id
                or data["cancelled"] is not True
                or data["state"] != "cancelled"
                or data.get("response", {}).get("content") != ""
                or data.get("error_message") is not None
                or data.get("content_truncated") is not False):
            raise RunStateError(
                "Direct chat main cancel acknowledgement differs from the exact Run")
        self.main_cancel_version = data["version"]
        return AdapterCall(data=data, evidence=call.evidence)

    def read_main_terminal_run(self) -> AdapterCall:
        if self.main_run_id is None or self.main_cancel_version == 0:
            raise RunStateError(
                "Direct chat main terminal read requires its cancelled Run")
        call = self._run(
            DIRECT_CHAT_MAIN_TERMINAL_OPERATION, case_class="main-terminal-read",
            run_id=self.main_run_id)
        data = self._status_projection(call.data, DIRECT_CHAT_MAIN_TERMINAL_OPERATION)
        if (data["run_id"] != self.main_run_id
                or data["session_id"] != self.main_session_id
                or data["bot_uuid"] != self.member_id
                or data["from_bot_id"] != self.driver_id
                or data["state"] != "cancelled"
                or data["is_terminal"] is not True
                or type(data.get("version")) is not int
                or data["version"] < self.main_cancel_version
                or data.get("error_message") is not None
                or data.get("response", {}).get("content") != ""
                or data.get("content_truncated") is not False):
            raise RunStateError(
                "Direct chat main terminal read differs from the exact cancelled Run")
        return AdapterCall(data=data, evidence=call.evidence)

    def cancel_ping_run(self) -> AdapterCall:
        if self.ping_run_id is None:
            raise RunStateError("Direct chat ping cancel requires its exact Run")
        call = self._run(
            DIRECT_CHAT_PING_CANCEL_OPERATION, case_class="ping-cancel-supplementary",
            run_id=self.ping_run_id)
        data = self._cancel_projection(call.data, DIRECT_CHAT_PING_CANCEL_OPERATION)
        if (data["run_id"] != self.ping_run_id
                or data["cancelled"] is not True
                or data["state"] != "cancelled"):
            raise RunStateError(
                "Direct chat ping cancel acknowledgement differs from the exact Run")
        self.ping_cancel_version = data["version"]
        return AdapterCall(data=data, evidence=call.evidence)

    def read_ping_terminal_run(self) -> AdapterCall:
        if self.ping_run_id is None or self.ping_cancel_version == 0:
            raise RunStateError(
                "Direct chat ping terminal read requires its cancelled Run")
        call = self._run(
            DIRECT_CHAT_PING_TERMINAL_OPERATION, case_class="ping-terminal-supplementary",
            run_id=self.ping_run_id)
        data = self._status_projection(call.data, DIRECT_CHAT_PING_TERMINAL_OPERATION)
        if (data["run_id"] != self.ping_run_id
                or data["session_id"] != self.ping_session_id
                or data["state"] != "cancelled"
                or data["is_terminal"] is not True
                or type(data.get("version")) is not int
                or data["version"] < self.ping_cancel_version):
            raise RunStateError(
                "Direct chat ping terminal read differs from the exact cancelled Run")
        return AdapterCall(data=data, evidence=call.evidence)

    def ping_lifecycle(self) -> dict:
        return {
            "ping_run_id": self.ping_run_id or "",
            "ping_session_id": self.ping_session_id or "",
            "main_run_id": self.main_run_id or "",
            "main_session_id": self.main_session_id or "",
            "cancel_version": self.main_cancel_version,
            "ping_cancel_version": self.ping_cancel_version,
        }


class DualDirectChatAdapter(RemoteLocalDirectChatAdapter):
    """Dual-environment Direct-chat adapter with tier-aware credential labels."""

    def _credential_kind(self) -> str:
        return direct_chat_cli_credential_kind(self.profile)


def _require_step_evidence(adapter: object, call: object, operation: str) -> dict:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"Direct chat {operation} did not return AdapterCall")
    call.validate()
    if (call.evidence.get("transport") != expected_step_transport(operation)
            or not isinstance(call.evidence.get("caller_role"), str)
            or not call.evidence["caller_role"]
            or call.evidence.get("credential_kind") != adapter._credential_kind()
            or call.evidence.get("remote_validation") != "NOT_RUN"
            or call.evidence.get("grants_release_credit") is not False
            or call.evidence.get("fixture_effect") != adapter.fixture_effect):
        raise RunStateError(f"Direct chat evidence for {operation} is invalid")
    return dict(call.evidence)


def execute_direct_chat_case(adapter: object) -> CaseExecution:
    direct_chat_cli_case_plan()
    if not isinstance(adapter, DualDirectChatAdapter):
        raise RunStateError("Direct chat requires its dual adapter")
    records: dict[str, dict] = {}
    projections: dict[str, dict] = {}
    error_type: str | None = None
    try:
        ping_submit = adapter.submit_ping_run()
        records[DIRECT_CHAT_PING_SUBMIT_OPERATION] = _require_step_evidence(
            adapter, ping_submit, DIRECT_CHAT_PING_SUBMIT_OPERATION)
        projections["ping_submit"] = ping_submit.data
        main_submit = adapter.submit_main_run()
        records[DIRECT_CHAT_MAIN_SUBMIT_OPERATION] = _require_step_evidence(
            adapter, main_submit, DIRECT_CHAT_MAIN_SUBMIT_OPERATION)
        projections["main_submit"] = main_submit.data
        main_read = adapter.read_main_run()
        records[DIRECT_CHAT_MAIN_READ_OPERATION] = _require_step_evidence(
            adapter, main_read, DIRECT_CHAT_MAIN_READ_OPERATION)
        projections["main_read"] = main_read.data
        main_cancel = adapter.cancel_main_run()
        records[DIRECT_CHAT_MAIN_CANCEL_OPERATION] = _require_step_evidence(
            adapter, main_cancel, DIRECT_CHAT_MAIN_CANCEL_OPERATION)
        projections["main_cancel"] = main_cancel.data
        main_terminal = adapter.read_main_terminal_run()
        records[DIRECT_CHAT_MAIN_TERMINAL_OPERATION] = _require_step_evidence(
            adapter, main_terminal, DIRECT_CHAT_MAIN_TERMINAL_OPERATION)
        projections["main_terminal"] = main_terminal.data
        ping_cancel = adapter.cancel_ping_run()
        records[DIRECT_CHAT_PING_CANCEL_OPERATION] = _require_step_evidence(
            adapter, ping_cancel, DIRECT_CHAT_PING_CANCEL_OPERATION)
        projections["ping_cancel"] = ping_cancel.data
        ping_terminal = adapter.read_ping_terminal_run()
        records[DIRECT_CHAT_PING_TERMINAL_OPERATION] = _require_step_evidence(
            adapter, ping_terminal, DIRECT_CHAT_PING_TERMINAL_OPERATION)
        projections["ping_terminal"] = ping_terminal.data
    except Exception as error:
        error_type = type(error).__name__

    def step(requirement_id: str, method: str, data: object,
             operations: tuple[str, ...]) -> StepOutcome:
        if error_type is not None:
            return StepOutcome(
                requirement_id=requirement_id, method=method, status="NOT_RUN",
                data=None, evidence={}, error_type=error_type)
        return StepOutcome(
            requirement_id=requirement_id, method=method, status="PASS",
            data=data,
            evidence={
                "requests": [dict(records[operation]) for operation in operations],
                "fixture_effect": adapter.fixture_effect,
                "remote_validation": "NOT_RUN",
                "grants_release_credit": False,
            },
            error_type=None)

    if error_type is None:
        main_terminal_data = dict(projections["main_terminal"])
        main_terminal_data["terminal_version_gte_cancel_version"] = True
        main_terminal_data["supplementary_ping_lifecycle"] = {
            "cancel": dict(projections["ping_cancel"]),
            "terminal": dict(projections["ping_terminal"]),
            "parent_requirement_credit": False,
        }
    steps = (
        step(DIRECT_CHAT_REQUIREMENTS[0], "submit_ping_run",
             projections.get("ping_submit"), (DIRECT_CHAT_PING_SUBMIT_OPERATION,)),
        step(DIRECT_CHAT_REQUIREMENTS[1], "submit_main_run",
             projections.get("main_submit"), (DIRECT_CHAT_MAIN_SUBMIT_OPERATION,)),
        step(DIRECT_CHAT_REQUIREMENTS[2], "read_main_run",
             projections.get("main_read"), (DIRECT_CHAT_MAIN_READ_OPERATION,)),
        step(DIRECT_CHAT_REQUIREMENTS[3], "cancel_main_run",
             projections.get("main_cancel"), (DIRECT_CHAT_MAIN_CANCEL_OPERATION,)),
        step(DIRECT_CHAT_REQUIREMENTS[4], "read_main_terminal_run",
             main_terminal_data if error_type is None else None,
             (DIRECT_CHAT_MAIN_TERMINAL_OPERATION, DIRECT_CHAT_PING_CANCEL_OPERATION,
              DIRECT_CHAT_PING_TERMINAL_OPERATION)),
    )
    execution = CaseExecution(
        case_id=DUAL_DIRECT_CHAT_CASE_ID,
        parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=steps,
        completed_count=5 if error_type is None else 0,
        total_count=5,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


_CASES = {
    "direct-chat": {
        "case_id": DUAL_DIRECT_CHAT_CASE_ID,
        "validator": validate_direct_chat_cli_profile,
        "adapter": DualDirectChatAdapter,
        "execute": execute_direct_chat_case,
        "fixture_effect": DIRECT_CHAT_CLI_EFFECT,
    },
}


def load_direct_chat_cli_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("direct chat case must be direct-chat")
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"direct chat {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError(f"direct chat {case_key} profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def dual_direct_chat_case_run(
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
        raise ConfigError("direct chat case must be direct-chat")
    run_id = identifier(run_id, f"direct chat {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"direct chat {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_direct_chat_cli_case_profile(profile_path, case_key)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
            or requested_state_dir.exists()
            or not requested_state_dir.parent.is_dir()
            or requested_state_dir.parent.is_symlink()):
        raise ConfigError(f"direct chat {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"direct chat {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    write(state_dir / PROFILE_FILE, profile)
    binding = {
        "schema_version": SCHEMA_VERSION, "contract": REPORT_CONTRACT,
        "case_id": _CASES[case_key]["case_id"], "parent_id": PARENT,
        "run_id": run_id, "profile_digest": digest,
        "executor_digest": artifact, "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(state_dir / BINDING_FILE, binding)
    try:
        runner = DirectChatCliRunner(
            profile,
            binary=Path(cli_binary).resolve(),
            binary_sha256=cli_binary_sha256,
            state_dir=state_dir,
        )
        adapter = _CASES[case_key]["adapter"](profile, runner)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, OSError) as error:
        raise ConfigError(
            f"direct chat {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION, "contract": REPORT_CONTRACT,
        "case_id": execution.case_id, "parent_id": PARENT, "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest, "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256, "state_dir": str(state_dir),
        "environment": {"tier": profile["environment"]["tier"],
                        "id": profile["environment"]["id"],
                        "entrypoint_kind": profile["entrypoint"]["kind"]},
        "case": execution.snapshot(),
        "execution": {"status": execution.status,
                      "completed_count": execution.completed_count,
                      "total_count": execution.total_count,
                      "error_type": execution.error_type},
        "resource_disposition": _CASES[case_key]["fixture_effect"],
        "remote_validation": "NOT_RUN", "grants_release_credit": False,
        "credential_kind": direct_chat_cli_credential_kind(profile),
    }
    write(state_dir / REPORT_FILE, report)
    return report
