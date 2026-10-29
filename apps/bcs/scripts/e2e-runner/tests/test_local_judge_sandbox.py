"""Real-loopback tests for the owned slow-Judge Provider/Judge prerequisite."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_judge_sandbox import (
    LocalJudgeSandboxError,
    OwnedLocalJudgeSandbox,
    ProviderReceipt,
)
from bcs_e2e.runtime import RuntimeContractError


RUN_ID = "slow-judge-owner-1"
PROVIDER_ID = "provider-1"
BOT_ID = "bot-1"
PROVIDER_TOKEN = "provider-downlink-secret"


def provider_body(delivery_id: str, *, provider_id=PROVIDER_ID, provider_ref=RUN_ID):
    return {
        "type": "req",
        "id": delivery_id,
        "method": "chat.send",
        "session_id": "session-1",
        "bcn_group_id": "group-1",
        "to_bot": {
            "provider_id": provider_id,
            "provider_bot_ref": provider_ref,
            "tags": [],
        },
        "from": {
            "kind": "bot",
            "name": "BCS State Machine",
            "actor_id": "bcs_state_machine",
        },
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "produce the next artifact"}],
            "timestamp": 1,
        },
        "timeout_ms": 120_000,
        "extensions": {"caller_wait_mode": "detached"},
    }


def judge_body():
    return {
        "model": "local-judge-model",
        "messages": [
            {"role": "system", "content": "return strict JSON"},
            {"role": "user", "content": "candidate awaiting slow judge"},
        ],
        "stream": False,
        "temperature": 0,
        "max_tokens": 4096,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "bcs_state_machine_judge_response"},
        },
    }


class LocalJudgeSandboxTests(unittest.TestCase):
    def start(self):
        sandbox = OwnedLocalJudgeSandbox(RUN_ID).start()
        self.addCleanup(sandbox.close)
        sandbox.arm_provider(PROVIDER_ID, BOT_ID, PROVIDER_TOKEN)
        return sandbox

    def post(self, url: str, value, *, headers=None, timeout=3):
        target = urlsplit(url)
        raw = json.dumps(value, separators=(",", ":")).encode()
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection = HTTPConnection(target.hostname, target.port, timeout=timeout)
        try:
            connection.request("POST", target.path, body=raw, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def provider_post(self, sandbox, delivery_id, *, token=PROVIDER_TOKEN, body=None):
        return self.post(
            sandbox.provider_url,
            body or provider_body(delivery_id),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "text/event-stream, application/json",
                "X-BCN-Protocol-Version": "2.0",
                "X-BCN-Transport": "sse",
                "X-BCN-Message-Id": "transport-" + delivery_id,
                "X-BCN-Timestamp": "1790000000000",
            },
        )

    def judge_post(self, sandbox, *, key=None, result=None):
        try:
            value = self.post(
                sandbox.judge_base_url + "/chat/completions",
                judge_body(),
                headers={"Authorization": "Bearer " + (key or sandbox.judge_api_key)},
                timeout=10,
            )
        except BaseException as error:
            value = error
        if result is not None:
            result.append(value)
        return value

    def test_lifecycle_exposes_only_private_live_capabilities_and_clears_secrets(self):
        sandbox = OwnedLocalJudgeSandbox(RUN_ID)
        self.assertIsInstance(LocalJudgeSandboxError("x"), RuntimeContractError)
        with self.assertRaises(LocalJudgeSandboxError):
            _ = sandbox.provider_url
        sandbox.start()
        provider_url = sandbox.provider_url
        judge_url = sandbox.judge_base_url
        judge_key = sandbox.judge_api_key
        self.assertRegex(provider_url, r"^http://127\.0\.0\.1:\d+/provider/[0-9a-f]{32}$")
        self.assertRegex(judge_url, r"^http://127\.0\.0\.1:\d+/judge/[0-9a-f]{32}$")
        self.assertNotEqual(provider_url, judge_url)
        self.assertGreaterEqual(len(judge_key), 32)
        sandbox.validate_live(RUN_ID)
        with self.assertRaises(LocalJudgeSandboxError):
            sandbox.validate_live("foreign-run")
        sandbox.arm_provider(PROVIDER_ID, BOT_ID, PROVIDER_TOKEN)
        sandbox.close()
        self.assertEqual(sandbox.snapshot()["state"], "CLOSED")
        self.assertIsNone(sandbox._judge_api_key)
        self.assertIsNone(sandbox._provider_token)
        self.assertIsNone(sandbox._provider_id)
        self.assertIsNone(sandbox._bot_id)
        self.assertFalse(sandbox._thread.is_alive())
        with self.assertRaises(LocalJudgeSandboxError):
            _ = sandbox.judge_api_key
        self.assertNotIn(judge_key, repr(sandbox.snapshot()))
        self.assertNotIn(PROVIDER_TOKEN, repr(sandbox.snapshot()))

    def test_three_provider_receipts_are_exact_filtered_and_never_cleared(self):
        sandbox = self.start()
        ids = ["implicit-review-1", "explicit-review-1", "publish-1"]
        for delivery_id in ids:
            status, payload = self.provider_post(sandbox, delivery_id)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload), {"ok": True})
        explicit = sandbox.wait_provider_request(ids[1], time.monotonic() + 1)
        implicit = sandbox.wait_provider_delivery(ids[0], time.monotonic() + 1)
        publish = sandbox.wait_provider_request(ids[2], time.monotonic() + 1)
        self.assertIsInstance(explicit, ProviderReceipt)
        self.assertEqual(explicit.delivery_request_id, ids[1])
        self.assertEqual(explicit.transport_request_id, "transport-" + ids[1])
        self.assertEqual(implicit.delivery_request_id, ids[0])
        self.assertEqual(publish.delivery_request_id, ids[2])
        self.assertEqual(explicit.provider_id, PROVIDER_ID)
        self.assertEqual(explicit.provider_bot_ref, RUN_ID)
        self.assertEqual(explicit.bot_id, BOT_ID)
        snapshot = sandbox.snapshot()
        self.assertEqual(
            [item["delivery_request_id"] for item in snapshot["provider_receipts"]], ids
        )
        serialized = json.dumps(snapshot)
        self.assertNotIn(PROVIDER_TOKEN, serialized)
        self.assertNotIn("produce the next artifact", serialized)
        status, _ = self.post(sandbox.provider_url.rsplit("/provider/", 1)[0] + "/control/provider/clear", {})
        self.assertEqual(status, 400)
        self.assertTrue(sandbox.snapshot()["failed"])
        self.assertEqual(len(sandbox.snapshot()["provider_receipts"]), 3)

    def test_fourth_provider_request_or_foreign_principal_latches_failure(self):
        sandbox = self.start()
        for index in range(3):
            self.assertEqual(self.provider_post(sandbox, f"delivery-{index}")[0], 200)
        self.assertEqual(self.provider_post(sandbox, "delivery-4")[0], 400)
        self.assertTrue(sandbox.snapshot()["failed"])
        with self.assertRaises(LocalJudgeSandboxError):
            sandbox.wait_provider_request("delivery-4", time.monotonic() + 0.1)

        foreign = self.start()
        self.assertEqual(self.provider_post(foreign, "delivery-x", token="wrong")[0], 400)
        self.assertTrue(foreign.snapshot()["failed"])
        self.assertEqual(foreign.snapshot()["provider_receipts"], [])

    def test_judge_blocks_until_owner_release_then_returns_original_approval(self):
        sandbox = self.start()
        result = []
        thread = threading.Thread(target=self.judge_post, args=(sandbox,), kwargs={"result": result})
        thread.start()
        receipt = sandbox.wait_judge_started(time.monotonic() + 2)
        self.assertEqual(receipt.model, "local-judge-model")
        self.assertEqual(receipt.message_count, 2)
        self.assertEqual(receipt.response_format_type, "json_schema")
        time.sleep(0.05)
        self.assertTrue(thread.is_alive())
        sandbox.release_judge()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        status, raw = result[0]
        self.assertEqual(status, 200)
        response = json.loads(raw)
        decision = json.loads(response["choices"][0]["message"]["content"])
        self.assertEqual(decision, {
            "outcome": "approved",
            "reason": "candidate satisfies the E2E criteria",
            "confidence": 0.99,
            "checked_criteria": [],
            "retry_instruction": "",
        })
        self.assertTrue(sandbox.snapshot()["judge_finished"])
        with self.assertRaises(LocalJudgeSandboxError):
            sandbox.release_judge()
        second = self.judge_post(sandbox)
        self.assertEqual(second[0], 400)
        self.assertTrue(sandbox.snapshot()["failed"])

    def test_close_during_blocked_judge_unblocks_with_error_not_approval(self):
        sandbox = self.start()
        result = []
        key = sandbox.judge_api_key
        thread = threading.Thread(
            target=self.judge_post,
            args=(sandbox,),
            kwargs={"key": key, "result": result},
        )
        thread.start()
        sandbox.wait_judge_started(time.monotonic() + 2)
        sandbox.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sandbox.snapshot()["state"], "CLOSED")
        self.assertFalse(sandbox.snapshot()["judge_finished"])
        self.assertEqual(len(result), 1)
        if isinstance(result[0], tuple):
            self.assertNotEqual(result[0][0], 200)
            self.assertNotIn(b'"outcome":"approved"', result[0][1])
        else:
            self.assertIsInstance(result[0], BaseException)
        self.assertIsNone(sandbox._judge_api_key)
        self.assertIsNone(sandbox._provider_token)

    def test_wrong_judge_key_and_deadline_misuse_fail_closed(self):
        sandbox = self.start()
        status, _ = self.judge_post(sandbox, key="wrong-key")
        self.assertEqual(status, 400)
        self.assertTrue(sandbox.snapshot()["failed"])

        fresh = self.start()
        with self.assertRaises(LocalJudgeSandboxError):
            fresh.wait_judge_started(time.monotonic() + 5.5)
        with self.assertRaises(LocalJudgeSandboxError):
            fresh.wait_provider_request("unknown", time.monotonic() + 20.5)
        with self.assertRaises(LocalJudgeSandboxError):
            fresh.release_judge()



class ScriptedJudgeOutcomeTests(LocalJudgeSandboxTests):
    def start_scripted(self, outcomes, **kwargs):
        sandbox = OwnedLocalJudgeSandbox(RUN_ID, judge_outcomes=outcomes, **kwargs).start()
        self.addCleanup(sandbox.close)
        sandbox.arm_provider(PROVIDER_ID, BOT_ID, PROVIDER_TOKEN)
        return sandbox

    def test_scripted_outcomes_are_consumed_in_order(self):
        sandbox = self.start_scripted(("revise", "approved"))
        self.assertEqual(sandbox.judge_admissions, 2)
        self.assertEqual(sandbox.judge_request_count, 0)
        results = []
        threads = []
        for _ in range(2):
            captured = []
            thread = threading.Thread(
                target=self.judge_post, args=(sandbox,), kwargs={"result": captured})
            thread.start()
            threads.append(thread)
            results.append(captured)
        first = sandbox.wait_judge_started(time.monotonic() + 2, 0)
        second = sandbox.wait_judge_started(time.monotonic() + 2, 1)
        self.assertEqual(first.model, "local-judge-model")
        self.assertEqual(second.message_count, 2)
        self.assertEqual(sandbox.judge_request_count, 2)
        time.sleep(0.05)
        self.assertTrue(all(thread.is_alive() for thread in threads))
        sandbox.release_judge(0)
        threads[0].join(5)
        self.assertFalse(threads[0].is_alive())
        sandbox.release_judge(1)
        threads[1].join(5)
        self.assertFalse(threads[1].is_alive())
        outcomes = []
        for captured in results:
            self.assertEqual(len(captured), 1)
            status, raw = captured[0]
            self.assertEqual(status, 200)
            decision = json.loads(json.loads(raw)["choices"][0]["message"]["content"])
            self.assertEqual(decision["reason"], "scripted E2E Judge outcome")
            self.assertEqual(decision["confidence"], 1.0)
            outcomes.append(decision["outcome"])
        self.assertEqual(outcomes, ["revise", "approved"])
        self.assertTrue(sandbox.snapshot()["judge_finished"])
        self.assertEqual(sandbox.snapshot()["judge_outcomes"], ["revise", "approved"])
        with self.assertRaises(LocalJudgeSandboxError):
            sandbox.release_judge(1)
        third = self.judge_post(sandbox)
        self.assertEqual(third[0], 400)
        self.assertTrue(sandbox.snapshot()["failed"])

    def test_scripted_sequence_admits_exactly_the_declared_set(self):
        sandbox = self.start_scripted(("revise", "revise"))
        for index in range(2):
            captured = []
            thread = threading.Thread(
                target=self.judge_post, args=(sandbox,), kwargs={"result": captured})
            thread.start()
            sandbox.wait_judge_started(time.monotonic() + 2, index)
            sandbox.release_judge(index)
            thread.join(3)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0][0], 200)
        with self.assertRaises(LocalJudgeSandboxError):
            sandbox.wait_judge_started(time.monotonic() + 0.1, 2)
        with self.assertRaises(LocalJudgeSandboxError):
            sandbox.release_judge(2)

    def test_default_sandbox_keeps_the_legacy_single_fixed_decision(self):
        sandbox = self.start()
        self.assertEqual(sandbox.judge_admissions, 1)
        self.assertEqual(sandbox.judge_request_count, 0)
        self.assertIsNone(sandbox.snapshot()["judge_outcomes"])
        self.assertEqual(sandbox.snapshot()["provider_receipt_limit"], 3)

    def test_extended_provider_receipt_limit_admits_four_deliveries(self):
        sandbox = self.start_scripted(("revise", "approved"), provider_receipt_limit=4)
        for index in range(4):
            status, _ = self.provider_post(sandbox, f"delivery-{index}")
            self.assertEqual(status, 200)
        receipt = sandbox.wait_provider_request("delivery-3", time.monotonic() + 2)
        self.assertEqual(receipt.method, "chat.send")
        self.assertEqual(len(sandbox.snapshot()["provider_receipts"]), 4)
        overflow = self.provider_post(sandbox, "delivery-overflow")
        self.assertEqual(overflow[0], 400)
        self.assertTrue(sandbox.snapshot()["failed"])

    def test_invalid_scripted_outcomes_or_receipt_limits_fail_closed(self):
        for value in ((), "revise", ["revise"] * 9, ["revise", 3], [""]):
            with self.subTest(judge_outcomes=value):
                with self.assertRaises(RuntimeContractError):
                    OwnedLocalJudgeSandbox(RUN_ID, judge_outcomes=value)
        for value in (0, 17, True, "3"):
            with self.subTest(provider_receipt_limit=value):
                with self.assertRaises(RuntimeContractError):
                    OwnedLocalJudgeSandbox(RUN_ID, provider_receipt_limit=value)


if __name__ == "__main__":
    unittest.main()
