from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.evidence import AlisdDbEvidenceProvider


class Reply:
    def __init__(self, body, code=200):
        self.body = json.dumps(body).encode() if isinstance(body, dict) else body
        self.code = code
        self.headers = {"x-request-id": "e2e-server-request"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit=-1):
        return self.body


def envelope(data, status=200):
    return {"code": {200: 20000, 201: 20100}[status], "message": "Created" if status == 201 else "OK", "data": data, "request_id": "server"}


class PilotTests(unittest.TestCase):
    def setUp(self):
        registry = tempfile.TemporaryDirectory()
        self.addCleanup(registry.cleanup)
        root_patch = patch("bcs_e2e.pool.pool_root", return_value=Path(registry.name))
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def profile(self):
        root = Path(__file__).resolve().parents[1]
        profile = json.loads((root / "profiles/preprod.example.json").read_text())
        profile["environment"].update(id="pre-test", expected_bcs_env="pre")
        profile["entrypoint"].update(origin="https://gateway.example.com", api_face="public-v1")
        profile["human"]["user_id"] = "owner-1"
        profile["actors"]["driver"].update(bot_id="bot-a", owner_id="owner-1")
        profile["actors"]["member"].update(bot_id="bot-b", owner_id="owner-1")
        profile["auth"]["header_env"] = {"Cookie": "BCS_E2E_HUMAN_COOKIE"}
        profile["observer"].update(server="alisddbquery", database="agentclawdb",
                                  environment_binding_confirmed=True)
        profile["policy"]["exclusive_use_confirmed"] = True
        return profile

    def test_synthetic_pre01_executes_exact_owned_crud_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            profile_path = tmp / "profile.json"
            profile_path.write_text(json.dumps(self.profile()))
            state = tmp / "state"
            self.assertEqual(main(["prepare", "--profile", str(profile_path), "--run-id", "run-1",
                                   "--state-dir", str(state)]), 0)
            group = {"group_id": "g-1", "driver_bot_uuid": "bot-a", "initial_session_id": "s-initial",
                     "originator_actor_id": "human_owner-1",
                     "participants": [{"actor_id": "bot-a", "role": "driver"}, {"actor_id": "bot-b", "role": "consultant"}],
                     "name": "E2E run-1 updated"}
            bot_a = {"bot_id": "bot-a", "kind": "bot", "created_by": "owner-1",
                     "env": "pre", "status": "online"}
            bot_b = {**bot_a, "bot_id": "bot-b"}
            replies = [
                Reply(envelope({"env": "pre"})), Reply(envelope({"user_id": "owner-1"})),
                Reply(envelope(bot_a)), Reply(envelope(bot_b)),
                Reply(envelope(group, 201), 201), Reply(envelope(group)),
                Reply(envelope(group)), Reply(envelope(group)),
                Reply(envelope({"session_id": "s-initial", "group_id": "g-1"})),
                Reply(envelope({"deleted": True})),
                HTTPError("https://gateway.example.com", 404, "not found", {}, io.BytesIO(b"secret")),
                Reply(envelope({"deleted": True})),
                HTTPError("https://gateway.example.com", 404, "not found", {}, io.BytesIO(b"secret")),
            ]
            opener = type("FakeOpener", (), {})()
            calls = []
            def open_request(request, timeout=None):
                calls.append((request.method, request.full_url, request.data))
                result = replies.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            opener.open = open_request
            observer = AlisdDbEvidenceProvider(
                database="agentclawdb", environment="pre", environment_binding_confirmed=True,
                describe=lambda db, table: {"success": True, "columns": [
                    "group_id", "env", "driver_bot", "originator", "status"]},
                query=lambda plan: {"success": True, "data": [{
                    "group_id": "g-1", "env": "pre", "driver_bot": "bot-a",
                    "originator": "human_owner-1", "status": "active"}]},
            )
            # This is a synthetic wire-contract test, not real remote admission.
            # The fake opener cannot dispatch Bot work. Real admission refusal
            # before clients/intents is covered by test_remote_effects.py.
            with patch("bcs_e2e.adapters.require_group_effect_admission"), \
                    patch("bcs_e2e.adapters.RemotePilotAdapter.termination_mode", "LOCAL_CONTROLLED"), \
                    patch("bcs_e2e.adapters.RemotePilotAdapter.semantic_observer", return_value=observer), \
                    patch.dict(os.environ, {"BCS_E2E_HUMAN_COOKIE": "private-cookie"}), \
                    patch("bcs_e2e.transport.build_opener", return_value=opener), \
                    patch("bcs_e2e.write_transport.build_opener", return_value=opener):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = main(["run", "--profile", str(profile_path), "--state-dir", str(state),
                               "--run-id", "run-1", "--case", "PRE-01", "--allow-write",
                               "--confirm-run-id", "run-1"])
            self.assertEqual(rc, 0)
            report = json.loads((state / "report-PRE-01.json").read_text())
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["cleanup"], "PASS")
            self.assertFalse(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())
            self.assertEqual([method for method, _, _ in calls],
                             ["GET", "GET", "GET", "GET", "POST", "GET", "PATCH", "GET", "GET", "DELETE", "GET", "DELETE", "GET"])
            self.assertTrue(all("private-cookie" not in json.dumps(item) for item in report["evidence"]))


    def test_local_mock_profile_runs_pre01_without_socket_or_credentials(self):
        root = Path(__file__).resolve().parents[1]
        profile_path = root / "profiles/local.mock.json"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with patch("bcs_e2e.transport.build_opener") as remote_read, \
                    patch("bcs_e2e.write_transport.build_opener") as remote_write:
                self.assertEqual(main(["prepare", "--profile", str(profile_path), "--run-id", "local-1",
                                       "--state-dir", str(state)]), 0)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = main(["run", "--profile", str(profile_path), "--state-dir", str(state),
                               "--run-id", "local-1", "--case", "PRE-01", "--allow-write",
                               "--confirm-run-id", "local-1"])
            self.assertEqual(rc, 0)
            report = json.loads((state / "report-PRE-01.json").read_text())
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["cleanup"], "PASS")
            remote_read.assert_not_called()
            remote_write.assert_not_called()
            self.assertFalse(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())

    def test_local_mock_profile_runs_pre02_session_and_ws_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        profile_path = root / "profiles/local.mock.json"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with patch("bcs_e2e.transport.build_opener") as remote_read, \
                    patch("bcs_e2e.write_transport.build_opener") as remote_write:
                self.assertEqual(main(["prepare", "--profile", str(profile_path), "--run-id", "local-2",
                                       "--state-dir", str(state)]), 0)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = main(["run", "--profile", str(profile_path), "--state-dir", str(state),
                               "--run-id", "local-2", "--case", "PRE-02", "--allow-write",
                               "--confirm-run-id", "local-2"])
            self.assertEqual(rc, 0)
            report = json.loads((state / "report-PRE-02.json").read_text())
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["cleanup"], "PASS")
            assertion_ids = {item["id"] for item in report["assertions"]}
            self.assertIn("PRE-02.websocket_upgrade", assertion_ids)
            self.assertIn("PRE-02.websocket_closed", assertion_ids)
            self.assertEqual(report["evidence_status"], "LOCAL_SURROGATE")
            self.assertEqual(len(report["semantic_evidence"]), 2)
            remote_read.assert_not_called()
            remote_write.assert_not_called()
            self.assertFalse(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())

    def test_local_mock_profile_runs_pre03_file_lifecycle_and_bytes(self):
        root = Path(__file__).resolve().parents[1]
        profile_path = root / "profiles/local.mock.json"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with patch("bcs_e2e.transport.build_opener") as remote_read, \
                    patch("bcs_e2e.write_transport.build_opener") as remote_write:
                self.assertEqual(main(["prepare", "--profile", str(profile_path), "--run-id", "local-3",
                                       "--state-dir", str(state)]), 0)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = main(["run", "--profile", str(profile_path), "--state-dir", str(state),
                               "--run-id", "local-3", "--case", "PRE-03", "--allow-write",
                               "--confirm-run-id", "local-3"])
            self.assertEqual(rc, 0)
            report = json.loads((state / "report-PRE-03.json").read_text())
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["cleanup"], "PASS")
            assertion_ids = {item["id"] for item in report["assertions"]}
            self.assertIn("PRE-03.file_listed", assertion_ids)
            self.assertIn("PRE-03.download_bytes_match", assertion_ids)
            self.assertIn("PRE-03.share_bytes_match", assertion_ids)
            self.assertIn("PRE-03.file_owner_matches", assertion_ids)
            self.assertIn("PRE-03.ob_file_matches", assertion_ids)
            operations = [item["operation"] for item in report["evidence"]]
            self.assertIn("file.list", operations)
            self.assertNotIn("file.capabilities", operations)
            self.assertTrue(all("bcs-e2e-pre03" not in json.dumps(item) for item in report["evidence"]))
            remote_read.assert_not_called()
            remote_write.assert_not_called()
            self.assertFalse(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())

    def test_local_mock_runs_all_three_cases_sequentially_in_one_run(self):
        root = Path(__file__).resolve().parents[1]
        profile_path = root / "profiles/local.mock.json"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self.assertEqual(main(["prepare", "--profile", str(profile_path), "--run-id", "local-suite",
                                   "--state-dir", str(state)]), 0)
            for case in ("PRE-01", "PRE-02", "PRE-03"):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = main(["run", "--profile", str(profile_path), "--state-dir", str(state),
                               "--run-id", "local-suite", "--case", case, "--allow-write",
                               "--confirm-run-id", "local-suite"])
                self.assertEqual(rc, 0, case)
                report = json.loads((state / f"report-{case}.json").read_text())
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["evidence_status"], "LOCAL_SURROGATE")
            self.assertEqual(json.loads((state / "run.json").read_text())["status"], "CASE_COMPLETED")
            self.assertFalse(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())

    def test_run_requires_two_factor_write_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            profile = self.profile()
            profile_path = tmp / "profile.json"
            profile_path.write_text(json.dumps(profile))
            state = tmp / "state"
            self.assertEqual(main(["prepare", "--profile", str(profile_path), "--run-id", "run-2",
                                   "--state-dir", str(state)]), 0)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main(["run", "--profile", str(profile_path), "--state-dir", str(state),
                           "--run-id", "run-2", "--case", "PRE-01", "--confirm-run-id", "run-2"])
            self.assertEqual(rc, 2)
            self.assertIn("BLOCKED", output.getvalue())
            self.assertTrue(Path(json.loads((state / "run.json").read_text())["lock_path"]).exists())
            self.assertEqual(main(["release", "--state-dir", str(state)]), 0)


if __name__ == "__main__":
    unittest.main()
