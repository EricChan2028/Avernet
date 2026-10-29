"""Source-backed 42-operation Provider publish/retire contract.

The frozen ``provider-migration-contract.json`` retained only the 32
write-shaped operations because the local source scanner did not capture
``api_get`` calls.  The original story also asserts ten human discovery reads:
six slug discovery/negative reads before the rename, two reads after the
rename, and two reads around disable/enable.  The dual family keeps the 32
frozen ids intact and adds those ten reads as explicit discovery
obligations, in exact source order.
"""
from __future__ import annotations

import re
from pathlib import Path

from .local_provider_contract import (
    PARENT,
    REQUIREMENTS as CORE_REQUIREMENTS,
    provider_source_binding,
)
from .profile import fingerprint
from .run_state import RunStateError

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "DUAL-PROVIDER-PUBLISHES-01"
SUITE = "dual-provider-publishes-v1"

DISCOVERY_REQUIREMENTS = (
    "PROVIDER.discovery-public-slug",
    "PROVIDER.discovery-collaboration-slug",
    "PROVIDER.discovery-collaboration-unknown",
    "PROVIDER.discovery-collaboration-invalid",
    "PROVIDER.discovery-public-unknown-before-rename",
    "PROVIDER.discovery-public-invalid",
    "PROVIDER.discovery-old-slug-after-rename",
    "PROVIDER.discovery-new-slug-after-rename",
    "PROVIDER.discovery-disabled-slug",
    "PROVIDER.discovery-enabled-slug",
)


def _row(
    requirement_id: str,
    operation: str,
    method: str,
    path: str,
    caller: str,
    expected_status: int,
    function: str,
    expectation: str,
    *,
    body: str | None = None,
    provider_header: bool = False,
    case_class: str,
) -> dict:
    return {
        "requirement_id": requirement_id,
        "operation": operation,
        "method": method,
        "path": path,
        "caller": caller,
        "expected_status": expected_status,
        "function": function,
        "expectation": expectation,
        "body": body,
        "provider_header": provider_header,
        "case_class": case_class,
    }


_MAIN = "story_provider_operator_publishes_agent"
_HELPER = "_story_provider_manages_organization"

