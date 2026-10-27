"""Strict Profile for the dual-environment Workspace product-CLI slice."""

from __future__ import annotations

import json

from .profile import ConfigError, identifier, object_keys, origin, parsed_origin, unique_keys


WORKSPACE_CLI_CREATE_GET_PROFILE_CONTRACT = "dual-workspace-cli-profile-v1"
WORKSPACE_CLI_STATUS_PROFILE_CONTRACT = "dual-workspace-cli-status-profile-v1"
WORKSPACE_CLI_LIST_PROFILE_CONTRACT = "dual-workspace-cli-list-profile-v1"
WORKSPACE_CLI_DETAIL_PROFILE_CONTRACT = "dual-workspace-cli-detail-profile-v1"
WORKSPACE_CLI_FUSE_PROFILE_CONTRACT = "dual-workspace-cli-fuse-profile-v1"
WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT = "dual-workspace-cli-member-profile-v1"
SESSION_FILE_CORE_PROFILE_CONTRACT = "dual-session-file-core-profile-v1"

SESSION_CLI_CREATE_GET_PROFILE_CONTRACT = "dual-session-cli-create-list-get-profile-v1"
SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT = "dual-session-cli-lifecycle-profile-v1"
SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT = "dual-session-complete-invoke-profile-v1"
SESSION_CLI_COLLECTION_PROFILE_CONTRACT = "dual-session-cli-collection-profile-v1"

WORKSPACE_GROUP_LIFECYCLE_PROFILE_CONTRACT = (
    "dual-workspace-group-lifecycle-profile-v1")
WORKSPACE_GROUP_GOVERNANCE_PROFILE_CONTRACT = (
    "dual-workspace-group-governance-profile-v1")
WORKSPACE_INCIDENT_CORE_PROFILE_CONTRACT = (
    "dual-workspace-incident-core-profile-v1")
WORKSPACE_ROUNDTRIP_PROFILE_CONTRACT = (
    "dual-workspace-roundtrip-profile-v1")
WORKSPACE_GROUP_CREATE_OPERATION = "workspace_group.create"
WORKSPACE_GROUP_DETAIL_OPERATION = "workspace_group.detail"
WORKSPACE_GROUP_PATCH_OPERATION = "workspace_group.patch"
WORKSPACE_GROUP_LIST_OPERATION = "workspace_group.list"
WORKSPACE_GROUP_MEMBER_ADD_OPERATION = "workspace_group.member.add"
WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION = "workspace_group.member.remove"
WORKSPACE_GROUP_LABEL_OPERATION = "workspace_group.label"
WORKSPACE_GROUP_VISIBILITY_OPERATION = "workspace_group.visibility"
WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION = (
    "workspace_group.participant_mode")
WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION = (
    "workspace_group.incident_delete")
WORKSPACE_ROUNDTRIP_CREATE_OPERATION = "workspace_roundtrip.create"
WORKSPACE_ROUNDTRIP_DETAIL_OPERATION = "workspace_roundtrip.detail"
WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION = (
    "workspace_roundtrip.workspace_update")
WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION = (
    "workspace_roundtrip.workspace_read")
WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION = (
    "workspace_roundtrip.routing_policy")
WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION = (
    "workspace_roundtrip.settings")
WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION = (
    "workspace_roundtrip.messages")
WORKSPACE_ROUNDTRIP_CHAT_OPERATION = "workspace_roundtrip.chat"
WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION = (
    "workspace_roundtrip.callback")
WORKSPACE_ROUNDTRIP_DELETE_OPERATION = (
    "workspace_roundtrip.delete")
