"""Session-CLI dual-environment Profile contracts and validators.

Split from ``workspace_cli_profile`` by command family so each
Profile module stays within the reviewed file-size limit; the
registry (contract names, retained effects and the shared base
validator) remains the single source of truth in
``workspace_cli_profile``.
"""

from __future__ import annotations

from .profile import ConfigError, identifier, object_keys
from .workspace_cli_profile import (
    SESSION_CLI_COLLECTION_PROFILE_CONTRACT,
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_CREATE_GET_PROFILE_CONTRACT,
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
    _validate_workspace_cli_profile,
)

SESSION_CLI_GROUP_DELETE_OPERATION = "session_cli.group.delete"
SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION = "session_cli.group.verify_deleted"
SESSION_CLI_BOT_GROUPS_OPERATION = "session_cli.bot_groups"
SESSION_CLI_GROUP_INVITE_CREATE_OPERATION = "session_cli.group.invite.create"
SESSION_CLI_GROUP_INVITE_JOIN_OPERATION = "session_cli.group.invite.join"
SESSION_CLI_SESSION_INVITE_CREATE_OPERATION = "session_cli.session.invite.create"
SESSION_CLI_SESSION_INVITE_JOIN_OPERATION = "session_cli.session.invite.join"
SESSION_CLI_SESSION_PATCH_TITLE_OPERATION = "session_cli.session.patch_title"
SESSION_CLI_SESSION_MEMBER_ADD_OPERATION = "session_cli.session.member.add"
SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION = "session_cli.session.member.patch_mode"
SESSION_CLI_SESSION_CHAT_OPERATION = "session_cli.session.chat"
SESSION_CLI_SESSION_MESSAGES_OPERATION = "session_cli.session.messages"
SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION = "session_cli.session.member.remove"
SESSION_CLI_SESSION_DELETE_OPERATION = "session_cli.session.delete"
SESSION_CLI_SESSION_GROUP_LISTING_OPERATION = "session_cli.session.group_listing"
SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION = "session_cli.message.deliveries.read"
SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION = "session_cli.message.deliveries.query"
SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION = "session_cli.message.delivery.cancel_one"
SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION = "session_cli.message.delivery.cancel_all"
SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION = "session_cli.message.delivery.resolve_one"
SESSION_CLI_SESSION_COMPLETE_OPERATION = "session_cli.session.complete"
SESSION_CLI_SESSION_READ_OPERATION = "session_cli.session.read"
SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION = (
    "session_cli.session.service_complete_guardrail")
SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION = "session_cli.session.creator_delete"
SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION = "session_cli.session.bot_creator_delete"
SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION = "session_cli.group.settings_patch"
SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION = "session_cli.service.invocation_create"
SESSION_CLI_SERVICE_SESSION_READ_OPERATION = "session_cli.service.session.read"
SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION = "session_cli.service.session.read_member"
SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION = (
    "session_cli.session.collected_list_driver")
SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION = (
    "session_cli.session.collected_list_member")
SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION = (
    "session_cli.session.collected_list_missing_participant")
SESSION_CLI_SESSION_COLLECT_OPERATION = "session_cli.session.collect"
SESSION_CLI_SESSION_UNCOLLECT_OPERATION = "session_cli.session.uncollect"

