"""Shared legacy-HTTP contract for the Workspace proposal confirmation.

The proposal confirmation drives one minted proposal for the owned
trio: the driver Bot requests a release-readiness Group, the owning
Human previews the confirmation page and confirms it, the confirmed
Group is read back with the proposed driver, and the Human finalizes
the Group with an exact delete followed by a not-found read. The
confirmation URL is a capability: only its bounded in-memory path
reaches the preview and confirm requests, and every evidence record
carries the redacted capability path plus a report-safe digest.

The confirmation page and action are minted-token scoped routes that
do not bind a route-level caller in the reviewed OCB HTTP surface;
the case records that as a documented identity gap instead of an
exact-actor admission, while the group detail read after confirmation
stays an open read-only projection.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .run_state import RunStateError
from .write_transport import WriteClient
from .workspace_cli_profile import (
    WORKSPACE_PROPOSAL_CONFIRM_OPERATION,
    WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION,
    WORKSPACE_PROPOSAL_DELETE_OPERATION,
    WORKSPACE_PROPOSAL_DETAIL_OPERATION,
    WORKSPACE_PROPOSAL_REQUEST_OPERATION,
    validate_workspace_proposal_profile,
)

WORKSPACE_PARENT = "story_user_operates_group_workspace"
CASE_ID = "DUAL-WORKSPACE-PROPOSAL-01"
REQUIREMENT_IDS = ("WORKSPACE.proposal",)
CASE_METHODS = ("confirm_group_proposal_flow",)
FIXTURE_EFFECT = "FINALIZED_BOUNDED_OWNED_BOTS_PROPOSAL_CONFIRMATION"
SOURCE_FUNCTIONS = ("_story_group_proposal_confirmation",)
STEP_OPERATIONS = {
    "WORKSPACE.proposal": WORKSPACE_PROPOSAL_REQUEST_OPERATION,
}
PROPOSAL_TOPIC = "Review release readiness"

# Route-level caller gaps in the reviewed OCB HTTP surface.
ROUTE_CALLER_GAPS = frozenset({
    WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION,
    WORKSPACE_PROPOSAL_CONFIRM_OPERATION,
})
OPEN_READS = frozenset({WORKSPACE_PROPOSAL_DETAIL_OPERATION,
                        WORKSPACE_PROPOSAL_CONFIRM_PAGE_OPERATION})


def route_caller_binding(operation: object) -> str:
    """Classify a route's caller admission for the evidence trail."""
    if operation in ROUTE_CALLER_GAPS:
        return "documented_gap"
    if operation in OPEN_READS:
        return "open_read"
    return "route_bound"


@dataclass(frozen=True)
class WorkspaceProposalCasePlan:
    case_id: str
    parent_id: str
    requirement_ids: tuple[str, ...]
    source_revision: str
    source_functions: tuple[str, ...]

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != WORKSPACE_PARENT
                or self.requirement_ids != REQUIREMENT_IDS
                or self.source_functions != SOURCE_FUNCTIONS):
            raise RunStateError("workspace proposal case is invalid")


def workspace_proposal_case_plan() -> WorkspaceProposalCasePlan:
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
        raise RunStateError("workspace proposal selected the wrong parent")
    rows_by_id = {row["id"]: row for row in story["requirements"]}
    expected_functions = dict(zip(REQUIREMENT_IDS, SOURCE_FUNCTIONS))
    for requirement_id in REQUIREMENT_IDS:
        row = rows_by_id.get(requirement_id)
        if row is None or row["functions"][0] != expected_functions[requirement_id]:
            raise RunStateError("workspace proposal source obligation changed")
    if story["migration_status"] != "NOT_MIGRATED":
        raise RunStateError("workspace proposal parent is already migrated")
    plan = WorkspaceProposalCasePlan(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        requirement_ids=REQUIREMENT_IDS,
        source_revision=contract["source_revision"],
        source_functions=SOURCE_FUNCTIONS,
    )
    plan.validate()
    return plan


