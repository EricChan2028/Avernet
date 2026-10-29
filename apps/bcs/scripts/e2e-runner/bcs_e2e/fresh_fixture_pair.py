"""Per-run fresh owned-Bot pair provisioning for bounded friend cases.

The preprod station retains friend connections across runs.  A fixed Bot pair
therefore accumulates connected state, so a replay of a create/decision slice
can observe an already-approved friendship instead of the reviewed pending
state.  This module registers two new Human-owned Bots through the reviewed
public-v1 register path before the family Case runs, rewrites only the
profile's actor pair and friendship bindings, and returns both the effective
profile and a sanitized retention receipt.

The public-v1 face has no Bot-delete route.  Fresh Bots are intentionally
single-run fixtures and are disclosed as retained in the receipt; process and
credential cleanup remain the wrapper's existing leak-scan/orphan-sweep
contract.
"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass

from .case_executor import CaseExecution
from .profile import ConfigError, fingerprint, identifier, validate
from .local_network_register_contract import execute_network_register_case
from .remote_network_register_adapter import RemoteNetworkRegisterAdapter
from .route_bindings import _EDGE_CASES
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient

CONTRACT = "dual-fresh-owned-bot-pair-v2"
REGISTER_CONTRACT = "PUBLIC-NETWORK-REGISTER-01"
REGISTER_OPERATIONS = (
    "register.token.read",
    "register.bot.create",
    "register.bot.readback",
)
PROFILE_OPERATIONS = (
    "friend_connection_mutation.bot_detail.read",
    "friend_connection_mutation.visibility.write",
)
PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE = {
    "visibility": "public",
    "friend_check_in_strategy": "OPEN",
}
RETAINED_DISPOSITION = "FRESH_OWNED_BOT_PAIR_RETAINED_PUBLIC_V1_NO_DELETE_ROUTE"
PARTIAL_DISPOSITION = "FRESH_OWNED_BOT_PAIR_PARTIAL_RETAINED"
EMPTY_DISPOSITION = "NO_FRESH_FIXTURE_CREATED"
CLEANUP_CONTRACT = {
    "fixture_lifetime": "SINGLE_RUN_ONLY_NEVER_REUSED",
    "bot_deletion": "UNAVAILABLE_ON_PUBLIC_V1",
    "retained_resource_disclosure": "FRESH_PAIR_LISTED_IN_RECEIPT",
    "temporary_profile_restore": "PATCH_ORIGINAL_FIELDS_AND_READBACK",
    "leak_scan": "WRAPPER_POST_RUN",
    "process_orphan_sweep": "BULK_WRAPPER_POST_FAMILY",
}

_REGISTER_BINDINGS = {
    operation: {
        "method": _EDGE_CASES[operation][0],
        "path": _EDGE_CASES[operation][1],
        "api_face": "public-v1",
        "auth": _EDGE_CASES[operation][2],
        "response": "public-envelope",
        "traceparent": "disabled",
    }
    for operation in REGISTER_OPERATIONS
}

_PROFILE_BINDINGS = {
    operation: {
        "method": _EDGE_CASES[operation][0],
        "path": _EDGE_CASES[operation][1],
        "api_face": "public-v1",
        "auth": _EDGE_CASES[operation][2],
        "response": "public-envelope",
        "traceparent": "disabled",
    }
    for operation in PROFILE_OPERATIONS
}

_REQUEST_FIELDS = (
    "operation",
    "method",
    "path",
    "http_status",
    "credentials_sent",
    "authorization_sent",
    "platform_gateway_browser_context_sent",
    "browser_origin_context_sent",
    "request_id_echoed",
    "traceparent_mode",
    "identity",
)
_PROFILE_REQUEST_FIELDS = (
    "operation",
    "method",
    "path",
    "http_status",
    "credentials_sent",
    "authorization_sent",
    "request_id_echoed",
    "traceparent_mode",
    "bound_bot_id",
)


class FreshFixtureProvisioningError(RunStateError):
    """Provisioning failed after recording exactly which fresh Bots were created."""

    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = copy.deepcopy(evidence)


class FreshFixtureReleaseError(RunStateError):
    """A temporary fixture profile could not be restored after its Case run."""

    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = copy.deepcopy(evidence)


@dataclass(frozen=True)
class FreshFixturePair:
    """Effective profile for this run plus its sanitized retention receipt."""

    profile: dict
    evidence: dict


def _progress_receipt(*, run_id: str, purpose: str,
                      member_profile: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "run_id": run_id,
        "purpose": purpose,
        "status": "IN_PROGRESS",
        "register_contract": REGISTER_CONTRACT,
        "register_adapter": "RemoteNetworkRegisterAdapter",
        "register_operations": list(REGISTER_OPERATIONS),
        "fresh_pair": {"driver": None, "member": None},
        "steps": [],
        "member_profile_preparation": {
            "status": "NOT_REQUIRED" if member_profile is None else "NOT_RUN",
            "target_role": "member",
            "requested": copy.deepcopy(member_profile),
            "baseline": None,
            "observed": None,
            "requests": [],
        },
        "member_profile_release": {
            "status": "NOT_REQUIRED" if member_profile is None else "NOT_RUN",
            "target_role": "member",
            "requested": None,
            "observed": None,
            "requests": [],
        },
        "resource_disposition": "FRESH_FIXTURE_PROVISIONING_IN_FLIGHT",
        "cleanup_contract": dict(CLEANUP_CONTRACT),
    }


def initial_fresh_fixture_receipt(*, run_id: str, purpose: str,
                                  member_profile: dict | None = None) -> dict:
    """Return the in-flight receipt written before the first network call."""
    return _progress_receipt(
        run_id=identifier(run_id, "fresh fixture run ID"),
        purpose=identifier(purpose, "fresh fixture purpose"),
        member_profile=member_profile,
    )


def _redacted_error_detail(error: BaseException, profile: dict) -> str:
    detail = str(error) or type(error).__name__
    auth = profile.get("auth", {})
    references = auth.get("header_env", {}) if isinstance(auth, dict) else {}
    for reference in references.values():
        value = os.environ.get(reference, "")
        if value:
            detail = detail.replace(value, "[REDACTED]")
    detail = re.sub(r"\b[0-9a-f]{64}\b", "[REDACTED-HASH]", detail)
    return detail[:800]


def _project_register_requests(evidence: dict) -> list[dict]:
    requests = evidence.get("requests") if isinstance(evidence, dict) else None
    if not isinstance(requests, list) or len(requests) != len(REGISTER_OPERATIONS):
        raise RunStateError("fresh-pair register evidence is incomplete")
    projected = []
    for record in requests:
        if not isinstance(record, dict):
            raise RunStateError("fresh-pair register request evidence is invalid")
        row = {key: record[key] for key in _REQUEST_FIELDS if key in record}
        if row.get("operation") not in REGISTER_OPERATIONS:
            raise RunStateError("fresh-pair register operation is outside the contract")
        projected.append(row)
    if [row.get("operation") for row in projected] != list(REGISTER_OPERATIONS):
        raise RunStateError("fresh-pair register request order is outside the contract")
    return projected


def _validated_member_profile(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise ConfigError("fresh fixture member profile must be a nonempty object")
    if not set(value) <= {"visibility", "friend_check_in_strategy"}:
        raise ConfigError("fresh fixture member profile has unsupported fields")
    if "visibility" in value and value["visibility"] not in {
            "public", "protected", "private"}:
        raise ConfigError("fresh fixture member visibility is unsupported")
    if ("friend_check_in_strategy" in value
            and value["friend_check_in_strategy"] not in {
                "OPEN", "APPROVAL", "DEPT_FREE"}):
        raise ConfigError("fresh fixture member friend strategy is unsupported")
    return dict(value)


def _profile_bindings(target_actor_id: str) -> dict:
    return {
        operation: {**binding, "target_actor_id": target_actor_id}
        for operation, binding in _PROFILE_BINDINGS.items()
    }


def _project_profile_request(record: dict) -> dict:
    return {key: record[key] for key in _PROFILE_REQUEST_FIELDS if key in record}


def _prepare_member_profile(profile: dict, member_id: str,
                            fields: dict) -> tuple[dict, list[dict], dict]:
    provisioning_profile = copy.deepcopy(profile)
    provisioning_profile["edge_case_bindings"] = {
        **provisioning_profile["edge_case_bindings"],
        **_profile_bindings(member_id),
    }
    writer = WriteClient(provisioning_profile)
    reader = ReadClient(provisioning_profile)
    baseline_observation = reader.read(
        "friend.mutation.bot_detail.read", bot_id=member_id)
    baseline = baseline_observation.data
    baseline_fields = {
        key: baseline.get(key) if isinstance(baseline, dict) else None
        for key in ("visibility", "friend_check_in_strategy")
    }
    if (not isinstance(baseline, dict)
            or any(value is None for value in baseline_fields.values())
            or baseline.get("created_by") != profile["human"]["user_id"]):
        raise RunStateError(
            "fresh member Bot baseline read did not prove the exact owned state")
    _validated_member_profile(baseline_fields)
    patch_data, patch_record = writer.patch_bot_friend_profile(
        member_id, visibility=fields.get("visibility"),
        friend_check_in_strategy=fields.get("friend_check_in_strategy"))
    if (not isinstance(patch_data, dict)
            or any(patch_data.get(key) != value for key, value in fields.items())):
        raise RunStateError(
            "fresh member Bot profile patch did not acknowledge the exact state")
    observation = reader.read("friend.mutation.bot_detail.read", bot_id=member_id)
    observed = observation.data
    if (not isinstance(observed, dict)
            or any(observed.get(key) != value for key, value in fields.items())
            or observed.get("created_by") != profile["human"]["user_id"]):
        raise RunStateError(
            "fresh member Bot profile read-back did not prove the exact owned state")
    requests = [_project_profile_request(baseline_observation.evidence),
                _project_profile_request(patch_record),
                _project_profile_request(observation.evidence)]
    secrets = [secret for client in (writer, reader)
               for secret in client.headers.values()]
    if any(secret and secret in repr(requests) for secret in secrets):
        raise RunStateError("fresh member profile evidence must not expose a credential")
    projection = {
        key: observed[key]
        for key in ("bot_id", "visibility", "friend_check_in_strategy", "created_by")
        if key in observed
    }
    return projection, requests, baseline_fields


def release_fresh_owned_bot_pair(*, profile: dict, evidence: dict,
                                 member_profile: dict | None,
                                 on_progress=None) -> dict:
    """Restore a temporarily widened fresh member profile after its Case run.

    The Bots themselves remain retained and disclosed because public-v1 has no
    Bot-delete route.  Any temporary playbook fields (for example public/OPEN)
    are restored to the exact baseline observed immediately after registration
    and independently read back.
    """
    if not isinstance(evidence, dict):
        raise ConfigError("fresh fixture release requires a provisioning receipt")
    receipt = copy.deepcopy(evidence)
    release = receipt.get("member_profile_release")
    if not isinstance(release, dict):
        raise ConfigError("fresh fixture release receipt is missing")
    member_profile = _validated_member_profile(member_profile)
    if member_profile is None:
        release.update({
            "status": "NOT_REQUIRED",
            "requested": None,
            "observed": None,
            "requests": [],
        })
        if on_progress is not None:
            on_progress(copy.deepcopy(receipt))
        return receipt
    member = (receipt.get("fresh_pair") or {}).get("member")
    if receipt.get("status") != "PASS" or not isinstance(member, dict):
        release.update({
            "status": "NOT_RUN",
            "error_type": "FreshFixtureNotProvisioned",
            "error_detail": "no completed fresh member is available to restore",
        })
        if on_progress is not None:
            on_progress(copy.deepcopy(receipt))
        return receipt
    preparation = receipt.get("member_profile_preparation")
    baseline = preparation.get("baseline") if isinstance(preparation, dict) else None
    prepared = preparation.get("status") if isinstance(preparation, dict) else None
    if prepared != "PASS":
        release.update({
            "status": "NOT_RUN",
            "error_type": "MemberProfilePreparationNotCompleted",
            "error_detail": "temporary member profile was never applied",
        })
        if on_progress is not None:
            on_progress(copy.deepcopy(receipt))
        return receipt
    baseline = _validated_member_profile(baseline)
    if (baseline is None
            or set(baseline) != {"visibility", "friend_check_in_strategy"}):
        raise RunStateError("fresh member profile baseline is incomplete")
    release.update({
        "status": "IN_PROGRESS",
        "requested": copy.deepcopy(baseline),
    })
    if on_progress is not None:
        on_progress(copy.deepcopy(receipt))
    try:
        observed, requests, _ = _prepare_member_profile(
            profile, member["bot_id"], baseline)
    except Exception as error:
        detail = _redacted_error_detail(error, profile)
        release.update({
            "status": "BLOCKED",
            "error_type": type(error).__name__,
            "error_detail": detail,
        })
        if on_progress is not None:
            on_progress(copy.deepcopy(receipt))
        raise FreshFixtureReleaseError(
            f"fresh member profile release failed: {detail}", receipt) from None
    release.update({
        "status": "PASS",
        "observed": observed,
        "requests": requests,
    })
    if on_progress is not None:
        on_progress(copy.deepcopy(receipt))
    return receipt


def _registration_step(role: str, execution: CaseExecution) -> dict:
    if execution.status != "PASS" or len(execution.steps) != 1:
        error_type = execution.error_type or "FreshFixtureRegistrationBlocked"
        raise RunStateError(f"{role} fresh Bot registration is BLOCKED ({error_type})")
    step = execution.steps[0]
    data = step.data
    if not isinstance(data, dict):
        raise RunStateError(f"{role} fresh Bot registration has no result data")
    bot_id = data.get("bot_uuid")
    bot_name = data.get("bot_name")
    if (not isinstance(bot_id, str) or not bot_id
            or not isinstance(bot_name, str) or not bot_name):
        raise RunStateError(f"{role} fresh Bot registration result is invalid")
    return {
        "role": role,
        "status": "PASS",
        "bot_id": bot_id,
        "bot_name": bot_name,
        "requests": _project_register_requests(step.evidence),
    }


def _quoted_identifier(value: str) -> str:
    """Encode the two non-unreserved characters admitted by Bot identifiers."""
    return value.replace(":", "%3A").replace("@", "%40")


def _replace_actor_pair(profile: dict, driver: str, member: str) -> dict:
    effective = copy.deepcopy(profile)
    old_driver = effective["actors"]["driver"]["bot_id"]
    old_member = effective["actors"]["member"]["bot_id"]
    replacements = {
        old_driver: driver,
        old_member: member,
        _quoted_identifier(old_driver): _quoted_identifier(driver),
        _quoted_identifier(old_member): _quoted_identifier(member),
    }

    def rewrite(value):
        if isinstance(value, str):
            for previous, fresh in replacements.items():
                value = value.replace(previous, fresh)
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    effective["actors"]["driver"]["bot_id"] = identifier(driver, "fresh driver Bot ID")
    effective["actors"]["member"]["bot_id"] = identifier(member, "fresh member Bot ID")
    effective["edge_case_bindings"] = rewrite(effective["edge_case_bindings"])
    # Re-run the full profile contract after the narrow actor-pair rewrite.
    return validate(effective)


def _retained_bots(steps: list[dict]) -> list[dict]:
    return [
        {
            "role": step["role"],
            "bot_id": step["bot_id"],
            "bot_name": step["bot_name"],
            "disposition": "RETAINED_PUBLIC_V1_NO_BOT_DELETE_ROUTE",
        }
        for step in steps if step.get("status") == "PASS"
    ]


def _fresh_pair_from_steps(steps: list[dict]) -> dict:
    result = {}
    for role in ("driver", "member"):
        step = next(
            (item for item in steps
             if item.get("role") == role and item.get("status") == "PASS"),
            None,
        )
        result[role] = (
            {"bot_id": step["bot_id"], "bot_name": step["bot_name"]}
            if step is not None else None
        )
    return result


def provision_fresh_owned_bot_pair(
    *,
    profile: dict,
    run_id: str,
    purpose: str,
    member_profile: dict | None = None,
    on_progress=None,
) -> FreshFixturePair:
    """Register a fresh Human-owned driver/member pair for one Case run.

    ``on_progress`` receives detached receipts before and after each network
    registration so a crashed process still leaves the created Bot identities
    and its retention disposition in the state directory.
    """
    if not isinstance(profile, dict):
        raise ConfigError("fresh fixture provisioning requires an admitted profile")
    run_id = identifier(run_id, "fresh fixture run ID")
    purpose = identifier(purpose, "fresh fixture purpose")
    member_profile = _validated_member_profile(member_profile)
    entrypoint = profile.get("entrypoint", {})
    environment = profile.get("environment", {})
    valid_entrypoint = (
        entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
    ) or (
        entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
    )
    if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
        raise ConfigError(
            "fresh fixture provisioning requires a public-v1 Singlebox or gateway profile")
    headers = profile.get("auth", {}).get("header_env", {})
    if "Cookie" not in headers or "Authorization" in headers:
        raise ConfigError(
            "fresh fixture provisioning requires only the owning Human Cookie")

    provisioning_profile = copy.deepcopy(profile)
    provisioning_profile["edge_case_bindings"] = {
        **provisioning_profile["edge_case_bindings"],
        **_REGISTER_BINDINGS,
    }
    receipt = _progress_receipt(
        run_id=run_id, purpose=purpose, member_profile=member_profile)

    def publish() -> None:
        if on_progress is not None:
            on_progress(copy.deepcopy(receipt))

    publish()
    for role in ("driver", "member"):
        step = {"role": role, "status": "IN_PROGRESS"}
        receipt["steps"].append(step)
        publish()
        try:
            adapter = RemoteNetworkRegisterAdapter(provisioning_profile)
            execution = execute_network_register_case(adapter)
            completed = _registration_step(role, execution)
        except Exception as error:
            detail = _redacted_error_detail(error, profile)
            step.update({
                "status": "BLOCKED",
                "error_type": type(error).__name__,
                "error_detail": detail,
            })
            receipt.update({
                "status": "BLOCKED",
                "error_type": type(error).__name__,
                "error_detail": detail,
                "fresh_pair": _fresh_pair_from_steps(receipt["steps"]),
                "resource_disposition": (
                    PARTIAL_DISPOSITION if _retained_bots(receipt["steps"])
                    else EMPTY_DISPOSITION
                ),
            })
            receipt["retained_bots"] = _retained_bots(receipt["steps"])
            publish()
            raise FreshFixtureProvisioningError(
                f"{role} fresh Bot registration failed: {detail}",
                receipt,
            ) from None
        step.update(completed)
        receipt["fresh_pair"][role] = {
            "bot_id": completed["bot_id"],
            "bot_name": completed["bot_name"],
        }
        publish()

    if member_profile is not None:
        preparation = receipt["member_profile_preparation"]
        preparation["status"] = "IN_PROGRESS"
        publish()
        try:
            observed, requests, baseline = _prepare_member_profile(
                profile, receipt["fresh_pair"]["member"]["bot_id"],
                member_profile)
        except Exception as error:
            detail = _redacted_error_detail(error, profile)
            preparation.update({
                "status": "BLOCKED",
                "error_type": type(error).__name__,
                "error_detail": detail,
            })
            receipt.update({
                "status": "BLOCKED",
                "error_type": type(error).__name__,
                "error_detail": detail,
                "fresh_pair": _fresh_pair_from_steps(receipt["steps"]),
                "resource_disposition": PARTIAL_DISPOSITION,
                "retained_bots": _retained_bots(receipt["steps"]),
            })
            publish()
            raise FreshFixtureProvisioningError(
                f"member fresh Bot profile preparation failed: {detail}",
                receipt,
            ) from None
        preparation.update({
            "status": "PASS",
            "baseline": baseline,
            "observed": observed,
            "requests": requests,
        })
        receipt["member_profile_release"]["requested"] = copy.deepcopy(baseline)
        publish()

    effective_profile = _replace_actor_pair(
        profile,
        receipt["fresh_pair"]["driver"]["bot_id"],
        receipt["fresh_pair"]["member"]["bot_id"],
    )
    receipt.update({
        "status": "PASS",
        "resource_disposition": RETAINED_DISPOSITION,
        "retained_bots": _retained_bots(receipt["steps"]),
        "effective_profile_digest": fingerprint(effective_profile),
    })
    publish()
    return FreshFixturePair(profile=effective_profile, evidence=receipt)
