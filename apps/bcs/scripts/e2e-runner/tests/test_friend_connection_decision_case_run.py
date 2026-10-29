"""Command and receipt tests for the dual friend-connection decision lifecycle."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.cli import main
from bcs_e2e.fresh_fixture_pair import (
    CONTRACT as FRESH_FIXTURE_CONTRACT,
    FreshFixturePair,
    FreshFixtureProvisioningError,
    initial_fresh_fixture_receipt,
)
from bcs_e2e.friend_connection_decision_case_run import (
    CREDENTIAL_KIND,
    dual_friend_connection_decision_case_run,
    RESOURCE_DISPOSITION,
)
from bcs_e2e.local_friend_connection_decision_contract import (
    CASE_ID,
    PUBLIC_OPERATIONS,
    friend_connection_decision_case_plan,
)
from bcs_e2e.profile import ConfigError
from bcs_e2e.route_bindings import (
    friend_connection_driver_connections_path,
    friend_connection_driver_requests_path,
    friend_connection_member_requests_path,
    FRIEND_CONNECTION_ACCEPT_PATH,
    FRIEND_CONNECTION_DECISION_CREATE_PATH,
    FRIEND_CONNECTION_REJECT_PATH,
    _EDGE_CASES,
)
from bcs_e2e.run_state import read

RUN_ID = "friend-decision-run-1"
DRIVER = "driver-bot"
MEMBER = "member-bot"


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="friend-decision-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id=DRIVER, owner_id="owner-1")
    value["actors"]["member"].update(bot_id=MEMBER, owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "friend-decision-local-pool"
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    value["relationships"]["friendship"] = "bounded-owned-bot-decisions"

    common = {
        "api_face": "public-v1",
        "auth": "human_cookie",
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
    return value


def profile_value() -> dict:
    return base_profile()


def expected_execution(*, status: str = "PASS") -> CaseExecution:
    plan = friend_connection_decision_case_plan()
    projections = (
        {
            "request_id": "reject-request",
            "status": "rejected",
            "friendship_visible": False,
            "from_actor_id": DRIVER,
            "to_actor_id": MEMBER,
        },
        {
            "request_id": "accept-request",
            "status": "approved",
            "friendship_visible": True,
            "from_actor_id": MEMBER,
            "to_actor_id": DRIVER,
        },
    )
    steps = []
    for index, requirement_id in enumerate(plan.requirement_ids):
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method="reject_friend_connection_request" if index == 0
                    else "accept_friend_connection_request",
            status="PASS" if status == "PASS" else "NOT_RUN",
            data=projections[index] if status == "PASS" else None,
            evidence={} if status == "PASS" else {},
            error_type=None,
        ))
    execution = CaseExecution(
        case_id=plan.case_id,
        parent_id=plan.parent_id,
        status="PASS" if status == "PASS" else "BLOCKED",
        steps=tuple(steps),
        completed_count=2 if status == "PASS" else 0,
        total_count=2,
        error_type=None if status == "PASS" else "RemoteFriendConnectionDecisionAdapterError",
        ready_to_execute=status == "PASS",
    )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class FriendConnectionDecisionCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def fresh_pair(self, profile: dict) -> FreshFixturePair:
        receipt = initial_fresh_fixture_receipt(
            run_id=RUN_ID, purpose="friend-connection-decisions")
        receipt.update({
            "status": "PASS",
            "resource_disposition": "FRESH_OWNED_BOT_PAIR_RETAINED_PUBLIC_V1_NO_DELETE_ROUTE",
            "fresh_pair": {
                "driver": {"bot_id": DRIVER, "bot_name": "story-agent-driver"},
                "member": {"bot_id": MEMBER, "bot_name": "story-agent-member"},
            },
        })
        return FreshFixturePair(profile=profile, evidence=receipt)

    def invoke(self, status: str = "PASS"):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution(status=status)
        with patch(
                "bcs_e2e.friend_connection_decision_case_run.provision_fresh_owned_bot_pair",
                return_value=self.fresh_pair(profile)), \
             patch(
                "bcs_e2e.friend_connection_decision_case_run.RemoteFriendConnectionDecisionAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.friend_connection_decision_case_run.execute_friend_connection_decision_case",
                lambda adapter: expected):
            report = dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected, self.state_dir

    def test_local_run_writes_sanitized_receipts_and_case_scope(self):
        report, profile, expected, state_dir = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["resource_disposition"], RESOURCE_DISPOSITION)
        self.assertEqual(report["credential_kind"], CREDENTIAL_KIND)
        self.assertEqual(report["case_scope"]["requirement_ids"],
                         ["FRIEND.protected-reject", "FRIEND.protected-accept"])
        self.assertEqual(report["case_scope"]["selected_obligations"],
                         ["protected-reject", "protected-accept"])
        self.assertEqual(report["case_scope"]["public_operations"],
                         list(dict.fromkeys(PUBLIC_OPERATIONS)))
        self.assertEqual(report["fresh_fixture"]["status"], "PASS")
        self.assertEqual(report["fresh_fixture"]["contract"], FRESH_FIXTURE_CONTRACT)
        self.assertEqual(
            report["fresh_fixture"]["resource_disposition"],
            "FRESH_OWNED_BOT_PAIR_RETAINED_PUBLIC_V1_NO_DELETE_ROUTE")
        self.assertEqual(read(state_dir / "friend-connection-decision-profile.json"), profile)
        self.assertEqual(
            read(state_dir / "friend-connection-decision-fresh-fixture.json"),
            report["fresh_fixture"])
        self.assertEqual(
            read(state_dir / "friend-connection-decision-binding.json")["case_id"],
            expected.case_id)
        self.assertEqual(
            read(state_dir / "friend-connection-decision-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("human-cookie-private-value", serialized)

    def test_blocked_run_never_claims_finalization(self):
        report, profile, expected, state_dir = self.invoke(status="BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["resource_disposition"], "UNKNOWN_OWNED_BOT_DECISIONS")
        self.assertEqual(report["case"]["completed_count"], 0)
        self.assertEqual(
            read(state_dir / "friend-connection-decision-run.json"), report)

    def test_fresh_fixture_failure_blocks_before_the_case_adapter(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        partial = initial_fresh_fixture_receipt(
            run_id=RUN_ID, purpose="friend-connection-decisions")
        partial.update({
            "status": "BLOCKED",
            "resource_disposition": "FRESH_OWNED_BOT_PAIR_PARTIAL_RETAINED",
            "retained_bots": [{"role": "driver", "bot_id": "fresh-driver"}],
        })
        with patch(
                "bcs_e2e.friend_connection_decision_case_run.provision_fresh_owned_bot_pair",
                side_effect=FreshFixtureProvisioningError(
                    "member fresh Bot registration failed", partial)), \
             patch(
                "bcs_e2e.friend_connection_decision_case_run.RemoteFriendConnectionDecisionAdapter",
                side_effect=AssertionError("case adapter must not be constructed")):
            report = dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["fresh_fixture"]["status"], "BLOCKED")
        self.assertEqual(
            report["resource_disposition"],
            "FRESH_OWNED_BOT_PAIR_PARTIAL_RETAINED")
        self.assertEqual(report["case"]["completed_count"], 0)
        self.assertEqual(
            read(self.state_dir / "friend-connection-decision-fresh-fixture.json"),
            report["fresh_fixture"])

    def test_profile_must_bind_the_exact_case_operations(self):
        profile = profile_value()
        profile["edge_case_bindings"].pop("friend_connection_decision.accept")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError,
                                    "exactly this mode's bounded bindings are required"):
            dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_must_not_carry_a_bot_authorization_reference(self):
        profile = profile_value()
        profile["auth"]["header_env"]["Authorization"] = "BCS_E2E_DRIVER_BOT_TOKEN"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError,
                                    "bounded friendship effects must send only the Human Cookie"):
            dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_requires_bounded_friendship_effect_admission(self):
        profile = profile_value()
        profile["relationships"]["friendship"] = "unchanged"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError,
                                    "bounded friendship effects are not admitted"):
            dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_friend_connection_decision_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_friend_connection_decision_lifecycle(self):
        profile = profile_value()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-friend-connection-decisions-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_friend_connection_decision_case_run",
                return_value={"status": "PASS"}) as execute, \
             redirect_stdout(io.StringIO()) as stdout:
            rc = main(arguments)
        self.assertEqual(rc, 0)
        execute.assert_called_once_with(
            profile_path=self.profile_path,
            state_dir=self.state_dir,
            run_id=RUN_ID,
            allow_write=True,
            confirm_run_id=RUN_ID,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
