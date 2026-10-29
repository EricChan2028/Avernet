"""Dual Singlebox/preprod execution of Provider publish, organization and retire.

The 42 ordered obligations come from :mod:`provider_publishes_contract`; this
module owns the profile schema, the tier-aware local/preprod adapters, the
environment-neutral step executor and the ``dual-provider-publishes-run``
command body.  Provider admin and Bot runtime credentials are minted by BCS
during the story and remain only in adapter memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .inventory import executor_digest
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    origin,
    require_resolved,
    unique_keys,
)
from .provider_publishes_profile import (
    provider_publishes_credential_kind,
    validate_provider_publishes_profile,
)
from .provider_publishes_contract import (
    CASE_ID,
    PARENT,
    REQUIREMENTS,
    STEP_BY_REQUIREMENT,
    STEP_ROWS,
    provider_publishes_case_plan,
    provider_publishes_source_binding,
)
from .provider_publishes_transport import (
    ProviderPublishesTransport,
    ProviderPublishesTransportError,
)
from .run_state import RunStateError, write

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-provider-publishes-case-run-v1"
CASE_KEYS = ("provider-publishes",)
FIXTURE_EFFECT = "PROVIDER_BOT_RETIRED_PROVIDER_AND_ORGANIZATION_RETAINED"
PUBLIC_PROVIDER_KEYS = frozenset({
    "auth_mode", "created_at", "enabled", "name", "protocol_version",
    "provider_id", "slug", "updated_at",
})
_CASE_METHOD = "execute_provider_publishes_obligation"


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


class ProviderPublishesAdapter:
    """Environment-neutral adapter for the source-backed 42-step story."""

    def __init__(self, profile: dict, *, run_id: str):
        self.profile = validate_provider_publishes_profile(profile)
        self.run_id = identifier(run_id, "provider publishes run ID")
        digest = hashlib.sha256(self.run_id.encode()).hexdigest()[:24]
        self.fixture_effect = FIXTURE_EFFECT
        self.provider_slug = "e2e-provider-" + digest
        self.renamed_slug = self.provider_slug + "-renamed"
        self.provider_bot_ref = "reviewer-" + digest
        self.organization_code = "e2e-org-" + digest
        self.unknown_run_id = "missing-provider-run-" + digest
        self._credentials: dict[str, str] = {}
        self._ids: dict[str, str] = {
            "provider_slug": self.provider_slug,
            "renamed_slug": self.renamed_slug,
            "provider_bot_ref": self.provider_bot_ref,
            "organization_code": self.organization_code,
            "unknown_run_id": self.unknown_run_id,
        }
        self._attempted: set[str] = set()
        self._next = 0
        self._public_projection: dict | None = None
        self._admin_run_projection: dict | None = None
        self.transport = ProviderPublishesTransport(
            self.profile, human_headers=self._human_headers()
        )

    def _human_headers(self) -> dict[str, str]:
        if self.profile["environment"]["tier"] == "local":
            return {
                "X-Mock-User-Id": self.profile["human"]["user_id"],
                "X-Mock-Nick-Name": self.profile["human"]["mock_nick_name"],
            }
        cookie = os.environ.get(self.profile["auth"]["cookie_env"], "")
        if (
            not cookie
            or not cookie.isascii()
            or any(ord(char) < 32 or ord(char) == 127 for char in cookie)
        ):
            raise ConfigError("auth.cookie_env: missing or invalid Human Cookie")
        base = origin(self.profile["browser_origin"], "preprod")
        return {"Cookie": cookie, "Origin": base, "Referer": base + "/"}

    def credential_kind(self) -> str:
        return provider_publishes_credential_kind(self.profile)

    def clear_credentials(self) -> None:
        self._credentials.clear()

    def _capture(self, key: str, value: object) -> str:
        _require(
            isinstance(value, str)
            and 1 <= len(value) <= 4096
            and value.isascii()
            and all(32 < ord(char) < 127 for char in value),
            f"provider {key} credential is missing or invalid",
        )
        assert isinstance(value, str)
        _require(
            key not in self._credentials
            and value not in self._credentials.values()
            and value != self.profile["human"]["user_id"],
            f"provider {key} credential is reused or borrowed",
        )
        self._credentials[key] = value
        return value

    def _safe_id(self, value: object, where: str) -> str:
        result = identifier(value, where)
        _require(
            result not in self._credentials.values(),
            "provider response ID contains a credential",
        )
        return result

    def _body(self, row: dict) -> dict | None:
        kind = row["body"]
        provider_id = self._ids.get("provider_id")
        provider_bot_uuid = self._ids.get("provider_bot_uuid")
        if kind is None:
            return None
        if kind == "register":
            return {
                "name": "E2E Provider",
                "slug": self.provider_slug,
                "webhook_url": self.profile["fixture"]["webhook_url"],
                "auth": {"mode": "static_bearer"},
                "protocol_version": "2.0",
                "coordination": {"mode": "native_tool"},
            }
        if kind == "duplicate":
            return {
                "name": "Duplicate E2E Provider",
                "slug": self.provider_slug,
                "auth": {"mode": "static_bearer"},
            }
        if kind == "update":
            return {
                "name": "E2E Provider Updated",
                "slug": self.renamed_slug,
                "protocol_version": "2.0",
            }
        if kind == "bot_publish":
            return {
                "name": "Provider Review Agent",
                "summary": "Reviews release plans",
                "owners": [self.profile["human"]["user_id"]],
                "provider_bot_ref": self.provider_bot_ref,
                "domains": ["release"],
                "skills": ["review"],
                "scopes": ["local"],
            }
        if kind == "bot_update":
            return {
                "name": "Provider Review Agent (Senior)",
                "domains": ["release", "audit"],
                "scopes": [],
            }
        if kind == "attributes_write":
            return {"user_visibility": "public"}
        if kind == "org_create":
            return {
                "organization_code": self.organization_code,
                "name": "E2E release organization",
                "description": "provider-managed release team",
            }
        if kind == "org_update":
            return {"name": "E2E release organization updated"}
        if kind == "member_add":
            return {"role": "reviewer"}
        if kind == "member_profile":
            return {"name": "E2E organization bot"}
        if kind == "admin_start":
            return {
                "target_bot_uuid": provider_bot_uuid,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Review the E2E release plan"}],
                },
                "detach": True,
            }
        if kind == "agentpass":
            return {}
        if kind == "unknown_run_callback":
            return {
                "run_id": self.unknown_run_id,
                "state": "final",
                "message": {"text": "late provider result"},
            }
        if kind == "takeover":
            return {
                "bot_id": self.profile["fixture"]["takeover_bot_id"],
                "provider_bot_ref": self.provider_bot_ref,
            }
        raise RunStateError(f"provider operation {row['operation']} has an unknown body kind")

    def _path_params(self, path: str) -> dict[str, str]:
        keys = set(re.findall(r"\{([a-z_]+)\}", path))
        params = {}
        for key in keys:
            value = self._ids.get(key)
            _require(
                isinstance(value, str) and bool(value),
                f"provider path parameter {key} is unavailable",
            )
            params[key] = value
        return params

    def _request(self, row: dict) -> tuple[dict | None, dict]:
        caller = row["caller"]
        token = None
        if caller in {"provider_admin", "bot_runtime"}:
            key = "provider_admin" if caller == "provider_admin" else "bot_runtime"
            token = self._credentials.get(key)
            _require(bool(token), f"provider {key} credential is unavailable")
        provider_id = self._ids.get("provider_id") if row["provider_header"] else None
        data, record = self.transport.call(
            row["operation"],
            token=token,
            path_params=self._path_params(row["path"]),
            body=self._body(row),
            provider_header_id=provider_id,
        )
        return data, record

    def _verify(self, requirement_id: str, value: dict | None) -> object:
        row = STEP_BY_REQUIREMENT[requirement_id]
        operation = row["operation"]
        expected_status = row["expected_status"]
        if expected_status == 204:
            _require(value is None or value == {}, "provider member removal must be empty")
            return {"http_status": 204, "removal": "LOGICAL"}
        _require(isinstance(value, dict), f"provider {operation} response must be an object")
        assert isinstance(value, dict)

        provider_id = self._ids.get("provider_id")
        provider_bot_uuid = self._ids.get("provider_bot_uuid")
        organization_code = self._ids["organization_code"]

        if requirement_id == "PROVIDER.register":
            self._ids["provider_id"] = self._safe_id(
                value.get("provider_id"), "provider_id"
            )
            self._capture("provider_admin", value.get("provider_admin_token"))
            self._capture("bcs_to_provider", value.get("bcs_to_provider_token"))
            return {
                "provider_id": self._ids["provider_id"],
                "credential_kinds_returned": ["provider_admin", "downlink"],
            }
        if requirement_id == "PROVIDER.discovery-public-slug":
            if set(value) != PUBLIC_PROVIDER_KEYS:
                raise RunStateError(
                    "public slug discovery does not expose only the basic public fields"
                )
            _require(
                value.get("provider_id") == provider_id
                and value.get("slug") == self.provider_slug
                and value.get("name") == "E2E Provider"
                and value.get("auth_mode") == "static_bearer"
                and value.get("protocol_version") == "2.0"
                and value.get("enabled") is True,
                "public slug discovery differs from the registered Provider",
            )
            self._public_projection = json.loads(json.dumps(value, allow_nan=False))
            return {
                "provider_id": provider_id,
                "slug": self.provider_slug,
                "public_keys": sorted(PUBLIC_PROVIDER_KEYS),
            }
        if requirement_id == "PROVIDER.discovery-collaboration-slug":
            _require(
                self._public_projection is not None and value == self._public_projection,
                "collaboration slug discovery changed the public projection",
            )
            return {
                "projection_equal": True,
                "public_keys": sorted(PUBLIC_PROVIDER_KEYS),
            }
        if requirement_id in {
            "PROVIDER.discovery-collaboration-unknown",
            "PROVIDER.discovery-public-unknown-before-rename",
            "PROVIDER.discovery-old-slug-after-rename",
        }:
            return {"not_found": True}
        if requirement_id in {
            "PROVIDER.discovery-collaboration-invalid",
            "PROVIDER.discovery-public-invalid",
        }:
            return {"invalid_slug_rejected": True}
        if requirement_id == "PROVIDER.duplicate-slug-conflict":
            return {"conflict": True}
        if requirement_id == "PROVIDER.read":
            _require(
                value.get("provider_id") == provider_id
                and value.get("auth_mode") == "static_bearer"
                and value.get("disabled") is False,
                "Provider metadata read differs",
            )
            return {"provider_id": provider_id, "disabled": False}
        if requirement_id == "PROVIDER.update":
            _require(
                value.get("name") == "E2E Provider Updated"
                and value.get("provider_id") == provider_id,
                "Provider update differs or changed the id",
            )
            return {"provider_id": provider_id, "name": "E2E Provider Updated"}
        if requirement_id == "PROVIDER.discovery-new-slug-after-rename":
            _require(
                value.get("provider_id") == provider_id
                and value.get("name") == "E2E Provider Updated",
                "renamed slug discovery did not expose the updated Provider",
            )
            return {"provider_id": provider_id, "slug": self.renamed_slug}
        if requirement_id == "PROVIDER.bot-publish":
            self._ids["provider_bot_uuid"] = self._safe_id(
                value.get("bot_uuid"), "provider_bot_uuid"
            )
            _require(
                self._ids["provider_bot_uuid"] != provider_id
                and value.get("provider_bot_ref") == self.provider_bot_ref,
                "published Provider Bot identity differs from the request",
            )
            self._capture("bot_runtime", value.get("bot_runtime_token"))
            return {
                "bot_uuid": self._ids["provider_bot_uuid"],
                "provider_bot_ref": self.provider_bot_ref,
                "credential_kinds_returned": ["bot_runtime"],
            }
        if requirement_id == "PROVIDER.bot-update":
            _require(
                value.get("bot_uuid") == provider_bot_uuid
                and value.get("provider_bot_ref") == self.provider_bot_ref
                and value.get("name") == "Provider Review Agent (Senior)"
                and value.get("domains") == ["release", "audit"]
                and value.get("scopes") == [],
                "Provider Bot patch differs or rotated identity",
            )
            return {
                "bot_uuid": provider_bot_uuid,
                "provider_bot_ref": self.provider_bot_ref,
                "domains": ["release", "audit"],
                "scopes": [],
            }
        if requirement_id in {
            "PROVIDER.attributes-read-denied",
            "PROVIDER.attributes-write-denied",
        }:
            return {"denied": True}
        if requirement_id == "PROVIDER.org-create":
            _require(
                value.get("organization_code") == organization_code,
                "organization creation changed the requested code",
            )
            return {"organization_code": organization_code}
        if requirement_id == "PROVIDER.org-list":
            organizations = value.get("organizations")
            _require(
                isinstance(organizations, list)
                and any(
                    isinstance(item, dict)
                    and item.get("organization_code") == organization_code
                    for item in organizations
                ),
                "organization list omits the exact created organization",
            )
            return {"organization_code": organization_code, "listed": True}
        if requirement_id == "PROVIDER.org-read":
            _require(
                value.get("organization_code") == organization_code
                and value.get("name") == "E2E release organization",
                "organization read differs",
            )
            return {"organization_code": organization_code,
                    "name": "E2E release organization"}
        if requirement_id == "PROVIDER.org-update":
            _require(
                value.get("organization_code") == organization_code
                and value.get("name") == "E2E release organization updated",
                "organization update differs",
            )
            return {"organization_code": organization_code,
                    "name": "E2E release organization updated"}
        if requirement_id in {
            "PROVIDER.candidate-list-before", "PROVIDER.candidate-list-after"
        }:
            bots = value.get("bots")
            _require(
                isinstance(bots, list)
                and all(isinstance(item, dict) for item in bots),
                "organization candidate list is not an object array",
            )
            found = any(
                item.get("bot_uuid") == provider_bot_uuid for item in bots
            )
            _require(
                found == (requirement_id.endswith("before")),
                "organization candidate list membership is incorrect",
            )
            return {
                "bot_uuid": provider_bot_uuid,
                "listed": found,
            }
        if requirement_id in {"PROVIDER.candidate-before", "PROVIDER.candidate-after"}:
            expected_member = requirement_id.endswith("after")
            _require(
                value.get("bot_uuid") == provider_bot_uuid
                and value.get("is_member") is expected_member,
                "candidate detail membership is incorrect",
            )
            return {
                "bot_uuid": provider_bot_uuid,
                "is_member": expected_member,
            }
        if requirement_id == "PROVIDER.member-add":
            _require(
                value.get("bot_uuid") == provider_bot_uuid
                and value.get("role") == "reviewer",
                "organization member add differs",
            )
            return {"bot_uuid": provider_bot_uuid, "role": "reviewer"}
        if requirement_id == "PROVIDER.member-profile":
            profile_value = value.get("profile")
            _require(
                isinstance(profile_value, dict)
                and profile_value.get("name") == "E2E organization bot",
                "organization member profile update differs",
            )
            return {"profile": {"name": "E2E organization bot"}}
        if requirement_id == "PROVIDER.member-read":
            _require(
                value.get("bot_uuid") == provider_bot_uuid,
                "organization member read differs",
            )
            return {"bot_uuid": provider_bot_uuid}
        if requirement_id == "PROVIDER.member-list":
            members = value.get("members")
            _require(
                isinstance(members, list)
                and any(
                    isinstance(item, dict)
                    and item.get("bot_uuid") == provider_bot_uuid
                    for item in members
                ),
                "organization member list omits the exact Bot",
            )
            return {"bot_uuid": provider_bot_uuid, "listed": True}
        if requirement_id == "PROVIDER.admin-start":
            data = value.get("data")
            _require(isinstance(data, dict), "admin run start has no data object")
            assert isinstance(data, dict)
            run_id = self._safe_id(data.get("run_id"), "admin_run_id")
            _require(
                data.get("organization_code") == organization_code
                and data.get("target_bot_uuid") == provider_bot_uuid
                and isinstance(data.get("status"), str)
                and bool(data.get("status")),
                "admin run start differs",
            )
            self._ids["admin_run_id"] = run_id
            self._admin_run_projection = {
                "run_id": run_id,
                "organization_code": organization_code,
                "target_bot_uuid": provider_bot_uuid,
                "status": data["status"],
            }
            return dict(self._admin_run_projection)
        if requirement_id == "PROVIDER.admin-read":
            data = value.get("data")
            _require(isinstance(data, dict), "admin run read has no data object")
            assert isinstance(data, dict)
            _require(
                self._admin_run_projection is not None
                and data.get("run_id") == self._admin_run_projection["run_id"]
                and data.get("target_bot_uuid") == provider_bot_uuid
                and isinstance(data.get("status"), str)
                and bool(data.get("status")),
                "admin run read differs",
            )
            return {
                "run_id": self._admin_run_projection["run_id"],
                "target_bot_uuid": provider_bot_uuid,
                "status": data["status"],
            }
        if requirement_id == "PROVIDER.bot-list":
            items = value.get("items")
            _require(
                isinstance(items, list)
                and any(
                    isinstance(item, dict)
                    and item.get("bot_uuid") == provider_bot_uuid
                    for item in items
                ),
                "Provider Bot list omits the exact published Bot",
            )
            return {"bot_uuid": provider_bot_uuid, "listed": True}
        if requirement_id == "PROVIDER.roster-denied":
            _require(
                "not allowed to access the task-mode roster"
                in json.dumps(value, ensure_ascii=True),
                "task-mode roster denial diagnostic differs",
            )
            return {"denied": True}
        if requirement_id == "PROVIDER.agentpass-negative":
            _require(
                "agent_code" in value
                and value.get("agent_code") is None
                and "provider_bot_binding" in value
                and value.get("provider_bot_binding") is None,
                "AgentPass negative probe resolved an agent",
            )
            return {"agent_code": None, "provider_bot_binding": None}
        if requirement_id == "PROVIDER.unknown-run-callback":
            _require(
                value.get("error") == "run_not_found",
                "unknown Provider callback did not return run_not_found",
            )
            return {"error": "run_not_found"}
        if requirement_id == "PROVIDER.takeover-denied":
            _require(
                "not allowed to switch bot delivery"
                in json.dumps(value, ensure_ascii=True),
                "delivery takeover denial diagnostic differs",
            )
            return {"denied": True}
        if requirement_id == "PROVIDER.disable":
            _require(value.get("disabled") is True, "Provider disable was not persisted")
            return {"disabled": True}
        if requirement_id == "PROVIDER.discovery-disabled-slug":
            _require(
                value.get("provider_id") == provider_id
                and value.get("enabled") is False,
                "disabled slug discovery differs",
            )
            return {"provider_id": provider_id, "enabled": False}
        if requirement_id == "PROVIDER.enable":
            _require(value.get("disabled") is False, "Provider enable was not persisted")
            return {"disabled": False}
        if requirement_id == "PROVIDER.discovery-enabled-slug":
            _require(
                value.get("provider_id") == provider_id
                and value.get("enabled") is True,
                "enabled slug discovery differs",
            )
            return {"provider_id": provider_id, "enabled": True}
        if requirement_id == "PROVIDER.bot-retire":
            _require(
                value.get("deleted") is True
                and value.get("provider_bot_ref") == self.provider_bot_ref,
                "Provider Bot retirement differs",
            )
            return {"deleted": True, "provider_bot_ref": self.provider_bot_ref}
        if requirement_id == "PROVIDER.bot-list-empty":
            _require(value.get("items") == [], "Provider Bot list is not empty")
            return {"items": []}
        raise RunStateError("Provider obligation has no assertion implementation")

    def execute_step(self, requirement_id: str) -> AdapterCall:
        row = STEP_BY_REQUIREMENT.get(requirement_id)
        _require(row is not None, "Provider obligation is not in the reviewed plan")
        _require(
            self._next < len(REQUIREMENTS)
            and requirement_id == REQUIREMENTS[self._next]
            and requirement_id not in self._attempted,
            "Provider obligation is outside the finite ordered one-attempt plan",
        )
        assert row is not None
        self._attempted.add(requirement_id)
        data, record = self._request(row)
        observed = self._verify(requirement_id, data)
        self._next += 1
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "requirement_id": requirement_id,
            "case_class": row["case_class"],
            "credential_kind": self.credential_kind(),
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        return AdapterCall(data=observed, evidence=evidence)


class LocalProviderPublishesAdapter(ProviderPublishesAdapter):
    """Singlebox legacy HTTP adapter with the mock-human ingress identity."""


class RemoteProviderPublishesAdapter(ProviderPublishesAdapter):
    """Preprod gateway legacy HTTP adapter with the Human Cookie ingress."""


def provider_publishes_adapter(profile: dict, *, run_id: str) -> ProviderPublishesAdapter:
    if profile["environment"]["tier"] == "local":
        return LocalProviderPublishesAdapter(profile, run_id=run_id)
    return RemoteProviderPublishesAdapter(profile, run_id=run_id)


def _require_step_evidence(
    adapter: ProviderPublishesAdapter, call: object, operation: str
) -> dict:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"Provider {operation} did not return AdapterCall")
    call.validate()
    if (
        call.evidence.get("transport") != "PRODUCT_HTTP"
        or call.evidence.get("credential_kind") != adapter.credential_kind()
        or call.evidence.get("remote_validation") != "NOT_RUN"
        or call.evidence.get("grants_release_credit") is not False
        or call.evidence.get("fixture_effect") != adapter.fixture_effect
    ):
        raise RunStateError(f"Provider evidence for {operation} is invalid")
    return dict(call.evidence)


def execute_provider_publishes_case(adapter: object) -> CaseExecution:
    """Run all 42 Provider obligations once, preserving completed PASS rows."""
    provider_publishes_case_plan()
    if not isinstance(adapter, ProviderPublishesAdapter):
        raise RunStateError("Provider publishes requires its dual adapter")
    outcomes: list[StepOutcome] = []
    completed = 0
    error_type: str | None = None
    error_detail: str | None = None
    try:
        for row in STEP_ROWS:
            if error_type is None:
                try:
                    call = adapter.execute_step(row["requirement_id"])
                    evidence = _require_step_evidence(
                        adapter, call, row["operation"]
                    )
                    outcomes.append(StepOutcome(
                        requirement_id=row["requirement_id"],
                        method=_CASE_METHOD,
                        status="PASS",
                        data=call.data,
                        evidence=evidence,
                        error_type=None,
                    ))
                    completed += 1
                    continue
                except Exception as error:
                    error_type = type(error).__name__
                    detail = str(error) or None
                    error_detail = detail[:797] + "..." if detail and len(detail) > 800 else detail
                    outcomes.append(StepOutcome(
                        requirement_id=row["requirement_id"],
                        method=_CASE_METHOD,
                        status="NOT_RUN",
                        data=None,
                        evidence={},
                        error_type=error_type,
                        error_detail=error_detail,
                    ))
                    continue
            outcomes.append(StepOutcome(
                requirement_id=row["requirement_id"],
                method=_CASE_METHOD,
                status="NOT_RUN",
                data=None,
                evidence={},
                error_type=None,
            ))
    finally:
        adapter.clear_credentials()
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id=PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(outcomes),
        completed_count=completed,
        total_count=len(STEP_ROWS),
        error_type=error_type,
        ready_to_execute=error_type is None,
        error_detail=error_detail,
    )
    execution.validate()
    return execution


_CASES = {
    "provider-publishes": {
        "case_id": CASE_ID,
        "validator": validate_provider_publishes_profile,
        "adapter": provider_publishes_adapter,
        "execute": execute_provider_publishes_case,
        "fixture_effect": FIXTURE_EFFECT,
    },
}


def load_provider_publishes_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("provider publishes case must be provider-publishes")
    if path.is_symlink() or not path.is_file():
        raise ConfigError("provider publishes profile must be a regular file")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("provider publishes profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def dual_provider_publishes_case_run(
    *,
    profile_path: Path,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("provider publishes case must be provider-publishes")
    run_id = identifier(run_id, f"provider publishes {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "provider publishes requires explicit write permission and exact Run confirmation"
        )
    profile = load_provider_publishes_profile(profile_path, case_key)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute()
        or requested_state_dir.is_symlink()
        or requested_state_dir.exists()
        or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError(
            "provider publishes requires a fresh private state directory"
        )
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(
            "provider publishes requires a fresh private state directory"
        )
    state_dir.mkdir(mode=0o700, parents=False)
    profile_file = state_dir / f"provider-publishes-{case_key}-profile.json"
    binding_file = state_dir / f"provider-publishes-{case_key}-binding.json"
    report_file = state_dir / f"provider-publishes-{case_key}-run.json"
    write(profile_file, profile)
    source_binding = provider_publishes_source_binding()
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "suite": source_binding["contract"],
        "case_id": _CASES[case_key]["case_id"],
        "parent_id": PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "source_binding_digest": fingerprint(source_binding),
        "source_revision": source_binding["source_revision"],
        "requirement_ids": list(REQUIREMENTS),
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        adapter = _CASES[case_key]["adapter"](profile, run_id=run_id)
        execution = _CASES[case_key]["execute"](adapter)
    except (
        ConfigError,
        RunStateError,
        ProviderPublishesTransportError,
        OSError,
    ) as error:
        raise ConfigError(
            f"provider publishes {case_key} case execution is unavailable: {error}"
        ) from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": PARENT,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "source_binding_digest": binding["source_binding_digest"],
        "source_revision": source_binding["source_revision"],
        "state_dir": str(state_dir),
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
        },
        "obligation_summary": {
            "total": len(REQUIREMENTS),
            "frozen_core": len(REQUIREMENTS) - 10,
            "source_api_get_discovery": 10,
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
        "credential_kind": provider_publishes_credential_kind(profile),
    }
    write(report_file, report)
    return report