def validate_session_cli_create_get_cleanup_bindings(value: object, driver_id: str) -> dict:
    expected = {
        SESSION_CLI_GROUP_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
        SESSION_CLI_BOT_GROUPS_OPERATION: {
            "method": "GET",
            "path": "/bots/{driver_actor_id}/groups?limit=50&group_kind=all",
        },
        SESSION_CLI_GROUP_INVITE_CREATE_OPERATION: {
            "method": "POST",
            "path": "/groups/{group_id}/invite-link",
        },
        SESSION_CLI_GROUP_INVITE_JOIN_OPERATION: {
            "method": "POST",
            "path": "/groups/join/{invite_token}",
        },
        SESSION_CLI_SESSION_INVITE_CREATE_OPERATION: {
            "method": "POST",
            "path": "/sessions/{session_id}/invite-link",
        },
        SESSION_CLI_SESSION_INVITE_JOIN_OPERATION: {
            "method": "POST",
            "path": "/sessions/join/{invite_token}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("session CLI cleanup bindings are incomplete")
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
            raise ConfigError(f"route_bindings.{operation}: cleanup route differs from the product contract")
        result[operation] = dict(binding)
    if identifier(driver_id, "actors.driver.bot_id") != driver_id:
        raise ConfigError("session CLI cleanup driver is invalid")
    return result


def validate_session_cli_lifecycle_bindings(value: object, driver_id: str) -> dict:
    expected = {
        SESSION_CLI_SESSION_PATCH_TITLE_OPERATION: {
            "method": "PATCH",
            "path": "/sessions/{session_id}",
        },
        SESSION_CLI_SESSION_MEMBER_ADD_OPERATION: {
            "method": "POST",
            "path": "/sessions/{session_id}/members",
        },
        SESSION_CLI_SESSION_MEMBER_PATCH_MODE_OPERATION: {
            "method": "PATCH",
            "path": "/sessions/{session_id}/members/{member_actor_id}",
        },
        SESSION_CLI_SESSION_CHAT_OPERATION: {
            "method": "POST",
            "path": "/sessions/{session_id}/chat",
        },
        SESSION_CLI_SESSION_MESSAGES_OPERATION: {
            "method": "GET",
            "path": "/sessions/{session_id}/messages",
        },
        SESSION_CLI_SESSION_MEMBER_REMOVE_OPERATION: {
            "method": "DELETE",
            "path": "/sessions/{session_id}/members/{member_actor_id}",
        },
        SESSION_CLI_SESSION_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_SESSION_GROUP_LISTING_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}/sessions",
        },
        SESSION_CLI_GROUP_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("session CLI lifecycle bindings are incomplete")
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
            raise ConfigError(f"route_bindings.{operation}: lifecycle route differs from the product contract")
        result[operation] = dict(binding)
    if identifier(driver_id, "actors.driver.bot_id") != driver_id:
        raise ConfigError("session CLI lifecycle driver is invalid")
    return result


def validate_session_cli_lifecycle_profile(raw: object) -> dict:
    """Validate a two-Bot Session CLI Profile with the lifecycle route set."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError("session CLI lifecycle profile requires route bindings")
    cleanup = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(base, SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "session":
        raise ConfigError("cli.command_family: Session CLI lifecycle requires the session command family")
    profile["route_bindings"] = validate_session_cli_lifecycle_bindings(
        cleanup, profile["actors"]["driver"]["bot_id"])
    return profile


def validate_session_cli_create_get_profile(raw: object) -> dict:
    """Validate a retained two-Bot Session CLI Profile with exact cleanup routes."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError("session CLI profile requires cleanup route bindings")
    cleanup = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(base, SESSION_CLI_CREATE_GET_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "session":
        raise ConfigError("cli.command_family: Session CLI requires the session command family")
    profile["route_bindings"] = validate_session_cli_create_get_cleanup_bindings(
        cleanup, profile["actors"]["driver"]["bot_id"])
    return profile


def validate_session_cli_complete_invoke_bindings(value: object,
                                                   driver_id: str) -> dict:
    expected = {
        SESSION_CLI_MESSAGE_DELIVERIES_READ_OPERATION: {
            "method": "GET",
            "path": ("/openapi/v1/collaboration/messages/not-a-message"
                     "/deliveries?session_id={session_id}"),
        },
        SESSION_CLI_MESSAGE_DELIVERIES_QUERY_OPERATION: {
            "method": "POST",
            "path": ("/openapi/v1/collaboration/sessions/{session_id}"
                     "/message-deliveries/query"),
        },
        SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ONE_OPERATION: {
            "method": "POST",
            "path": ("/openapi/v1/collaboration/messages/not-a-message"
                     "/deliveries/not-a-delivery/cancel"),
        },
        SESSION_CLI_MESSAGE_DELIVERY_CANCEL_ALL_OPERATION: {
            "method": "POST",
            "path": ("/openapi/v1/collaboration/messages/not-a-message"
                     "/deliveries/cancel"),
        },
        SESSION_CLI_MESSAGE_DELIVERY_RESOLVE_ONE_OPERATION: {
            "method": "POST",
            "path": ("/openapi/v1/collaboration/messages/not-a-message"
                     "/deliveries/not-a-delivery/resolve"),
        },
        SESSION_CLI_SESSION_COMPLETE_OPERATION: {
            "method": "POST",
            "path": "/sessions/{session_id}/complete",
        },
        SESSION_CLI_SESSION_READ_OPERATION: {
            "method": "GET",
            "path": "/sessions/{session_id}",
        },
        SESSION_CLI_SESSION_SERVICE_COMPLETE_GUARDRAIL_OPERATION: {
            "method": "POST",
            "path": "/sessions/{session_id}/complete",
        },
        SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_SESSION_BOT_CREATOR_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/sessions/{session_id}?bot_id=bot:{driver_actor_id}",
        },
        SESSION_CLI_GROUP_SETTINGS_PATCH_OPERATION: {
            "method": "PATCH",
            "path": "/groups/{group_id}/settings",
        },
        SESSION_CLI_SERVICE_INVOCATION_CREATE_OPERATION: {
            "method": "POST",
            "path": "/services/{group_id}/sessions",
        },
        SESSION_CLI_SERVICE_SESSION_READ_OPERATION: {
            "method": "GET",
            "path": "/services/{group_id}/sessions/{session_id}",
        },
        SESSION_CLI_SERVICE_SESSION_READ_MEMBER_OPERATION: {
            "method": "GET",
            "path": "/services/{group_id}/sessions/{session_id}",
            "auth": "case_owned_member",
        },
        SESSION_CLI_GROUP_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("session CLI completion-invocation bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        expected_auth = route.get("auth", "case_owned_driver")
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != route["method"] or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != expected_auth
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: completion-invocation route "
                "differs from the product contract")
        result[operation] = dict(binding)
    if identifier(driver_id, "actors.driver.bot_id") != driver_id:
        raise ConfigError("session CLI completion-invocation driver is invalid")
    return result


def validate_session_cli_complete_invoke_profile(raw: object) -> dict:
    """Validate a two-Bot Session completion-invocation Profile."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError(
            "session CLI completion-invocation profile requires route bindings")
    cleanup = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(base, SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "session":
        raise ConfigError(
            "cli.command_family: completion-invocation requires the session "
            "command family")
    profile["route_bindings"] = validate_session_cli_complete_invoke_bindings(
        cleanup, profile["actors"]["driver"]["bot_id"])
    return profile


def validate_session_cli_collection_bindings(value: object,
                                             driver_id: str,
                                             member_id: str,
                                             qa_id: str) -> dict:
    expected = {
        SESSION_CLI_SESSION_COLLECTED_LIST_DRIVER_OPERATION: {
            "method": "GET",
            "path": ("/groups/{group_id}/sessions"
                     "?participant={driver_actor_id}&collected=true"),
        },
        SESSION_CLI_SESSION_COLLECTED_LIST_MEMBER_OPERATION: {
            "method": "GET",
            "path": ("/groups/{group_id}/sessions"
                     "?participant={member_actor_id}&collected=true"),
            "auth": "case_owned_member",
        },
        SESSION_CLI_SESSION_COLLECTED_LIST_MISSING_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}/sessions?collected=true",
        },
        SESSION_CLI_SESSION_COLLECT_OPERATION: {
            "method": "POST",
            "path": "/sessions/{session_id}/collect",
        },
        SESSION_CLI_SESSION_UNCOLLECT_OPERATION: {
            "method": "DELETE",
            "path": ("/sessions/{session_id}/collect"
                     "?participant={driver_actor_id}"),
        },
        SESSION_CLI_SESSION_CREATOR_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/sessions/{session_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_GROUP_DELETE_OPERATION: {
            "method": "DELETE",
            "path": "/groups/{group_id}?bot_id={driver_actor_id}",
        },
        SESSION_CLI_GROUP_VERIFY_DELETED_OPERATION: {
            "method": "GET",
            "path": "/groups/{group_id}",
        },
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("session CLI collection bindings are incomplete")
    result = {}
    for operation, route in expected.items():
        expected_auth = route.get("auth", "case_owned_driver")
        binding = object_keys(value[operation], {
            "method", "path", "api_face", "auth", "response", "traceparent",
        }, f"route_bindings.{operation}")
        if (binding["method"] != route["method"] or binding["path"] != route["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != expected_auth
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(
                f"route_bindings.{operation}: collection route "
                "differs from the product contract")
        result[operation] = dict(binding)
    for role, actor_id in (("driver", driver_id), ("member", member_id),
                            ("qa", qa_id)):
        if identifier(actor_id, f"actors.{role}.bot_id") != actor_id:
            raise ConfigError(f"session CLI collection {role} is invalid")
    return result


def validate_session_cli_collection_profile(raw: object) -> dict:
    """Validate a three-Bot Session collection Profile."""
    if not isinstance(raw, dict) or type(raw.get("route_bindings")) is not dict:
        raise ConfigError(
            "session CLI collection profile requires route bindings")
    bindings = raw["route_bindings"]
    base = {key: value for key, value in raw.items() if key != "route_bindings"}
    profile = _validate_workspace_cli_profile(
        base, SESSION_CLI_COLLECTION_PROFILE_CONTRACT)
    if profile["cli"]["command_family"] != "session":
        raise ConfigError(
            "cli.command_family: collection requires the session "
            "command family")
    profile["route_bindings"] = validate_session_cli_collection_bindings(
        bindings,
        profile["actors"]["driver"]["bot_id"],
        profile["actors"]["member"]["bot_id"],
        profile["actors"]["qa"]["bot_id"])
    return profile