# Exact source order.  The 32 frozen core ids keep their original names; the
# ten api_get obligations are inserted where the story executes them.
STEP_ROWS = (
    _row("PROVIDER.register", "provider.register", "POST", "/providers",
         "human", 200, _MAIN,
         "Human registers Provider 2.0; id, admin and BCS callback credentials returned.",
         body="register", case_class="human-register"),
    _row("PROVIDER.discovery-public-slug", "provider.discovery_public_slug", "GET",
         "/providers/by-slug/{provider_slug}", "human", 200, _MAIN,
         "Public slug discovery returns exact id/slug/name/auth/protocol/enabled and the basic public projection.",
         case_class="public-slug-discovery"),
    _row("PROVIDER.discovery-collaboration-slug", "provider.discovery_collaboration_slug",
         "GET", "/api/v1/collaboration/providers/by-slug/{provider_slug}",
         "human", 200, _MAIN,
         "Collaboration slug discovery preserves the public projection exactly.",
         case_class="collaboration-slug-discovery"),
    _row("PROVIDER.discovery-collaboration-unknown", "provider.discovery_collaboration_unknown",
         "GET", "/api/v1/collaboration/providers/by-slug/{renamed_slug}",
         "human", 404, _MAIN,
         "Unknown collaboration slug is not found before the rename.",
         case_class="collaboration-slug-not-found"),
    _row("PROVIDER.discovery-collaboration-invalid", "provider.discovery_collaboration_invalid",
         "GET", "/api/v1/collaboration/providers/by-slug/Invalid_slug",
         "human", 400, _MAIN,
         "Invalid collaboration slug is rejected.",
         case_class="collaboration-slug-invalid"),
    _row("PROVIDER.discovery-public-unknown-before-rename",
         "provider.discovery_public_unknown_before_rename", "GET",
         "/providers/by-slug/{renamed_slug}", "human", 404, _MAIN,
         "Unknown public slug is not found before the rename.",
         case_class="public-slug-not-found"),
    _row("PROVIDER.discovery-public-invalid", "provider.discovery_public_invalid", "GET",
         "/providers/by-slug/Invalid_slug", "human", 400, _MAIN,
         "Invalid public slug is rejected.",
         case_class="public-slug-invalid"),
    _row("PROVIDER.duplicate-slug-conflict", "provider.duplicate_slug_conflict", "POST",
         "/providers", "human", 409, _MAIN,
         "A second Provider cannot claim the registered slug.",
         body="duplicate", case_class="duplicate-slug-conflict"),
    _row("PROVIDER.read", "provider.read", "GET", "/providers/{provider_id}",
         "provider_admin", 200, _MAIN,
         "Admin reads the exact Provider, static_bearer auth and enabled state.",
         case_class="admin-read"),
    _row("PROVIDER.update", "provider.update", "PATCH", "/providers/{provider_id}",
         "provider_admin", 200, _MAIN,
         "Admin plus Human updates name/slug, keeping the Provider id stable.",
         body="update", case_class="admin-update"),
    _row("PROVIDER.discovery-old-slug-after-rename",
         "provider.discovery_old_slug_after_rename", "GET",
         "/providers/by-slug/{provider_slug}", "human", 404, _MAIN,
         "Rename releases the old provider slug.",
         case_class="old-slug-released"),
    _row("PROVIDER.discovery-new-slug-after-rename",
         "provider.discovery_new_slug_after_rename", "GET",
         "/providers/by-slug/{renamed_slug}", "human", 200, _MAIN,
         "Renamed Provider is discoverable under the new slug with updated metadata.",
         case_class="renamed-slug-discovery"),
    _row("PROVIDER.bot-publish", "provider.bot_publish", "POST",
         "/providers/{provider_id}/bots", "provider_admin", 200, _MAIN,
         "Admin publishes its own Bot and captures exact UUID/ref and runtime credential.",
         body="bot_publish", case_class="bot-publish"),
    _row("PROVIDER.bot-update", "provider.bot_update", "PATCH",
         "/providers/{provider_id}/bots/{provider_bot_ref}", "provider_admin", 200,
         _MAIN,
         "Bot patch keeps UUID/ref stable, updates name/domains and clears scopes.",
         body="bot_update", case_class="bot-update"),
    _row("PROVIDER.attributes-read-denied", "provider.attributes_read_denied", "GET",
         "/providers/{provider_id}/bots/{provider_bot_uuid}/attributes",
         "provider_admin", 403, _MAIN,
         "Non-allowlisted Provider attribute read is denied.",
         case_class="attributes-read-denied"),
    _row("PROVIDER.attributes-write-denied", "provider.attributes_write_denied", "PATCH",
         "/providers/{provider_id}/bots/{provider_bot_uuid}/attributes",
         "provider_admin", 403, _MAIN,
         "Non-allowlisted Provider attribute write is denied.",
         body="attributes_write", case_class="attributes-write-denied"),
    _row("PROVIDER.org-create", "provider.org_create", "POST",
         "/providers/{provider_id}/organizations", "provider_admin", 200, _HELPER,
         "Create the exact unique organization under the owned Provider.",
         body="org_create", case_class="organization-create"),
    _row("PROVIDER.org-list", "provider.org_list", "GET",
         "/providers/{provider_id}/organizations", "provider_admin", 200, _HELPER,
         "Provider organization list contains the exact created code.",
         case_class="organization-list"),
    _row("PROVIDER.org-read", "provider.org_read", "GET",
         "/organizations/{organization_code}", "provider_admin", 200, _HELPER,
         "Organization read returns the exact code/name.",
         case_class="organization-read"),
    _row("PROVIDER.org-update", "provider.org_update", "PATCH",
         "/organizations/{organization_code}", "provider_admin", 200, _HELPER,
         "Organization rename is persisted.",
         body="org_update", case_class="organization-update"),
    _row("PROVIDER.candidate-list-before", "provider.candidate_list_before", "GET",
         "/providers/{provider_id}/organization-candidate-bots?organization_code={organization_code}&q=Provider",
         "provider_admin", 200, _HELPER,
         "Candidates include the exact owned Bot before membership.",
         case_class="candidate-list-before"),
    _row("PROVIDER.candidate-before", "provider.candidate_before", "GET",
         "/providers/{provider_id}/organization-candidate-bots/{provider_bot_uuid}?organization_code={organization_code}",
         "provider_admin", 200, _HELPER,
         "Candidate detail reports is_member false.",
         case_class="candidate-before"),
    _row("PROVIDER.member-add", "provider.member_add", "PUT",
         "/organizations/{organization_code}/members/{provider_bot_uuid}",
         "provider_admin", 200, _HELPER,
         "Add exact Bot membership with reviewer role.",
         body="member_add", case_class="member-add"),
    _row("PROVIDER.candidate-list-after", "provider.candidate_list_after", "GET",
         "/providers/{provider_id}/organization-candidate-bots?organization_code={organization_code}&q=Provider",
         "provider_admin", 200, _HELPER,
         "Candidates exclude the exact Bot after membership.",
         case_class="candidate-list-after"),
    _row("PROVIDER.candidate-after", "provider.candidate_after", "GET",
         "/providers/{provider_id}/organization-candidate-bots/{provider_bot_uuid}?organization_code={organization_code}",
         "provider_admin", 200, _HELPER,
         "Candidate detail reports is_member true.",
         case_class="candidate-after"),
    _row("PROVIDER.member-profile", "provider.member_profile", "PATCH",
         "/organizations/{organization_code}/members/{provider_bot_uuid}/profile",
         "provider_admin", 200, _HELPER,
         "Member profile name update is persisted.",
         body="member_profile", case_class="member-profile"),
    _row("PROVIDER.member-read", "provider.member_read", "GET",
         "/organizations/{organization_code}/members/{provider_bot_uuid}",
         "provider_admin", 200, _HELPER,
         "Member read returns the exact Bot.",
         case_class="member-read"),
    _row("PROVIDER.member-list", "provider.member_list", "GET",
         "/organizations/{organization_code}/members?role=reviewer",
         "provider_admin", 200, _HELPER,
         "Reviewer member list contains the exact Bot.",
         case_class="member-list"),
    _row("PROVIDER.admin-start", "provider.admin_start", "POST",
         "/organizations/{organization_code}/admin-runs", "provider_admin", 200,
         _HELPER,
         "Detached admin run returns id, organization, target and a status; acceptance is not engine completion.",
         body="admin_start", provider_header=True, case_class="admin-run-start"),
    _row("PROVIDER.admin-read", "provider.admin_read", "GET",
         "/organizations/{organization_code}/admin-runs/{admin_run_id}",
         "provider_admin", 200, _HELPER,
         "Exact admin run read returns id, target and nonempty status.",
         provider_header=True, case_class="admin-run-read"),
    _row("PROVIDER.member-remove", "provider.member_remove", "DELETE",
         "/organizations/{organization_code}/members/{provider_bot_uuid}",
         "provider_admin", 204, _HELPER,
         "Exact owned member removal returns 204; the organization is retained.",
         case_class="member-remove"),
    _row("PROVIDER.bot-list", "provider.bot_list", "GET",
         "/providers/{provider_id}/bots", "provider_admin", 200, _MAIN,
         "Provider Bot list contains the exact Bot.",
         case_class="bot-list"),
    _row("PROVIDER.roster-denied", "provider.roster_denied", "GET",
         "/providers/{provider_id}/bots/by-task-modes", "provider_admin", 403,
         _MAIN,
         "Non-allowlisted task-mode roster is denied with its diagnostic.",
         case_class="roster-denied"),
    _row("PROVIDER.agentpass-negative", "provider.agentpass_negative", "POST",
         "/providers/agentpass/resolve", "bot_runtime", 200, _MAIN,
         "Original runtime token resolves no AgentPass agent_code or provider_bot_binding.",
         body="agentpass", provider_header=True, case_class="agentpass-negative"),
    _row("PROVIDER.unknown-run-callback", "provider.unknown_run_callback", "POST",
         "/bot/events", "bot_runtime", 404, _MAIN,
         "Runtime-token callback for an unknown run returns run_not_found.",
         body="unknown_run_callback", provider_header=True,
         case_class="unknown-run-callback"),
    _row("PROVIDER.takeover-denied", "provider.takeover_denied", "POST",
         "/providers/{provider_id}/delivery/switch-bot", "provider_admin", 403,
         _MAIN,
         "Non-allowlisted takeover of an existing Bot is denied with its diagnostic.",
         body="takeover", case_class="takeover-denied"),
    _row("PROVIDER.disable", "provider.disable", "POST",
         "/providers/{provider_id}/disable", "provider_admin", 200, _MAIN,
         "Owned Provider disable is persisted.",
         case_class="provider-disable"),
    _row("PROVIDER.discovery-disabled-slug", "provider.discovery_disabled_slug",
         "GET", "/providers/by-slug/{renamed_slug}", "human", 200, _MAIN,
         "Disabled Provider remains discoverable and reports enabled false.",
         case_class="disabled-slug-discovery"),
    _row("PROVIDER.enable", "provider.enable", "POST",
         "/providers/{provider_id}/enable", "provider_admin", 200, _MAIN,
         "Owned Provider re-enable is persisted.",
         case_class="provider-enable"),
    _row("PROVIDER.discovery-enabled-slug", "provider.discovery_enabled_slug",
         "GET", "/providers/by-slug/{renamed_slug}", "human", 200, _MAIN,
         "Re-enabled Provider remains discoverable and reports enabled true.",
         case_class="enabled-slug-discovery"),
    _row("PROVIDER.bot-retire", "provider.bot_retire", "DELETE",
         "/providers/{provider_id}/bots/{provider_bot_ref}", "provider_admin", 200,
         _MAIN,
         "Exact owned Bot/ref retirement is acknowledged.",
         case_class="bot-retire"),
    _row("PROVIDER.bot-list-empty", "provider.bot_list_empty", "GET",
         "/providers/{provider_id}/bots", "provider_admin", 200, _MAIN,
         "Provider Bot list is empty after retirement.",
         case_class="bot-list-empty"),
)

