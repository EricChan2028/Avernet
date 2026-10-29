"""Shared legacy-HTTP contract for the Workspace group governance slice."""

from __future__ import annotations

from dataclasses import dataclass
import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome, record_error_detail
from .run_state import RunStateError
from .write_transport import WriteClient
from .workspace_cli_profile import (
    WORKSPACE_GROUP_CREATE_OPERATION,
    WORKSPACE_GROUP_DETAIL_OPERATION,
    WORKSPACE_GROUP_LABEL_OPERATION,
    WORKSPACE_GROUP_VISIBILITY_OPERATION,
    validate_workspace_group_governance_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-GROUP-GOVERNANCE-01"
REQUIREMENT_IDS = (
    "WORKSPACE.label",
    "WORKSPACE.visibility",
)
CASE_METHODS = (
    "relabel_owned_workspace_group",
    "set_workspace_group_visibility",
)
FIXTURE_EFFECT = "RETAINED_BOUNDED_OWNED_BOTS_GROUP_GOVERNANCE"
SOURCE_FUNCTIONS = (
    "test_update_group_label",
    "test_update_group_visibility",
)
STEP_OPERATIONS = {
    "WORKSPACE.label": WORKSPACE_GROUP_LABEL_OPERATION,
    "WORKSPACE.visibility": WORKSPACE_GROUP_VISIBILITY_OPERATION,
}
GOVERNANCE_LABEL = "governance-label-e2e"
GOVERNANCE_VISIBILITY = "public"


@dataclass(frozen=True)
class WorkspaceGroupGovernanceCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace group governance case is invalid")


def workspace_group_governance_case_plan() -> WorkspaceGroupGovernanceCasePlan:
    from pathlib import Path

    from .migration_inventory import validate_contract
    from .run_state import read

    root = Path(__file__).resolve().parents[1]
    contract = validate_contract(
        read(root / "migration-contracts.json"),
        source_root=root.parent / "e2e-test",
    )
    story = next((row for row in contract["stories"]
                  if row["legacy_id"] == WORKSPACE_PARENT), None)
    if story is None:
        raise RunStateError("workspace group governance selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    selected = {requirement_id: rows_by_id.get(requirement_id)
                for requirement_id in REQUIREMENT_IDS}
    for requirement_id in REQUIREMENT_IDS:
        row = selected[requirement_id]
        if row is None or row["functions"][0] != dict(zip(
                REQUIREMENT_IDS, SOURCE_FUNCTIONS))[requirement_id]:
            raise RunStateError(
                "workspace group governance source obligation changed")
    if story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("workspace group governance parent is already migrated")
    plan = WorkspaceGroupGovernanceCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_group_governance_adaptability() -> dict:
    plan = workspace_group_governance_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["label", "visibility"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-http-legacy-groups",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The legacy Group governance HTTP contract is implemented "
            "and mock-verified, but real Singlebox or preprod effect "
            "admission and execution remain independent gates; the "
            "governed Group stays retained."
        ),
    }


def validate_workspace_group_governance_adapter(adapter: object) -> None:
    for name in CASE_METHODS:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(
                f"workspace group governance adapter is missing {name}")


def _validate_evidence(result: AdapterCall, *, operation: str,
                        case_class: str) -> None:
    evidence = result.evidence
    requests = evidence.get("requests")
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_HTTP"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "case_owned_driver"
            or evidence.get("credential_kind")
                not in {"owned_local_bot_token", "human_cookie"}
            or evidence.get("case_class") != case_class
            or evidence.get("fixture_effect") != FIXTURE_EFFECT
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(requests, list) or not requests):
        raise RunStateError(
            f"workspace group governance evidence for {case_class} "
            "is outside the contract")
    for record in requests:
        if (not isinstance(record, dict)
                or record.get("method") not in {"GET", "POST", "PUT"}
                or not isinstance(record.get("path"), str)
                or not record["path"]
                or not isinstance(record.get("submitted_request_id"), str)):
            raise RunStateError(
                "workspace group governance request record is "
                "outside the contract")


def _validate_label(data: object, group_id: str, caller: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or not isinstance(group_id, str) or not group_id
            or data.get("label") != GOVERNANCE_LABEL
            or data.get("changed_by") != caller
            or data.get("readback_verified") is not True):
        raise RunStateError("workspace group governance label is not exact")


def _validate_visibility(data: object, group_id: str, caller: str) -> None:
    if (not isinstance(data, dict)
            or data.get("group_id") != group_id
            or not isinstance(group_id, str) or not group_id
            or data.get("visibility") != GOVERNANCE_VISIBILITY
            or data.get("changed_by") != caller
            or data.get("readback_verified") is not True):
        raise RunStateError(
            "workspace group governance visibility is not exact")


def execute_workspace_group_governance_case(adapter: object) -> CaseExecution:
    plan = workspace_group_governance_case_plan()
    validate_workspace_group_governance_adapter(adapter)
    caller = getattr(adapter, "caller_actor_id", "")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    first_error_detail: str | None = None
    for requirement_id, method_name in zip(REQUIREMENT_IDS, CASE_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={}, error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            result.validate()
            _validate_evidence(
                result, operation=STEP_OPERATIONS[requirement_id],
                case_class=requirement_id.split(".")[1],
            )
            group_id = result.data.get("group_id", "")
            if requirement_id == "WORKSPACE.label":
                _validate_label(result.data, group_id, caller)
            else:
                _validate_visibility(result.data, group_id, caller)
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="PASS",
                                      data=result.data,
                                      evidence=dict(result.evidence),
                                      error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            detail = record_error_detail(error)
            if first_error_detail is None:
                first_error_detail = detail
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={},
                                      error_type=error_type,
                                      error_detail=detail))
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed,
        total_count=len(REQUIREMENT_IDS),
        error_type=error_type, error_detail=first_error_detail,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


