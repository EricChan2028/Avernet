"""Bounded product-CLI runner tests; no real network or service request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys_path_marker = None  # keep import order explicit and readable.
import sys
sys_path_marker = sys.path.insert(0, str(ROOT))
from bcs_e2e.friend_cli_profile import validate_friend_cli_profile
from bcs_e2e.friend_cli_runner import FriendCliRunner, FriendCliRunnerError

ROOT_GUARD = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
DRIVER = "driver-bot"
MEMBER = "member-bot"
COOKIE = "human-cookie-private-value"
DRIVER_TOKEN = "driver-token-private-value"
MEMBER_TOKEN = "member-token-private-value"


def profile_value(*, gateway=False):
    return {
        "schema_version": 1, "contract": "dual-friend-cli-profile-v1",
        "environment": {"tier": "preprod" if gateway else "local",
                         "id": "preprod" if gateway else "local",
                         "expected_bcs_env": "pre" if gateway else "local"},
        "entrypoint": {"kind": "gateway" if gateway else "singlebox",
                       "origin": "https://gateway.example.com" if gateway else "http://127.0.0.1:21000",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {"driver": {"bot_id": DRIVER, "owner_id": "owner-1", "dedicated": True,
                              "visibility": "public"},
                   "member": {"bot_id": MEMBER, "owner_id": "owner-1", "dedicated": True,
                              "visibility": "protected"}},
        "auth": ({"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"} if gateway else
                 {"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN",
                  "member_token_env": "BCS_E2E_MEMBER_BOT_TOKEN"}),
        "relationships": {"friendship": "bounded-owned-bot-cli-acceptance",
                          "baseline": "no-pending-driver-member",
                          "visibility": "member-protected",
                          "retention": "request-history-and-friendship"},
        "policy": {"fixture_pool": "friend-cli-pool", "exclusive_use_confirmed": True,
                   "timeout_seconds": 10, "max_response_bytes": 65536,
                   "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "friend", "api_face": "legacy"},
    }


def output_for(operation):
    if operation == "friend.request":
        return {"success": True, "data": {
            "id": "request-1", "from_bot": DRIVER, "to_bot": MEMBER,
            "status": "pending"}}
    if operation == "friend.accept":
        return {"success": True}
    if operation == "friend.list":
        return [{"bot_uuid": MEMBER}]
    return []


class FriendCliRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.binary = self.root / "bcs-cli"
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o755)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.env = {
            "BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_TOKEN,
            "BCS_E2E_MEMBER_BOT_TOKEN": MEMBER_TOKEN,
            "BCS_E2E_HUMAN_COOKIE": COOKIE,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_case_guard_accepts_the_runner_contract(self):
        result = subprocess.run([sys.executable, str(ROOT_GUARD),
                                  str(ROOT / "bcs_e2e/friend_cli_runner.py")],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def run_execute(self, operation, *, gateway=False, returncode=0,
                    body=None, leak=False):
        profile = validate_friend_cli_profile(profile_value(gateway=gateway))
        runner = FriendCliRunner(profile, binary=self.binary,
                                 binary_sha256=self.digest, state_dir=self.root)
        command: list[str] = []
        context_state: dict[str, object] = {}

        def fake_capture(cmd, **kwargs):
            command.extend(cmd)
            environment = kwargs["environment"]
            home = Path(environment["HOME"])
            context = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            context_state["context"] = json.loads(context.read_text())
            context_state["cookie"] = environment.get("BCS_COOKIE")
            context_state["environment"] = environment
            context_state["cwd"] = kwargs["cwd"]
            raw = json.dumps(output_for(operation)).encode()
            if leak:
                raw = json.dumps({"secret": COOKIE}).encode()
            return subprocess.CompletedProcess(cmd, returncode, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.friend_cli_runner.capture", side_effect=fake_capture) as capture:
            result = runner.execute(operation, request_id="request-1"
                                    if operation == "friend.accept" else None)
            self.assertEqual(capture.call_count, 1)
        return result, command, context_state, runner

    def test_cookie_profile_uses_private_context_and_exact_friend_command(self):
        result, command, context, _ = self.run_execute("friend.requests.before", gateway=True)
        self.assertEqual(command, [str(self.binary), "--url", "https://gateway.example.com",
                                    "--json", "--log-level", "error", "friend", "requests",
                                    "--direction", "received", "--status", "pending"])
        self.assertEqual(context["cookie"], COOKIE)
        self.assertEqual(context["context"], {"token": "", "bot_uuid": MEMBER,
                                               "api_base_url": "https://gateway.example.com"})
        self.assertEqual(context["environment"]["tc_sdb_nenv"], "production")
        self.assertEqual(result["data"], [])
        evidence = result["evidence"]
        self.assertEqual(evidence["operation"], "friend.requests.before")
        self.assertEqual(evidence["caller_role"], "member")
        self.assertEqual(evidence["credential_kind"], "human_cookie")
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(COOKIE, serialized)
        self.assertFalse(Path(context["cwd"]).exists())

    def test_local_profile_uses_role_token_and_cleans_private_context(self):
        result, command, context, _ = self.run_execute("friend.request")
        self.assertIn("--bot-uuid", command)
        self.assertEqual(command[command.index("--bot-uuid") + 1], MEMBER)
        self.assertEqual(context["context"], {"token": DRIVER_TOKEN, "bot_uuid": DRIVER,
                                               "api_base_url": "http://127.0.0.1:21000"})
        self.assertNotIn("BCS_COOKIE", context)
        self.assertNotIn("tc_sdb_nenv", context["environment"])
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(DRIVER_TOKEN, serialized)
        self.assertFalse(Path(context["cwd"]).exists())

    def test_accept_command_binds_the_exact_request_and_member_context(self):
        result, command, context, _ = self.run_execute("friend.accept")
        self.assertIn("request-1", command)
        self.assertEqual(context["context"]["bot_uuid"], MEMBER)
        self.assertEqual(result["data"], {"success": True})

    def test_operation_is_one_shot(self):
        _, _, _, runner = self.run_execute("friend.list")
        with self.assertRaisesRegex(FriendCliRunnerError, "already executed"):
            runner.execute("friend.list")

    def test_nonzero_output_is_bounded_redacted_and_journaled(self):
        profile = validate_friend_cli_profile(profile_value(gateway=True))
        runner = FriendCliRunner(profile, binary=self.binary,
                                 binary_sha256=self.digest, state_dir=self.root)

        def fake_capture(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, b'{"status":"auth_failed"}',
                                                 b"stderr-marker")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.friend_cli_runner.capture", side_effect=fake_capture):
            with self.assertRaises(FriendCliRunnerError) as caught:
                runner.execute("friend.requests.before")
        message = str(caught.exception)
        self.assertIn("nonzero", message)
        self.assertIn("stderr-marker", message)

        redacted = json.dumps({"echo": COOKIE}).encode() + b" " + b"a" * 700

        def leaking_capture(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, redacted, b"")

        bounded = FriendCliRunner(profile, binary=self.binary,
                                  binary_sha256=self.digest, state_dir=self.root)
        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.friend_cli_runner.capture",
                   side_effect=leaking_capture):
            with self.assertRaises(FriendCliRunnerError) as caught:
                bounded.execute("friend.requests.before")
        bounded_message = str(caught.exception)
        self.assertNotIn(COOKIE, bounded_message)
        self.assertLessEqual(len(bounded_message), 600)

    def test_nonzero_or_leaking_cli_blocks(self):
        with self.assertRaisesRegex(FriendCliRunnerError, "nonzero"):
            self.run_execute("friend.request", returncode=1)
        with self.assertRaisesRegex(FriendCliRunnerError, "credential"):
            self.run_execute("friend.requests.before", gateway=True, leak=True)

    def test_missing_cookie_blocks_before_process(self):
        profile = validate_friend_cli_profile(profile_value(gateway=True))
        runner = FriendCliRunner(profile, binary=self.binary,
                                 binary_sha256=self.digest, state_dir=self.root)
        environment = {k: v for k, v in os.environ.items() if k != "BCS_E2E_HUMAN_COOKIE"}
        with patch.dict(os.environ, environment, clear=True), \
             patch("bcs_e2e.friend_cli_runner.capture") as capture:
            with self.assertRaisesRegex(FriendCliRunnerError, "Human Cookie is absent"):
                runner.execute("friend.requests.before")
            capture.assert_not_called()