PROFILE_EFFECTS = {
    SESSION_CLI_CREATE_GET_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-cli-session-create-list-get",
        "initial_session": "deleted-with-group",
        "explicit_session": "created-listed-read-deleted-with-group",
        "cleanup": "exact-owned-group",
        "participant_mode": "driver-member",
    },
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-cli-session-lifecycle",
        "initial_session": "deleted-with-group",
        "explicit_session": "renamed-muted-chatted-deleted",
        "cleanup": "exact-owned-group",
        "participant_mode": "driver-member",
    },
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-cli-service-invoke",
        "initial_session": "deleted-with-group",
        "explicit_session": "completed-then-creator-deleted",
        "service_session": "invoked-then-creator-deleted",
        "cleanup": "exact-owned-group",
        "participant_mode": "driver-member",
    },
    SESSION_CLI_COLLECTION_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-cli-session-collection",
        "initial_session": "deleted-with-group",
        "explicit_session": "collected-uncollected-deleted-with-group",
        "cleanup": "exact-owned-group",
        "participant_mode": "driver-member",
    },

    WORKSPACE_CLI_CREATE_GET_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-cli-create-get",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_CLI_STATUS_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-cli-status-termination",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_CLI_LIST_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-cli-create-list",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_CLI_DETAIL_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-cli-create-detail",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_CLI_FUSE_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-cli-create-fuse",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_GROUP_LIFECYCLE_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-group-lifecycle",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_GROUP_GOVERNANCE_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-group-governance",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    WORKSPACE_INCIDENT_CORE_PROFILE_CONTRACT: {
        "incident_group": "bounded-owned-bots-incident-room",
        "participant_mode": "self-muted-by-owned-bot",
        "finalization": "exact-owned-group-delete-and-not-found",
    },

    WORKSPACE_ROUNDTRIP_PROFILE_CONTRACT: {
        "creation": "finalized-bounded-owned-bots-incident-room",
        "workspace": "finalized-bounded-owned-bots-deprecated-workspace-404",
        "routing_service": (
            "finalized-bounded-owned-bots-structured-routing"),
        "messaging": "finalized-bounded-owned-bots-group-messaging",
        "finalization": "exact-owned-group-delete-and-not-found",
    },

    WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-cli-create-add-member",
        "initial_session": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
    SESSION_FILE_CORE_PROFILE_CONTRACT: {
        "group": "bounded-owned-bots-retained-session-file",
        "initial_session": "retained",
        "explicit_session": "retained",
        "file": "retained",
        "share_link": "retained",
        "cleanup": "none",
        "participant_mode": "driver-member",
    },
}


def _env_ref(value: object, where: str) -> str:
    if (not isinstance(value, str)
            or not 3 <= len(value) <= 100
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in value)):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value


def _actor(value: object, role: str, human: str) -> dict:
    actor = object_keys(value, {"bot_id", "owner_id", "dedicated", "visibility"},
                        f"actors.{role}")
    identifier(actor["bot_id"], f"actors.{role}.bot_id")
    if actor["owner_id"] != human or actor["dedicated"] is not True:
        raise ConfigError(f"actors.{role}: must be dedicated and owned by the configured Human")
    if actor["visibility"] != "public":
        raise ConfigError(f"actors.{role}.visibility: workspace admission requires public Bots")
    return actor


