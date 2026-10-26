"""Cross-environment Workspace product-CLI runner tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.workspace_cli_profile import (
    validate_workspace_cli_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError


DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_workspace-01"
SESSION = GROUP + ":01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"


def profile_value(*, gateway=False):
    local = not gateway
    value = {
        "schema_version": 1,
        "contract": "dual-workspace-cli-profile-v1",
        "environment": {"tier": "local" if local else "preprod",
                         "id": "workspace-cli-local" if local else "workspace-cli-preprod",
                         "expected_bcs_env": "local" if local else "pre"},
        "entrypoint": {"kind": "singlebox" if local else "gateway",
                        "origin": "http://127.0.0.1:21000" if local else "https://gateway.example.com",
                        "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {
            "driver": {"bot_id": DRIVER, "owner_id": "owner-1", "dedicated": True, "visibility": "public"},
            "member": {"bot_id": MEMBER, "owner_id": "owner-1", "dedicated": True, "visibility": "public"},
        },
        "auth": ({"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
                 if local else {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}),
        "relationships": {"group": "bounded-owned-bots-retained-cli-create-get",
                          "initial_session": "retained", "cleanup": "none",
                          "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "workspace-cli-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 65536,
                    "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "group", "api_face": "legacy"},
    }
    return value


class WorkspaceCliRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}
        self.state = self.root / "state"
        self.state.mkdir()

    def run_execute(self, operation, *, gateway=False, returncode=0,
                    create=None, get=None):
        profile = validate_workspace_cli_profile(profile_value(gateway=gateway))
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                     binary_sha256=self.digest, state_dir=self.state)
        command: list[str] = []
        context_state: dict[str, object] = {}

        def fake_capture(cmd, **kwargs):
            command.extend(cmd)
            environment = kwargs["environment"]
            context = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            context_state["context"] = json.loads(context.read_text())
            context_state["cookie"] = environment.get("BCS_COOKIE")
            if operation == "group.create":
                raw = create.encode() if create is not None else (
                    f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
                    f"  Participants: {DRIVER},{MEMBER}\n  Session: {SESSION}\n"
                ).encode()
            else:
                raw = get.encode() if get is not None else json.dumps({
                    "id": GROUP, "driver_bot": DRIVER, "status": "active",
                    "participants": [{"bot_uuid": DRIVER}, {"bot_uuid": MEMBER}],
                    "label": "bcs-e2e-workspace-cli-create-get",
                }).encode()
            return subprocess.CompletedProcess(cmd, returncode, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture) as capture:
            result = runner.execute(operation, group_id=GROUP if operation == "group.get" else None)
            capture.assert_called_once()
        return result, command, context_state, runner

    def test_public_facing_local_create_command(self):
        result, command, context, _ = self.run_execute("group.create")
        self.assertEqual(command, [str(Path(self.binary).resolve()), "--url", "http://127.0.0.1:21000",
                                    "--json", "--log-level", "error", "create-group",
                                    "--driver", DRIVER, "--participants", f"{DRIVER},{MEMBER}",
                                    "--topic", "bcs-e2e-workspace-cli-create-get"])
        self.assertEqual(context["context"], {"token": TOKEN, "bot_uuid": DRIVER,
                                                "api_base_url": "http://127.0.0.1:21000"})
        self.assertEqual(result["data"], {"group_id": GROUP, "driver_bot": DRIVER,
                                            "participants": [DRIVER, MEMBER], "session_id": SESSION})
        self.assertEqual(result["evidence"]["transport"], "PRODUCT_CLI")
        self.assertNotIn(TOKEN, json.dumps(result, sort_keys=True))

    def test_cookie_gateway_get_command(self):
        result, command, context, _ = self.run_execute("group.get", gateway=True)
        self.assertEqual(command[-6:], ["--json", "--log-level", "error", "get-group",
                                         "--id", GROUP])
        self.assertEqual(context["cookie"], COOKIE)
        self.assertEqual(context["context"], {"token": "", "bot_uuid": DRIVER,
                                               "api_base_url": "https://gateway.example.com"})
        self.assertNotIn(COOKIE, json.dumps(result, sort_keys=True))

    def test_bad_create_output_blocks(self):
        bad_outputs = [
            f"Group created:\n",
            f"Group created:\n  ID: {GROUP}\n  Driver: wrong\n"
            f"  Participants: {DRIVER},{MEMBER}\n  Session: {SESSION}\n",
            f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},other\n  Session: {SESSION}\n",
            f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n  Session: other:01020304\n",
        ]
        for output in bad_outputs:
            with self.subTest(output=output):
                with self.assertRaisesRegex(WorkspaceCliRunnerError, "output"):
                    self.run_execute("group.create", create=output)

    def test_operation_is_one_shot(self):
        _, _, _, runner = self.run_execute("group.create")
        with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
            runner.execute("group.create")

    def test_nonzero_cli_blocks(self):
        with self.assertRaisesRegex(WorkspaceCliRunnerError, "nonzero"):
            self.run_execute("group.create", returncode=1)

    def test_missing_credential_blocks_before_process(self):
        for gateway in (False, True):
            with self.subTest(gateway=gateway):
                profile = validate_workspace_cli_profile(profile_value(gateway=gateway))
                runner = WorkspaceCliRunner(profile, binary=self.binary,
                                            binary_sha256=self.digest, state_dir=self.state)
                with patch.dict(os.environ, {}, clear=True), \
                     patch("bcs_e2e.workspace_cli_runner.capture") as capture:
                    with self.assertRaisesRegex(WorkspaceCliRunnerError, "(?:owned Bot credential|Human Cookie) is absent"):
                        runner.execute("group.create")
                capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
