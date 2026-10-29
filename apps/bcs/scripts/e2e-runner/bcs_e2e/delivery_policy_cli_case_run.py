"""Command entry for the reviewed dual Delivery Policy product-HTTP case.

The reviewed delivery-policy migration contract owns ten ordered HTTP
obligations against the legacy administration face of the effective
message-delivery policy.  The owned Singlebox family replayed them with
direct loopback HTTP against one owned runtime; this dual module re-binds
the same frozen obligations to the product HTTP surface on both admitted
environments: the Singlebox simulator envelope and the preprod gateway
legacy face.  The story has no product-CLI leaves, so every step exits
through the reviewed WriteClient surface and is labelled ``PRODUCT_HTTP``;
the frozen product CLI artifact stays pinned in the receipts for audit.
The dual module adds the dual-environment receipts: the DUAL case id,
per-step transport and caller labelling, the tier-aware credential label,
the dual adaptability row, and the ``dual-delivery-policy-run`` command
body mirrored after the dual Session Service CLI command.

    On the preprod gateway tier the driver denial probes present the profile-declared
    credential trio - the owned human session Cookie alongside the dedicated driver
    Bot bearer token - mirroring the workstation browser posture the ACE edge
    admits; the BCS application layer still authorizes the presented Bot identity,
    so both tiers assert the application-layer 403 denial.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .inventory import executor_digest
from .local_cli import LocalCliError, require_cli_binary
from .local_delivery_policy import (
    BOT_ACTOR_INDEXES,
    CONFLICT_ERROR,
    POLICY_PATH,
    PUT_REQUIREMENTS,
)
from .local_delivery_policy_contract import (
    KINDS,
    PARENT,
    REQUIREMENTS,
    delivery_policy_source_binding,
)
from .profile import (
    ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM,
    ConfigError,
    fingerprint,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    require_resolved,
    unique_keys,
)
from .run_state import RunStateError, write
from .workspace_cli_commands import WorkspaceCliRunnerError
from .write_transport import WriteClient, WriteTransportError

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-delivery-policy-case-run-v1"
DELIVERY_POLICY_PARENT = PARENT
DELIVERY_POLICY_PROFILE_CONTRACT = "dual-delivery-policy-profile-v1"
CASE_KEYS = ("delivery-policy",)

DUAL_DELIVERY_POLICY_CASE_ID = "DUAL-DELIVERY-POLICY-01"
DUAL_DELIVERY_POLICY_CASE_METHOD = "execute_delivery_policy_case"

# The local family replays its finite CAS Story with no created Bot, no
# deleted Group and the original policy restored exactly; the dual family
# pins the same constant as its per-step fixture-effect label.
DELIVERY_POLICY_FIXTURE_EFFECT = "NO_BOT_CREATED_NO_GROUP_DELETED_POLICY_RESTORED"

# One product-HTTP operation per reviewed migration-contract requirement,
# in the frozen story order.
DELIVERY_POLICY_OPERATIONS = tuple(
    "delivery_policy." + requirement.split(".", 1)[1].replace("-", "_")
    for requirement in REQUIREMENTS
)
DELIVERY_POLICY_HTTP_OPERATIONS = frozenset(DELIVERY_POLICY_OPERATIONS)

# Per-obligation adapter seam and evidence case class, indexed by the
# frozen requirement order shared with the local family.
STEP_METHOD_BY_REQUIREMENT = {
    "DELIVERY.human-read": "read_policy_as_human",
    "DELIVERY.bot-read-denied": "read_policy_as_bot",
    "DELIVERY.bot-write-denied": "write_policy_as_bot",
    "DELIVERY.denied-preserves": "read_policy_after_denial",
    "DELIVERY.cas-update": "replace_policy_with_matching_version",
    "DELIVERY.readback": "read_policy_after_update",
    "DELIVERY.stale-rejected": "write_policy_with_stale_version",
    "DELIVERY.conflict-preserves": "read_policy_after_conflict",
    "DELIVERY.restore": "restore_original_policy",
    "DELIVERY.restored": "read_restored_policy",
}
CASE_CLASS_BY_REQUIREMENT = {
    "DELIVERY.human-read": "human-read",
    "DELIVERY.bot-read-denied": "bot-read-denial",
    "DELIVERY.bot-write-denied": "bot-write-denial",
    "DELIVERY.denied-preserves": "denied-preserves-read",
    "DELIVERY.cas-update": "human-cas-update",
    "DELIVERY.readback": "human-readback",
    "DELIVERY.stale-rejected": "stale-write-conflict",
    "DELIVERY.conflict-preserves": "conflict-preserves-read",
    "DELIVERY.restore": "human-restore",
    "DELIVERY.restored": "restored-read",
}
CALLER_ROLE_BY_KIND = {"api": "human", "bot": "driver"}


def delivery_policy_step_rows() -> tuple[dict, ...]:
    """Derive the frozen ten-call step table from the local family map."""
    rows = []
    for index, requirement in enumerate(REQUIREMENTS):
        kind = KINDS[index]
        rows.append({
            "requirement_id": requirement,
            "operation": DELIVERY_POLICY_OPERATIONS[index],
            "method": "PUT" if requirement in PUT_REQUIREMENTS else "GET",
            "path": POLICY_PATH,
            "caller_role": "driver" if index in BOT_ACTOR_INDEXES else "human",
            "binding_auth": "case_owned_driver" if kind == "bot" else "case_owned_human",
            "adapter_method": STEP_METHOD_BY_REQUIREMENT[requirement],
            "case_class": CASE_CLASS_BY_REQUIREMENT[requirement],
        })
    return tuple(rows)


STEP_ROWS = delivery_policy_step_rows()


def delivery_policy_credential_kind(profile: dict) -> str:
    """Label the preprod credential family without echoing any secret."""
    if profile["environment"]["tier"] == "local":
        return "owned_local_bot_token"
    if profile["auth"]["kind"] == "human-cookie-with-owned-bot-tokens":
        return "human_cookie_with_owned_driver_bot_token"
    return "human_cookie"


def delivery_policy_step_transport(operation: str) -> str:
    """Every reviewed delivery-policy obligation exits as PRODUCT_HTTP."""
    return ("PRODUCT_HTTP" if operation in DELIVERY_POLICY_HTTP_OPERATIONS
            else "PRODUCT_CLI")


def delivery_policy_case_plan() -> dict:
    """Reject drift in the frozen ten-call migration source map."""
    binding = delivery_policy_source_binding()
    if (binding["parent"] != DELIVERY_POLICY_PARENT
            or [row["id"] for row in binding["requirements"]] != list(REQUIREMENTS)
            or [row["method"] for row in binding["requirements"]]
            != [row["method"] for row in STEP_ROWS]
            or any(row["path"] != POLICY_PATH for row in binding["requirements"])):
        raise RunStateError("Delivery policy source obligation map changed")
    return {
        "case_id": DUAL_DELIVERY_POLICY_CASE_ID,
        "parent_id": DELIVERY_POLICY_PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "operations": list(DELIVERY_POLICY_OPERATIONS),
        "source_expectations": {
            row["id"]: row["expectation"] for row in binding["requirements"]
        },
    }


def delivery_policy_adaptability() -> dict:
    return {
        "case_id": DUAL_DELIVERY_POLICY_CASE_ID,
        "legacy_id": DELIVERY_POLICY_PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "selected_obligations": [
            "human-read", "bot-read-denied", "bot-write-denied",
            "denied-preserves", "cas-update", "readback", "stale-rejected",
            "conflict-preserves", "restore", "restored",
        ],
        "environments": ["singlebox-product-http", "preprod-product-http"],
        "local_executor_status": "LOCAL_FULL_EXECUTOR",
        "cli_surface": "product-bcs-http-admin-policy",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Singlebox and preprod product-HTTP live admissions PASS "
            "(preprod proof dp-live-preprod-r2: 10/10, driver denial probes "
            "reach the BCS application-layer 403 through the ACE edge on the "
            "declared trio posture); remote_release_validation and the OB "
            "physical environment binding remain separate gates."
        ),
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
        raise ConfigError(f"actors.{role}.visibility: delivery-policy admission requires public Bots")
    return actor


def _validate_delivery_policy_bindings(value: object) -> dict:
    expected = {row["operation"]: row for row in STEP_ROWS}
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError("delivery policy route bindings are incomplete")
    result = {}
    for operation, row in expected.items():
        raw_binding = value[operation]
        budget = None
        if isinstance(raw_binding, dict) and "max_response_bytes" in raw_binding:
            budget = raw_binding["max_response_bytes"]
            if (type(budget) is not int or not 1 <= budget <= ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM):
                raise ConfigError(f"route_bindings.{operation}: response budget is outside the reviewed bound")
            raw_binding = {key: item for key, item in raw_binding.items()
                           if key != "max_response_bytes"}
        binding = object_keys(raw_binding,
                              {"method", "path", "api_face", "auth", "response", "traceparent"},
                              f"route_bindings.{operation}")
        if (binding["method"] != row["method"] or binding["path"] != row["path"]
                or binding["api_face"] != "legacy"
                or binding["auth"] != row["binding_auth"]
                or binding["response"] != "legacy-json"
                or binding["traceparent"] != "disabled"):
            raise ConfigError(f"route_bindings.{operation}: delivery-policy route differs from the reviewed contract")
        validated = dict(binding)
        if budget is not None:
            validated["max_response_bytes"] = budget
        result[operation] = validated
    return result


def validate_delivery_policy_profile(raw: object) -> dict:
    """Validate the dual Delivery Policy Profile from the reviewed map.

    The validator derives the environment, entrypoint, actor, auth, policy
    and route-binding forms from the reviewed migration contract and the
    owned-local delivery-policy family conventions; it never resolves
    credential references or contacts a server.
    """
    if not isinstance(raw, dict) and not hasattr(raw, "get"):
        raise ConfigError("delivery policy profile must be an object")
    expected_keys = {
        "schema_version", "contract", "environment", "entrypoint", "human",
        "actors", "auth", "policy", "cli", "route_bindings",
    }
    if isinstance(raw, dict) and "browser_origin" in raw:
        expected_keys.add("browser_origin")
    profile = object_keys(raw, expected_keys, "delivery policy profile")
    if (type(profile["schema_version"]) is not int
            or profile["schema_version"] != 1
            or profile["contract"] != DELIVERY_POLICY_PROFILE_CONTRACT):
        raise ConfigError("delivery policy profile: unsupported version or contract")
    environment = object_keys(profile["environment"],
                              {"tier", "id", "expected_bcs_env"},
                              "delivery policy environment")
    if environment["tier"] not in {"local", "preprod"}:
        raise ConfigError("environment.tier: only Singlebox and preprod are admitted")
    identifier(environment["id"], "environment.id")
    if environment["expected_bcs_env"] != environment["tier"]:
        if environment["tier"] != "preprod" or environment["expected_bcs_env"] != "pre":
            raise ConfigError("environment.expected_bcs_env: environment binding mismatch")
    entrypoint = object_keys(profile["entrypoint"],
                             {"kind", "origin", "api_face", "manifest_face"},
                             "delivery policy entrypoint")
    if entrypoint["api_face"] != "legacy" or entrypoint["manifest_face"] != "legacy":
        raise ConfigError("entrypoint: delivery policy requires the legacy product face")
    target = parsed_origin(entrypoint["origin"], environment["tier"])
    if environment["tier"] == "local":
        if (entrypoint["kind"] != "singlebox" or target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not target.port):
            raise ConfigError("entrypoint: local delivery policy requires an owned loopback")
    else:
        if entrypoint["kind"] != "gateway" or target.scheme != "https":
            raise ConfigError("entrypoint: preprod delivery policy requires the admitted HTTPS gateway")
    human = object_keys(profile["human"], {"user_id"}, "human")
    identifier(human["user_id"], "human.user_id")
    actors = object_keys(profile["actors"], {"driver", "member"}, "actors")
    actors["driver"] = _actor(actors["driver"], "driver", human["user_id"])
    actors["member"] = _actor(actors["member"], "member", human["user_id"])
    if actors["driver"]["bot_id"] == actors["member"]["bot_id"]:
        raise ConfigError("actors: delivery policy requires two distinct dedicated Bots")
    if environment["tier"] == "local":
        auth = object_keys(profile["auth"], {"kind", "driver_token_env"}, "auth")
        if auth["kind"] != "owned-local-bot-tokens":
            raise ConfigError("auth.kind: local delivery policy requires an owned Bot token")
        _env_ref(auth["driver_token_env"], "auth.driver_token_env")
    else:
        candidate = profile["auth"]
        if not isinstance(candidate, dict) or "kind" not in candidate:
            raise ConfigError("auth: preprod delivery policy requires a reviewed auth family")
        if candidate["kind"] == "human-cookie-with-owned-bot-tokens":
            auth = object_keys(
                candidate,
                {"kind", "driver_token_env", "member_token_env", "cookie_env"},
                "auth")
            _env_ref(auth["driver_token_env"], "auth.driver_token_env")
            _env_ref(auth["member_token_env"], "auth.member_token_env")
            _env_ref(auth["cookie_env"], "auth.cookie_env")
        elif candidate["kind"] == "human-cookie":
            auth = object_keys(candidate, {"kind", "cookie_env"}, "auth")
            _env_ref(auth["cookie_env"], "auth.cookie_env")
        else:
            raise ConfigError(
                "auth.kind: preprod delivery policy requires the Human Cookie "
                "or the reviewed ACE trio")
    policy = object_keys(profile["policy"],
                         {"fixture_pool", "exclusive_use_confirmed", "timeout_seconds",
                          "max_response_bytes", "failure_retention_hours"},
                         "policy")
    identifier(policy["fixture_pool"], "policy.fixture_pool")
    if (type(policy["exclusive_use_confirmed"]) is not bool
            or policy["exclusive_use_confirmed"] is not True):
        raise ConfigError("policy.exclusive_use_confirmed: retained policy effects require exclusive use")
    for key, maximum in (("timeout_seconds", 30), ("max_response_bytes", 65536),
                         ("failure_retention_hours", 168)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ConfigError(f"policy.{key}: integer outside the CLI contract")
    cli = object_keys(profile["cli"], {"transport", "command_family", "api_face"}, "cli")
    if cli != {"transport": "product-http", "command_family": "delivery-policy",
               "api_face": "legacy"}:
        raise ConfigError("cli: delivery policy binds the reviewed legacy product-HTTP surface")
    if "browser_origin" in profile:
        value = profile["browser_origin"]
        if environment["tier"] != "preprod":
            raise ConfigError("browser_origin: preprod browser context only")
        if (not isinstance(value, str) or not value.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            raise ConfigError("browser_origin: ASCII origin without whitespace or controls")
        origin(value, "preprod")
    profile["route_bindings"] = _validate_delivery_policy_bindings(profile["route_bindings"])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)


def require(value, message="Delivery policy dual contract differs"):
    if not value:
        raise RunStateError(message)


# Dual adapters: the ten reviewed obligations replay through the product
# HTTP surface on both admitted tiers; the credential contexts stay
# tier-aware and never leave the transport layer.


class RemoteLocalDeliveryPolicyAdapter:
    """Replay the ten ordered delivery-policy CAS obligations exactly once."""

    _profile_validator = staticmethod(validate_delivery_policy_profile)

    def __init__(self, profile: dict):
        self.profile = self._profile_validator(profile)
        self.driver_id = self.profile["actors"]["driver"]["bot_id"]
        self.member_id = self.profile["actors"]["member"]["bot_id"]
        self.human_actor = "human_" + self.profile["human"]["user_id"]
        self.fixture_effect = DELIVERY_POLICY_FIXTURE_EFFECT
        self.version: int | None = None
        self.original_policy: dict | None = None
        self.updated_policy: dict | None = None
        self.policy_change: dict | None = None
        self._attempted: set[str] = set()
        self._bot_client: WriteClient | None = None
        self.human_client = WriteClient(self._human_profile(self.profile))

    def _credential_kind(self) -> str:
        return delivery_policy_credential_kind(self.profile)

    @staticmethod
    def _derive_profile(profile: dict) -> dict:
        value = json.loads(json.dumps(profile, allow_nan=False))
        value["entrypoint"] = {
            "kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
            "api_face": "legacy",
            "manifest_face": "legacy",
        }
        return value

    @staticmethod
    def _human_profile(profile: dict) -> dict:
        """Human administration face: owned Cookie on preprod, ambient owned-local identity on Singlebox."""
        value = RemoteLocalDeliveryPolicyAdapter._derive_profile(profile)
        if profile["environment"]["tier"] == "local":
            value["auth"] = {"kind": "authenticated-product", "header_env": {}}
        else:
            value["auth"] = {
                "kind": "human-cookie",
                "header_env": {"Cookie": profile["auth"]["cookie_env"]},
            }
        return value

    @staticmethod
    def _bot_profile(profile: dict) -> dict:
        """Bot denial face: the dedicated driver Bot token the contract must reject."""
        auth = profile["auth"]
        driver_token_env = auth.get("driver_token_env")
        if not isinstance(driver_token_env, str) or not driver_token_env:
            raise RunStateError(
                "Delivery policy Bot denial probes require the owned dedicated "
                "driver Bot token")
        value = RemoteLocalDeliveryPolicyAdapter._derive_profile(profile)
        if profile["environment"]["tier"] == "local":
            value["auth"] = {
                "kind": "authenticated-product",
                "header_env": {"X-BCS-Bot-Token": driver_token_env},
            }
        else:
            # The preprod ACE edge only admits the workstation browser posture:
            # the owned human session Cookie rides with the dedicated driver
            # Bot bearer token exactly like the browser session the product
            # itself runs.  The BCS application layer still authorizes the
            # presented Bot identity, so the denial probes keep asserting the
            # application-layer 403 on both tiers instead of dying inside a
            # gateway login envelope.
            header_env = {"Authorization": driver_token_env}
            if auth.get("kind") == "human-cookie-with-owned-bot-tokens":
                header_env["Cookie"] = auth["cookie_env"]
            value["auth"] = {
                "kind": "authenticated-product",
                "authorization_scheme": "bearer",
                "header_env": header_env,
            }
        return value

    def _bot_client_or_raise(self) -> WriteClient:
        if self._bot_client is None:
            self._bot_client = WriteClient(self._bot_profile(self.profile))
        return self._bot_client

    def _once(self, requirement_id: str) -> None:
        if requirement_id in self._attempted:
            raise RunStateError("Delivery policy obligation was repeated")
        self._attempted.add(requirement_id)

    def _require_read(self) -> None:
        if self.version is None or self.original_policy is None:
            raise RunStateError(
                "Delivery policy human read must precede the denial probes and CAS obligations")

    def _request(self, caller: str, row: dict, *, body: dict | None = None,
                 expected: tuple[int, ...] = (200,), parse_error: bool = False):
        client = (self._bot_client_or_raise() if caller == "driver"
                  else self.human_client)
        try:
            data, record = client.call(row["operation"], body=body,
                                       expected=expected, parse_error=parse_error)
        except Exception as error:
            raise RunStateError(
                f"Delivery policy product-HTTP execution for {row['operation']} failed"
            ) from error
        if not isinstance(record, dict) or record.get("http_status") not in expected:
            raise RunStateError(f"Delivery policy {row['operation']} response record is invalid")
        evidence = dict(record)
        evidence.update({
            "transport": "PRODUCT_HTTP",
            "requirement_id": row["requirement_id"],
            "operation": row["operation"],
            "caller_role": row["caller_role"],
            "credential_kind": self._credential_kind(),
            "case_class": row["case_class"],
            "fixture_effect": self.fixture_effect,
            "remote_validation": "NOT_RUN",
            "grants_release_credit": False,
        })
        if body is not None:
            evidence["body_digest"] = fingerprint(body)
        return data, AdapterCall(data=data, evidence=evidence)

    @staticmethod
    def _replace_body(expected_version: int, policy: dict) -> dict:
        return {"expected_version": expected_version, "policy": copy.deepcopy(policy)}

    def read_policy_as_human(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.human-read"]
        self._once(row["requirement_id"])
        data, call = self._request("human", row)
        require(isinstance(data, dict), "delivery policy human read must return the policy record")
        version = data.get("version")
        require(isinstance(version, int) and not isinstance(version, bool) and version >= 0,
                "delivery policy version is not a non-negative integer")
        require(isinstance(data.get("policy"), dict)
                and isinstance(data.get("updated_by"), str)
                and isinstance(data.get("updated_at_ms"), int)
                and not isinstance(data.get("updated_at_ms"), bool),
                "delivery policy record is incomplete")
        self.version = version
        self.original_policy = copy.deepcopy(data["policy"])
        return AdapterCall(data={"version": self.version, "policy": self.original_policy},
                           evidence=call.evidence)

    def read_policy_as_bot(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.bot-read-denied"]
        self._once(row["requirement_id"])
        self._require_read()
        data, call = self._request("driver", row, expected=(403,))
        require(data is None, "denied Bot administration must not expose a policy payload")
        return AdapterCall(data={"http_status": 403}, evidence=call.evidence)

    def write_policy_as_bot(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.bot-write-denied"]
        self._once(row["requirement_id"])
        self._require_read()
        body = self._replace_body(self.version, self.original_policy)
        data, call = self._request("driver", row, body=body, expected=(403,))
        require(data is None, "denied Bot administration must not replace the policy")
        return AdapterCall(data={"http_status": 403}, evidence=call.evidence)

    def read_policy_after_denial(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.denied-preserves"]
        self._once(row["requirement_id"])
        self._require_read()
        data, call = self._request("human", row)
        require(isinstance(data, dict)
                and data.get("version") == self.version
                and data.get("policy") == self.original_policy,
                "denied Bot writes must preserve the effective policy")
        return AdapterCall(data={"version": self.version, "policy": self.original_policy},
                           evidence=call.evidence)

    def replace_policy_with_matching_version(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.cas-update"]
        self._once(row["requirement_id"])
        self._require_read()
        updated = copy.deepcopy(self.original_policy)
        target = 23 if updated.get("max_context_messages") != 23 else 24
        updated["max_context_messages"] = target
        self.updated_policy = updated
        self.policy_change = {
            "field": "max_context_messages",
            "from": self.original_policy.get("max_context_messages"),
            "to": target,
        }
        body = self._replace_body(self.version, updated)
        data, call = self._request("human", row, body=body)
        require(isinstance(data, dict)
                and data.get("version") == self.version + 1
                and data.get("updated_by") == self.human_actor
                and data.get("policy") == updated,
                "human CAS policy replacement differs")
        self.version = self.version + 1
        return AdapterCall(data={"version": self.version, "updated_by": self.human_actor,
                                 "policy": updated}, evidence=call.evidence)

    def read_policy_after_update(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.readback"]
        self._once(row["requirement_id"])
        self._require_read()
        if self.updated_policy is None:
            raise RunStateError("Delivery policy CAS update must precede its read-back")
        data, call = self._request("human", row)
        require(isinstance(data, dict)
                and data.get("version") == self.version
                and data.get("policy") == self.updated_policy,
                "policy read-back after replacement differs")
        return AdapterCall(data={"version": self.version, "policy": self.updated_policy},
                           evidence=call.evidence)

    def write_policy_with_stale_version(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.stale-rejected"]
        self._once(row["requirement_id"])
        self._require_read()
        if self.updated_policy is None:
            raise RunStateError("Delivery policy CAS update must precede the stale-write probe")
        body = self._replace_body(self.version - 1, self.updated_policy)
        data, call = self._request("human", row, body=body, expected=(409,), parse_error=True)
        require(data == {"error": CONFLICT_ERROR},
                "stale delivery-policy replacement is not rejected with the conflict contract")
        return AdapterCall(data={"http_status": 409, "error": CONFLICT_ERROR},
                           evidence=call.evidence)

    def read_policy_after_conflict(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.conflict-preserves"]
        self._once(row["requirement_id"])
        self._require_read()
        if self.updated_policy is None:
            raise RunStateError("Delivery policy stale-write conflict must precede its preservation read")
        data, call = self._request("human", row)
        require(isinstance(data, dict)
                and data.get("version") == self.version
                and data.get("policy") == self.updated_policy,
                "stale-write conflict must preserve the effective policy")
        return AdapterCall(data={"version": self.version, "policy": self.updated_policy},
                           evidence=call.evidence)

    def restore_original_policy(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.restore"]
        self._once(row["requirement_id"])
        self._require_read()
        if self.updated_policy is None:
            raise RunStateError("Delivery policy CAS update must precede the restoration")
        body = self._replace_body(self.version, self.original_policy)
        data, call = self._request("human", row, body=body)
        require(isinstance(data, dict)
                and data.get("version") == self.version + 1
                and data.get("updated_by") == self.human_actor
                and data.get("policy") == self.original_policy,
                "policy restoration differs")
        self.version = self.version + 1
        return AdapterCall(data={"version": self.version, "updated_by": self.human_actor,
                                 "policy": self.original_policy}, evidence=call.evidence)

    def read_restored_policy(self) -> AdapterCall:
        row = STEP_ROW_BY_REQUIREMENT["DELIVERY.restored"]
        self._once(row["requirement_id"])
        self._require_read()
        if self.updated_policy is None:
            raise RunStateError("Delivery policy restoration must precede the final read-back")
        data, call = self._request("human", row)
        require(isinstance(data, dict)
                and data.get("version") == self.version
                and data.get("policy") == self.original_policy,
                "restored policy read-back differs")
        return AdapterCall(data={"version": self.version, "policy": self.original_policy},
                           evidence=call.evidence)


STEP_ROW_BY_REQUIREMENT = {row["requirement_id"]: row for row in STEP_ROWS}


class DualDeliveryPolicyAdapter(RemoteLocalDeliveryPolicyAdapter):
    """Dual-environment Delivery Policy adapter with tier-aware labels."""

    def _credential_kind(self) -> str:
        return delivery_policy_credential_kind(self.profile)


def _require_step_evidence(adapter: object, call: object, operation: str) -> dict:
    if not isinstance(call, AdapterCall):
        raise RunStateError(f"Delivery policy {operation} did not return AdapterCall")
    call.validate()
    if (call.evidence.get("transport") != delivery_policy_step_transport(operation)
            or not isinstance(call.evidence.get("caller_role"), str)
            or not call.evidence["caller_role"]
            or call.evidence.get("credential_kind") != adapter._credential_kind()
            or call.evidence.get("remote_validation") != "NOT_RUN"
            or call.evidence.get("grants_release_credit") is not False
            or call.evidence.get("fixture_effect") != adapter.fixture_effect):
        raise RunStateError(f"Delivery policy evidence for {operation} is invalid")
    return dict(call.evidence)


def execute_delivery_policy_case(adapter: object) -> CaseExecution:
    """Replay the ten ordered obligations; completed steps keep PASS."""
    delivery_policy_case_plan()
    if not isinstance(adapter, DualDeliveryPolicyAdapter):
        raise RunStateError("Delivery policy requires its dual adapter")
    outcomes: list[StepOutcome] = []
    completed = 0
    error_type: str | None = None
    for row in STEP_ROWS:
        if error_type is None:
            try:
                call = getattr(adapter, row["adapter_method"])()
                evidence = _require_step_evidence(adapter, call, row["operation"])
                outcomes.append(StepOutcome(
                    requirement_id=row["requirement_id"],
                    method=DUAL_DELIVERY_POLICY_CASE_METHOD,
                    status="PASS",
                    data=call.data,
                    evidence=evidence,
                    error_type=None))
                completed += 1
                continue
            except Exception as error:
                error_type = type(error).__name__
                outcomes.append(StepOutcome(
                    requirement_id=row["requirement_id"],
                    method=DUAL_DELIVERY_POLICY_CASE_METHOD,
                    status="NOT_RUN",
                    data=None,
                    evidence={},
                    error_type=error_type))
                continue
        outcomes.append(StepOutcome(
            requirement_id=row["requirement_id"],
            method=DUAL_DELIVERY_POLICY_CASE_METHOD,
            status="NOT_RUN",
            data=None,
            evidence={},
            error_type=None))
    execution = CaseExecution(
        case_id=DUAL_DELIVERY_POLICY_CASE_ID,
        parent_id=DELIVERY_POLICY_PARENT,
        status="PASS" if error_type is None else "BLOCKED",
        steps=tuple(outcomes),
        completed_count=completed,
        total_count=len(STEP_ROWS),
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution


_CASES = {
    "delivery-policy": {
        "case_id": DUAL_DELIVERY_POLICY_CASE_ID,
        "validator": validate_delivery_policy_profile,
        "adapter": DualDeliveryPolicyAdapter,
        "execute": execute_delivery_policy_case,
        "fixture_effect": DELIVERY_POLICY_FIXTURE_EFFECT,
    },
}


def load_delivery_policy_cli_case_profile(path: Path, case_key: str) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("delivery policy case must be delivery-policy")
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"delivery policy {case_key} profile must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError(f"delivery policy {case_key} profile is unreadable JSON") from None
    return _CASES[case_key]["validator"](raw)


def dual_delivery_policy_case_run(
    *,
    profile_path: Path,
    cli_binary: Path,
    cli_binary_sha256: str,
    case_key: str,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
) -> dict:
    if case_key not in CASE_KEYS:
        raise ConfigError("delivery policy case must be delivery-policy")
    run_id = identifier(run_id, f"delivery policy {case_key} run ID")
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            f"delivery policy {case_key} requires explicit write permission and exact Run confirmation")
    profile = load_delivery_policy_cli_case_profile(profile_path, case_key)
    require_resolved(profile)
    digest = fingerprint(profile)
    artifact = executor_digest()
    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute() or requested_state_dir.is_symlink()
        or requested_state_dir.exists() or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError(f"delivery policy {case_key} requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError(f"delivery policy {case_key} requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)
    profile_file = state_dir / f"delivery-policy-{case_key}-profile.json"
    binding_file = state_dir / f"delivery-policy-{case_key}-binding.json"
    report_file = state_dir / f"delivery-policy-{case_key}-run.json"
    write(profile_file, profile)
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": _CASES[case_key]["case_id"],
        "parent_id": DELIVERY_POLICY_PARENT,
        "run_id": run_id,
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)
    try:
        require_cli_binary(Path(cli_binary).resolve(), cli_binary_sha256)
        adapter = _CASES[case_key]["adapter"](profile)
        execution = _CASES[case_key]["execute"](adapter)
    except (ConfigError, RunStateError, WorkspaceCliRunnerError, LocalCliError,
            WriteTransportError, OSError) as error:
        raise ConfigError(
            f"delivery policy {case_key} case execution is unavailable: {error}") from None
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": execution.case_id,
        "parent_id": DELIVERY_POLICY_PARENT,
        "run_id": run_id,
        "status": "PASS" if execution.status == "PASS" else "BLOCKED",
        "profile_digest": digest,
        "executor_digest": artifact,
        "cli_binary_sha256": cli_binary_sha256,
        "state_dir": str(state_dir),
        "environment": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
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
        "credential_kind": delivery_policy_credential_kind(profile),
    }
    write(report_file, report)
    return report