def _validate_workspace_cli_profile(raw: object, contract_name: str) -> dict:
    """Validate a retained two-Bot Workspace CLI Profile."""
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("workspace CLI profile must be an object")
    expected_keys = {
        "schema_version", "contract", "environment", "entrypoint", "human",
        "actors", "auth", "relationships", "policy", "cli",
    }
    if contract_name == SESSION_FILE_CORE_PROFILE_CONTRACT and "file_transfer" in raw:
        expected_keys.add("file_transfer")
    profile = object_keys(raw, expected_keys, "workspace CLI profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != contract_name):
        raise ConfigError("workspace CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                              {"tier", "id", "expected_bcs_env"},
                              "workspace CLI environment")
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                              {"kind", "origin", "api_face", "manifest_face"},
                              "workspace CLI entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: workspace CLI requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local workspace CLI requires an owned loopback")
    else:
        if entrypoint["kind"] != "gateway" or target.scheme != "https":
            raise ConfigError("entrypoint: preprod workspace CLI requires the admitted HTTPS gateway")

    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    expected_actors = (
        {"driver", "member", "qa"}
        if contract_name in {SESSION_CLI_COLLECTION_PROFILE_CONTRACT,
                               WORKSPACE_INCIDENT_CORE_PROFILE_CONTRACT,
                               WORKSPACE_ROUNDTRIP_PROFILE_CONTRACT}
        else {"driver", "member"})
    actors = object_keys(profile["actors"], expected_actors, "actors")
    actors["driver"] = _actor(actors["driver"], "driver", human["user_id"])
    actors["member"] = _actor(actors["member"], "member", human["user_id"])
    if actors["driver"]["bot_id"] == actors["member"]["bot_id"]:
        raise ConfigError("actors: workspace CLI requires two distinct dedicated Bots")
    if "qa" in actors:
        actors["qa"] = _actor(actors["qa"], "qa", human["user_id"])
        if (actors["qa"]["bot_id"] == actors["driver"]["bot_id"]
                or actors["qa"]["bot_id"] == actors["member"]["bot_id"]):
            raise ConfigError(
                "actors.qa: requires a distinct dedicated Bot")

    if contract_name == SESSION_CLI_COLLECTION_PROFILE_CONTRACT:
        if environment["tier"] == "local":
            auth = object_keys(
                profile["auth"],
                {"kind", "driver_token_env", "member_token_env",
                 "qa_token_env"},
                "auth")
            if auth["kind"] != "owned-local-bot-tokens":
                raise ConfigError(
                    "auth.kind: Session collection requires owned Bot tokens "
                    "for the driver, member and non-participant QA pool Bots "
                    "on Singlebox; legacy collection routes resolve a Bot "
                    "token to itself")
            _env_ref(auth["driver_token_env"], "auth.driver_token_env")
            _env_ref(auth["member_token_env"], "auth.member_token_env")
            _env_ref(auth["qa_token_env"], "auth.qa_token_env")
        else:
            auth = object_keys(profile["auth"], {"kind", "cookie_env"}, "auth")
            if auth["kind"] != "human-cookie":
                raise ConfigError(
                    "auth.kind: preprod Session collection requires the "
                    "owned Human Cookie; the Human principal must own the "
                    "three dedicated pool Bots it collects for")
            _env_ref(auth["cookie_env"], "auth.cookie_env")
    elif contract_name == WORKSPACE_INCIDENT_CORE_PROFILE_CONTRACT:
        auth = object_keys(
            profile["auth"],
            {"kind", "driver_token_env", "qa_token_env", "cookie_env"},
            "auth")
        if auth["kind"] != "incident-bundle":
            raise ConfigError(
                "auth.kind: workspace incident core requires the owned "
                "incident credential bundle; the Human Cookie owns the "
                "dedicated driver and QA pool Bots it finalizes for")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        _env_ref(auth["qa_token_env"], "auth.qa_token_env")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
    elif contract_name == WORKSPACE_ROUNDTRIP_PROFILE_CONTRACT:
        auth = object_keys(
            profile["auth"],
            {"kind", "driver_token_env", "cookie_env"}, "auth")
        if auth["kind"] != "roundtrip-bundle":
            raise ConfigError(
                "auth.kind: workspace round trip requires the owned "
                "round-trip credential bundle; the Human Cookie owns "
                "the dedicated driver pool Bot it proves deprecated "
                "Workspace unavailability for")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
    elif contract_name == SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT:
        auth = object_keys(
            profile["auth"], {"kind", "driver_token_env", "member_token_env"},
            "auth")
        if auth["kind"] != "owned-bot-tokens":
            raise ConfigError(
                "auth.kind: Session completion-invocation requires owned Bot "
                "tokens on both tiers; service routes resolve Bot identity only")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        _env_ref(auth["member_token_env"], "auth.member_token_env")
    elif environment["tier"] == "local":
        auth = object_keys(profile["auth"], {"kind", "driver_token_env"}, "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local workspace CLI requires an owned Bot token")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
    elif contract_name == SESSION_FILE_CORE_PROFILE_CONTRACT:
        auth = object_keys(
            profile["auth"], {"kind", "cookie_env", "driver_token_env"}, "auth")
        if auth["kind"] != "human-cookie-with-owned-driver-bot-token":
            raise ConfigError(
                "auth.kind: preprod Session-file requires the Human Cookie "
                "plus the owned driver Bot token")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
    else:
        auth = object_keys(profile["auth"], {"kind", "cookie_env"}, "auth")
        if auth["kind"] != "human-cookie":
            raise ConfigError("auth.kind: preprod workspace CLI requires a Human Cookie")
        _env_ref(auth["cookie_env"], "auth.cookie_env")

    if contract_name in {SESSION_FILE_CORE_PROFILE_CONTRACT,
                         SESSION_CLI_CREATE_GET_PROFILE_CONTRACT,
                         SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
                         SESSION_CLI_COLLECTION_PROFILE_CONTRACT}:
        expected_keys = (
            {"group", "initial_session", "explicit_session", "file", "share_link",
             "cleanup", "participant_mode"}
            if contract_name == SESSION_FILE_CORE_PROFILE_CONTRACT else
            {"group", "initial_session", "explicit_session", "cleanup",
             "participant_mode"}
        )
        relationships = object_keys(profile["relationships"], expected_keys,
                                   "relationships")
    elif contract_name == SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT:
        relationships = object_keys(
            profile["relationships"],
            {"group", "initial_session", "explicit_session", "service_session",
             "cleanup", "participant_mode"},
            "relationships")
    elif contract_name == WORKSPACE_INCIDENT_CORE_PROFILE_CONTRACT:
        relationships = object_keys(
            profile["relationships"],
            {"incident_group", "participant_mode", "finalization"},
            "relationships")
    elif contract_name == WORKSPACE_ROUNDTRIP_PROFILE_CONTRACT:
        relationships = object_keys(
            profile["relationships"],
            {"creation", "workspace", "routing_service", "messaging",
             "finalization"},
            "relationships")
    else:
        relationships = object_keys(profile["relationships"],
                                   {"group", "initial_session", "cleanup", "participant_mode"},
                                   "relationships")
    if relationships != PROFILE_EFFECTS[contract_name]:
        raise ConfigError("relationships: retained Workspace CLI effect admission differs")
    policy = object_keys(profile["policy"],
                         {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                          "max_response_bytes", "failure_retention_hours"},
                         "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError("policy.exclusive_use_confirmed: retained Group effects require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    expected_cli = ({"transport": "product-cli", "command_family": "session",
                     "api_face": "legacy"}
                    if contract_name in {SESSION_CLI_CREATE_GET_PROFILE_CONTRACT,
                                         SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
                                         SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
                                         SESSION_CLI_COLLECTION_PROFILE_CONTRACT} else
                    {"transport": "product-cli", "command_family": "group",
                     "api_face": "legacy"})
    if cli != expected_cli:
        raise ConfigError("cli: product CLI binding differs from the case contract")
    if "file_transfer" in profile:
        _validate_session_file_transfer(profile["file_transfer"], environment["tier"])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)


