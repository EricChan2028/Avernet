"""Command and receipt tests for the dual friend auto-acceptance lifecycle."""

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
    PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE,
    FreshFixturePair,
    FreshFixtureProvisioningError,
    initial_fresh_fixture_receipt,
)
from bcs_e2e.friend_public_accept_case_run import (
    CREDENTIAL_KIND,
    dual_friend_public_accept_case_run,
    RESOURCE_DISPOSITION,
)
from bcs_e2e.local_friend_public_accept_contract import CASE_ID
from bcs_e2e.profile import ConfigError
from bcs_e2e.route_bindings import (
    FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH,
    friend_connection_public_accept_driver_connections_path,
)
from bcs_e2e.run_state import read

RUN_ID = "friend-public-accept-run-1"
DRIVER = "driver-bot"
MEMBER = "member-bot"


def base_profile() -> dict:
    value = json.loads(
        (ROOT / "profiles/local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(
        id="friend-public-local", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id=DRIVER, owner_id="owner-1")
    value["actors"]["member"].update(bot_id=MEMBER, owner_id="owner-1")
    value["observer"].update(kind="local-sqlite", server="owned-local",
                             database="owned-local")
    value["policy"]["fixture_pool"] = "friend-public-local-pool"
    value["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
    value["relationships"]["friendship"] = "bounded-owned-bot-public-acceptance"

    common = {
        "api_face": "public-v1", "auth": "human_cookie",
        "response": "public-envelope", "traceparent": "disabled",
    }
    value["edge_case_bindings"] = {
        "friend_connection_public_accept.create": {
            **common, "method": "POST",
            "path": FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH,
            "from_actor_id": DRIVER, "to_actor_id": MEMBER,
        },
        "friend_connection_public_accept.driver_connections.read": {
            **common, "method": "GET",
            "path": friend_connection_public_accept_driver_connections_path(value),
            "actor_id": DRIVER,
        },
    }
    return value


def expected_execution(*, status: str = "PASS") -> CaseExecution:
    accept_data = {
        "status": "approved",
        "auto_accepted": True,
        "friendship_visible_before": False,
        "friendship_visible_after": True,
        "request_count": 2,
        "edge_count": 2,
        "from_actor_id": DRIVER,
        "to_actor_id": MEMBER,
    }
    list_data = {
        "driver_actor_id": DRIVER,
        "friend_actor_id": MEMBER,
        "list_contains_friend": True,
        "friend_count": 1,
    }
    steps = [
        StepOutcome(
            requirement_id="FRIEND.public-accept",
            method="auto_accept_public_friend_connection",
            status="PASS" if status == "PASS" else "NOT_RUN",
            data=accept_data if status == "PASS" else None,
            evidence={} if status == "PASS" else {},
            error_type=None,
        ),
        StepOutcome(
            requirement_id="FRIEND.list",
            method="read_established_friend_list",
            status="PASS" if status == "PASS" else "NOT_RUN",
            data=list_data if status == "PASS" else None,
            evidence={} if status == "PASS" else {},
            error_type=None,
        ),
    ]
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id="story_user_builds_trusted_team",
        status="PASS" if status == "PASS" else "BLOCKED",
        steps=tuple(steps),
        completed_count=2 if status == "PASS" else 0,
        total_count=2,
        error_type=None if status == "PASS" else "RemoteFriendPublicAcceptAdapterError",
        ready_to_execute=status == "PASS",
    )
    execution.validate()
    return execution


class FakeAdapter:
    def __init__(self, profile):
        self.profile = profile


class FriendPublicAcceptCaseRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.profile_path = self.root / "profile.json"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def fresh_pair(self, profile: dict) -> FreshFixturePair:
        receipt = initial_fresh_fixture_receipt(
            run_id=RUN_ID, purpose="friend-public-accept",
            member_profile=PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE)
        receipt.update({
            "status": "PASS",
            "resource_disposition": "FRESH_OWNED_BOT_PAIR_RETAINED_PUBLIC_V1_NO_DELETE_ROUTE",
            "fresh_pair": {
                "driver": {"bot_id": DRIVER, "bot_name": "story-agent-driver"},
                "member": {"bot_id": MEMBER, "bot_name": "story-agent-member"},
            },
        })
        receipt["member_profile_preparation"].update({
            "status": "PASS",
            "baseline": {
                "visibility": "protected",
                "friend_check_in_strategy": "APPROVAL",
            },
            "observed": {
                "bot_id": MEMBER,
                "visibility": "public",
                "friend_check_in_strategy": "OPEN",
                "created_by": "owner-1",
            },
            "requests": [],
        })
        receipt["member_profile_release"]["requested"] = {
            "visibility": "protected",
            "friend_check_in_strategy": "APPROVAL",
        }
        return FreshFixturePair(profile=profile, evidence=receipt)

    @staticmethod
    def release_pair(*, profile: dict, evidence: dict, member_profile: dict,
                     on_progress=None) -> dict:
        released = json.loads(json.dumps(evidence))
        released["member_profile_release"].update({
            "status": "PASS",
            "requested": {"visibility": "protected",
                          "friend_check_in_strategy": "APPROVAL"},
            "observed": {"bot_id": MEMBER, "visibility": "protected",
                         "friend_check_in_strategy": "APPROVAL",
                         "created_by": "owner-1"},
            "requests": [],
        })
        if on_progress is not None:
            on_progress(released)
        return released

    def invoke(self, status: str = "PASS"):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        expected = expected_execution(status=status)
        with patch(
                "bcs_e2e.friend_public_accept_case_run.provision_fresh_owned_bot_pair",
                return_value=self.fresh_pair(profile)) as provision, \
             patch(
                "bcs_e2e.friend_public_accept_case_run.release_fresh_owned_bot_pair",
                side_effect=self.release_pair) as release, \
             patch(
                "bcs_e2e.friend_public_accept_case_run.RemoteFriendPublicAcceptAdapter",
                FakeAdapter), \
             patch(
                "bcs_e2e.friend_public_accept_case_run.execute_friend_public_accept_case",
                lambda adapter: expected):
            report = dual_friend_public_accept_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        return report, profile, expected, self.state_dir, provision, release

    def test_local_run_writes_sanitized_receipts_and_case_scope(self):
        report, profile, expected, state_dir, provision, release = self.invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["resource_disposition"], RESOURCE_DISPOSITION)
        self.assertEqual(report["credential_kind"], CREDENTIAL_KIND)
        self.assertEqual(report["case_scope"]["requirement_ids"],
                         ["FRIEND.public-accept", "FRIEND.list"])
        self.assertEqual(report["case_scope"]["selected_obligations"],
                         ["public-accept", "list"])
        self.assertEqual(report["fresh_fixture"]["status"], "PASS")
        self.assertEqual(report["fresh_fixture"]["contract"], FRESH_FIXTURE_CONTRACT)
        self.assertEqual(
            report["fresh_fixture"]["resource_disposition"],
            "FRESH_OWNED_BOT_PAIR_RETAINED_PUBLIC_V1_NO_DELETE_ROUTE")
        self.assertEqual(report["cleanup"]["status"], "PASS")
        self.assertEqual(report["cleanup"]["member_profile_release"], "PASS")
        self.assertEqual(
            provision.call_args.kwargs["member_profile"],
            PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE)
        self.assertEqual(
            release.call_args.kwargs["member_profile"],
            PUBLIC_AUTO_ACCEPT_MEMBER_PROFILE)
        self.assertEqual(release.call_args.kwargs["profile"], profile)
        self.assertEqual(read(state_dir / "friend-public-accept-profile.json"), profile)
        self.assertEqual(
            read(state_dir / "friend-public-accept-fresh-fixture.json"),
            report["fresh_fixture"])
        self.assertEqual(
            read(state_dir / "friend-public-accept-binding.json")["case_id"], expected.case_id)
        self.assertEqual(read(state_dir / "friend-public-accept-run.json"), report)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE", serialized)
        self.assertNotIn("human-cookie-private-value", serialized)

    def test_blocked_run_never_claims_finalization(self):
        report, profile, expected, state_dir, provision, release = self.invoke(
            status="BLOCKED")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["resource_disposition"], "UNKNOWN_OWNED_BOT_PUBLIC_ACCEPTANCE")
        self.assertEqual(report["case"]["completed_count"], 0)
        self.assertEqual(read(state_dir / "friend-public-accept-run.json"), report)

    def test_fresh_fixture_failure_blocks_before_the_case_adapter(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        partial = initial_fresh_fixture_receipt(
            run_id=RUN_ID, purpose="friend-public-accept")
        partial.update({
            "status": "BLOCKED",
            "resource_disposition": "FRESH_OWNED_BOT_PAIR_PARTIAL_RETAINED",
            "retained_bots": [{"role": "driver", "bot_id": "fresh-driver"}],
        })
        with patch(
                "bcs_e2e.friend_public_accept_case_run.provision_fresh_owned_bot_pair",
                side_effect=FreshFixtureProvisioningError(
                    "member fresh Bot registration failed", partial)), \
             patch(
                "bcs_e2e.friend_public_accept_case_run.RemoteFriendPublicAcceptAdapter",
                side_effect=AssertionError("case adapter must not be constructed")):
            report = dual_friend_public_accept_case_run(
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
            read(self.state_dir / "friend-public-accept-fresh-fixture.json"),
            report["fresh_fixture"])

    def test_profile_must_bind_the_exact_case_operations(self):
        profile = base_profile()
        profile["edge_case_bindings"].pop("friend_connection_public_accept.driver_connections.read")
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(
                ConfigError, "exactly this mode's bounded bindings are required"):
            dual_friend_public_accept_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_must_not_carry_a_bot_authorization_reference(self):
        profile = base_profile()
        profile["auth"]["header_env"]["Authorization"] = "BCS_E2E_DRIVER_BOT_TOKEN"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(
                ConfigError, "bounded friendship effects must send only the Human Cookie"):
            dual_friend_public_accept_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_profile_requires_bounded_public_acceptance_admission(self):
        profile = base_profile()
        profile["relationships"]["friendship"] = "unchanged"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(
                ConfigError, "bounded friendship effects are not admitted"):
            dual_friend_public_accept_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_write_permission_and_fresh_state_are_enforced(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "write permission"):
            dual_friend_public_accept_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=False,
                confirm_run_id=RUN_ID,
            )
        self.state_dir.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_friend_public_accept_case_run(
                profile_path=self.profile_path,
                state_dir=self.state_dir,
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )

    def test_cli_dispatch_uses_the_friend_public_accept_lifecycle(self):
        profile = base_profile()
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        arguments = [
            "dual-friend-public-accept-run",
            "--profile", str(self.profile_path),
            "--state-dir", str(self.state_dir),
            "--run-id", RUN_ID,
            "--confirm-run-id", RUN_ID,
            "--allow-write",
        ]
        with patch(
                "bcs_e2e.cli.dual_friend_public_accept_case_run",
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
