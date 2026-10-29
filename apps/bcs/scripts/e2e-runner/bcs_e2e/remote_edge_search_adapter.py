"""Cross-environment adapter for the reviewed Bot-search candidate reads."""
from __future__ import annotations

from .case_adapters import AdapterCall
from .edge_search_case import (
    EDGE_BOT_SEARCH_CANDIDATES_CASE_ID,
    execute_edge_bot_search_candidates_case,
)
from .profile import ConfigError
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient


class RemoteEdgeBotSearchCandidatesAdapter:
    """Execute only the name-fuzzy and empty-query Bot-search reads."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("Bot-search candidates adapter requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError("Bot-search candidates require a public-v1/internal-v1 Singlebox or gateway profile")
        try:
            self.name_binding = require_edge_case_binding(profile, "edge_bot_search_name.read")
            self.empty_binding = require_edge_case_binding(profile, "edge_bot_search_empty.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        driver = profile["actors"]["driver"]["bot_id"]
        if (self.name_binding.get("caller_actor_id") != driver
                or self.empty_binding.get("caller_actor_id") != driver):
            raise RunStateError("Bot-search candidate viewpoint must be the dedicated driver Bot")
        self.case_id = EDGE_BOT_SEARCH_CANDIDATES_CASE_ID
        self.profile = profile
        self.viewpoint_actor_id = driver
        self.client = ReadClient(profile)

    def _read(self, requirement_id: str, operation: str,
              binding: dict, expected_modes: set[str]) -> AdapterCall:
        if (requirement_id != "EDGE-BOT-SEARCH.name-fuzzy"
                and requirement_id != "EDGE-BOT-SEARCH.empty-query"):
            raise RunStateError("unsupported Bot-search candidate requirement")
        observation = self.client.read(operation)
        body, record = observation.data, observation.evidence
        if (record.get("path") != binding["path"]
                or record.get("method") != "GET"
                or record.get("http_status") != 200):
            raise RunStateError("Bot-search candidate transport evidence differs from the fixed contract")
        items = body.get("items")
        search_mode = body.get("search_mode")
        if (search_mode not in expected_modes
                or not isinstance(items, list) or len(items) > 20):
            raise RunStateError("Bot-search candidate response violates its bounded contract")
        for item in items:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("bot"), dict)
                    or not isinstance(item["bot"].get("bot_id"), str)
                    or not item["bot"]["bot_id"]
                    or type(item.get("is_friend")) is not bool
                    or not isinstance(item.get("tags"), dict)
                    or ("score" in item and type(item.get("score")) not in (int, float))
                    or ("short_profile" in item and not isinstance(item.get("short_profile"), str))):
                raise RunStateError("Bot-search candidate response item is malformed")
        data = {"item_count": len(items), "search_mode": search_mode}
        evidence = {
            "transport": "internal-v1-http",
            "operation": operation,
            "path": record["path"],
            "api_face": "internal-v1",
            "http_method": "GET",
            "http_status": record["http_status"],
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
            "viewpoint_actor_id": self.viewpoint_actor_id,
            "fixture_effect": "NONE",
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("Bot-search candidate evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def read_bot_search_name_fuzzy(self) -> AdapterCall:
        return self._read(
            "EDGE-BOT-SEARCH.name-fuzzy",
            "edge.bot_search_name.read",
            self.name_binding,
            {"semantic", "name_fallback"},
        )

    def read_bot_search_empty_query(self) -> AdapterCall:
        return self._read(
            "EDGE-BOT-SEARCH.empty-query",
            "edge.bot_search_empty.read",
            self.empty_binding,
            {"empty_query"},
        )

    def execute(self):
        return execute_edge_bot_search_candidates_case(self)