def workspace_proposal_adaptability() -> dict:
    plan = workspace_proposal_case_plan()
    return {
        "case_id": plan.case_id,
        "legacy_id": plan.parent_id,
        "requirement_ids": list(plan.requirement_ids),
        "selected_obligations": ["proposal"],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LEGACY_OWNED_LOCAL_EXECUTOR",
        "cli_surface": "product-bcs-http-legacy-groups",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The legacy Group proposal confirmation HTTP contract is "
            "implemented and mock-verified, but real Singlebox or "
            "preprod effect admission and execution remain independent "
            "gates; the confirmed Group is finalized exactly, not "
            "retained."
        ),
    }


def validate_workspace_proposal_adapter(adapter: object) -> None:
    for name in CASE_METHODS:
        if not callable(getattr(adapter, name, None)):
            raise RunStateError(
                f"workspace proposal adapter is missing {name}")


def _validate_evidence(result: AdapterCall, *, operation: str,
                        case_class: str, credential_kind: str,
                        credential_kinds: list[str]) -> None:
    evidence = result.evidence
    requests = evidence.get("requests")
    if (not isinstance(evidence, dict)
            or evidence.get("transport") != "PRODUCT_HTTP"
            or evidence.get("operation") != operation
            or evidence.get("caller_role") != "case_owned_driver"
            or evidence.get("credential_kind") != credential_kind
            or evidence.get("credential_kinds") != credential_kinds
            or evidence.get("case_class") != case_class
            or evidence.get("fixture_effect") != FIXTURE_EFFECT
            or evidence.get("remote_validation") != "NOT_RUN"
            or evidence.get("grants_release_credit") is not False
            or not isinstance(requests, list)
            or len(requests) != len(credential_kinds)):
        raise RunStateError(
            f"workspace proposal evidence for {case_class} "
            "is outside the contract")
    for record, kind in zip(requests, credential_kinds):
        if (not isinstance(record, dict)
                or record.get("method") not in {"GET", "POST", "PUT",
                                                "PATCH", "DELETE"}
                or not isinstance(record.get("path"), str)
                or not record["path"]
                or not isinstance(record.get("submitted_request_id"), str)
                or record.get("credential_kind") != kind
                or record.get("route_caller_binding")
                != route_caller_binding(record.get("operation"))):
            raise RunStateError(
                "workspace proposal request record is outside the "
                "contract")


def _validate_proposal_created(data: object, driver: str,
                               member: str, qa: str) -> str:
    # Bound the transport result to the derived capability path:
    # the transport admits the owned entrypoint-scoped capability
    # URL but exposes only its bounded in-memory path and a
    # report-safe digest, so the raw usable URL never reaches the
    # Case executor or its evidence trail.
    confirm_path = (data.get("confirm_path")
                    if isinstance(data, dict) else None)
    if (not isinstance(data, dict)
            or data.get("proposal_created") is not True
            or data.get("driver_bot") != driver
            or data.get("participants") != [member, qa]
            or not isinstance(confirm_path, str) or not confirm_path
            or not isinstance(data.get("confirm_url_sha256"), str)
            or len(data["confirm_url_sha256"]) != 64
            or "confirm_url" in data):
        raise RunStateError(
            "workspace proposal was not created for the exact owned "
            "trio with a redacted capability path")
    return confirm_path


def _validate_confirmation_page(data: object, topic: str) -> None:
    if (not isinstance(data, dict)
            or data.get("confirm_path") != "/groups/<redacted-capability>/confirm"
            or data.get("topic_present") is not True
            or data.get("form_action_matches") is not True):
        raise RunStateError(
            "workspace proposal confirmation page did not preserve the "
            "topic and same-token form action")


def _validate_confirmation_created(data: object) -> str:
    if (not isinstance(data, dict)
            or data.get("created") is not True
            or not isinstance(data.get("group_id"), str)
            or not data["group_id"]):
        raise RunStateError(
            "workspace proposal confirmation did not acknowledge the "
            "created Group")
    return data["group_id"]


