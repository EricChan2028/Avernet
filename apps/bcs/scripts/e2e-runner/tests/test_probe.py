from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.probe import ws_probe
from bcs_e2e.run_state import write
from bcs_e2e.profile import fingerprint
from bcs_e2e.probe import http_probe
from bcs_e2e.profile import ConfigError, validate
from bcs_e2e.transport import Observation, ObservationError


ROOT = Path(__file__).resolve().parents[1]


def profile() -> dict:
    value = json.loads((ROOT / "profiles/preprod.example.json").read_text())
    value["environment"].update(id="test-pre", expected_bcs_env="pre")
    value["human"]["user_id"] = "owner-1"
    for role, bot in zip(("driver", "member"), ("bot-a", "bot-b")):
        value["actors"][role].update(bot_id=bot, owner_id="owner-1")
    value["observer"].update(server="alisddbquery", database="agentclawdb")
    value["policy"]["exclusive_use_confirmed"] = True
    return validate(value)




class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeWsClient:
    last = None

    def __init__(self, origin, *, timeout_seconds):
        self.origin = origin
        self.timeout_seconds = timeout_seconds
        self.connection = FakeConnection()
        FakeWsClient.last = self

    def connect(self, token, group_id, session_id):
        self.token = token
        return self.connection, {
            "operation": "session.ws.connect", "method": "WS",
            "path": "/openapi/v1/collaboration/messages/ws?token=[REDACTED]",
            "http_status": 101, "connected": True,
            "connect_request_id": "connect-1", "group_id": group_id,
            "session_id": session_id,
        }


def prepare_live_scope(root: Path, p: dict):
    state = root / "state"
    state.mkdir()
    ledger_path = state / "run.sqlite"
    digest = fingerprint(p)
    write(state / "run.json", {"schema_version": 1, "status": "RUNNING",
                                "run_id": "ws-run", "profile_digest": digest,
                                "ledger_path": str(ledger_path)})
    with Ledger(ledger_path, "ws-run", digest, create=True) as ledger:
        group = ledger.intent("PRE-02", 1, "group", "planned-group")
        ledger.observed(group, "g-1", origin="created")
        session = ledger.intent("PRE-02", 1, "session", "planned-session", group)
        ledger.observed(session, "s-1", origin="created")
    return state

class HttpProbeTests(unittest.TestCase):
    def test_probe_returns_metadata_without_response_body_or_credential(self):
        p = profile()
        fake = Mock()
        fake.read.return_value = Observation(
            {"user_id": "owner-1", "session_token": "should-not-be-output"},
            {"operation": "identity.read", "path": "/openapi/v1/auth/user",
             "submitted_request_id": "r-1", "response_request_id": "r-2"})
        with patch.dict("os.environ", {"BCS_E2E_HUMAN_COOKIE": "private-cookie"}, clear=True), \
             patch("bcs_e2e.probe.ReadClient", return_value=fake):
            result = http_probe(p, "identity.read")
        self.assertEqual(result["status"], "PROBE_PASSED")
        self.assertNotIn("session_token", json.dumps(result))
        self.assertNotIn("private-cookie", json.dumps(result))
        self.assertEqual(result["cross_origin_auth"], "NOT_SENT")
        fake.read.assert_called_once_with("identity.read", "")

    def test_probe_requires_explicit_read_and_bound_bot(self):
        p = profile()
        with self.assertRaisesRegex(ConfigError, "unsupported"):
            http_probe(p, "delete")
        with self.assertRaisesRegex(ConfigError, "bot.read"):
            http_probe(p, "bot.read")

    def test_transport_failure_is_blocked_without_exception_or_secret(self):
        p = profile()
        fake = Mock()
        fake.read.side_effect = ObservationError("private-cookie should never escape")
        with patch("bcs_e2e.probe.ReadClient", return_value=fake):
            result = http_probe(p, "environment.read")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["auth_leak_check"], "PASS")
        self.assertNotIn("private", json.dumps(result))

    def test_cli_requires_allow_read_and_calls_only_finite_operation(self):
        p = profile()
        with __import__("tempfile").TemporaryDirectory(dir="/tmp") as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(p))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["probe", "http", "--profile", str(path),
                                       "--operation", "identity.read"]), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")
            output = io.StringIO()
            with patch("bcs_e2e.cli.http_probe", return_value={"status": "PROBE_PASSED"}) as probe, \
                 contextlib.redirect_stdout(output):
                self.assertEqual(main(["probe", "http", "--profile", str(path),
                                       "--operation", "identity.read", "--allow-read"]), 0)
            probe.assert_called_once_with(p, "identity.read", bot_id="")


class WebSocketProbeTests(unittest.TestCase):
    def test_ws_probe_requires_run_owned_live_resources_and_redacts_token(self):
        p = profile()
        with __import__("tempfile").TemporaryDirectory(dir="/tmp") as tmp:
            state = prepare_live_scope(Path(tmp), p)
            with patch.dict("os.environ", {"BCS_E2E_SESSION_TOKEN": "short-lived-secret"}, clear=True):
                result = ws_probe(p, state, "ws-run", token_env="BCS_E2E_SESSION_TOKEN",
                                  group_id="g-1", session_id="s-1", client_factory=FakeWsClient)
            self.assertEqual(result["status"], "PROBE_PASSED")
            self.assertNotIn("short-lived-secret", json.dumps(result))
            self.assertTrue(FakeWsClient.last.connection.closed)
            self.assertEqual(FakeWsClient.last.token, "short-lived-secret")

    def test_ws_probe_rejects_unowned_scope_or_invalid_token_reference_before_connect(self):
        p = profile()
        with __import__("tempfile").TemporaryDirectory(dir="/tmp") as tmp:
            state = prepare_live_scope(Path(tmp), p)
            with patch.dict("os.environ", {"BCS_E2E_SESSION_TOKEN": "secret"}, clear=True),                  patch("bcs_e2e.probe.SessionWebSocketClient") as client:
                with self.assertRaisesRegex(ConfigError, "owned"):
                    ws_probe(p, state, "ws-run", token_env="BCS_E2E_SESSION_TOKEN",
                             group_id="other", session_id="s-1")
                with self.assertRaisesRegex(ConfigError, "environment reference"):
                    ws_probe(p, state, "ws-run", token_env="literal-secret",
                             group_id="g-1", session_id="s-1")
                client.assert_not_called()

    def test_ws_probe_cli_requires_explicit_read_permission(self):
        p = profile()
        with __import__("tempfile").TemporaryDirectory(dir="/tmp") as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(p))
            state = Path(tmp) / "state"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["probe", "ws", "--profile", str(path),
                                       "--state-dir", str(state), "--run-id", "ws-run",
                                       "--token-env", "BCS_E2E_SESSION_TOKEN",
                                       "--group-id", "g-1", "--session-id", "s-1"]), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")
            output = io.StringIO()
            with patch("bcs_e2e.cli.ws_probe", return_value={"status": "PROBE_PASSED"}) as probe,                  contextlib.redirect_stdout(output):
                self.assertEqual(main(["probe", "ws", "--profile", str(path),
                                       "--state-dir", str(state), "--run-id", "ws-run",
                                       "--token-env", "BCS_E2E_SESSION_TOKEN",
                                       "--group-id", "g-1", "--session-id", "s-1",
                                       "--allow-read"]), 0)
            probe.assert_called_once_with(p, state, "ws-run", token_env="BCS_E2E_SESSION_TOKEN",
                                          group_id="g-1", session_id="s-1")


if __name__ == "__main__":
    unittest.main()
