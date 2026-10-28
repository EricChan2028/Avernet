"""Strict validation for pilot profiles and bound owned-local artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .route_bindings import validate_route_bindings


# Reviewed route bindings may declare a per-operation response budget above
# the Profile policy default for bounded list operations whose rows carry
# free-form context text (for example the owned Group listing). The policy
# budget stays the fail-closed default; only an explicitly reviewed binding
# override may exceed it.
ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM = 16 * 1024 * 1024


class ConfigError(ValueError):
    """Messages deliberately exclude untrusted configuration values."""


def object_keys(value: object, keys: set[str], where: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ConfigError(f"{where}: missing or unknown fields")
    return value


def text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ConfigError(f"{where}: expected nonempty bounded string")
    if any(ord(c) < 32 for c in value):
        raise ConfigError(f"{where}: control characters forbidden")
    return value


def identifier(value: object, where: str) -> str:
    value = text(value, where)
    if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", value):
        raise ConfigError(f"{where}: invalid identifier")
    return value


def origin(value: object, tier: str) -> str:
    value = text(value, "origin")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ConfigError("origin: invalid URL") from None
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ConfigError("origin: require scheme and host only; no credentials/path/query")
    if parsed.scheme != "https":
        if not (tier == "local" and parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ConfigError("origin: HTTPS required except local loopback")
    return value.rstrip("/")


def parsed_origin(value: object, tier: str):
    """Return the reviewed split form of an already validated origin."""
    return urlsplit(origin(value, tier))


def validate(raw: object) -> dict:
    if isinstance(raw, dict) and type(raw.get('schema_version')) is int and raw['schema_version'] == 2:
        from .trust_context import validate_profile
        return validate_profile(raw)
    optional = set()
    if isinstance(raw, dict) and "browser_origin" in raw:
        optional.add("browser_origin")
    if isinstance(raw, dict) and "identity_binding" in raw:
        optional.add("identity_binding")
    if isinstance(raw, dict) and "file_transfer" in raw:
        optional.add("file_transfer")
    if isinstance(raw, dict) and "route_bindings" in raw:
        optional.add("route_bindings")
    if isinstance(raw, dict) and "relationships" in raw:
        optional.add("relationships")
    if isinstance(raw, dict) and "edge_case_bindings" in raw:
        optional.add("edge_case_bindings")
    p = object_keys(raw, {"schema_version", "environment", "entrypoint", "human",
                          "actors", "auth", "observer", "policy"} | optional, "profile")
    if type(p["schema_version"]) is not int or p["schema_version"] != 1:
        raise ConfigError("schema_version: expected 1")
    env = object_keys(p["environment"], {"tier", "id", "expected_bcs_env", "lifecycle", "deployment", "provider"}, "environment")
    if env["tier"] not in ("local", "preprod"):
        raise ConfigError("environment.tier: only local/preprod admitted in pilot")
    identifier(env["id"], "environment.id")
    identifier(env["expected_bcs_env"], "environment.expected_bcs_env")
    if env["lifecycle"] not in ("local-provision", "remote-attach"):
        raise ConfigError("environment.lifecycle: expected local-provision or remote-attach")
    if env["deployment"] not in ("singlebox", "managed", "docker"):
        raise ConfigError("environment.deployment: expected singlebox, managed or docker")
    if env["provider"] not in ("local", "internal", "aliyun"):
        raise ConfigError("environment.provider: expected local, internal or aliyun")
    if env["lifecycle"] == "local-provision":
        if env["tier"] != "local" or env["deployment"] != "singlebox" or env["provider"] != "local":
            raise ConfigError("environment.local-provision: requires local/singlebox/local")
    else:
        if env["tier"] != "preprod" or env["deployment"] not in {"managed", "docker"} or env["provider"] == "local":
            raise ConfigError("environment.remote-attach: requires preprod with internal/aliyun and managed/docker")
    ep = object_keys(p["entrypoint"], {"kind", "origin", "api_face", "manifest_face"}, "entrypoint")
    if ep["kind"] not in ("gateway", "bcs-direct", "singlebox", "mock"):
        raise ConfigError("entrypoint.kind: expected gateway, bcs-direct, singlebox or mock")
    if env["lifecycle"] == "local-provision" and ep["kind"] not in ("singlebox", "mock"):
        raise ConfigError("environment.lifecycle: local-provision requires singlebox or mock")
    if env["lifecycle"] == "remote-attach" and ep["kind"] not in ("gateway", "bcs-direct"):
        raise ConfigError("environment.lifecycle: remote-attach requires gateway or bcs-direct")
    if ep["kind"] == "mock":
        if env["tier"] != "local" or ep["origin"] != "mock://local":
            raise ConfigError("entrypoint: mock adapter requires local/mock://local")
    if ep["kind"] == "singlebox" and env["tier"] != "local":
        raise ConfigError("entrypoint: singlebox adapter requires local environment")
    if "identity_binding" in p:
        from .identity_binding import validate_identity_binding
        validate_identity_binding(p["identity_binding"], ep)
    if ep["api_face"] != "public-v1":
        raise ConfigError("entrypoint.api_face: initial pilot requires public-v1")
    if ep["manifest_face"] not in ("internal-v1", "legacy"):
        raise ConfigError("entrypoint.manifest_face: expected internal-v1 or legacy")
    if ep["kind"] != "mock":
        origin(ep["origin"], env["tier"])
        if "route_bindings" not in p:
            raise ConfigError("route_bindings: required for non-mock profiles")
        route_bindings = validate_route_bindings(p["route_bindings"], api_face=ep["api_face"])
    elif "route_bindings" in p:
        raise ConfigError("route_bindings: mock profiles must not declare remote routes")
    if "relationships" not in p:
        raise ConfigError("relationships: pilot relationship policy is required")
    relationships = object_keys(p["relationships"], {"friendship"}, "relationships")
    if relationships["friendship"] not in {
            "unchanged", "bounded-owned-bot-decisions",
            "bounded-owned-bot-public-acceptance",
            "bounded-owned-bot-mutations"}:
        raise ConfigError("relationships.friendship: unsupported pilot relationship policy")
    h = object_keys(p["human"], {"user_id"}, "human")
    identifier(h["user_id"], "human.user_id")
    actors = object_keys(p["actors"], {"driver", "member"}, "actors")
    for role in actors:
        a = object_keys(actors[role], {"bot_id", "owner_id", "dedicated"}, f"actors.{role}")
        identifier(a["bot_id"], f"actors.{role}.bot_id")
        if a["owner_id"] != h["user_id"] or a["dedicated"] is not True:
            raise ConfigError(f"actors.{role}: must be dedicated and owned by configured Human")
    if actors["driver"]["bot_id"] == actors["member"]["bot_id"]:
        raise ConfigError("actors: two distinct Bot IDs required")
    if p["auth"] == {"kind": "owned-local"}:
        if (ep["kind"] != "singlebox" or env["tier"] != "local"
                or env["expected_bcs_env"] != "local"):
            raise ConfigError("owned-local auth requires an owned local singlebox")
    else:
        auth = p["auth"]
        if (not isinstance(auth, dict)
                or not set(auth) <= {"header_env", "principal_signing_key_env"}
                or not auth):
            raise ConfigError("auth: use Authorization/Cookie environment references or an operator-provisioned principal signing key reference only")
        headers = auth.get("header_env")
        if headers is None:
            headers = {}
        if (not isinstance(headers, dict)
                or not set(headers) <= {"Authorization", "Cookie"}):
            raise ConfigError("auth.header_env: use Authorization/Cookie environment references only")
        for ref in headers.values():
            if not isinstance(ref, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", ref):
                raise ConfigError("auth.header_env: invalid environment variable reference")
        key_ref = auth.get("principal_signing_key_env")
        if key_ref is not None:
            if (not isinstance(key_ref, str)
                    or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", key_ref)):
                raise ConfigError("auth.principal_signing_key_env: invalid environment variable reference")
        if not headers and key_ref is None:
            raise ConfigError("auth: declare a header_env or principal_signing_key_env reference")
    browser_headers(p)
    ob = object_keys(p["observer"], {"kind", "server", "database", "environment_binding_confirmed"}, "observer")
    allowed_observers = {"alisddbquery"}
    if ep["kind"] in {"mock", "singlebox"} and env["tier"] == "local":
        allowed_observers.add("local-mock")
    if ep["kind"] == "singlebox" and env["tier"] == "local":
        allowed_observers.add("local-sqlite")
    if ob["kind"] == "local-sqlite" and (ob["server"] != "owned-local"
            or ob["database"] != "owned-local" or env["expected_bcs_env"] != "local"):
        raise ConfigError("local-sqlite requires the exact owned-local physical binding")
    if ob["kind"] not in allowed_observers:
        raise ConfigError("observer.kind: unsupported observer for this profile")
    identifier(ob["server"], "observer.server")
    identifier(ob["database"], "observer.database")
    if type(ob["environment_binding_confirmed"]) is not bool:
        raise ConfigError("observer.environment_binding_confirmed: expected boolean")
    policy_keys = {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                    "max_response_bytes", "failure_retention_hours"}
    if "remote_effect_review" in p["policy"]:
        policy_keys.add("remote_effect_review")
    policy = object_keys(p["policy"], policy_keys, "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if type(policy["exclusive_use_confirmed"]) is not bool:
        raise ConfigError("policy.exclusive_use_confirmed: expected boolean")
    for key, maximum in (("timeout_seconds", 60), ("max_response_bytes", 2097152),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside supported bounds")
    if "route_bindings" in p:
        p["route_bindings"] = route_bindings
    if "edge_case_bindings" in p:
        from .route_bindings import validate_edge_case_bindings
        p["edge_case_bindings"] = validate_edge_case_bindings(p["edge_case_bindings"], p)
        decision_operations = {
            "friend_connection_decision.reject.create",
            "friend_connection_decision.accept.create",
            "friend_connection_decision.member_requests.read",
            "friend_connection_decision.reject",
            "friend_connection_decision.driver_requests.read",
            "friend_connection_decision.accept",
            "friend_connection_decision.driver_connections.read",
        }
        public_accept_operations = {
            "friend_connection_public_accept.create",
            "friend_connection_public_accept.driver_connections.read",
        }
        # The dual-environment Edge friend-mutation family splits its reviewed
        # operations by API face: the Gateway public-v1 face carries the pair
        # mutations, decisions and vantage reads; the station-direct legacy
        # face carries the register-onboard, discovery, admission and revoked
        # legacy routes. Each admitted profile declares exactly its own face's
        # bindings; the Human Cookie stays the-only credential on the Gateway
        # face, while the station legacy revoke route is the one reviewed
        # double-identity exception (Cookie owner plus the owned registered
        # Bot Bearer that the legacy caller contract derives from).
        mutation_gateway_operations = {
            "friend_connection_mutation.create",
            "friend_connection_mutation.accept",
            "friend_connection_mutation.reject",
            "friend_connection_mutation.cancel",
            "friend_connection_mutation.requests_sent.read",
            "friend_connection_mutation.requests_received.read",
            "friend_connection_mutation.requests_all.read",
            "friend_connection_mutation.connections.read",
            "friend_connection_mutation.bot_detail.read",
            "friend_connection_mutation.visibility.write",
            "friend_connection_mutation.revoke",
            "friend_connection_mutation.discover_denied.read",
        }
        mutation_station_operations = {
            "friend_connection_mutation.register_token.read",
            "friend_connection_mutation.register_bot.create",
            "friend_connection_mutation.legacy_revoke",
            "friend_connection_mutation.station_admission.read",
            "friend_connection_mutation.station_discover_collaborate.read",
            "friend_connection_mutation.station_discover_role_no_org.read",
            "friend_connection_mutation.station_discover_org_no_requester.read",
        }
        mutation_operations = mutation_gateway_operations | mutation_station_operations
        admitted_operations = {
            "bounded-owned-bot-decisions": decision_operations,
            "bounded-owned-bot-public-acceptance": public_accept_operations,
            "bounded-owned-bot-mutations": mutation_operations,
        }
        declared = set(p["edge_case_bindings"]) & set().union(*admitted_operations.values())
        mode = relationships["friendship"]
        if mode == "unchanged":
            if declared:
                raise ConfigError("relationships.friendship: bounded friendship effects are not admitted")
        elif mode == "bounded-owned-bot-mutations":
            if not declared <= mutation_operations:
                raise ConfigError(
                    "relationships.friendship: edge friend-mutation bindings must stay within the reviewed mutation set")
            face_expected = (
                mutation_station_operations if ep["kind"] == "bcs-direct"
                else mutation_gateway_operations)
            if declared != face_expected:
                raise ConfigError(
                    "relationships.friendship: bounded-owned-bot-mutations requires exactly this face's "
                    "reviewed mutation bindings")
            if p["policy"]["exclusive_use_confirmed"] is not True:
                raise ConfigError("relationships.friendship: bounded friendship effects require exclusive fixture use")
            if p["auth"] == {"kind": "owned-local"}:
                raise ConfigError("relationships.friendship: cross-environment friendship effects require a Cookie-bound profile")
            headers = p["auth"].get("header_env", {})
            if ep["kind"] == "bcs-direct":
                if set(headers) != {"Cookie", "Authorization"}:
                    raise ConfigError(
                        "relationships.friendship: the station legacy mutation face requires the Human "
                        "Cookie plus the owned registered-Bot Authorization reference")
            elif set(headers) != {"Cookie"}:
                raise ConfigError("relationships.friendship: bounded friendship effects must send only the Human Cookie")
        elif declared != admitted_operations[mode]:
            raise ConfigError("relationships.friendship: exactly this mode's bounded bindings are required")
        else:
            if p["policy"]["exclusive_use_confirmed"] is not True:
                raise ConfigError("relationships.friendship: bounded friendship effects require exclusive fixture use")
            if p["auth"] == {"kind": "owned-local"}:
                raise ConfigError("relationships.friendship: cross-environment friendship effects require a Cookie-bound profile")
            headers = p["auth"].get("header_env", {})
            if set(headers) != {"Cookie"}:
                raise ConfigError("relationships.friendship: bounded friendship effects must send only the Human Cookie")
    if "remote_effect_review" in p["policy"]:
        from .remote_effects import validate_remote_effect_review
        validate_remote_effect_review(p["policy"]["remote_effect_review"], p)
    if "file_transfer" in p:
        transfer = object_keys(p["file_transfer"], {"allowed_storage_origins"}, "file_transfer")
        origins = transfer["allowed_storage_origins"]
        if not isinstance(origins, list) or len(origins) > 16:
            raise ConfigError("file_transfer.allowed_storage_origins: expected at most 16 exact origins")
        normalized = []
        for value in origins:
            validated = origin(value, env["tier"])
            parsed = urlsplit(validated)
            if "*" in parsed.netloc or not validated.isascii():
                raise ConfigError("file_transfer: storage origins must be exact ASCII hosts")
            normalized.append((parsed.scheme, parsed.hostname, parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)))
        if len(set(normalized)) != len(normalized):
            raise ConfigError("file_transfer: duplicate storage origins")
    # Return a detached value; validation never resolves secrets or contacts a server.
    return json.loads(json.dumps(p))


def load(path: Path) -> dict:
    if path.is_symlink():
        raise ConfigError("profile: symlink files are not accepted")
    if not path.is_file():
        raise ConfigError("profile: file not found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except UnicodeError:
        raise ConfigError("profile: file must be UTF-8 JSON") from None
    except json.JSONDecodeError:
        raise ConfigError("profile: invalid JSON") from None
    except OSError:
        raise ConfigError("profile: file cannot be read") from None
    return validate(raw)


def require_resolved(profile: dict) -> None:
    """Reject the checked-in example's placeholders before network or writes."""
    if profile.get('schema_version') == 2:
        raise ConfigError('trusted-team Profile is a bound local artifact, not pilot execution authority')
    def visit(value: object) -> bool:
        if isinstance(value, str):
            return value.startswith("REPLACE_WITH_")
        if isinstance(value, dict):
            return any(visit(item) for item in value.values())
        if isinstance(value, list):
            return any(visit(item) for item in value)
        return False
    if visit(profile):
        raise ConfigError("profile: replace all REPLACE_WITH_* placeholders before execution")