def _validate_confirmed_detail(data: object, group_id: str,
                               driver: str) -> None:
    if (not isinstance(data, dict)
            or data.get("id") != group_id
            or data.get("driver_bot") != driver
            or not isinstance(data.get("group_kind"), str)
            or not data["group_kind"]):
        raise RunStateError(
            "workspace proposal confirmation read-back differs from the "
            "confirmed Group")


def _validate_delete_ack(data: object, group_id: str) -> None:
    if (not isinstance(data, dict)
            or data.get("deleted") is not True
            or data.get("id") != group_id):
        raise RunStateError(
            "workspace proposal deletion did not acknowledge the exact "
            "Group finalization")


def _validate_proposal_result(data: object, driver: str,
                              member: str, qa: str) -> None:
    if (not isinstance(data, dict)
            or not isinstance(data.get("group_id"), str)
            or not data["group_id"]
            or data.get("proposal_created") is not True
            or data.get("confirmation_previewed") is not True
            or data.get("confirmed") is not True
            or data.get("deleted") is not True
            or data.get("not_found_verified") is not True
            or data.get("proposed_driver") != driver
            or data.get("proposed_participants") != [member, qa]
            or data.get("confirmation_topic") != PROPOSAL_TOPIC
            or "confirm_url" in data):
        raise RunStateError(
            "workspace proposal confirmation result is outside the "
            "contract")


