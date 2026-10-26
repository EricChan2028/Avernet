"""Per-resource retention and recycling audit (task 6.4).

Task 6.4 requires an explicit, resource-by-resource audit of the
retention and recycling mechanisms for DM, Provider, Organization,
definition versions, message history and audit history. The governing
rule is structural: a resource without a product-level delete API
must never be cleaned by touching the database directly.

The audit is a machine-readable registry, not a report card: each
entry records what retains the resource, what recycles it (or that
nothing recycles it inside E2E scope), whether a product delete API
exists, the mandatory DB-direct-cleanup prohibition, the E2E
disposition, and the code-level evidence. Entries are validated as a
whole so a future change cannot silently widen a recycling claim.
"""
from __future__ import annotations

from typing import Any

from .profile import ConfigError, object_keys

AUDIT_SCHEMA_VERSION = 1

# Structural invariants for this audit.
DB_DIRECT_CLEANUP_FORBIDDEN = "FORBIDDEN_NOT_AUTHORIZED_WITHOUT_PRODUCT_DELETE_API"

DISPOSITION_ENVIRONMENT_OWNER_REVIEW = "ENVIRONMENT_OWNER_REVIEW_REQUIRED"
DISPOSITION_RETAINED_UNRESOLVED = "RETAINED_UNRESOLVED"
DISPOSITION_APPEND_ONLY = "APPEND_ONLY_NEVER_RECYCLED"

ENTRYPOINT_EVIDENCE = "engine-facing services own chat history per OCB entity ownership"

REQUIRED_ENTRY_KEYS = {
    "resource_family", "retention_mechanism", "recycling_mechanism",
    "delete_api", "db_direct_cleanup", "e2e_disposition", "evidence",
}
ALLOWED_DISPOSITIONS = (
    DISPOSITION_ENVIRONMENT_OWNER_REVIEW,
    DISPOSITION_RETAINED_UNRESOLVED,
    DISPOSITION_APPEND_ONLY,
)

RESOURCE_AUDIT: dict[str, dict[str, Any]] = {
    "DM": {
        "resource_family": "DM",
        "retention_mechanism": (
            "direct-chat conversations and their DM.* lifecycle artifacts are "
            "persisted by engine-facing chat storage; the E2E runner only "
            "exercises the DM.ping/main submit/read/cancel/terminal steps"),
        "recycling_mechanism": (
            "no product API in the pilot deletes DM conversations; disposal is "
            "mediated by the environment owner after retaining review"),
        "delete_api": None,
        "db_direct_cleanup": DB_DIRECT_CLEANUP_FORBIDDEN,
        "e2e_disposition": DISPOSITION_ENVIRONMENT_OWNER_REVIEW,
        "evidence": (
            "bcs_e2e/local_direct_chat_contract.py DM.* REQUIREMENTS contain no "
            "delete step; " + ENTRYPOINT_EVIDENCE),
    },
    "PROVIDER": {
        "resource_family": "PROVIDER",
        "retention_mechanism": (
            "provider bots and provider members created by a Run are retained "
            "in the owned pool with exact ledger intent rows until finalization"),
        "recycling_mechanism": (
            "business delete transitions member-remove and bot-retire issue a "
            "DELETE_INTENDED state transition through the product API with "
            "request-id correlation; retired providers remain retained rows, "
            "not physical erasure"),
        "delete_api": ("member-remove", "bot-retire"),
        "db_direct_cleanup": DB_DIRECT_CLEANUP_FORBIDDEN,
        "e2e_disposition": DISPOSITION_RETAINED_UNRESOLVED,
        "evidence": (
            "bcs_e2e/local_provider_api.py _DELETE transitions and "
            "bcs_e2e/local_provider_storage.py retirement projections"),
    },
    "ORGANIZATION": {
        "resource_family": "ORGANIZATION",
        "retention_mechanism": (
            "organization is modeled as the provider workspace role bindings "
            "(workspace CEO/PM/ENG/QA) verified per Run and bound to the "
            "retention target; membership state persists across Runs"),
        "recycling_mechanism": (
            "no product API removes organization bindings; role binding drift "
            "is resolved by environment owner review, never by Run cleanup"),
        "delete_api": None,
        "db_direct_cleanup": DB_DIRECT_CLEANUP_FORBIDDEN,
        "e2e_disposition": DISPOSITION_RETAINED_UNRESOLVED,
        "evidence": (
            "bcs_e2e/local_provider_api.py OwnedLocalProviderApi role "
            "verification contract (ROLES must be freshly verified per Run)"),
    },
    "DEFINITION_VERSION": {
        "resource_family": "DEFINITION_VERSION",
        "retention_mechanism": (
            "executable case and provider definitions carry per-Run versions "
            "and are only admitted when finalization is 'retain'; frozen "
            "inventories and executor digests make every published version "
            "reproducible and immutable"),
        "recycling_mechanism": (
            "definition versions are immutable once published; superseded "
            "versions stay reviewable and reach disposal only through "
            "environment owner review, never by a Run"),
        "delete_api": None,
        "db_direct_cleanup": DB_DIRECT_CLEANUP_FORBIDDEN,
        "e2e_disposition": DISPOSITION_ENVIRONMENT_OWNER_REVIEW,
        "evidence": (
            "bcs_e2e/retained.py finalization=='retain' gate; "
            "bcs_e2e/inventory.py freeze_manifest/executor_digest"),
    },
    "MESSAGE_HISTORY": {
        "resource_family": "MESSAGE_HISTORY",
        "retention_mechanism": (
            "chat content exchanged during a Run is persisted by engine-facing "
            "message storage outside the E2E runner's ownership boundary; the "
            "runner records only redacted evidence summaries"),
        "recycling_mechanism": (
            "no purge API exists in the pilot; history disposal requires "
            "environment owner review and is reported as unverified"),
        "delete_api": None,
        "db_direct_cleanup": DB_DIRECT_CLEANUP_FORBIDDEN,
        "e2e_disposition": DISPOSITION_ENVIRONMENT_OWNER_REVIEW,
        "evidence": (
            "bcs_e2e/reporting.py cleanup section reports "
            "associated_history_purge=NOT_VERIFIED and "
            "physical_data_erasure=NOT_VERIFIED; " + ENTRYPOINT_EVIDENCE),
    },
    "AUDIT_HISTORY": {
        "resource_family": "AUDIT_HISTORY",
        "retention_mechanism": (
            "runner audit trails (execution ledger intent/confirmation rows, "
            "retention quota journals, network operation journals) are "
            "append-only by contract and bound to Run IDs and digests"),
        "recycling_mechanism": (
            "audit history is never recycled: operations journals reject "
            "replay and the retention quota journal forbids implicit rebasing, "
            "so deletion would destroy the audit trail it exists to provide"),
        "delete_api": None,
        "db_direct_cleanup": DB_DIRECT_CLEANUP_FORBIDDEN,
        "e2e_disposition": DISPOSITION_APPEND_ONLY,
        "evidence": (
            "bcs_e2e/local_network_api.py 'network operation cannot be "
            "replayed'; bcs_e2e/retention_quota.py 'implicit rebasing is "
            "forbidden'"),
    },
}


