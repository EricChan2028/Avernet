"""Cross-environment first Session-file CLI core tests; no live request."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.session_file_core_contract import (
    CASE_ID,
    RemoteSessionFileCoreAdapter,
    execute_session_file_core_case,
    session_file_core_adaptability,
)
from bcs_e2e.workspace_cli_profile import (
    ConfigError,
    validate_session_file_core_profile,
)
from bcs_e2e.workspace_cli_runner import WorkspaceCliRunner, WorkspaceCliRunnerError
from bcs_e2e.file_transport import RemoteFileClient

DRIVER = "driver-bot"
MEMBER = "member-bot"
GROUP = "bcs_grp_session-file-core-01"
INITIAL_SESSION = GROUP + ":01020304"
EXPLICIT_SESSION = GROUP + ":02020304"
FILE = "file-01020304"
TOKEN = "driver-token-secret"
COOKIE = "cookie-secret"
SHARE_TOKEN = "fake-share-token"
SHARED_ROUTE = "/sessions/shared-file/content?token=" + SHARE_TOKEN
SHARE_URL = "http://127.0.0.1:21000" + SHARED_ROUTE
GATEWAY_SHARE_URL = "https://share.example.com" + SHARED_ROUTE
EXPIRES_AT = 1_772_000_000
SOURCE = "bcs-e2e-session-file-source.txt"


def profile_value(*, gateway=False, mutate=None):
    value = {
        "schema_version": 1,
        "contract": "dual-session-file-core-profile-v1",
        "environment": {"tier": "local" if not gateway else "preprod",
                        "id": "session-file-local" if not gateway else "session-file-preprod",
                        "expected_bcs_env": "local" if not gateway else "pre"},
        "entrypoint": {"kind": "singlebox" if not gateway else "gateway",
                       "origin": "http://127.0.0.1:21000" if not gateway else "https://gateway.example.com",
                       "api_face": "legacy", "manifest_face": "legacy"},
        "human": {"user_id": "owner-1"},
        "actors": {"driver": {"bot_id": DRIVER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"},
                   "member": {"bot_id": MEMBER, "owner_id": "owner-1",
                              "dedicated": True, "visibility": "public"}},
        "auth": ({"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}
                 if not gateway else {"kind": "human-cookie-with-owned-driver-bot-token",
                                      "cookie_env": "BCS_E2E_HUMAN_COOKIE",
                                      "driver_token_env": "BCS_E2E_DRIVER_BOT_TOKEN"}),
        "relationships": {"group": "bounded-owned-bots-retained-session-file",
                          "initial_session": "retained", "explicit_session": "retained",
                          "file": "retained", "share_link": "retained",
                          "cleanup": "none", "participant_mode": "driver-member"},
        "policy": {"fixture_pool": "session-file-core-pool", "exclusive_use_confirmed": True,
                    "timeout_seconds": 10, "max_response_bytes": 65536,
                    "failure_retention_hours": 24},
        "cli": {"transport": "product-cli", "command_family": "group", "api_face": "legacy"},
    }
    if gateway:
        value["file_transfer"] = {
            "allowed_share_origins": ["https://share.example.com"],
            "allowed_storage_origins": ["https://storage.example.com"],
        }
    if mutate is not None:
        mutate(value)
    return value


def file_path_prefix(*, gateway=False):
    return "" if gateway else "/api/v1/collaboration"


def metadata_result(*, gateway=False):
    return ({"file_id": FILE}, {
        "operation": "file.metadata",
        "method": "GET",
        "path": file_path_prefix(gateway=gateway) + "/sessions/" + EXPLICIT_SESSION
                + "/files/" + FILE,
        "target_kind": "product",
        "submitted_request_id": "e2e-file-metadata-1",
        "http_status": 200,
        "request_id_echoed": True,
    })


def shared_metadata_result():
    return ({
        "file_id": FILE,
        "file_name": SOURCE,
        "mime_type": "text/plain",
        "size": 29,
        "sha256": None,
        "owner": {"actor_kind": "Bot", "actor_id": DRIVER},
        "storage_backend": "local",
        "status": "Ready",
        "created_at": 1,
        "updated_at": 1,
    }, {
        "operation": "file.shared_metadata",
        "method": "GET",
        "path": "/sessions/shared-file?token=[REDACTED]",
        "target_kind": "product",
        "submitted_request_id": "e2e-shared-metadata-1",
        "http_status": 200,
        "request_id_echoed": True,
    })


def shared_content_result(*, gateway=False):
    return (b"bcs-e2e-session-file-payload\n", {
        "operation": "file.shared_download",
        "method": "GET",
        "path": file_path_prefix(gateway=gateway) + "/sessions/shared-file/content?token=[REDACTED]",
        "target_kind": "product",
        "submitted_request_id": "e2e-shared-content-1",
        "http_status": 200,
        "request_id_echoed": True,
    })


def file_absence_result(*, gateway=False):
    return {
        "operation": "file.verify_deleted", "method": "GET",
        "path": file_path_prefix(gateway=gateway) + "/sessions/" + EXPLICIT_SESSION + "/files/" + FILE,
        "target_kind": "product", "submitted_request_id": "e2e-file-absence-1",
        "http_status": 404, "request_id_echoed": True,
    }


def shared_metadata_absence_result():
    return {
        "operation": "file.shared_metadata_verify_deleted", "method": "GET",
        "path": "/sessions/shared-file?token=[REDACTED]", "target_kind": "product",
        "submitted_request_id": "e2e-shared-metadata-absence-1",
        "http_status": 404,
    }


def shared_content_absence_result(*, gateway=False):
    return {
        "operation": "file.shared_verify_deleted", "method": "GET",
        "path": "/api/v1/collaboration/sessions/shared-file/content?token=[REDACTED]",
        "target_kind": "product", "submitted_request_id": "e2e-shared-absence-1",
        "http_status": 404, "request_id_echoed": True,
    }


def create_output():
    return (f"Group created:\n  ID: {GROUP}\n  Driver: {DRIVER}\n"
            f"  Participants: {DRIVER},{MEMBER}\n  Session: {INITIAL_SESSION}\n").encode()


def json_output(value):
    return json.dumps(value).encode()


def outputs(*, session=True, session_status="active", upload=True, files=True,
            file_status="ready", capabilities=None, download=True, share=True,
            delete=True, gateway=False):
    upload_value = {
        "file_id": FILE, "file_name": SOURCE, "size": 29,
        "status": file_status,
    }
    list_value = {
        "total": 1,
        "items": [{"file_id": FILE, "file_name": SOURCE, "size": 29,
                   "status": file_status}],
    }
    storage = capabilities if capabilities is not None else ("local" if not gateway else "baas")
    if gateway:
        capability_value = {
            "storage": storage, "max_size": 209_715_200,
            "inline_view": True, "presign_upload": True, "presign_download": True,
        }
    else:
        capability_value = {
            "storage": storage, "max_file_size_bytes": 10_000_000,
            "file_uploads": True,
        }
    download_value = {"out": str(Path("/unused-download-target"))}
    share_value = {
        "share_token": SHARE_TOKEN,
        "share_url": GATEWAY_SHARE_URL if gateway else SHARE_URL,
        "expires_at": EXPIRES_AT,
    }
    delete_value = b"null\n" if delete else b"{}\n"
    return (
        json_output({
            "session_id": EXPLICIT_SESSION, "session_kind": "chat",
            "status": session_status,
        }) if session else b"{}",
        json_output(upload_value) if upload else b"{}",
        json_output(list_value) if files else b"{}",
        json_output(capability_value),
        json_output(download_value) if download else b"{}",
        json_output(share_value) if share else b"{}",
        delete_value,
    )


class SessionFileCoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.binary = self.root / "bcs-cli"
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {"BCS_E2E_DRIVER_BOT_TOKEN": TOKEN, "BCS_E2E_HUMAN_COOKIE": COOKIE}

    def run_case(self, *, gateway=False, session=None, session_status="active",
                 upload=None, files=None, file_status="ready", capabilities=None,
                 metadata=None, download=None, share=None,
                 shared_metadata=None, shared=None, delete=None,
                 file_absent=None, shared_metadata_absent=None,
                 shared_content_absent=None, returncode=0):
        profile = validate_session_file_core_profile(profile_value(gateway=gateway))
        run_state = self.state / f"run-{uuid.uuid4().hex}"
        run_state.mkdir()
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest,
                                    state_dir=run_state)
        (session_raw, upload_raw, files_raw, capability_raw, download_raw,
         share_raw, delete_raw) = outputs(
            session=session if session is not None else True,
            session_status=session_status,
            upload=upload if upload is not None else True,
            files=files if files is not None else True,
            file_status=file_status,
            capabilities=capabilities,
            download=download if download is not None else True,
            share=share if share is not None else True,
            delete=delete if delete is not None else True,
            gateway=gateway)
        commands = []
        contexts = []

        def fake_metadata(_client, session_id, file_id):
            data, record = metadata_result(gateway=gateway)
            if metadata is False:
                data = {}
            if session_id != EXPLICIT_SESSION or file_id != FILE:
                raise AssertionError("session-file metadata exact binding")
            return data, record

        def fake_shared_metadata(_client, token):
            data, record = shared_metadata_result()
            if shared_metadata is False:
                data["session_id"] = EXPLICIT_SESSION
            binding = _client.share_targets.get(token)
            if binding != (GATEWAY_SHARE_URL if gateway else SHARE_URL):
                raise AssertionError("session-file share binding is not exact")
            if token != SHARE_TOKEN:
                raise AssertionError("session-file shared metadata exact token")
            return data, record

        def fake_shared_download(_client, token):
            body, record = shared_content_result(gateway=gateway)
            if shared is False:
                body = b"wrong payload"
            if token != SHARE_TOKEN:
                raise AssertionError("session-file shared download exact token")
            return body, record

        def fake_verify_deleted(_client, session_id, file_id):
            record = file_absence_result(gateway=gateway)
            if file_absent is False:
                record["http_status"] = 200
            if session_id != EXPLICIT_SESSION or file_id != FILE:
                raise AssertionError("session-file deletion exact binding")
            return None, record

        def fake_shared_metadata_verify_deleted(_client, token):
            record = shared_metadata_absence_result()
            if shared_metadata_absent is False:
                record["http_status"] = 200
            if token != SHARE_TOKEN:
                raise AssertionError("session-file shared metadata absence exact token")
            return None, record

        def fake_shared_content_verify_deleted(_client, token):
            record = shared_content_absence_result(gateway=gateway)
            if shared_content_absent is False:
                record["http_status"] = 200
            if token != SHARE_TOKEN:
                raise AssertionError("session-file shared content absence exact token")
            return None, record

        def fake_capture(cmd, **kwargs):
            commands.append(cmd)
            environment = kwargs["environment"]
            context_path = Path(environment["BOT_DATA_DIR"]) / ".bcs" / "session.json"
            contexts.append((json.loads(context_path.read_text()),
                              environment.get("BCS_COOKIE")))
            if "create-group" in cmd:
                raw = create_output()
            elif "session" in cmd and "create" in cmd:
                raw = session_raw
            elif "file" in cmd and "upload" in cmd:
                raw = upload_raw
            elif "file" in cmd and "list" in cmd:
                raw = files_raw
            elif "file" in cmd and "capabilities" in cmd:
                raw = capability_raw
            elif "file" in cmd and "download" in cmd:
                if download is None or download:
                    out_argument = cmd[cmd.index("--out") + 1]
                    Path(out_argument).write_bytes(b"bcs-e2e-session-file-payload\n")
                    raw = json_output({"out": out_argument})
                else:
                    raw = download_raw
            elif "file" in cmd and "share" in cmd:
                raw = share_raw
            elif "file" in cmd and "delete" in cmd:
                raw = delete_raw
            else:
                raw = b"{}"
            return subprocess.CompletedProcess(cmd, returncode, raw, b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture), \
             patch.object(RemoteFileClient, "metadata", fake_metadata), \
             patch.object(RemoteFileClient, "shared_metadata", fake_shared_metadata), \
             patch.object(RemoteFileClient, "shared_download", fake_shared_download), \
             patch.object(RemoteFileClient, "verify_deleted", fake_verify_deleted), \
             patch.object(RemoteFileClient, "shared_metadata_verify_deleted", fake_shared_metadata_verify_deleted), \
             patch.object(RemoteFileClient, "verify_shared_deleted", fake_shared_content_verify_deleted):
            execution = execute_session_file_core_case(
                RemoteSessionFileCoreAdapter(profile, runner))
        return execution, runner, commands, contexts

    def test_case_guard_accepts_the_session_file_contract(self):
        guard = ROOT.parents[3] / "scripts/ci/e2e_runner_case_guard.py"
        files = (ROOT / "bcs_e2e/workspace_cli_profile.py",
                  ROOT / "bcs_e2e/workspace_cli_runner.py",
                  ROOT / "bcs_e2e/session_file_core_contract.py",
                  ROOT / "bcs_e2e/session_cli_contract.py")
        result = subprocess.run([sys.executable, str(guard), *map(str, files)],
                                 text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_adaptability_and_inventory_do_not_grant_credit(self):
        value = session_file_core_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["requirement_ids"], [
            "CLI-FILE.setup", "CLI-FILE.upload", "CLI-FILE.list",
            "CLI-FILE.capabilities", "CLI-FILE.metadata", "CLI-FILE.download",
            "CLI-FILE.share", "CLI-FILE.shared-metadata",
            "CLI-FILE.shared-content", "CLI-FILE.delete"])
        self.assertEqual(value["assertion_ids"], [
            "CLI-FILE.setup", "CLI-FILE.upload", "CLI-FILE.list",
            "CLI-FILE.capabilities", "CLI-FILE.metadata-status",
            "CLI-FILE.metadata-file-id", "CLI-FILE.download", "CLI-FILE.share",
            "CLI-FILE.shared-metadata-status",
            "CLI-FILE.shared-metadata-hides-session",
            "CLI-FILE.shared-content-bytes", "CLI-FILE.delete"])
        self.assertEqual(value["assertion_count"], 12)
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        rows = migration_plan()["cross_environment_product_cli_case_slices"]
        self.assertIn(CASE_ID, [row["case_id"] for row in rows])
        row = next(row for row in rows if row["case_id"] == CASE_ID)
        self.assertEqual(row["environments"], ["singlebox-product-cli", "preprod-product-cli"])

    def test_local_core_sequence_and_capabilities(self):
        execution, runner, commands, contexts = self.run_case()
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 12)
        self.assertEqual(execution.total_count, 12)
        self.assertEqual(runner.executed, [
            "session-file.group.create", "session-file.session.create",
            "session-file.upload", "session-file.list",
            "session-file.capabilities", "session-file.download",
            "session-file.share", "session-file.delete"])
        self.assertEqual(commands[1][-2:], ["--title", "file-ws"])
        self.assertEqual(commands[-1][-4:], ["--session", EXPLICIT_SESSION, "--file-id", FILE])
        source = runner.session_file_source
        self.assertEqual(source.read_bytes(), b"bcs-e2e-session-file-payload\n")
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertIsNone(cookie)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot["steps"][0]["data"]["explicit_session"]["session_id"],
                         EXPLICIT_SESSION)
        self.assertFalse("session_id" in json.dumps(snapshot["steps"][1]["data"]))
        self.assertEqual(snapshot["steps"][5]["data"]["file_id"], FILE)
        self.assertEqual(snapshot["steps"][4]["evidence"]["transport"], "PRODUCT_HTTP")
        self.assertEqual(snapshot["steps"][4]["evidence"]["operation"], "file.metadata")
        self.assertEqual(snapshot["steps"][4]["evidence"]["credential_kind"],
                         "owned_local_bot_token")
        self.assertEqual(snapshot["steps"][4]["evidence"]["http_status"], 200)
        self.assertEqual(snapshot["steps"][5]["data"]["file_id"], FILE)
        self.assertEqual(snapshot["steps"][6]["data"]["sha256"],
                         hashlib.sha256(b"bcs-e2e-session-file-payload\n").hexdigest())
        self.assertEqual(snapshot["steps"][7]["data"], {
            "share_granted": True, "share_token_present": True,
            "expires_at": EXPIRES_AT,
        })
        self.assertEqual(snapshot["steps"][8]["data"]["file_id"], FILE)
        self.assertNotIn("session_id", snapshot["steps"][9]["data"])
        self.assertEqual(snapshot["steps"][8]["evidence"]["transport"], "PRODUCT_HTTP")
        self.assertEqual(snapshot["steps"][8]["evidence"]["operation"], "file.shared_metadata")
        self.assertEqual(snapshot["steps"][8]["evidence"]["credential_kind"], "share_token")
        self.assertEqual(snapshot["steps"][10]["data"]["size"], 29)
        self.assertEqual(snapshot["steps"][10]["evidence"]["transport"], "PRODUCT_HTTP")
        self.assertEqual(snapshot["steps"][10]["evidence"]["operation"], "file.shared_download")
        self.assertEqual(snapshot["steps"][10]["evidence"]["credential_kind"], "share_token")
        self.assertEqual(snapshot["steps"][10]["evidence"]["caller_role"], "public_share")
        self.assertEqual(snapshot["steps"][11]["data"], {
            "file_deleted": True, "invalidation_count": 3,
        })
        self.assertEqual(snapshot["steps"][11]["evidence"]["operation"], "session-file.delete")
        self.assertEqual(snapshot["steps"][11]["evidence"]["http_method_declared"], "DELETE")
        self.assertEqual(snapshot["steps"][11]["evidence"]["route_declared"],
                         f"/sessions/{EXPLICIT_SESSION}/files/{FILE}")
        self.assertEqual(snapshot["steps"][11]["evidence"]["fixture_effect"],
                         "RETAINED_DYNAMIC_GROUP_SESSIONS_DELETED_FILE")
        self.assertEqual([(item["operation"], item["http_status"])
                          for item in snapshot["steps"][11]["evidence"]["invalidations"]],
                         [("file.verify_deleted", 404),
                          ("file.shared_metadata_verify_deleted", 404),
                          ("file.shared_verify_deleted", 404)])
        self.assertNotIn(TOKEN, json.dumps(snapshot, sort_keys=True))
        self.assertNotIn(SHARE_TOKEN, json.dumps(snapshot, sort_keys=True))
        self.assertNotIn(SHARE_URL, json.dumps(snapshot, sort_keys=True))

    def test_gateway_cookie_and_baas_capabilities(self):
        execution, _, _, contexts = self.run_case(gateway=True)
        self.assertEqual(execution.status, "PASS")
        for context, cookie in contexts:
            self.assertEqual(context["token"], TOKEN)
            self.assertEqual(cookie, COOKIE)
        snapshot = execution.snapshot()
        self.assertIn(snapshot["steps"][0]["data"]["explicit_session"]["status"],
                      ("active", "running"))

        self.assertEqual(snapshot["steps"][3]["data"], {
            "storage": "baas", "max_size": 209_715_200,
            "inline_view": True, "presign_upload": True, "presign_download": True,
        })
        self.assertEqual(snapshot["steps"][4]["evidence"]["transport"], "PRODUCT_HTTP")
        self.assertEqual(snapshot["steps"][4]["evidence"]["operation"], "file.metadata")
        self.assertEqual(
            snapshot["steps"][4]["evidence"]["credential_kind"],
            "human_cookie_with_owned_driver_bot_token")
        self.assertEqual(snapshot["steps"][10]["evidence"]["caller_role"], "public_share")
        self.assertNotIn(GATEWAY_SHARE_URL, json.dumps(snapshot, sort_keys=True))
        self.assertNotIn("share_url", json.dumps(snapshot, sort_keys=True))
        self.assertNotIn(COOKIE, json.dumps(snapshot, sort_keys=True))

    def test_gateway_accepts_running_explicit_session(self):
        execution = self.run_case(gateway=True, session_status="running")[0]
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 12)

    def test_gateway_rejects_unknown_explicit_session_status(self):
        execution = self.run_case(gateway=True, session_status="pending")[0]
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)

    def test_gateway_accepts_preprod_ready_file_status(self):
        execution = self.run_case(gateway=True, file_status="Ready")[0]
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 12)

    def test_inexact_after_setup_blocks_remaining_steps(self):
        cases = {
            "upload": self.run_case(upload=False)[0],
            "list": self.run_case(files=False)[0],
            "local-capabilities": self.run_case(capabilities="baas")[0],
            "preprod-capabilities": self.run_case(gateway=True, capabilities="local")[0],
            "metadata": self.run_case(metadata=False)[0],
            "download": self.run_case(download=False)[0],
            "share": self.run_case(share=False)[0],
            "shared-metadata": self.run_case(shared_metadata=False)[0],
            "shared-content": self.run_case(shared=False)[0],
            "delete": self.run_case(delete=False)[0],
            "file-absent": self.run_case(file_absent=False)[0],
            "shared-metadata-absent": self.run_case(shared_metadata_absent=False)[0],
            "shared-content-absent": self.run_case(shared_content_absent=False)[0],
        }
        for name, execution in cases.items():
            with self.subTest(name=name):
                self.assertEqual(execution.status, "BLOCKED")
                expected_error = ("WorkspaceCliRunnerError" if name == "delete"
                                  else "RunStateError")
                self.assertEqual(execution.error_type, expected_error)

    def test_inexact_setup_has_no_partial_credit(self):
        execution, _, _, _ = self.run_case(session=False)
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual([step["status"] for step in execution.snapshot()["steps"]],
                         ["NOT_RUN"] * 12)

    def test_nonzero_session_file_output_is_rejected(self):
        execution, _, _, _ = self.run_case(returncode=1)
        self.assertEqual(execution.status, "BLOCKED")

    def test_group_setup_is_one_shot(self):
        profile = validate_session_file_core_profile(profile_value())
        run_state = self.state / f"run-{uuid.uuid4().hex}"
        run_state.mkdir()
        runner = WorkspaceCliRunner(profile, binary=self.binary,
                                    binary_sha256=self.digest,
                                    state_dir=run_state)

        def fake_capture(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, create_output(), b"")

        with patch.dict(os.environ, self.env), \
             patch("bcs_e2e.workspace_cli_runner.capture", side_effect=fake_capture):
            runner.execute("session-file.group.create")
            with self.assertRaisesRegex(WorkspaceCliRunnerError, "already executed"):
                runner.execute("session-file.group.create")

    def test_profile_rejects_unadmitted_session_file_effects(self):
        cases = {
            "cleanup": lambda value: value["relationships"].update(cleanup="automatic"),
            "file-delete": lambda value: value["relationships"].update(file="automatic-delete"),
            "borrowed-bot": lambda value: value["actors"]["member"].update(dedicated=False),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    validate_session_file_core_profile(profile_value(mutate=mutate))


if __name__ == "__main__":
    unittest.main()
