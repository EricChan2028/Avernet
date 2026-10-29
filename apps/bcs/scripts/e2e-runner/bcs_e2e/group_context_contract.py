"""Source binding and execution contract for the Group Context CLI Story."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Protocol

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .run_state import RunStateError, read

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "DUAL-GROUP-CONTEXT-CLI-01"
SUITE = "dual-group-context-cli-v1"
PARENT = "story_cli_operator_manages_group_context"
RESOURCE_DISPOSITION = "REQUEST_AND_GROUP_HISTORY_RETAINED"
ADAPTER_METHODS = (
    "create_workspace",
    "add_group_context_entry",
    "add_session_context_entry",
    "add_round_context_entry",
    "get_initial_context",
    "update_group_context_entry",
    "list_context_entries",
    "get_updated_context",
    "cleanup_group_context",
)


def _function_body(text: str, name: str) -> str:
    if len(re.findall("^" + re.escape(name) + r"\(\) \{$", text, re.M)) != 1:
        raise RunStateError("Group Context source function changed")
    start = text.index(name + "() {")
    end = text.index("\n}", start)
    return text[start:end + 1]


def group_context_source_binding() -> dict:
    try:
        value = read(ROOT / "group-context-migration-contract.json")
        if (set(value) != {"schema_version", "source_revision", "parent", "sources", "requirements"}
                or value["schema_version"] != 1
                or value["parent"] != PARENT
                or not re.fullmatch("[0-9a-f]{40}", value["source_revision"])
                or set(value["sources"]) != {"cli-stories.sh"}):
            raise RunStateError("Group Context contract changed")
        source_path = ROOT.parent / "e2e-test" / "cli-stories.sh"
        if source_path.is_symlink() or not source_path.is_file():
            raise RunStateError("Group Context source is missing")
        raw = source_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value["sources"]["cli-stories.sh"]:
            raise RunStateError("Group Context source hash changed")
        body = _function_body(raw.decode("utf-8"), PARENT)
        anchors = (
            '_cli_story_create_pm_group "CLI group-context workspace"',
            '_cli_story_run "operator adds a group context entry" PM group-context add',
            '_cli_story_run "operator adds a session context entry" PM group-context add',
            '_cli_story_run "operator adds a round context entry" PM group-context add',
            '_cli_story_run "operator gets the group context entry" PM group-context get',
            '_cli_story_run "operator updates the group context entry" PM group-context update',
            '_cli_story_run "operator lists group context entries" PM group-context list',
            '_cli_story_run "operator gets the updated group context entry" PM group-context get',
            'api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"',
        )
        if any(body.count(anchor) != 1 for anchor in anchors[:-1]) or body.count(anchors[-1]) != 9:
            raise RunStateError("Group Context source anchors changed")
        ids = [row["id"] for row in value["requirements"]]
        if len(ids) != len(ADAPTER_METHODS) or len(set(ids)) != len(ids):
            raise RunStateError("Group Context obligation set changed")
        for row in value["requirements"]:
            if set(row) != {"id", "command", "expectation"}:
                raise RunStateError("Group Context obligation schema changed")
            if not isinstance(row["expectation"], str) or not 1 <= len(row["expectation"]) <= 1024:
                raise RunStateError("Group Context expectation is invalid")
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError("Group Context source binding changed") from None


def group_context_case_plan() -> dict:
    binding = group_context_source_binding()
    requirement_ids = tuple(row["id"] for row in binding["requirements"])
    if len(requirement_ids) != len(ADAPTER_METHODS):
        raise RunStateError("Group Context case plan changed before execution")
    return {
        "case_id": CASE_ID,
        "parent_id": PARENT,
        "requirement_ids": list(requirement_ids),
        "adapter_methods": list(ADAPTER_METHODS),
        "source_revision": binding["source_revision"],
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def group_context_adaptability() -> dict:
    plan = group_context_case_plan()
    return {
        **plan,
        "selected_obligations": ["group-context-full-parent"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_PARENT_EXECUTOR",
        "cli_surface": "product-bcs-cli-group-context",
        "blockers": [
            "Preprod product-CLI admission and execution remain NOT_RUN; "
            "remote_validation must stay NOT_RUN until explicitly authorized.",
            "Owned-local execution and unified Singlebox evidence receive no "
            "release credit for the full 29-Story release.",
        ],
    }


def validate_group_context_adapter(adapter: object) -> None:
    missing = [name for name in ADAPTER_METHODS if not callable(getattr(adapter, name, None))]
    if missing:
        raise RunStateError("Group Context adapter is missing methods: " + ", ".join(missing))


def _validate_call(call: object, requirement_id: str) -> AdapterCall:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"Group Context obligation {requirement_id} did not return AdapterCall")
    call.validate()
    evidence = call.evidence
    if (not isinstance(evidence, dict)
            or evidence.get("transport") not in {"PRODUCT_CLI", "PRODUCT_HTTP"}
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False):
        raise RunStateError(f"Group Context evidence for {requirement_id} is outside its contract")
    return call


def execute_group_context_case(adapter: object) -> CaseExecution:
    plan = group_context_case_plan()
    validate_group_context_adapter(adapter)
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for requirement_id, method_name in zip(plan["requirement_ids"], ADAPTER_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={}, error_type=None))
            continue
        try:
            call = _validate_call(getattr(adapter, method_name)(), requirement_id)
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="PASS", data=call.data, evidence=dict(call.evidence),
                error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=method_name,
                status="NOT_RUN", data=None, evidence={},
                error_type=error_type))
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps),
        completed_count=completed,
        total_count=len(steps),
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


class GroupContextAdapter(Protocol):
    def create_workspace(self) -> AdapterCall: ...
    def add_group_context_entry(self) -> AdapterCall: ...
    def add_session_context_entry(self) -> AdapterCall: ...
    def add_round_context_entry(self) -> AdapterCall: ...
    def get_initial_context(self) -> AdapterCall: ...
    def update_group_context_entry(self) -> AdapterCall: ...
    def list_context_entries(self) -> AdapterCall: ...
    def get_updated_context(self) -> AdapterCall: ...
    def cleanup_group_context(self) -> AdapterCall: ...
