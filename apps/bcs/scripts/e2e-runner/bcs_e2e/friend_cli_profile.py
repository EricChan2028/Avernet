"""Strict Profile for the dual-environment friendship product-CLI slice."""

from __future__ import annotations

import json

from .profile import ConfigError, identifier, object_keys, parsed_origin, unique_keys


FRIEND_CLI_PROFILE_CONTRACT = "dual-friend-cli-profile-v1"


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
    if actor["visibility"] not in {"public", "protected", "private"}:
        raise ConfigError(f"actors.{role}.visibility: unsupported visibility")
    if role == "member" and actor["visibility"] != "protected":
        raise ConfigError("actors.member.visibility: CLI acceptance requires protected admission")
    return actor


def validate_friend_cli_profile(raw: object) -> dict:
    """Validate a case-local Profile without resolving or persisting credentials."""
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("friendship CLI profile must be an object")
    profile = object_keys(raw, {
        "schema_version", "contract", "environment", "entrypoint", "human",
        "actors", "auth", "relationships", "policy", "cli",
    }, "friendship CLI profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != FRIEND_CLI_PROFILE_CONTRACT):
        raise ConfigError("friendship CLI profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                              {"tier", "id", "expected_bcs_env"},
                              "friendship CLI environment")
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                              {"kind", "origin", "api_face", "manifest_face"},
                              "friendship CLI entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: friendship CLI requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local friendship CLI requires an owned loopback")
    else:
        if entrypoint["kind"] != "gateway" or target.scheme != "https":
            raise ConfigError("entrypoint: preprod friendship CLI requires the admitted HTTPS gateway")

    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(profile["actors"], {"driver", "member"}, "actors")
    actors["driver"] = _actor(actors["driver"], "driver", human["user_id"])
    actors["member"] = _actor(actors["member"], "member", human["user_id"])
    if actors["driver"]["bot_id"] == actors["member"]["bot_id"]:
        raise ConfigError("actors: friendship CLI requires two distinct dedicated Bots")

    if environment["tier"] == "local":
        auth = object_keys(profile["auth"],
                           {"kind", "driver_token_env", "member_token_env"},
                           "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local friendship CLI requires owned Bot tokens")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
        _env_ref(auth["member_token_env"], "auth.member_token_env")
    else:
        auth = object_keys(profile["auth"], {"kind", "cookie_env"}, "auth")
        if auth["kind"] != "human-cookie":
            raise ConfigError("auth.kind: preprod friendship CLI requires a Human Cookie")
        _env_ref(auth["cookie_env"], "auth.cookie_env")

    relationships = object_keys(profile["relationships"],
                                {"friendship", "baseline", "visibility", "retention"},
                                "relationships")
    if relationships != {
        "friendship": "bounded-owned-bot-cli-acceptance",
        "baseline": "no-pending-driver-member",
        "visibility": "member-protected",
        "retention": "request-history-and-friendship",
    }:
        raise ConfigError("relationships: bounded CLI acceptance admission differs")
    policy = object_keys(profile["policy"],
                         {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                          "max_response_bytes", "failure_retention_hours"},
                         "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError("policy.exclusive_use_confirmed: bounded CLI effects require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"},
                      "cli")
    if cli != {"transport": "product-cli", "command_family": "friend",
               "api_face": "legacy"}:
        raise ConfigError("cli: product friendship CLI binding differs")
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)