def _validate_session_file_transfer(value: object, tier: str) -> None:
    transfer = object_keys(
        value, {"allowed_share_origins", "allowed_storage_origins"}, "file_transfer")
    for key, allow_empty in (("allowed_share_origins", False),
                             ("allowed_storage_origins", True)):
        origins = transfer[key]
        if not isinstance(origins, list) or len(origins) > 16 or (not origins and not allow_empty):
            raise ConfigError(f"file_transfer.{key}: expected at most 16 exact origins")
    normalized = []
    for remote_origin in transfer["allowed_share_origins"] + transfer["allowed_storage_origins"]:
        parsed = parsed_origin(remote_origin, tier)
        if "*" in parsed.netloc or not remote_origin.isascii():
            raise ConfigError("file_transfer: share origins must be exact ASCII hosts")
        normalized.append(origin(remote_origin, tier))
    if len(set(normalized)) != len(normalized):
        raise ConfigError("file_transfer: duplicate share origins")


def validate_workspace_cli_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, WORKSPACE_CLI_CREATE_GET_PROFILE_CONTRACT)


def validate_workspace_cli_status_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, WORKSPACE_CLI_STATUS_PROFILE_CONTRACT)


def validate_workspace_cli_list_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, WORKSPACE_CLI_LIST_PROFILE_CONTRACT)


def validate_workspace_cli_detail_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, WORKSPACE_CLI_DETAIL_PROFILE_CONTRACT)


def validate_workspace_cli_fuse_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, WORKSPACE_CLI_FUSE_PROFILE_CONTRACT)


def validate_workspace_cli_member_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, WORKSPACE_CLI_MEMBER_PROFILE_CONTRACT)


def validate_session_file_core_profile(raw: object) -> dict:
    return _validate_workspace_cli_profile(raw, SESSION_FILE_CORE_PROFILE_CONTRACT)


