"""The fixed-Loop per-Run sandbox replaces shared control-plane reset."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_fixed_loop_sandbox import (
    FixedLoopReceiptCursor,
    FixedLoopRunFixture,
    FixedLoopSandboxError,
    SHARED_CONTROL_RESETS,
)


def provider(body: str = "p") -> dict:
    return {"delivery_request_id": "dr-" + body, "body_sha256": body}


def judge(body: str = "j") -> dict:
    return {"body_sha256": body}


class FixedLoopPerRunSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = FixedLoopRunFixture("run-fixed-loop-e2e-1")
        self.fixture.start()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_each_run_provisions_a_fresh_sandbox(self):
        other = FixedLoopRunFixture("run-fixed-loop-e2e-2")
        other.start()
        try:
            # Each Run owns a distinct ephemeral loopback Provider and
            # Judge sandbox; nothing is shared or reused across Runs.
            self.assertNotEqual(
                self.fixture.provider_url, other.provider_url)
            self.assertNotEqual(
                self.fixture.judge_base_url, other.judge_base_url)
            self.assertTrue(self.fixture.fresh_per_run)
            self.assertTrue(other.fresh_per_run)
        finally:
            other.close()

    def test_shared_control_plane_resets_fail_closed(self):
        for path in ("/control/reset", "/control/provider/clear"):
            with self.subTest(path=path):
                with self.assertRaises(FixedLoopSandboxError):
                    self.fixture.forbid_shared_control_reset(path)
        # An unrelated control path is not a shared reset.
        self.assertFalse(
            self.fixture.forbid_shared_control_reset("/control/status"))

    def test_run_that_issues_a_shared_reset_is_rejected(self):
        with self.assertRaises(FixedLoopSandboxError):
            self.fixture.assert_no_shared_reset(
                ["/me/ensure-human", "/control/provider/clear"])
        # Ordinary case routes are not shared resets.
        self.assertTrue(self.fixture.assert_no_shared_reset(
            ["/me/ensure-human",
             "/groups/g1/state-machine-runs",
             "/groups/g1/sessions"]))

    def test_fixture_lifecycle_releases_only_its_own_sandbox(self):
        snapshot = self.fixture.snapshot()
        self.assertEqual(snapshot["fresh_per_run"], True)
        self.assertEqual(snapshot["forbidden_reset_attempts"], 0)
        self.fixture.close()
        closed = self.fixture.snapshot()
        self.assertEqual(closed["state"], "CLOSED")
        # A closed fixture cannot record or serve a Run.
        with self.assertRaises(FixedLoopSandboxError):
            self.fixture.record_iteration(
                1, provider_receipt=provider(), judge_receipt=judge())

    def test_receipt_cursor_is_append_only_and_sequential(self):
        cursor = FixedLoopReceiptCursor("run-fixed-loop-e2e-1")
        cursor.record(1, provider_receipt=provider(), judge_receipt=judge())
        cursor.record(2, provider_receipt=provider("q"),
                      judge_receipt=judge("k"))
        self.assertEqual(cursor.last_iteration, 2)
        # Duplicate iteration is rejected.
        with self.assertRaises(FixedLoopSandboxError):
            cursor.record(2, provider_receipt=provider(),
                          judge_receipt=judge())
        # Skipping an iteration is rejected.
        with self.assertRaises(FixedLoopSandboxError):
            cursor.record(4, provider_receipt=provider(),
                          judge_receipt=judge())

    def test_cursor_preserves_prior_output_and_rejects_stale_ref(self):
        first = self.fixture.record_iteration(
            1, provider_receipt=provider(), judge_receipt=judge())
        # First iteration references no prior output.
        self.assertIsNone(self.fixture.expected_response_ref(1))
        # Second iteration must echo the first output_ref.
        self.assertEqual(
            self.fixture.expected_response_ref(2), first.output_ref)
        self.assertTrue(
            self.fixture.verify_response_ref(2, first.output_ref))
        # A stale or drifted reference is rejected.
        with self.assertRaises(FixedLoopSandboxError):
            self.fixture.verify_response_ref(2, "stale-output-ref")
        # The first iteration cannot fabricate a prior reference.
        with self.assertRaises(FixedLoopSandboxError):
            self.fixture.verify_response_ref(1, first.output_ref)

    def test_snapshot_is_sanitized_and_additive(self):
        self.fixture.record_iteration(
            1, provider_receipt=provider(), judge_receipt=judge())
        before = json.dumps(self.fixture.snapshot())
        self.fixture.record_iteration(
            2, provider_receipt=provider("q"), judge_receipt=judge("k"))
        after = json.dumps(self.fixture.snapshot())
        # The ledger only ever grows; no earlier receipt is mutated.
        self.assertIn('"iteration": 1', before)
        self.assertIn('"iteration": 1', after)
        self.assertIn('"iteration": 2', after)
        # No URL, host:port, or credential is reported.
        for secret in ("provider_url", "judge_base_url",
                       "judge_api_key", "127.0.0.1"):
            self.assertNotIn(secret, after)

    def test_shared_reset_constants_are_exactly_the_two_control_paths(self):
        self.assertEqual(
            set(SHARED_CONTROL_RESETS),
            {"/control/reset", "/control/provider/clear"})


if __name__ == "__main__":
    unittest.main()
