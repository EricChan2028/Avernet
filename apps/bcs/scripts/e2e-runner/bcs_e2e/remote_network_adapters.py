"""Focused remote network-read adapters for public-v1 Cases."""
from __future__ import annotations

from .case_adapters import AdapterCall
from .local_network_actor_status_contract import (
    CASE_ID as NETWORK_ACTOR_STATUS_CASE_ID,
    execute_network_actor_status_case,
)
from .local_channel_public_contract import (
    CASE_ID as CHANNEL_BINDINGS_CASE_ID,
    _REQUEST_OPERATIONS as _CHANNEL_REQUEST_OPERATIONS,
    _BLOCKED_REQUIREMENTS as CHANNEL_BLOCKED_REQUIREMENTS,
    execute_channel_bindings_case,
)
from .local_structured_public_contract import (
    CASE_ID as STRUCTURED_TEMPLATES_CASE_ID,
    TEMPLATE_ID as STRUCTURED_TEMPLATE_ID,
    TEMPLATE_LANG as STRUCTURED_TEMPLATE_LANG,
    execute_structured_templates_case,
)
from .local_network_contract import (
    NETWORK_ACTOR_LIST_CASE_ID,
    NETWORK_ACTOR_SEARCH_CASE_ID,
    NETWORK_BOTS_MINE_CASE_ID,
    NETWORK_BOTS_PAGED_CASE_ID,
    NETWORK_BOTS_QUERY_CASE_ID,
    NETWORK_PLATFORM_ASSET_CASE_ID,
    execute_network_actor_list_case,
    execute_network_actor_search_case,
    execute_network_bots_mine_case,
    execute_network_bots_paged_case,
    execute_network_bots_query_case,
    execute_network_platform_asset_case,
)
from .identity_binding import HUMAN_VIEW, human_actor_path, identity_request, resolve_identity_binding
from .profile import ConfigError
from .route_bindings import require_edge_case_binding
from .run_state import RunStateError
from .transport import ReadClient
from .write_transport import WriteClient