def _validate_entry(value: object) -> dict:
    """Validate one already-keyed audit entry."""
    entry_value = dict(object_keys(value, REQUIRED_ENTRY_KEYS,
                                    "retention audit entry"))
    family = entry_value["resource_family"]
    if not isinstance(family, str) or family not in RESOURCE_AUDIT:
        raise ConfigError(f"unknown retention audit resource family: {family!r}")
    for key in ("retention_mechanism", "recycling_mechanism"):
        if not isinstance(entry_value[key], str) or not entry_value[key].strip():
            raise ConfigError(f"retention audit {key} is required")
    if entry_value["db_direct_cleanup"] != DB_DIRECT_CLEANUP_FORBIDDEN:
        raise ConfigError(
            "retention audit must forbid DB-direct cleanup without a product delete API")
    if entry_value["e2e_disposition"] not in ALLOWED_DISPOSITIONS:
        raise ConfigError("retention audit disposition is not an audited disposition")
    if entry_value["delete_api"] is None:
        if entry_value["e2e_disposition"] == DISPOSITION_APPEND_ONLY:
            if "never recycled" not in entry_value["recycling_mechanism"].lower():
                raise ConfigError(
                    "an append-only audit resource must be never-recycled by contract")
        elif "owner" not in entry_value["recycling_mechanism"].lower():
            raise ConfigError(
                "a resource without a product delete API must require owner-mediated recycling")
    elif not (isinstance(entry_value["delete_api"], tuple)
              and entry_value["delete_api"]
              and all(isinstance(name, str) and name
                       for name in entry_value["delete_api"])):
        raise ConfigError(
            "retention audit delete_api must be a non-empty tuple of transition names")
    return entry_value


def entry(resource_family: object) -> dict:
    """Return the validated audit entry for one resource family."""
    if not isinstance(resource_family, str) or resource_family not in RESOURCE_AUDIT:
        raise ConfigError(f"unknown retention audit resource family: {resource_family!r}")
    return _validate_entry(RESOURCE_AUDIT[resource_family])


def retention_audit() -> dict:
    """The complete validated audit record carried with retention evidence."""
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "governing_rule": (
            "a resource without a product-level delete API is never cleaned "
            "by direct database access; cleanup converges on registered "
            "resource APIs under environment owner review"),
        "resources": [entry(family) for family in RESOURCE_AUDIT],
    }


def validate_registry(value: object) -> dict:
    """Re-validate an externalized audit record of the same shape."""
    record = object_keys(value, {"schema_version", "governing_rule", "resources"},
                         "retention audit registry")
    if (type(record["schema_version"]) is not int
            or record["schema_version"] != AUDIT_SCHEMA_VERSION):
        raise ConfigError("retention audit schema_version must be 1")
    if not isinstance(record["governing_rule"], str) or not record["governing_rule"].strip():
        raise ConfigError("retention audit governing_rule is required")
    resources = record["resources"]
    if (not isinstance(resources, list) or len(resources) != len(RESOURCE_AUDIT)
            or [row.get("resource_family") for row in resources] != list(RESOURCE_AUDIT)):
        raise ConfigError("retention audit resources must enumerate the six audited families in order")
    for row in resources:
        _validate_entry(row)
    return {
        "schema_version": record["schema_version"],
        "governing_rule": record["governing_rule"],
        "resources": [dict(row) for row in resources],
    }