class RemoteWorkspaceGroupGovernanceAdapter:
    """Drive the reviewed legacy Group label and visibility updates."""

    def __init__(self, profile: dict):
        self.profile = validate_workspace_group_governance_profile(profile)
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.group_id: str | None = None
        # resolve_group_member_caller resolves a bot token to itself and
        # a Human Cookie to the coordinator Bot the Human owns, so both
        # tiers acknowledge mutations with the dedicated driver Bot id.
        self.caller_actor_id = self.driver_id
        self.writer = WriteClient(self._http_profile(self.profile))

    @staticmethod
    def _http_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        if profile["environment"]["tier"] == "local":
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {
                    "Authorization": profile["auth"]["driver_token_env"],
                },
            }
        else:
            value["auth"] = {
                "kind": "human-cookie",
                "header_env": {"Cookie": profile["auth"]["cookie_env"]},
            }
        value["entrypoint"] = {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return value

    def _credential_kind(self) -> str:
        return ("owned_local_bot_token"
                if self.profile["environment"]["tier"] == "local"
                else "human_cookie")

    def _evidence(self, *records: dict, operation: str,
                    case_class: str) -> dict:
        evidence = {
            "transport": "PRODUCT_HTTP",
            "operation": operation,
            "caller_role": "case_owned_driver",
            "credential_kind": self._credential_kind(),
            "case_class": case_class,
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "requests": [dict(record) for record in records],
        }
        secrets = list(self.writer.headers.values())
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError(
                "workspace group governance evidence must not expose "
                "a credential")
        return evidence

    def _require_group(self) -> str:
        if self.group_id is None:
            raise RunStateError(
                "workspace group governance requires its exact "
                "created Group")
        return self.group_id

    def _create_setup_group(self) -> dict:
        data, record = self.writer.call(
            WORKSPACE_GROUP_CREATE_OPERATION,
            body={"driver_bot": self.driver_id}, expected=(200,))
        if (not isinstance(data, dict)
                or not isinstance(data.get("id"), str) or not data["id"]
                or data.get("driver_bot") != self.driver_id
                or not isinstance(data.get("participants"), list)
                or data.get("participants") != [self.driver_id]
                or not isinstance(data.get("group_kind"), str)
                or not data["group_kind"]
                or data.get("created") is not True):
            raise RunStateError(
                "workspace group governance setup did not acknowledge "
                "the exact owned Group")
        self.group_id = data["id"]
        return record

    def _participants(self, data: object) -> list[str]:
        rows = data.get("participants") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RunStateError(
                "workspace group governance participants are invalid")
        participants = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("bot_uuid"), str):
                raise RunStateError(
                    "workspace group governance participant is invalid")
            participants.append(row["bot_uuid"])
        return participants

    def relabel_owned_workspace_group(self) -> AdapterCall:
        if self.group_id is None:
            create_record = self._create_setup_group()
        else:
            create_record = None
        group_id = self._require_group()
        patched, label_record = self.writer.call(
            WORKSPACE_GROUP_LABEL_OPERATION, group_id=group_id,
            body={"label": GOVERNANCE_LABEL}, expected=(200,))
        if (not isinstance(patched, dict) or patched.get("updated") is not True
                or patched.get("group_id") != group_id
                or patched.get("label") != GOVERNANCE_LABEL
                or patched.get("changed_by") != self.caller_actor_id):
            raise RunStateError(
                "workspace group governance label update did not "
                "acknowledge the exact owned Group")
        detail, detail_record = self.writer.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        if (not isinstance(detail, dict)
                or detail.get("label") != GOVERNANCE_LABEL
                or detail.get("driver_bot") != self.driver_id
                or self.driver_id not in self._participants(detail)):
            raise RunStateError(
                "workspace group governance label read-back differs "
                "from the acknowledged update")
        records = ([create_record] if create_record is not None else [])
        records.extend([label_record, detail_record])
        return AdapterCall(
            data={"group_id": group_id, "label": GOVERNANCE_LABEL,
                  "changed_by": self.caller_actor_id,
                  "readback_verified": True},
            evidence=self._evidence(*records,
                                      operation=WORKSPACE_GROUP_LABEL_OPERATION,
                                      case_class="label"),
        )

    def set_workspace_group_visibility(self) -> AdapterCall:
        group_id = self._require_group()
        patched, visibility_record = self.writer.call(
            WORKSPACE_GROUP_VISIBILITY_OPERATION, group_id=group_id,
            body={"visibility": GOVERNANCE_VISIBILITY}, expected=(200,))
        if (not isinstance(patched, dict) or patched.get("updated") is not True
                or patched.get("group_id") != group_id
                or patched.get("visibility") != GOVERNANCE_VISIBILITY
                or patched.get("changed_by") != self.caller_actor_id):
            raise RunStateError(
                "workspace group governance visibility update did not "
                "acknowledge the exact owned Group")
        detail, detail_record = self.writer.call(
            WORKSPACE_GROUP_DETAIL_OPERATION, group_id=group_id,
            expected=(200,))
        if (not isinstance(detail, dict)
                or detail.get("visibility") != GOVERNANCE_VISIBILITY
                or detail.get("driver_bot") != self.driver_id
                or self.driver_id not in self._participants(detail)):
            raise RunStateError(
                "workspace group governance visibility read-back differs "
                "from the acknowledged update")
        return AdapterCall(
            data={"group_id": group_id,
                  "visibility": GOVERNANCE_VISIBILITY,
                  "changed_by": self.caller_actor_id,
                  "readback_verified": True},
            evidence=self._evidence(
                visibility_record, detail_record,
                operation=WORKSPACE_GROUP_VISIBILITY_OPERATION,
                case_class="visibility"),
        )
