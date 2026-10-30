"""Loopback self-test for the owned-local Provider/Judge mock receiver.

Ports the community coverage mock
(``apps/bcs/scripts/e2e-test/http_provider_judge_mock.py``) as
``bcs_e2e.mock_judge`` and asserts, on 127.0.0.1 only:

* clean start / GET / POST / close lifecycle;
* byte-exact pages for every fixed community response;
* the recorded-request control views the stories #27/#28 harness helpers
  poll (``wait_for_mock_provider_method`` / ``wait_for_event_webhook_type``
  / ``wait_for_mock_judge_start``);
* blocking-scripted Judge semantics (release, scripted outcomes, exhaustion);
* the ``--ready-file`` CLI contract the community ``mock_services.sh``
  lifecycle relies on;
* the owned-local loopback-only binding rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.mock_judge.page_bank import (  # noqa: E402
    CONTROL_ACK_BYTES,
    FRIEND_AUTH_SYNCED_BYTES,
    JUDGE_RELEASE_TIMEOUT_BYTES,
    JUDGE_UNEXPECTED_INVOCATION_BYTES,
    NOT_FOUND_BYTES,
    OUTCOMES_INVALID_BYTES,
    chat_completions_view,
    judge_content_view,
    judge_status_view,
    requests_view,
)
from bcs_e2e.mock_judge.server import (  # noqa: E402
    JUDGE_RELEASE_TIMEOUT_SECONDS,
    MockJudgeConfigError,
    MockJudgeReceiver,
)

INNER_APPROVED = (
    '{"outcome": "approved", "reason": "candidate satisfies the E2E criteria", '
    '"confidence": 0.99, "checked_criteria": [], "retry_instruction": ""}'
)
OUTER_APPROVED = (
    '{"choices":[{"message":{"content":' + json.dumps(INNER_APPROVED) + '}}]}'
).encode()


def http_request(base_url: str, method: str, path: str,
                 body: Any = None, headers: dict[str, str] | None = None,
                 timeout: float = 30.0) -> tuple[int, bytes, dict[str, str]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base_url + path, data=data, method=method, headers=headers or {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


class PageBankByteContractTests(unittest.TestCase):
    """Every canned page must reproduce the community mock bytes exactly."""

    def test_fixed_pages_match_community_bytes(self):
        self.assertEqual(b'{"ok":true}', CONTROL_ACK_BYTES)
        self.assertEqual(b'{"error":"not_found"}', NOT_FOUND_BYTES)
        self.assertEqual(
            b'{"error":"expected nonempty outcome strings"}', OUTCOMES_INVALID_BYTES,
        )
        self.assertEqual(
            b'{"error":"unexpected_judge_invocation"}', JUDGE_UNEXPECTED_INVOCATION_BYTES,
        )
        self.assertEqual(
            b'{"error":"judge_release_timeout"}', JUDGE_RELEASE_TIMEOUT_BYTES,
        )
        self.assertEqual(
            b'{"synced":true,"reason":"created","auth_id":1}', FRIEND_AUTH_SYNCED_BYTES,
        )
        self.assertEqual(CONTROL_ACK_BYTES, b'{"ok":true}')

    def test_request_views_use_compact_separators_and_null_authorization(self):
        self.assertEqual(b'{"requests":[]}', requests_view([]))
        records = [{"authorization": None, "body": {"method": "chat.send"}}]
        self.assertEqual(
            b'{"requests":[{"authorization":null,"body":{"method":"chat.send"}}]}',
            requests_view(records),
        )

    def test_judge_status_view_bytes(self):
        self.assertEqual(b'{"started":false,"released":false}', judge_status_view(False, False))
        self.assertEqual(b'{"started":true,"released":true}', judge_status_view(True, True))

    def test_judge_content_view_reproduces_community_decision(self):
        self.assertEqual(INNER_APPROVED, judge_content_view("approved"))
        decision = json.loads(judge_content_view("revise"))
        self.assertEqual(
            {
                "outcome": "revise",
                "reason": "candidate satisfies the E2E criteria",
                "confidence": 0.99,
                "checked_criteria": [],
                "retry_instruction": "",
            },
            decision,
        )

    def test_chat_completions_envelope_bytes(self):
        self.assertEqual(OUTER_APPROVED, chat_completions_view("approved"))
        envelope = json.loads(chat_completions_view("revise"))
        self.assertEqual("revise", json.loads(envelope["choices"][0]["message"]["content"])["outcome"])

    def test_dynamic_views_reproduce_community_dumps_policy(self):
        payload = {"started": True, "released": False}
        self.assertEqual(
            b'{"started":true,"released":false}',
            json.dumps(payload, separators=(",", ":")).encode(),
        )
        self.assertEqual(
            json.dumps({"requests": []}, separators=(",", ":")).encode(), requests_view([]),
        )


class MockJudgeReceiverLoopbackTests(unittest.TestCase):
    """Live loopback coverage of the ported routing table."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base_url = None
        self.receiver = MockJudgeReceiver()
        self.receiver.start()
        self.base_url = self.receiver.base_url
        self.addCleanup(self._force_stop)

    def _force_stop(self) -> None:
        try:
            self.receiver.stop()
        except Exception:  # noqa: S110 - teardown must not mask assertions
            pass

    def test_health_page_bytes_and_content_type(self):
        status, body, headers = http_request(self.base_url, "GET", "/health")
        self.assertEqual(200, status)
        self.assertEqual(b'{"ok":true}', body)
        self.assertEqual("application/json", headers.get("content-type"))

    def test_unknown_and_query_suffixed_paths_return_not_found_bytes(self):
        for method, path in (
            ("GET", "/nope"),
            ("GET", "/health?probe=1"),
            ("POST", "/unknown"),
            ("GET", "/control/provider/requests?limit=5"),
        ):
            status, body, _headers = http_request(self.base_url, method, path, body={})
            self.assertEqual(404, status, f"{method} {path}")
            self.assertEqual(NOT_FOUND_BYTES, body, f"{method} {path}")

    def test_provider_webhook_records_authorization_and_body(self):
        status, body, _ = http_request(
            self.base_url, "POST", "/provider/webhook",
            body={"method": "chat.send", "id": "run-1"},
            headers={"Authorization": "Bearer bcs-to-provider"},
        )
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        status, body, _ = http_request(
            self.base_url, "POST", "/provider/webhook",
            body={"method": "chat.send", "id": "run-2"},
            headers={"Authorization": "Bearer second"},
        )
        self.assertEqual(200, status)
        view = {
            "requests": [
                {"authorization": "Bearer bcs-to-provider", "body": {"method": "chat.send", "id": "run-1"}},
                {"authorization": "Bearer second", "body": {"method": "chat.send", "id": "run-2"}},
            ],
        }
        status, body, _ = http_request(self.base_url, "GET", "/control/provider/requests")
        self.assertEqual(200, status)
        self.assertEqual(json.dumps(view, separators=(",", ":")).encode(), body)

    def test_event_webhook_records_null_authorization_and_content_type(self):
        status, body, _ = http_request(
            self.base_url, "POST", "/event-webhook",
            body={"event_type": "group.created", "scope": {"group_id": "grp-1"}},
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        status, body, _ = http_request(self.base_url, "GET", "/control/event-webhook/requests")
        self.assertEqual(200, status)
        view = json.loads(body)
        self.assertEqual(1, len(view["requests"]))
        self.assertIsNone(view["requests"][0]["authorization"])
        self.assertEqual("application/json", view["requests"][0]["content_type"])
        self.assertEqual("group.created", view["requests"][0]["body"]["event_type"])

    def test_event_webhook_clear_preserves_provider_records(self):
        http_request(self.base_url, "POST", "/provider/webhook", body={"m": 1})
        http_request(self.base_url, "POST", "/event-webhook", body={"e": 1})
        status, body, _ = http_request(self.base_url, "POST", "/control/event-webhook/clear")
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        _, provider_view, _ = http_request(self.base_url, "GET", "/control/provider/requests")
        _, event_view, _ = http_request(self.base_url, "GET", "/control/event-webhook/requests")
        self.assertEqual(1, len(json.loads(provider_view)["requests"]))
        self.assertEqual([], json.loads(event_view)["requests"])

    def test_friend_auth_sync_and_work_order_ports_capture_principal(self):
        status, body, _ = http_request(
            self.base_url, "POST", "/api/internal/bot-friend-auth/sync",
            body={"to_bot": "tc-bot"}, headers={"X-Avernet-Principal": "iss=bcs"},
        )
        self.assertEqual((200, FRIEND_AUTH_SYNCED_BYTES), (status, body))
        status, body, _ = http_request(
            self.base_url, "POST", "/api/v1/work-orders/events",
            body={"event": "friend.created"}, headers={"X-Avernet-Principal": "iss=bcs"},
        )
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        _, sync_view, _ = http_request(self.base_url, "GET", "/control/friend-auth-sync/requests")
        view = json.loads(sync_view)
        self.assertEqual("iss=bcs", view["requests"][0]["principal"])
        self.assertEqual({"to_bot": "tc-bot"}, view["requests"][0]["body"])

    def test_judge_status_flags_release_and_reset(self):
        _, view, _ = http_request(self.base_url, "GET", "/control/judge/status")
        self.assertEqual(b'{"started":false,"released":false}', view)
        status, body, _ = http_request(self.base_url, "POST", "/control/judge/release")
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        _, view, _ = http_request(self.base_url, "GET", "/control/judge/status")
        self.assertEqual(b'{"started":false,"released":true}', view)
        http_request(self.base_url, "POST", "/control/reset")
        _, view, _ = http_request(self.base_url, "GET", "/control/judge/status")
        self.assertEqual(b'{"started":false,"released":false}', view)
        _, provider_view, _ = http_request(self.base_url, "GET", "/control/provider/requests")
        self.assertEqual(b'{"requests":[]}', provider_view)

    def test_scripted_judge_outcomes_flow_exhaustion_and_recovery(self):
        status, body, _ = http_request(
            self.base_url, "POST", "/control/judge/outcomes", body={"outcomes": "approved"},
        )
        self.assertEqual((400, OUTCOMES_INVALID_BYTES), (status, body))
        status, body, _ = http_request(
            self.base_url, "POST", "/control/judge/outcomes", body={"outcomes": ["revise", "approved"]},
        )
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        for expected_outcome in ("revise", "approved"):
            status, body, _ = http_request(
                self.base_url, "POST", "/v1/chat/completions", body={"model": "judge"},
                timeout=JUDGE_RELEASE_TIMEOUT_SECONDS + 5,
            )
            self.assertEqual(200, status)
            envelope = json.loads(body)
            decision = json.loads(envelope["choices"][0]["message"]["content"])
            self.assertEqual(expected_outcome, decision["outcome"])
            self.assertEqual("candidate satisfies the E2E criteria", decision["reason"])
        status, body, _ = http_request(
            self.base_url, "POST", "/v1/chat/completions", body={"model": "judge"},
            timeout=JUDGE_RELEASE_TIMEOUT_SECONDS + 5,
        )
        self.assertEqual((500, JUDGE_UNEXPECTED_INVOCATION_BYTES), (status, body))
        status, body, _ = http_request(
            self.base_url, "POST", "/control/judge/outcomes", body={"outcomes": ["approved"]},
        )
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))

    def test_blocking_judge_approves_after_release(self):
        result: list[tuple[int, bytes]] = []

        def _call_judge() -> None:
            result.append(http_request(
                self.base_url, "POST", "/v1/chat/completions", body={"model": "judge"},
                timeout=JUDGE_RELEASE_TIMEOUT_SECONDS + 5,
            )[:2])

        thread = threading.Thread(target=_call_judge)
        thread.start()
        deadline = time.monotonic() + 10
        started = False
        while time.monotonic() < deadline:
            _, view, _ = http_request(self.base_url, "GET", "/control/judge/status")
            if json.loads(view)["started"]:
                started = True
                break
            time.sleep(0.05)
        self.assertTrue(started, "Judge HTTP request should have started while blocked")
        status, body, _ = http_request(self.base_url, "POST", "/control/judge/release")
        self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
        thread.join(timeout=JUDGE_RELEASE_TIMEOUT_SECONDS + 10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((200, OUTER_APPROVED), result[0])
        _, view, _ = http_request(self.base_url, "GET", "/control/judge/status")
        self.assertEqual(b'{"started":true,"released":true}', view)

    def test_request_journal_is_machine_readable_jsonl(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "mock-judge.jsonl"
            receiver = MockJudgeReceiver(request_log_path=log_path)
            receiver.start()
            try:
                base = receiver.base_url
                http_request(base, "GET", "/health")
                http_request(base, "POST", "/event-webhook", body={"event_type": "session.created"})
                http_request(base, "GET", "/not-a-route")
            finally:
                receiver.stop()
            lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(3, len(lines))
        records = [json.loads(line) for line in lines]
        self.assertEqual(["GET", "POST", "GET"], [row["method"] for row in records])
        self.assertEqual([200, 200, 404], [row["status"] for row in records])
        self.assertEqual(["/health", "/event-webhook", "/not-a-route"], [row["path"] for row in records])
        for row in records:
            self.assertIn("received_at", row)
            self.assertIn("remote_address", row)
            self.assertIn("duration_ms", row)
            self.assertNotIn("authorization", row)
            self.assertNotIn("body", row)

    def test_stop_is_idempotent_and_releases_the_loopback_port(self):
        receiver = MockJudgeReceiver()
        receiver.start()
        url = receiver.base_url
        receiver.stop()
        receiver.stop()
        with self.assertRaises(urllib.error.URLError):
            http_request(url, "GET", "/health", timeout=2)

    def test_state_is_per_instance_not_module_global(self):
        other = MockJudgeReceiver()
        other.start()
        try:
            http_request(other.base_url, "POST", "/provider/webhook", body={"solo": True})
            _, sibling_view, _ = http_request(self.base_url, "GET", "/control/provider/requests")
            self.assertEqual([], json.loads(sibling_view)["requests"])
            _, own_view, _ = http_request(other.base_url, "GET", "/control/provider/requests")
            self.assertEqual(1, len(json.loads(own_view)["requests"]))
        finally:
            other.stop()


class MockJudgeReceiverProcessTests(unittest.TestCase):
    """Process-level contract with the community ``mock_services.sh`` lifecycle."""

    def test_cli_module_ready_file_and_graceful_terminate(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            ready = runtime / "ready"
            journal = runtime / "mock-judge.jsonl"
            env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/local/bin:/usr/bin:/bin"}
            process = subprocess.Popen(
                [sys.executable, "-m", "bcs_e2e.mock_judge.server",
                 "--ready-file", str(ready), "--request-log", str(journal)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )
            self.addCleanup(lambda: process.poll() is None and process.kill())
            deadline = time.monotonic() + 15
            base_url = None
            while time.monotonic() < deadline:
                if ready.is_file() and ready.stat().st_size:
                    base_url = ready.read_text(encoding="utf-8").strip()
                    break
                if process.poll() is not None:
                    self.fail(f"mock judge CLI exited early rc={process.returncode}")
                time.sleep(0.05)
            self.assertIsNotNone(base_url)
            self.assertTrue(base_url.startswith("http://127.0.0.1:"))
            status, body, _ = http_request(base_url, "GET", "/health")
            self.assertEqual((200, b'{"ok":true}'), (status, body))
            status, body, _ = http_request(
                base_url, "POST", "/event-webhook", body={"event_type": "message.created"},
            )
            self.assertEqual((200, CONTROL_ACK_BYTES), (status, body))
            process.terminate()
            self.assertEqual(0, process.wait(timeout=10))
            records = [json.loads(line) for line in journal.read_text().splitlines()]
            self.assertEqual(["GET", "POST"], [row["method"] for row in records])
            self.assertEqual([200, 200], [row["status"] for row in records])

    def test_cli_refuses_non_loopback_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "ready"
            process = subprocess.run(
                [sys.executable, "-m", "bcs_e2e.mock_judge.server",
                 "--host", "0.0.0.0", "--ready-file", str(ready)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PYTHONPATH": str(ROOT), "PATH": "/usr/local/bin:/usr/bin:/bin"},
                timeout=30,
            )
            self.assertEqual(2, process.returncode)
            self.assertFalse(ready.exists())

    def test_receiver_refuses_non_loopback_host(self):
        with self.assertRaises(MockJudgeConfigError):
            MockJudgeReceiver(host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