def execute_workspace_proposal_case(adapter: object) -> CaseExecution:
    plan = workspace_proposal_case_plan()
    validate_workspace_proposal_adapter(adapter)
    preprod = adapter.profile["environment"]["tier"] == "preprod"
    bot_credential = "owned_local_bot_token"
    human_credential = ("human_cookie" if preprod
                        else "owned_local_bot_token")
    steps: list[StepOutcome] = []
    error_type: str | None = None
    completed = 0
    for requirement_id, method_name in zip(REQUIREMENT_IDS, CASE_METHODS):
        if error_type is not None:
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={},
                                      error_type=None))
            continue
        try:
            result = getattr(adapter, method_name)()
            if not isinstance(result, AdapterCall):
                raise RunStateError(method_name + " did not return AdapterCall")
            result.validate()
            _validate_evidence(
                result,
                operation=WORKSPACE_PROPOSAL_REQUEST_OPERATION,
                case_class="proposal_confirmation",
                credential_kind=bot_credential,
                credential_kinds=[bot_credential, human_credential,
                                  human_credential, human_credential,
                                  human_credential, human_credential])
            _validate_proposal_result(result.data, adapter.driver_id,
                                      adapter.member_id, adapter.qa_id)
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="PASS",
                                      data=result.data,
                                      evidence=dict(result.evidence),
                                      error_type=None))
            completed += 1
        except Exception as error:
            error_type = type(error).__name__
            steps.append(StepOutcome(requirement_id=requirement_id,
                                      method=method_name, status="NOT_RUN",
                                      data=None, evidence={},
                                      error_type=error_type))
    execution = CaseExecution(
        case_id=CASE_ID, parent_id=WORKSPACE_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(steps), completed_count=completed,
        total_count=len(REQUIREMENT_IDS),
        error_type=error_type, ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


class RemoteWorkspaceProposalAdapter:
    """Drive the reviewed legacy Group proposal confirmation."""

    def __init__(self, profile: dict):
        self.profile = validate_workspace_proposal_profile(profile)
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.qa_id = self.profile["actors"]["qa"]["bot_id"]
        self.group_id: str | None = None
        self.confirm_path: str | None = None
        self.driver_client = WriteClient(
            self._bot_profile(self.profile["auth"]["driver_token_env"]))
        self.human_client = (
            WriteClient(self._cookie_profile())
            if self.profile["environment"]["tier"] == "preprod"
            else self.driver_client)

    @staticmethod
    def _entrypoint(profile: dict) -> dict:
        return {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }

    def _bot_profile(self, credential_ref: str) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "authenticated-product",
            "header_env": {"Authorization": credential_ref},
        }
        value["entrypoint"] = self._entrypoint(self.profile)
        return value

    def _cookie_profile(self) -> dict:
        value = json.loads(json.dumps(self.profile, allow_nan=False))
        value["auth"] = {
            "kind": "human-cookie",
            "header_env": {"Cookie": self.profile["auth"]["cookie_env"]},
        }
        value["entrypoint"] = self._entrypoint(self.profile)
        return value

    def _credential_kind(self, credential: str) -> str:
        if self.profile["environment"]["tier"] == "local":
            return "owned_local_bot_token"
        return ("human_cookie" if credential == "human"
                else "owned_local_bot_token")

    def _evidence(self, *calls: tuple[dict, str], operation: str,
                    case_class: str) -> dict:
        requests = []
        credential_kinds = []
        for record, credential in calls:
            kind = self._credential_kind(credential)
            entry = dict(record)
            entry["credential_kind"] = kind
            entry["route_caller_binding"] = route_caller_binding(
                entry.get("operation"))
            requests.append(entry)
            credential_kinds.append(kind)
        evidence = {
            "transport": "PRODUCT_HTTP",
            "operation": operation,
            "caller_role": "case_owned_driver",
            "credential_kind": credential_kinds[0],
            "credential_kinds": credential_kinds,
            "case_class": case_class,
            "fixture_effect": FIXTURE_EFFECT,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
            "requests": requests,
        }
        writers = {id(self.driver_client): self.driver_client,
                   id(self.human_client): self.human_client}
        secrets = [secret for writer in writers.values()
                   for secret in writer.headers.values()]
        if any(secret and secret in repr(evidence) for secret in secrets):
            raise RunStateError(
                "workspace proposal evidence must not expose a "
                "credential")
        return evidence

    def confirm_group_proposal_flow(self) -> AdapterCall:
        if self.group_id is not None or self.confirm_path is not None:
            raise RunStateError(
                "workspace proposal confirmation is a one-shot "
                "operation")
        proposal, proposal_record = self.driver_client.request_group_proposal(
            topic=PROPOSAL_TOPIC,
            suggested_participants=[self.member_id, self.qa_id],
            suggested_driver=self.driver_id)
        confirm_path = _validate_proposal_created(
            proposal, self.driver_id, self.member_id, self.qa_id)
        self.confirm_path = confirm_path
        page, page_record = (
            self.human_client.preview_group_proposal_confirmation(
                confirm_path=confirm_path, topic=PROPOSAL_TOPIC))
        _validate_confirmation_page(page, PROPOSAL_TOPIC)
        created, created_record = (
            self.human_client.confirm_group_proposal(
                confirm_path=confirm_path))
        self.group_id = _validate_confirmation_created(created)
        detail, detail_record = self.human_client.call(
            WORKSPACE_PROPOSAL_DETAIL_OPERATION,
            group_id=self.group_id, expected=(200,))
        _validate_confirmed_detail(detail, self.group_id, self.driver_id)
        deleted, deleted_record = self.human_client.call(
            WORKSPACE_PROPOSAL_DELETE_OPERATION,
            group_id=self.group_id, expected=(200,))
        _validate_delete_ack(deleted, self.group_id)
        gone, gone_record = self.human_client.call(
            WORKSPACE_PROPOSAL_DETAIL_OPERATION,
            group_id=self.group_id, expected=(404,))
        if gone is not None or gone_record.get("http_status") != 404:
            raise RunStateError(
                "workspace proposal finalization left the confirmed "
                "Group readable")
        return AdapterCall(
            data={"group_id": self.group_id,
                  "proposal_created": True,
                  "confirmation_previewed": True,
                  "confirmed": True,
                  "deleted": True,
                  "not_found_verified": True,
                  "proposed_driver": self.driver_id,
                  "proposed_participants": [self.member_id, self.qa_id],
                  "confirmation_topic": PROPOSAL_TOPIC},
            evidence=self._evidence(
                (proposal_record, "driver"), (page_record, "human"),
                (created_record, "human"), (detail_record, "human"),
                (deleted_record, "human"), (gone_record, "human"),
                operation=WORKSPACE_PROPOSAL_REQUEST_OPERATION,
                case_class="proposal_confirmation"),
        )