class RemoteNetworkBotsQueryAdapter:
    """Execute the one public-v1 bulk Bot query read from the network Story."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("Bot bulk-query adapter requires an admitted public-v1 profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("Bot bulk-query requires an admitted public-v1 Singlebox or gateway profile")
        try:
            self.binding = require_edge_case_binding(profile, "network_bot_query.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = NETWORK_BOTS_QUERY_CASE_ID
        self.profile = profile
        self.target_actor_ids = list(self.binding["target_actor_ids"])
        self.client = ReadClient(profile)

    def read_directory_bots(self) -> AdapterCall:
        observation = self.client.read_bot_query("network.bot_query.read")
        body, record = observation.data, observation.evidence
        if (record.get("path") != self.binding["path"]
                or record.get("method") != "POST"
                or record.get("http_status") != 200):
            raise RunStateError("Bot bulk-query transport evidence differs from the fixed contract")
        if not isinstance(body, dict):
            raise RunStateError("Bot bulk-query response is not a public object envelope")
        items = body.get("items")
        if not isinstance(items, list) or len(items) != 2:
            raise RunStateError("Bot bulk-query response must resolve both reviewed targets exactly")
        returned_ids = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("bot_id"), str):
                raise RunStateError("Bot bulk-query response item is malformed")
            returned_ids.append(item["bot_id"])
        if len(set(returned_ids)) != 2 or set(returned_ids) != set(self.target_actor_ids):
            raise RunStateError("Bot bulk-query resolved IDs differ from the reviewed target set")
        data = {"item_count": 2, "returned_ids": returned_ids}
        evidence = {
            "transport": "public-v1-http",
            "operation": "network.bot_query.read",
            "path": record["path"],
            "http_method": "POST",
            "http_status": record["http_status"],
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
            "target_actor_ids": list(self.target_actor_ids),
            "fixture_effect": "NONE",
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("Bot bulk-query evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_bots_query_case(self)


class RemoteNetworkActorListAdapter:
    """Execute the one public-v1 Human-managed Candidate directory read."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("actor-directory adapter requires an admitted public-v1 profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("actor-directory requires an admitted public-v1 Singlebox or gateway profile")
        try:
            self.binding = require_edge_case_binding(profile, "network_actor_list.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = NETWORK_ACTOR_LIST_CASE_ID
        self.profile = profile
        self.viewpoint_actor_id = profile["actors"]["driver"]["bot_id"]
        self.client = ReadClient(profile)

    def read_actor_directory(self) -> AdapterCall:
        observation = self.client.read("network.actor_list.read")
        body, record = observation.data, observation.evidence
        if (record.get("path") != self.binding["path"]
                or record.get("method") != "GET"
                or record.get("http_status") != 200):
            raise RunStateError("actor-directory transport evidence differs from the fixed contract")
        if not isinstance(body, dict):
            raise RunStateError("actor-directory response is not a public object envelope")
        items = body.get("items")
        if (not isinstance(items, list) or len(items) > 20
                or type(body.get("total")) is not int or body["total"] < len(items)
                or type(body.get("offset")) is not int or body.get("offset") != 0
                or type(body.get("limit")) is not int or body.get("limit") != 20):
            raise RunStateError("actor-directory response does not satisfy the reviewed page contract")
        for item in items:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("bot"), dict)
                    or not isinstance(item["bot"].get("bot_id"), str) or not item["bot"]["bot_id"]
                    or type(item.get("is_friend")) is not bool):
                raise RunStateError("actor-directory response item is malformed")
        data = {"item_count": len(items), "total": body["total"],
                "offset": body["offset"], "limit": body["limit"]}
        evidence = {
            "transport": "public-v1-http",
            "operation": "network.actor_list.read",
            "path": record["path"],
            "http_method": "GET",
            "http_status": record["http_status"],
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
            "viewpoint_actor_id": self.viewpoint_actor_id,
            "fixture_effect": "NONE",
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("actor-directory evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_actor_list_case(self)


class RemoteNetworkActorSearchAdapter:
    """Execute the exact station internal-v1 candidate-search read slice."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("actor-search adapter requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError("actor-search requires the reviewed public-v1/local internal-v1 face")
        try:
            self.binding = require_edge_case_binding(profile, "network_actor_search.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = NETWORK_ACTOR_SEARCH_CASE_ID
        self.profile = profile
        self.viewpoint_actor_id = profile["actors"]["driver"]["bot_id"]
        self.client = ReadClient(profile)

    def read_actor_search(self) -> AdapterCall:
        observation = self.client.read("network.actor_search.read")
        body, record = observation.data, observation.evidence
        if (record.get("path") != self.binding["path"]
                or record.get("method") != "GET"
                or record.get("http_status") != 200):
            raise RunStateError("actor-search transport evidence differs from the fixed contract")
        if not isinstance(body, dict):
            raise RunStateError("actor-search response is not a bounded object envelope")
        items = body.get("items")
        search_mode = body.get("search_mode")
        if (search_mode not in {"empty_query", "semantic", "name_fallback"}
                or not isinstance(items, list) or len(items) > 20):
            raise RunStateError("actor-search response violates its bounded search contract")
        for item in items:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("bot"), dict)
                    or not isinstance(item["bot"].get("bot_id"), str)
                    or not item["bot"]["bot_id"]
                    or type(item.get("is_friend")) is not bool
                    or not isinstance(item.get("tags"), dict)
                    or ("score" in item and type(item.get("score")) not in (int, float))
                    or ("short_profile" in item and not isinstance(item.get("short_profile"), str))):
                raise RunStateError("actor-search response item is malformed")
        data = {"item_count": len(items), "search_mode": search_mode}
        evidence = {
            "transport": "internal-v1-http",
            "operation": "network.actor_search.read",
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
            raise RunStateError("actor-search evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_actor_search_case(self)


class RemoteNetworkBotsMineAdapter:
    """Execute the one public-v1 owned-Bot directory read from the network Story."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("owned-Bot directory adapter requires an admitted public-v1 profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("owned-Bot directory requires an admitted public-v1 Singlebox or gateway profile")
        try:
            self.binding = require_edge_case_binding(profile, "network_bot_mine.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = NETWORK_BOTS_MINE_CASE_ID
        self.profile = profile
        self.client = ReadClient(profile)

    def read_owned_directory_bots(self) -> AdapterCall:
        observation = self.client.read("network.bot_mine.read")
        body, record = observation.data, observation.evidence
        if (record.get("path") != self.binding["path"]
                or record.get("method") != "GET"
                or record.get("http_status") != 200):
            raise RunStateError("owned-Bot directory transport evidence differs from the fixed contract")
        if not isinstance(body, dict):
            raise RunStateError("owned-Bot directory response is not a public object envelope")
        items = body.get("items")
        if (not isinstance(items, list) or not 1 <= len(items) <= 20
                or type(body.get("total")) is not int or body.get("total", 0) < 1
                or type(body.get("offset")) is not int or body.get("offset") != 0
                or type(body.get("limit")) is not int or body.get("limit") != 20):
            raise RunStateError("owned-Bot directory response does not satisfy the reviewed page contract")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("bot_id"), str) or not item["bot_id"]:
                raise RunStateError("owned-Bot directory response item is malformed")
        data = {"item_count": len(items), "total": body["total"],
                "offset": body["offset"], "limit": body["limit"]}
        evidence = {
            "transport": "public-v1-http",
            "operation": "network.bot_mine.read",
            "path": record["path"],
            "http_method": "GET",
            "http_status": record["http_status"],
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
            "fixture_effect": "NONE",
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("owned-Bot directory evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_bots_mine_case(self)


class RemoteNetworkBotsPagedAdapter:
    """Execute the station Human-actor Bot-page slice with Human Cookie only."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("Bot-page adapter requires an admitted public-v1 profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("Bot-page requires an admitted public-v1 Singlebox or gateway profile")
        try:
            self.binding = require_edge_case_binding(profile, "network_bots_paged.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = NETWORK_BOTS_PAGED_CASE_ID
        self.profile = profile
        self.acting_actor_id = "human_" + profile["human"]["user_id"]
        self.client = ReadClient(profile)

    def read_bots_paged(self) -> AdapterCall:
        observation = self.client.read("network.bots_paged.read")
        body, record = observation.data, observation.evidence
        if (record.get("path") != self.binding["path"]
                or record.get("method") != "GET"
                or record.get("http_status") != 200):
            raise RunStateError("Bot-page transport evidence differs from the fixed contract")
        if not isinstance(body, dict):
            raise RunStateError("Bot-page response is not a public object envelope")
        items = body.get("items")
        if (not isinstance(items, list) or not 1 <= len(items) <= 5
                or type(body.get("total")) is not int or body.get("total", 0) < 1
                or type(body.get("offset")) is not int or body.get("offset") != 0
                or type(body.get("limit")) is not int or body.get("limit") != 5):
            raise RunStateError("Bot-page response does not satisfy the reviewed first-page contract")
        for item in items:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("bot"), dict)
                    or not isinstance(item["bot"].get("bot_id"), str)
                    or not item["bot"]["bot_id"]
                    or type(item.get("is_friend")) is not bool):
                raise RunStateError("Bot-page response item is malformed")
        data = {"item_count": len(items), "total": body["total"],
                "offset": body["offset"], "limit": body["limit"]}
        evidence = {
            "transport": "public-v1-http",
            "operation": "network.bots_paged.read",
            "path": record["path"],
            "http_method": "GET",
            "http_status": record["http_status"],
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
            "acting_actor_id": self.acting_actor_id,
            "fixture_effect": "NONE",
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("Bot-page evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_bots_paged_case(self)



class RemoteNetworkActorStatusAdapter:
    """Execute and restore the dedicated driver Bot lifecycle-status transition."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("actor-status adapter requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("actor-status requires an admitted public-v1 Singlebox or gateway profile")
        try:
            require_edge_case_binding(profile, "network_actor_status.read")
            require_edge_case_binding(profile, "network_actor_status.write")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = NETWORK_ACTOR_STATUS_CASE_ID
        self.profile = profile
        self.target_actor_id = profile["actors"]["driver"]["bot_id"]
        self.client = ReadClient(profile)
        self.writer = WriteClient(profile)

    def _project(self, observation) -> dict:
        body, record = observation.data, observation.evidence
        if (record.get("target_actor_id") != self.target_actor_id
                or body.get("bot_id") != self.target_actor_id
                or body.get("status") not in {"online", "hidden"}):
            raise RunStateError("actor-status read did not resolve the dedicated driver Bot")
        return {"target_actor_id": self.target_actor_id, "status": body["status"]}

    def toggle_actor_status(self) -> AdapterCall:
        before = self._project(self.client.read("network.actor_status.read"))
        if before["status"] != "online":
            raise RunStateError("actor-status slice requires the driver Bot to start online")

        hidden_ack, hidden_write = self.writer.patch_bot_status(self.target_actor_id, "hidden")
        if hidden_ack.get("status") != "hidden":
            raise RunStateError("actor-status hidden update was not acknowledged")
        hidden_read = self._project(self.client.read("network.actor_status.read"))
        if hidden_read["status"] != "hidden":
            raise RunStateError("actor-status hidden readback differs")

        online_ack, online_write = self.writer.patch_bot_status(self.target_actor_id, "online")
        if online_ack.get("status") != "online":
            raise RunStateError("actor-status online restore was not acknowledged")
        online_read = self._project(self.client.read("network.actor_status.read"))
        if online_read["status"] != "online":
            raise RunStateError("actor-status online readback differs")

        read_records = list(self.client.evidence)
        wants = [row for row in read_records if row.get("operation") == "network.actor_status.read"]
        if len(wants) != 3:
            raise RunStateError("actor-status transport evidence is incomplete")
        requests = [dict(wants[0]), dict(hidden_write), dict(wants[1]),
                    dict(online_write), dict(wants[2])]
        data = {
            "target_actor_id": self.target_actor_id,
            "before_status": before["status"],
            "hidden_status": hidden_read["status"],
            "online_status": online_read["status"],
        }
        evidence = {
            "transport": "public-v1-http",
            "operation": "network.actor_status.toggle",
            "target_actor_id": self.target_actor_id,
            "fixture_effect": "BOUNDED_OWNED_BOT_STATUS_RESTORE",
            "projection": data,
            "requests": requests,
        }
        secrets = {**self.client.headers, **self.writer.headers}
        if any(secret and secret in repr(evidence) for secret in secrets.values()):
            raise RunStateError("actor-status evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_actor_status_case(self)


class RemoteChannelBindingsAdapter:
    """Execute the reviewed public-v1 uninstalled-channel quiet-state slice."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("channel-binding adapter requires an admitted public-v1 profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if not valid_entrypoint or entrypoint.get("api_face") != "public-v1":
            raise RunStateError("channel-binding adapter requires a public-v1 Singlebox or gateway profile")
        try:
            self.bindings = {
                name: require_edge_case_binding(profile, name)
                for name in (
                    "channel_bindings.create_denied",
                    "channel_bindings.list",
                    "channel_bindings.by_target",
                )
            }
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = CHANNEL_BINDINGS_CASE_ID
        self.profile = profile
        self.target_actor_id = profile["actors"]["driver"]["bot_id"]
        self.client = ReadClient(profile)

    @staticmethod
    def _items(observation) -> list:
        body, _ = observation.data, observation.evidence
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise RunStateError("channel-binding response is not a public binding page")
        for item in body["items"]:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("channel_type"), str) or not item["channel_type"]
                    or not isinstance(item.get("target"), dict)
                    or set(item["target"]) not in ({"bot"}, {"group"})):
                raise RunStateError("channel-binding list item violates the public shape")
            if item["target"].get("bot") is not None and (
                    not isinstance(item["target"].get("bot"), dict)
                    or not isinstance(item["target"]["bot"].get("bot_id"), str)
                    or not item["target"]["bot"]["bot_id"]):
                raise RunStateError("channel-binding Bot target is malformed")
            if item["target"].get("group") is not None and (
                    not isinstance(item["target"].get("group"), dict)
                    or not isinstance(item["target"]["group"].get("group_id"), str)
                    or not item["target"]["group"]["group_id"]):
                raise RunStateError("channel-binding Group target is malformed")
        return body["items"]

    def verify_uninstalled_channel_binding_is_rejected(self) -> AdapterCall:
        denied = self.client.attempt_channel_binding_create()
        if (denied.evidence.get("path") != self.bindings["channel_bindings.create_denied"]["path"]
                or denied.evidence.get("target_actor_id") != self.target_actor_id
                or denied.evidence.get("http_status") != 400
                or denied.data.get("error_code") != "invalid_channel_params"):
            raise RunStateError("uninstalled-channel create was not rejected by the exact public contract")

        before_observation = self.client.read("channel.bindings.list_before")
        before_items = self._items(before_observation)
        target_observation = self.client.read("channel.bindings.by_target_empty")
        target_items = self._items(target_observation)
        if target_items:
            raise RunStateError("the dedicated target already has a binding for the uninstalled channel")
        after_observation = self.client.read("channel.bindings.list_after")
        after_items = self._items(after_observation)
        ghost_items = [
            item for item in after_items
            if item["channel_type"] == "e2e-uninstalled-channel"
            and item["target"].get("bot", {}).get("bot_id") == self.target_actor_id
        ]
        if ghost_items:
            raise RunStateError("the rejected create produced a binding visible through the public list")

        requests = [dict(row) for row in self.client.evidence]
        if [row.get("operation") for row in requests] != list(_CHANNEL_REQUEST_OPERATIONS):
            raise RunStateError("channel-binding transport evidence is incomplete")
        data = {
            "target_actor_id": self.target_actor_id,
            "channel_type": "e2e-uninstalled-channel",
            "rejected_error_code": denied.data["error_code"],
            "before_item_count": len(before_items),
            "target_item_count": len(target_items),
            "after_item_count": len(after_items),
            "ghost_item_count": len(ghost_items),
        }
        evidence = {
            "transport": "public-v1-http",
            "operation": "channel.bindings.quiet_rejection",
            "target_actor_id": self.target_actor_id,
            "fixture_effect": "NO_BINDING_CREATED",
            "blocked_requirement_ids": list(CHANNEL_BLOCKED_REQUIREMENTS),
            "projection": data,
            "requests": requests,
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("channel-binding evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_channel_bindings_case(self)


class RemoteNetworkPlatformAssetAdapter:
    """Execute only the reviewed platform manifest/identity/asset read slice."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("platform asset adapter requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError("platform asset requires a public-v1 Singlebox or gateway internal-v1 profile")
        try:
            self.binding = require_edge_case_binding(profile, "network_platform_asset.read")
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        identity_binding = resolve_identity_binding(profile)
        expected_identity = (HUMAN_VIEW if environment.get("tier") == "preprod"
                             else {"method": "GET", "path": "/openapi/v1/auth/user",
                                   "response": "public-envelope"})
        if identity_binding != expected_identity:
            raise RunStateError(
                "platform identity requires the environment's exact authenticated Human view")
        self.case_id = NETWORK_PLATFORM_ASSET_CASE_ID
        self.profile = profile
        self.expected_env = environment["expected_bcs_env"]
        self.expected_user_id = profile["human"]["user_id"]
        self.client = ReadClient(profile)

    def read_platform_manifest_identity_asset(self) -> AdapterCall:
        manifest_observation = self.client.read("environment.read")
        identity_observation = self.client.read("identity.read")
        manifest_body, identity_body = manifest_observation.data, identity_observation.data
        records = list(self.client.evidence)
        if records[0].get("path") != "/api/v1/collaboration/manifest":
            raise RunStateError("platform identity discovery differs from the reviewed station face")
        if self.profile["environment"]["tier"] == "preprod":
            expected_paths = [human_actor_path(self.profile), identity_request(self.profile)["path"]]
            if [row.get("path") for row in records[1:3]] != expected_paths:
                raise RunStateError("platform identity discovery differs from the reviewed station face")
        elif records[1].get("path") != "/openapi/v1/auth/user":
            raise RunStateError("platform identity discovery differs from the reviewed station face")
        bundles = manifest_body.get("bundles")
        if (manifest_body.get("schema_version") != 1
                or manifest_body.get("env") != self.expected_env
                or not isinstance(bundles, list) or len(bundles) != 1
                or not isinstance(bundles[0], dict)
                or bundles[0].get("name") != self.binding["asset_name"]
                or bundles[0].get("url") != self.binding["path"]):
            raise RunStateError("platform manifest does not expose the exact reviewed panel bundle")
        if identity_body.get("user_id") != self.expected_user_id:
            raise RunStateError("platform identity does not resolve the exact configured Human")
        if (self.profile["environment"]["tier"] != "preprod"
                and (not isinstance(identity_body.get("provider"), str)
                     or not identity_body["provider"])):
            raise RunStateError("platform identity does not expose its authentication provider")
        asset_observation = self.client.read_platform_asset(self.binding["path"])
        asset = asset_observation.data
        records.append(asset_observation.evidence)
        if (asset.get("sha256") != self.binding["asset_sha256"]
                or asset.get("size") != asset_observation.evidence.get("size")):
            raise RunStateError("platform asset bytes do not match the reviewed SHA-256")
        data = {
            "schema_version": manifest_body["schema_version"],
            "env": manifest_body["env"],
            "panel_bundle_name": bundles[0]["name"],
            "panel_asset_url": bundles[0]["url"],
            "panel_asset_size": asset["size"],
            "panel_asset_sha256": asset["sha256"],
            "user_id": identity_body["user_id"],
        }
        evidence = {
            "transport": ["internal-v1-http", "public-v1-http", "javascript-asset-http"],
            "requests": records,
            "manifest_env_matches_profile": True,
            "identity_matches_profile": True,
            "fixture_effect": "NONE",
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("platform asset evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_network_platform_asset_case(self)


class RemoteStructuredTemplatesAdapter:
    """Execute the exact station catalog list and detail reads for one template."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError("structured-template adapter requires an admitted profile")
        entrypoint = profile.get("entrypoint", {})
        environment = profile.get("environment", {})
        valid_entrypoint = (
            entrypoint.get("kind") == "singlebox" and environment.get("tier") == "local"
        ) or (
            entrypoint.get("kind") == "gateway" and environment.get("tier") == "preprod"
        )
        if (not valid_entrypoint or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError("structured templates require a public-v1 Singlebox or gateway internal-v1 profile")
        try:
            self.bindings = {
                name: require_edge_case_binding(profile, name)
                for name in ("structured_templates.list", "structured_templates.detail")
            }
        except (ConfigError, ValueError) as error:
            raise RunStateError(str(error)) from None
        self.case_id = STRUCTURED_TEMPLATES_CASE_ID
        self.profile = profile
        self.client = ReadClient(profile)

    def read_public_templates(self) -> AdapterCall:
        list_observation = self.client.read("structured.templates.list")
        detail_observation = self.client.read("structured.template.detail")
        list_body, detail_body = list_observation.data, detail_observation.data
        templates = list_body.get("templates") if isinstance(list_body, dict) else None
        if not isinstance(templates, list):
            raise RunStateError("structured-template list response has no bounded catalog array")
        selected = [row for row in templates
                    if isinstance(row, dict) and row.get("id") == STRUCTURED_TEMPLATE_ID]
        if len(selected) != 1:
            raise RunStateError("the reviewed template is missing or duplicated in the catalog")

        definition = detail_body.get("definition")
        yaml_source = detail_body.get("yaml")
        if (detail_body.get("id") != STRUCTURED_TEMPLATE_ID
                or detail_body.get("lang") != STRUCTURED_TEMPLATE_LANG
                or not isinstance(detail_body.get("name"), str)
                or not detail_body.get("name")
                or not isinstance(definition, dict)
                or not isinstance(yaml_source, str)):
            raise RunStateError("structured-template detail violates its exact identity or JSON contract")

        requests = [dict(row) for row in self.client.evidence]
        if [row.get("operation") for row in requests] != [
                "structured.templates.list", "structured.template.detail"]:
            raise RunStateError("structured-template transport evidence is incomplete")
        data = {
            "selected_template_id": STRUCTURED_TEMPLATE_ID,
            "detail_template_id": detail_body["id"],
            "detail_lang": detail_body["lang"],
            "list_item_count": len(templates),
            "detail_yaml_chars": len(yaml_source),
        }
        evidence = {
            "transport": "internal-v1-http",
            "operation": "structured.templates.catalog_read",
            "fixture_effect": "NONE",
            "projection": data,
            "requests": requests,
            **data,
        }
        if any(secret and secret in repr(evidence) for secret in self.client.headers.values()):
            raise RunStateError("structured-template evidence must not expose a credential")
        return AdapterCall(data=data, evidence=evidence)

    def execute(self):
        return execute_structured_templates_case(self)
