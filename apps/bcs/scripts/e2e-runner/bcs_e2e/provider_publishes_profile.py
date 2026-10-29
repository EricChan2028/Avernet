"""Strict profile contract for the dual Provider publish/retire case."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .profile import (
    ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM,
    ConfigError,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    unique_keys,
)
from .provider_publishes_contract import STEP_ROWS

PROFILE_CONTRACT = "dual-provider-publishes-profile-v1"


def _env_ref(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", value)
    ):
        raise ConfigError(f"{where}: expected an uppercase environment reference")
    return value


def provider_publishes_credential_kind(profile: dict) -> str:
    if profile["environment"]["tier"] == "local":
        return "local_mock_human_with_minted_provider_credentials"
    return "preprod_human_cookie_with_minted_provider_credentials"


def _validate_route_bindings(value: object) -> dict:
    expected = {row["operation"]: row for row in STEP_ROWS}
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("provider route bindings are incomplete")
    result = {}
    for operation, row in expected.items():
        raw = value[operation]
        budget = None
        if isinstance(raw, dict) and "max_response_bytes" in raw:
            budget = raw["max_response_bytes"]
            if (
                type(budget) is not int
                or not 1 <= budget <= ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM
            ):
                raise ConfigError(
                    f"route_bindings.{operation}: response budget is outside the reviewed bound"
                )
            raw = {key: item for key, item in raw.items() if key != "max_response_bytes"}
        binding = object_keys(
            raw,
            {"method", "path", "api_face", "auth", "response", "traceparent"},
            f"route_bindings.{operation}",
        )
        if (
            binding["method"] != row["method"]
            or binding["path"] != row["path"]
            or binding["api_face"] != "legacy"
            or binding["auth"] != row["caller"]
            or binding["response"] != "legacy-json"
            or binding["traceparent"] != "disabled"
        ):
            raise ConfigError(
                f"route_bindings.{operation}: Provider route differs from the reviewed contract"
            )
        checked = dict(binding)
        if budget is not None:
            checked["max_response_bytes"] = budget
        result[operation] = checked
    return result


def validate_provider_publishes_profile(raw: object) -> dict:
    """Validate the dual Provider profile without resolving credentials."""
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("provider publishes profile must be an object")
    expected_keys = {
        "schema_version", "contract", "environment", "entrypoint", "human",
        "auth", "policy", "fixture", "cli", "route_bindings",
    }
    if isinstance(raw, dict):
        if "browser_origin" in raw:
            expected_keys.add("browser_origin")
        if "credential_files" in raw:
            expected_keys.add("credential_files")
    profile = object_keys(raw, expected_keys, "provider publishes profile")
    if (
        type(profile["schema_version"]) is not int
        or profile["schema_version"] != 1
        or profile["contract"] != PROFILE_CONTRACT
    ):
        raise ConfigError("provider publishes profile: unsupported version or contract")
    environment = object_keys(
        profile["environment"], {"tier", "id", "expected_bcs_env"},
        "provider publishes environment",
    )
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != (
        "local" if environment["tier"] == "local" else "pre"
    ):
        raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(
        profile["entrypoint"], {"kind", "origin", "api_face", "manifest_face"},
        "provider publishes entrypoint",
    )
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: Provider story requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (
            entrypoint["kind"] != "singlebox"
            or target.scheme != "http"
            or target.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not target.port
        ):
            raise ConfigError("entrypoint: local Provider story requires an owned loopback")
    elif entrypoint["kind"] != "gateway" or target.scheme != "https":
        raise ConfigError("entrypoint: preprod Provider story requires the admitted HTTPS gateway")

    if environment["tier"] == "local":
        human = object_keys(
            profile["human"], {"user_id", "mock_nick_name"}, "human"
        )
        identifier(human["user_id"], "human.user_id")
        identifier(human["mock_nick_name"], "human.mock_nick_name")
        auth = object_keys(profile["auth"], {"kind"}, "auth")
        if auth["kind"] != "local-mock-human":
            raise ConfigError("auth.kind: local Provider story requires the mock-human ingress")
    else:
        human = object_keys(profile["human"], {"user_id"}, "human")
        identifier(human["user_id"], "human.user_id")
        auth = object_keys(profile["auth"], {"kind", "cookie_env"}, "auth")
        if auth["kind"] != "preprod-human-cookie":
            raise ConfigError("auth.kind: preprod Provider story requires the Human Cookie")
        _env_ref(auth["cookie_env"], "auth.cookie_env")
        if "browser_origin" not in profile:
            raise ConfigError("browser_origin: preprod Provider story requires browser context")
        browser_origin = profile["browser_origin"]
        if (
            not isinstance(browser_origin, str)
            or not browser_origin.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in browser_origin)
        ):
            raise ConfigError("browser_origin: ASCII origin without whitespace or controls")
        origin(browser_origin, "preprod")
        if "credential_files" in profile:
            files = profile["credential_files"]
            if not isinstance(files, dict) or not files:
                raise ConfigError("credential_files: expected a nonempty environment-to-path map")
            for ref, path in files.items():
                _env_ref(ref, "credential_files")
                if (
                    not isinstance(path, str)
                    or not path.startswith("/")
                    or not path.isascii()
                    or any(ord(char) < 32 or ord(char) == 127 for char in path)
                ):
                    raise ConfigError("credential_files: expected absolute ASCII paths")

    fixture = object_keys(
        profile["fixture"], {"webhook_url", "takeover_bot_id"}, "fixture"
    )
    webhook = fixture["webhook_url"]
    if not isinstance(webhook, str) or not webhook.isascii() or not webhook.startswith("https://"):
        raise ConfigError("fixture.webhook_url: expected an HTTPS URL")
    parsed_webhook = urlsplit(webhook)
    if (
        not parsed_webhook.hostname
        or parsed_webhook.username is not None
        or parsed_webhook.password is not None
        or parsed_webhook.query
        or parsed_webhook.fragment
    ):
        raise ConfigError("fixture.webhook_url: invalid HTTPS fixture URL")
    identifier(fixture["takeover_bot_id"], "fixture.takeover_bot_id")
    policy = object_keys(
        profile["policy"],
        {
            "fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
            "max_response_bytes", "failure_retention_hours",
        },
        "policy",
    )
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (
        type(policy["exclusive_use_confirmed"]) is not bool
        or policy["exclusive_use_confirmed"] is not True
    ):
        raise ConfigError(
            "policy.exclusive_use_confirmed: retained Provider effects require exclusive use"
        )
    for key, maximum in (
        ("timeout_seconds", 30),
        ("max_response_bytes", 65536),
        ("failure_retention_hours", 168),
    ):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the Provider contract")
    cli = object_keys(
        profile["cli"], {"transport", "command_family", "api_face"}, "cli"
    )
    if cli != {
        "transport": "product-http",
        "command_family": "provider-publishes",
        "api_face": "legacy",
    }:
        raise ConfigError("cli: Provider story binds the reviewed legacy product-HTTP surface")
    profile["route_bindings"] = _validate_route_bindings(profile["route_bindings"])
    return json.loads(
        json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys
    )