def browser_headers(profile: dict) -> dict[str, str]:
    """Return only the explicit, validated browser-origin context for Cookie gateways."""
    if 'browser_origin' not in profile:
        return {}
    value = profile['browser_origin']
    auth = profile.get('auth', {})
    headers = auth.get('header_env', {})
    cookie_gateway = 'Cookie' in headers
    bot_gateway = 'Authorization' in headers
    # The finite Edge admission slices must prove native unauthenticated
    # behavior.  Their browser-origin context is still explicit, but it must
    # never create a credential Authorization/Cookie header into the request.
    unauthenticated_gateway = (
        auth == {'principal_signing_key_env': 'BCS_E2E_UNAUTHENTICATED'}
    )
    if (profile['entrypoint']['kind'] not in {'gateway', 'bcs-direct'}
            or not (cookie_gateway or bot_gateway or unauthenticated_gateway)):
        raise ConfigError(
            'browser_origin requires a Cookie-, Bot-token-authenticated, or explicit unauthenticated gateway binding')
    if (not isinstance(value, str) or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise ConfigError('browser_origin must be an ASCII origin without whitespace or controls')
    base = origin(value, profile['environment']['tier'])
    return {'Origin': base, 'Referer': base + '/'}


def unique_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("profile: duplicate JSON key")
        result[key] = value
    return result


def fingerprint(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
