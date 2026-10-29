"""Dual-environment adapter for the two reviewed Edge admission reads.

The route is the native BCS admission face on a public-v1 Singlebox or Gateway
origin. Both requests are unauthenticated, read-only, finite, and non-fallback.
This module grants no release credit and never infers a live fixture result.
"""
from __future__ import annotations

from .case_adapters import AdapterCall
from .edge_read_case import ADMISSION_CASE_ID, edge_read_case_plans
from .profile import ConfigError
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient

_HUMAN_ACTOR_OPERATION = "edge_admission_human_actor.read"
_ACTOR_KIND_OPERATION = "edge_admission_actor_kind.read"
_RESPONSE_KEYS = {"allowed", "grants", "reason_code", "public_default"}


class RemoteEdgeAdmissionAdapter:
    """Execute only the two fixed admission reads from a bound profile."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("Edge admission requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
            or entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError("Edge admission requires an admitted public-v1/native-face Singlebox or gateway profile")
        self.plan = next(row for row in edge_read_case_plans()
                         if row.case_id == ADMISSION_CASE_ID)
        try:
            self.human_actor_binding = require_edge_case_binding(profile, _HUMAN_ACTOR_OPERATION)
            self.actor_kind_binding = require_edge_case_binding(profile, _ACTOR_KIND_OPERATION)
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.profile = profile
        self.target_bot_id = profile["actors"]["driver"]["bot_id"]
        self.human_id = profile["human"]["user_id"]
        self.client = ReadClient(profile)
        self._attempted: set[str] = set()

    def _read(self, *, requirement_id: str, operation: str, binding: dict,
              actor_binding: str) -> AdapterCall:
        requirement = next((row for row in self.plan.requirements
                            if row.requirement_id == requirement_id), None)
        if requirement is None or requirement.adapter_method in self._attempted:
            raise RunStateError("Edge admission contract changed or method was repeated")
        self._attempted.add(requirement.adapter_method)
        observation = self.client.read(operation)
        body, record = observation.data, observation.evidence
        expected_cookie = binding["auth"] == "human_cookie"
        if (record.get("operation") != operation
                or record.get("method") != "GET"
                or record.get("path") != binding["path"]
                or record.get("http_status") != 200
                or record.get("credentials_sent") is not expected_cookie
                or record.get("authorization_sent") is not False):
            raise RunStateError("Edge admission transport evidence differs from the fixed contract")
        if not isinstance(body, dict) or set(body) != _RESPONSE_KEYS:
            raise RunStateError("Edge admission response does not satisfy the explicit route contract")
        grants = body["grants"]
        if (not isinstance(body["allowed"], bool)
                or not isinstance(grants, list) or len(grants) > 100
                or any(not isinstance(row, dict) for row in grants)
                or not isinstance(body["reason_code"], str)
                or not body["reason_code"]
                or not isinstance(body["public_default"], bool)):
            raise RunStateError("Edge admission response values are outside the explicit contract")
        data = {
            "allowed": body["allowed"], "reason_code": body["reason_code"],
            "grant_count": len(grants), "public_default": body["public_default"],
        }
        evidence = {
            "transport": "native-v1-http", "operation": operation,
            "path": record["path"], "http_method": "GET",
            "http_status": record["http_status"],
            "identity": "HUMAN_COOKIE_DRIVER_VIEW" if expected_cookie else "UNAUTHENTICATED",
            "target_actor_id": self.target_bot_id,
            "actor_binding": actor_binding, "credentials_sent": expected_cookie,
            "authorization_sent": False, "fixture_effect": "NONE", **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("Edge admission evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def read_admission_human_actor(self) -> AdapterCall:
        return self._read(
            requirement_id="EDGE-ADMISSION.human-actor",
            operation="edge.admission.human_actor.read",
            binding=self.human_actor_binding, actor_binding="PREFIXED_HUMAN",
        )

    def read_admission_actor_kind(self) -> AdapterCall:
        return self._read(
            requirement_id="EDGE-ADMISSION.actor-kind",
            operation="edge.admission.actor_kind.read",
            binding=self.actor_kind_binding, actor_binding="BARE_HUMAN_ACTOR_KIND",
        )