REQUIREMENTS = tuple(row["requirement_id"] for row in STEP_ROWS)
OPERATIONS = tuple(row["operation"] for row in STEP_ROWS)
STEP_BY_REQUIREMENT = {row["requirement_id"]: row for row in STEP_ROWS}
STEP_BY_OPERATION = {row["operation"]: row for row in STEP_ROWS}
AUTH_BY_CALLER = {
    "human": "human",
    "provider_admin": "provider_admin",
    "bot_runtime": "bot_runtime",
}


def _source_path(profile_path: str) -> str:
    """Project the profile template back to the exact story path form."""
    return re.sub(r"\{([a-z_]+)\}", r"${\1}", profile_path)


def provider_publishes_source_binding() -> dict:
    """Verify the frozen source hash and bind all 42 exact HTTP calls.

    The old 32-operation contract is still validated first.  This function
    additionally proves that the only omitted calls were the ten ``api_get``
    statements, and that both functions still contain the exact ordered
    method/path sequence represented by :data:`STEP_ROWS`.
    """
    try:
        frozen = provider_source_binding()
        if (frozen["parent"] != PARENT
                or tuple(row["id"] for row in frozen["requirements"]) != CORE_REQUIREMENTS):
            raise ValueError
        path = ROOT.parent / "e2e-test/stories.sh"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError
        text = path.read_text(encoding="utf-8")

        def body(function: str) -> str:
            if len(re.findall(r"^" + function + r"\(\) \{$", text, re.M)) != 1:
                raise ValueError
            start = text.index(function + "() {")
            return text[start:text.index("\n}", start) + 2]

        main, nested = body(PARENT), body(_HELPER)
        if main.count("    " + _HELPER + " ") != 1:
            raise ValueError
        pattern = r'^    (api_get|api_post|api_request_headers)\s+(?:([A-Z]+)\s+)?"([^"]+)"'
        before, after = main.split("    " + _HELPER + " ", 1)
        actual = []
        for function, part in ((PARENT, before), (_HELPER, nested), (PARENT, after)):
            for helper, method, route in re.findall(pattern, part, re.M):
                actual.append((
                    function,
                    "GET" if helper == "api_get" else "POST" if helper == "api_post" else method or "POST",
                    route,
                ))
        expected = [
            (row["function"], row["method"], _source_path(row["path"]))
            for row in STEP_ROWS
        ]
        if actual != expected:
            raise ValueError
        if (len(actual) != 42
                or set(REQUIREMENTS) != set(CORE_REQUIREMENTS) | set(DISCOVERY_REQUIREMENTS)
                or len(set(REQUIREMENTS)) != 42):
            raise ValueError
        return {
            "schema_version": 1,
            "contract": SUITE,
            "case_id": CASE_ID,
            "parent": PARENT,
            "source_revision": frozen["source_revision"],
            "source_sha256": frozen["sources"]["stories.sh"],
            "requirements": [
                {
                    "id": row["requirement_id"],
                    "operation": row["operation"],
                    "function": row["function"],
                    "method": row["method"],
                    "path": _source_path(row["path"]),
                    "expectation": row["expectation"],
                }
                for row in STEP_ROWS
            ],
        }
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError(
            "Provider publishes source or complete 42-operation map changed"
        ) from None


def provider_publishes_case_plan() -> dict:
    """Return the immutable source-backed obligation plan."""
    binding = provider_publishes_source_binding()
    return {
        "case_id": CASE_ID,
        "parent_id": PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "operations": list(OPERATIONS),
        "core_requirement_count": len(CORE_REQUIREMENTS),
        "discovery_requirement_count": len(DISCOVERY_REQUIREMENTS),
        "source_binding_digest": fingerprint(binding),
    }


def provider_publishes_adaptability() -> dict:
    return {
        "case_id": CASE_ID,
        "legacy_id": PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "selected_obligations": [
            "human registration", "public/collaboration slug discovery",
            "duplicate slug conflict", "admin metadata read/update",
            "provider Bot publish/update", "attributes fail-closed",
            "organization/member lifecycle", "detached admin run",
            "runtime negative probes", "disable/enable/retire",
        ],
        "environments": ["singlebox-product-http", "preprod-product-http"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-http-legacy-provider",
        "core_requirement_count": len(CORE_REQUIREMENTS),
        "discovery_requirement_count": len(DISCOVERY_REQUIREMENTS),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "The dual adapter is implemented and locally testable; preprod live "
            "execution and release credit remain separate gates."
        ),
    }
