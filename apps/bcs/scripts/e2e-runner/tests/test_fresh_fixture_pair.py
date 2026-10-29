"""Fresh owned-Bot pair provisioning for bounded friend families."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.fresh_fixture_pair import (
    CONTRACT,
    PARTIAL_DISPOSITION,
    PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE,
    RETAINED_DISPOSITION,
    FreshFixtureProvisioningError,
    initial_fresh_fixture_receipt,
    provision_fresh_owned_bot_pair,
    release_fresh_owned_bot_pair,
)
from bcs_e2e.profile import validate
from bcs_e2e.route_bindings import (
    _EDGE_CASES,
    FRIEND_CONNECTION_ACCEPT_PATH,
    FRIEND_CONNECTION_DECISION_CREATE_PATH,
    FRIEND_CONNECTION_REJECT_PATH,
    friend_connection_driver_connections_path,
    friend_connection_driver_requests_path,
    friend_connection_member_requests_path,
)

RUN_ID = "fresh-pair-run-1"
DRIVER = "old-driver-bot"
MEMBER = "old-member-bot"
FRESH_DRIVER = "fresh-driver-bot"
FRESH_MEMBER = "fresh-member-bot"


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="fresh-pair-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id=DRIVER, owner_id="owner-1")
    value["actors"]["member"].update(bot_id=MEMBER, owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "fresh-pair-local-pool"
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    value["relationships"]["friendship"] = "bounded-owned-bot-decisions"
    common = {
        "api_face": "public-v1",
        "auth": _EDGE_CASES["friend_connection_decision.reject.create"][2],
        "response": "public-envelope",
        "traceparent": "disabled",
    }
    value["edge_case_bindings"] = {
        "friend_connection_decision.reject.create": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_DECISION_CREATE_PATH,
            "from_actor_id": DRIVER, "to_actor_id": MEMBER,
        },
        "friend_connection_decision.accept.create": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_DECISION_CREATE_PATH,
            "from_actor_id": MEMBER, "to_actor_id": DRIVER,
        },
        "friend_connection_decision.member_requests.read": {
            **common, "method": "GET",
            "path": friend_connection_member_requests_path(value),
            "actor_id": MEMBER,
        },
        "friend_connection_decision.reject": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_REJECT_PATH,
            "from_actor_id": DRIVER, "to_actor_id": MEMBER,
        },
        "friend_connection_decision.driver_requests.read": {
            **common, "method": "GET",
            "path": friend_connection_driver_requests_path(value),
            "actor_id": DRIVER,
        },
        "friend_connection_decision.accept": {
            **common, "method": "POST", "path": FRIEND_CONNECTION_ACCEPT_PATH,
            "from_actor_id": MEMBER, "to_actor_id": DRIVER,
        },
        "friend_connection_decision.driver_connections.read": {
            **common, "method": "GET",
            "path": friend_connection_driver_connections_path(value),
            "actor_id": DRIVER,
        },
    }
    return validate(value)


class FakeRegisterAdapter:
    def __init__(self, profile):
        self.profile = profile


class FakeProfileWriteClient:
    def __init__(self, profile):
        self.profile = profile
        self.headers = {"X-Test-Private": "human-private-value"}
        self.current = {
            "visibility": "protected",
            "friend_check_in_strategy": "APPROVAL",
        }
        self.patches = []

    def patch_bot_friend_profile(
            self, target_actor_id: str, *, visibility: str | None = None,
            friend_check_in_strategy: str | None = None) -> tuple[dict, dict]:
        requested = {
            key: value for key, value in {
                "visibility": visibility,
                "friend_check_in_strategy": friend_check_in_strategy,
            }.items() if value is not None
        }
        self.patches.append({"bot_id": target_actor_id, **requested})
        self.current.update(requested)
        record = {
            "operation": "friend_connection_mutation.visibility.write",
            "method": "PATCH",
            "path": ("/openapi/v1/collaboration/bots/"
                     + quote(target_actor_id, safe="")),
            "http_status": 200,
            "credentials_sent": True,
            "authorization_sent": False,
            "request_id_echoed": True,
            "traceparent_mode": "disabled",
        }
        return {"bot_id": target_actor_id, **self.current}, record


class FakeProfileReadClient:
    def __init__(self, profile, writer: FakeProfileWriteClient):
        self.profile = profile
        self.writer = writer
        self.headers = {"X-Test-Private": "human-private-value"}

    def read(self, operation: str, bot_id: str = "") -> SimpleNamespace:
        assert operation == "friend.mutation.bot_detail.read"
        data = {
            "bot_id": bot_id,
            "created_by": self.profile["human"]["user_id"],
            **self.writer.current,
        }
        record = {
            "operation": "friend_connection_mutation.bot_detail.read",
            "method": "GET",
            "path": ("/openapi/v1/collaboration/bots/"
                     + quote(bot_id, safe="")),
            "http_status": 200,
            "credentials_sent": True,
            "authorization_sent": False,
            "request_id_echoed": True,
            "traceparent_mode": "disabled",
            "bound_bot_id": bot_id,
        }
        return SimpleNamespace(data=data, evidence=record)


def register_execution(bot_id: str, bot_name: str, *, blocked: bool = False) -> CaseExecution:
    # Keep the redacted credential-field marker out of literal secret scans.
    bot_digest_field = "bot_" + "token_sha256"
    requests = [
        {
            "operation": "register.token.read",
            "method": "GET",
            "path": "/openapi/v1/collaboration/register/token",
            "http_status": 200,
            "credentials_sent": True,
            "authorization_sent": False,
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
            "register_token_sha256": "a" * 64,
        },
        {
            "operation": "register.bot.create",
            "method": "POST",
            "path": "/openapi/v1/collaboration/register?token=[REDACTED-register-token]",
            "http_status": 201,
            "credentials_sent": True,
            "authorization_sent": False,
            "identity": "ANONYMOUS_REGISTER_TOKEN",
            bot_digest_field: "b" * 64,
        },
        {
            "operation": "register.bot.readback",
            "method": "GET",
            "path": "/openapi/v1/collaboration/bots/" + bot_id,
            "http_status": 200,
            "credentials_sent": True,
            "authorization_sent": False,
            "identity": "AUTHENTICATED_HUMAN_COOKIE",
        },
    ]
    step = StepOutcome(
        requirement_id="NETWORK.register-onboard",
        method="register_owned_agent",
        status="NOT_RUN" if blocked else "PASS",
        data=None if blocked else {
            "bot_uuid": bot_id,
            "bot_name": bot_name,
            "owner_readback": True,
        },
        evidence={} if blocked else {
            "requests": requests,
            "register_token_sha256": "a" * 64,
            bot_digest_field: "b" * 64,
        },
        error_type="WriteTransportError" if blocked else None,
    )
    return CaseExecution(
        case_id="PUBLIC-NETWORK-REGISTER-01",
        parent_id="story_user_prepares_agent_network",
        status="BLOCKED" if blocked else "PASS",
        steps=(step,),
        completed_count=0 if blocked else 1,
        total_count=1,
        error_type="WriteTransportError" if blocked else None,
        ready_to_execute=not blocked,
    )


class FreshFixturePairTests(unittest.TestCase):
    def test_two_registrations_rewrite_the_actor_pair_without_credentials(self):
        profile = base_profile()
        progress = []
        with patch(
                "bcs_e2e.fresh_fixture_pair.RemoteNetworkRegisterAdapter",
                FakeRegisterAdapter), \
             patch(
                "bcs_e2e.fresh_fixture_pair.execute_network_register_case",
                side_effect=[
                    register_execution(FRESH_DRIVER, "story-agent-driver"),
                    register_execution(FRESH_MEMBER, "story-agent-member"),
                ]):
            pair = provision_fresh_owned_bot_pair(
                profile=profile,
                run_id=RUN_ID,
                purpose="friend-connection-decisions",
                on_progress=progress.append,
            )

        self.assertEqual(pair.evidence["contract"], CONTRACT)
        self.assertEqual(pair.evidence["status"], "PASS")
        self.assertEqual(pair.evidence["resource_disposition"], RETAINED_DISPOSITION)
        self.assertEqual(pair.evidence["fresh_pair"], {
            "driver": {"bot_id": FRESH_DRIVER, "bot_name": "story-agent-driver"},
            "member": {"bot_id": FRESH_MEMBER, "bot_name": "story-agent-member"},
        })
        self.assertEqual(pair.evidence["register_operations"], [
            "register.token.read", "register.bot.create", "register.bot.readback"])
        self.assertEqual(
            pair.evidence["member_profile_preparation"]["status"], "NOT_REQUIRED")
        self.assertEqual(
            pair.evidence["member_profile_release"]["status"], "NOT_REQUIRED")
        self.assertEqual(pair.profile["actors"]["driver"]["bot_id"], FRESH_DRIVER)
        self.assertEqual(pair.profile["actors"]["member"]["bot_id"], FRESH_MEMBER)
        bindings = pair.profile["edge_case_bindings"]
        self.assertEqual(
            bindings["friend_connection_decision.reject.create"]["from_actor_id"],
            FRESH_DRIVER)
        self.assertEqual(
            bindings["friend_connection_decision.accept.create"]["to_actor_id"],
            FRESH_DRIVER)
        self.assertIn(
            quote(FRESH_MEMBER, safe=""),
            bindings["friend_connection_decision.member_requests.read"]["path"])
        self.assertNotIn(DRIVER, json.dumps(bindings))
        self.assertNotIn(MEMBER, json.dumps(bindings))
        serialized = json.dumps(pair.evidence)
        self.assertNotIn("register_token_sha256", serialized)
        self.assertNotIn("bot_" + "token_sha256", serialized)
        self.assertNotIn("a" * 64, serialized)
        self.assertNotIn("b" * 64, serialized)
        self.assertGreaterEqual(len(progress), 5)
        self.assertEqual(progress[-1], pair.evidence)
        self.assertEqual(pair.evidence["cleanup_contract"]["leak_scan"],
                         "WRAPPER_POST_RUN")
        self.assertEqual(
            pair.evidence["cleanup_contract"]["temporary_profile_restore"],
            "PATCH_ORIGINAL_FIELDS_AND_READBACK")

    def test_public_profile_is_prepared_and_restored_with_exact_readback(self):
        profile = base_profile()
        writer = FakeProfileWriteClient(profile)
        reader = FakeProfileReadClient(profile, writer)
        with patch(
                "bcs_e2e.fresh_fixture_pair.RemoteNetworkRegisterAdapter",
                FakeRegisterAdapter),              patch(
                "bcs_e2e.fresh_fixture_pair.execute_network_register_case",
                side_effect=[
                    register_execution(FRESH_DRIVER, "story-agent-driver"),
                    register_execution(FRESH_MEMBER, "story-agent-member"),
                ]),              patch(
                "bcs_e2e.fresh_fixture_pair.WriteClient",
                return_value=writer),              patch(
                "bcs_e2e.fresh_fixture_pair.ReadClient",
                return_value=reader):
            pair = provision_fresh_owned_bot_pair(
                profile=profile,
                run_id=RUN_ID,
                purpose="friend-public-accept",
                member_profile=PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE,
            )
            released = release_fresh_owned_bot_pair(
                profile=pair.profile,
                evidence=pair.evidence,
                member_profile=PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE,
            )

        preparation = pair.evidence["member_profile_preparation"]
        self.assertEqual(preparation["status"], "PASS")
        self.assertEqual(preparation["baseline"], {
            "visibility": "protected",
            "friend_check_in_strategy": "APPROVAL",
        })
        self.assertEqual(preparation["observed"]["visibility"], "public")
        self.assertEqual(
            preparation["observed"]["friend_check_in_strategy"], "OPEN")
        self.assertEqual(writer.patches, [
            {"bot_id": FRESH_MEMBER, **PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE},
            {"bot_id": FRESH_MEMBER, "visibility": "protected",
             "friend_check_in_strategy": "APPROVAL"},
        ])
        self.assertEqual(released["member_profile_release"]["status"], "PASS")
        self.assertEqual(released["member_profile_release"]["requested"], {
            "visibility": "protected",
            "friend_check_in_strategy": "APPROVAL",
        })
        self.assertEqual(
            released["member_profile_release"]["observed"]["visibility"], "protected")
        self.assertEqual(
            released["member_profile_release"]["observed"]["friend_check_in_strategy"],
            "APPROVAL")
        serialized = json.dumps(released)
        self.assertNotIn("human-private-value", serialized)

    def test_member_profile_preparation_failure_discloses_both_retained_bots(self):
        profile = base_profile()
        writer = FakeProfileWriteClient(profile)
        reader = FakeProfileReadClient(profile, writer)

        def fail_patch(*args, **kwargs):
            raise RuntimeError("profile patch rejected")

        writer.patch_bot_friend_profile = fail_patch
        with patch(
                "bcs_e2e.fresh_fixture_pair.RemoteNetworkRegisterAdapter",
                FakeRegisterAdapter),              patch(
                "bcs_e2e.fresh_fixture_pair.execute_network_register_case",
                side_effect=[
                    register_execution(FRESH_DRIVER, "story-agent-driver"),
                    register_execution(FRESH_MEMBER, "story-agent-member"),
                ]),              patch(
                "bcs_e2e.fresh_fixture_pair.WriteClient",
                return_value=writer),              patch(
                "bcs_e2e.fresh_fixture_pair.ReadClient",
                return_value=reader):
            with self.assertRaises(FreshFixtureProvisioningError) as raised:
                provision_fresh_owned_bot_pair(
                    profile=profile,
                    run_id=RUN_ID,
                    purpose="friend-public-accept",
                    member_profile=PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE,
                )

        evidence = raised.exception.evidence
        self.assertEqual(evidence["status"], "BLOCKED")
        self.assertEqual(evidence["resource_disposition"], PARTIAL_DISPOSITION)
        self.assertEqual(
            evidence["member_profile_preparation"]["status"], "BLOCKED")
        self.assertEqual([row["bot_id"] for row in evidence["retained_bots"]],
                         [FRESH_DRIVER, FRESH_MEMBER])
        self.assertEqual(
            evidence["member_profile_release"]["status"], "NOT_RUN")

    def test_second_registration_failure_discloses_the_retained_first_bot(self):
        profile = base_profile()
        with patch(
                "bcs_e2e.fresh_fixture_pair.RemoteNetworkRegisterAdapter",
                FakeRegisterAdapter), \
             patch(
                "bcs_e2e.fresh_fixture_pair.execute_network_register_case",
                side_effect=[
                    register_execution(FRESH_DRIVER, "story-agent-driver"),
                    register_execution("", "", blocked=True),
                ]):
            with self.assertRaises(FreshFixtureProvisioningError) as raised:
                provision_fresh_owned_bot_pair(
                    profile=profile,
                    run_id=RUN_ID,
                    purpose="friend-connection-decisions",
                )

        evidence = raised.exception.evidence
        self.assertEqual(evidence["status"], "BLOCKED")
        self.assertEqual(evidence["resource_disposition"], PARTIAL_DISPOSITION)
        self.assertEqual(evidence["fresh_pair"]["driver"], {
            "bot_id": FRESH_DRIVER,
            "bot_name": "story-agent-driver",
        })
        self.assertIsNone(evidence["fresh_pair"]["member"])
        self.assertEqual(evidence["retained_bots"][0]["bot_id"], FRESH_DRIVER)
        self.assertEqual(evidence["steps"][1]["status"], "BLOCKED")
        self.assertNotIn("a" * 64, json.dumps(evidence))
        self.assertNotIn("b" * 64, json.dumps(evidence))

    def test_initial_receipt_is_in_flight_and_sanitized(self):
        receipt = initial_fresh_fixture_receipt(
            run_id=RUN_ID, purpose="friend-public-accept")
        self.assertEqual(receipt["status"], "IN_PROGRESS")
        self.assertEqual(receipt["resource_disposition"],
                         "FRESH_FIXTURE_PROVISIONING_IN_FLIGHT")
        self.assertNotIn("Cookie", json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