def validate_workspace_group_lifecycle_bindings(
        value: object, driver_id: str, member_id: str) -> dict:
    expected = {
        WORKSPACE_GROUP_CREATE_OPERATION: {
            "method": "POST",
            "path": "/groups",
        },
        WORKSPACE_GROUP_DETAIL_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
        WORKSPACE_GROUP_PATCH_OPERATION: {
            "method": "PATCH",
            "path": "/groups/{group_id}",
        },
        WORKSPACE_GROUP_LIST_OPERATION: {
            "method": "GET",
            "path": "/groups?limit=50&group_kind=all",
        },
        WORKSPACE_GROUP_MEMBER_ADD_OPERATION: {
            "method": "POST",
            "path": "/groups/{group_id}/members",
        },
        WORKSPACE_GROUP_MEMBER_REMOVE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}/members/{member_actor_id}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("workspace group lifecycle bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != route["method"] or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_driver"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: workspace group lifecycle "
                "route differs from the product contract")
        result[operation] = dict(binding)
    if (identifier(driver_id, "actors.driver.bot_id") != driver_id
            or identifier(member_id, "actors.member.bot_id") != member_id):
        raise ConfigError("workspace group lifecycle actor binding is invalid")
    return result


def validate_workspace_group_lifecycle_profile(raw: object) -> dict:
    """Validate a two-Bot Workspace Group lifecycle Profile."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError(
            "workspace group lifecycle profile requires route bindings")
    bindings = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(
        base, WORKSPACE_GROUP_LIFECYCLE_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "group":
        raise ConfigError(
            "cli.command_family: workspace group lifecycle requires "
            "the group command family")
    profile["route_bindings"] = validate_workspace_group_lifecycle_bindings(
        bindings,
        profile["actors"]["driver"]["bot_id"],
        profile["actors"]["member"]["bot_id"])
    return profile


def validate_workspace_group_governance_bindings(
        value: object, driver_id: str, member_id: str) -> dict:
    expected = {
        WORKSPACE_GROUP_CREATE_OPERATION: {
            "method": "POST",
            "path": "/groups",
        },
        WORKSPACE_GROUP_DETAIL_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
        WORKSPACE_GROUP_LABEL_OPERATION: {
            "method": "PUT",
            "path": "/groups/{group_id}/label",
        },
        WORKSPACE_GROUP_VISIBILITY_OPERATION: {
            "method": "PUT",
            "path": "/groups/{group_id}/visibility",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("workspace group governance bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != route["method"] or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_driver"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: workspace group governance "
                "route differs from the product contract")
        result[operation] = dict(binding)
    if (identifier(driver_id, "actors.driver.bot_id") != driver_id
            or identifier(member_id, "actors.member.bot_id") != member_id):
        raise ConfigError("workspace group governance actor binding is invalid")
    return result


def validate_workspace_group_governance_profile(raw: object) -> dict:
    """Validate a two-Bot Workspace Group governance Profile."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError(
            "workspace group governance profile requires route bindings")
    bindings = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(
        base, WORKSPACE_GROUP_GOVERNANCE_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "group":
        raise ConfigError(
            "cli.command_family: workspace group governance requires "
            "the group command family")
    profile["route_bindings"] = validate_workspace_group_governance_bindings(
        bindings,
        profile["actors"]["driver"]["bot_id"],
        profile["actors"]["member"]["bot_id"])
    return profile


def validate_workspace_incident_core_bindings(
        value: object, driver_id: str, member_id: str, qa_id: str) -> dict:
    expected = {
        WORKSPACE_GROUP_CREATE_OPERATION: {
            "method": "POST",
            "path": "/groups",
        },
        WORKSPACE_GROUP_DETAIL_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
        WORKSPACE_GROUP_PARTICIPANT_MODE_OPERATION: {
            "method": "PUT",
            "path": "/groups/{group_id}/participants/{qa_actor_id}",
        },
        WORKSPACE_GROUP_INCIDENT_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={driver_actor_id}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("workspace incident core bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != route["method"] or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_driver"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: workspace incident core "
                "route differs from the product contract")
        result[operation] = dict(binding)
    if (identifier(driver_id, "actors.driver.bot_id") != driver_id
            or identifier(member_id, "actors.member.bot_id") != member_id
            or identifier(qa_id, "actors.qa.bot_id") != qa_id):
        raise ConfigError("workspace incident core actor binding is invalid")
    return result


def validate_workspace_incident_core_profile(raw: object) -> dict:
    """Validate a three-Bot Workspace incident core Profile."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError(
            "workspace incident core profile requires route bindings")
    bindings = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(
        base, WORKSPACE_INCIDENT_CORE_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "group":
        raise ConfigError(
            "cli.command_family: workspace incident core requires "
            "the group command family")
    profile["route_bindings"] = validate_workspace_incident_core_bindings(
        bindings,
        profile["actors"]["driver"]["bot_id"],
        profile["actors"]["member"]["bot_id"],
        profile["actors"]["qa"]["bot_id"])
    return profile


def _validate_exact_legacy_route_bindings(
        value: object, expected: dict, family: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError(f"{family} bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response",
            "traceparent"}, f"route_bindings.{operation}")
        if (binding["method"] != route["method"]
                or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != "case_owned_driver"
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: {family} route "
                "differs from the product contract")
        result[operation] = dict(binding)
    return result


def validate_workspace_roundtrip_bindings(
        value: object, driver_id: str, member_id: str,
        qa_id: str) -> dict:
    """Validate the ten exact legacy routes of the dual-environment
    Workspace round trip. The shared-workspace and bounded-service
    settings routes do not bind a route-level caller in the reviewed
    OCB HTTP surface; the case contract records that as a documented
    identity gap instead of an exact-actor admission, while the
    routing-policy route binds the authenticated driver Bot."""
    expected = {
        WORKSPACE_ROUNDTRIP_CREATE_OPERATION: {
            "method": "POST", "path": "/groups"},
        WORKSPACE_ROUNDTRIP_DETAIL_OPERATION: {
            "method": "GET", "path": "/groups/{group_id}"},
        WORKSPACE_ROUNDTRIP_WORKSPACE_UPDATE_OPERATION: {
            "method": "PUT", "path": "/groups/{group_id}/workspace"},
        WORKSPACE_ROUNDTRIP_WORKSPACE_READ_OPERATION: {
            "method": "GET", "path": "/groups/{group_id}/workspace"},
        WORKSPACE_ROUNDTRIP_ROUTING_POLICY_OPERATION: {
            "method": "PUT",
            "path": "/groups/{group_id}/routing-policy"},
        WORKSPACE_ROUNDTRIP_SETTINGS_OPERATION: {
            "method": "PATCH", "path": "/groups/{group_id}/settings"},
        WORKSPACE_ROUNDTRIP_MESSAGES_OPERATION: {
            "method": "POST", "path": "/groups/{group_id}/messages"},
        WORKSPACE_ROUNDTRIP_CHAT_OPERATION: {
            "method": "POST", "path": "/groups/{group_id}/chat"},
        WORKSPACE_ROUNDTRIP_CALLBACK_OPERATION: {
            "method": "POST", "path": "/groups/{group_id}/callback"},
        WORKSPACE_ROUNDTRIP_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={driver_actor_id}"},
    }
    result = _validate_exact_legacy_route_bindings(
        value, expected, "workspace round trip")
    if (identifier(driver_id, "actors.driver.bot_id") != driver_id
            or identifier(member_id, "actors.member.bot_id") != member_id
            or identifier(qa_id, "actors.qa.bot_id") != qa_id):
        raise ConfigError(
            "workspace round trip actor binding is invalid")
    return result


def validate_workspace_roundtrip_profile(raw: object) -> dict:
    """Validate a three-Bot Workspace round-trip Profile."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError(
            "workspace round trip profile requires route bindings")
    bindings = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(
        base, WORKSPACE_ROUNDTRIP_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "group":
        raise ConfigError(
            "cli.command_family: workspace round trip requires "
            "the group command family")
    profile["route_bindings"] = validate_workspace_roundtrip_bindings(
        bindings,
        profile["actors"]["driver"]["bot_id"],
        profile["actors"]["member"]["bot_id"],
        profile["actors"]["qa"]["bot_id"])
    return profile
