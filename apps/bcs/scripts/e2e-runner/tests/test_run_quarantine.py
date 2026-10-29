"""Faults cannot make an unresolved fixture pool available to another Run."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import PilotError, run_pre01, run_pre02
from bcs_e2e.pool import PoolBusyError, PoolLease, pool_path, pool_scope
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import read
from bcs_e2e.write_transport import WriteTransportError


class RunQuarantineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "run"
        self.profile_path = ROOT / "profiles/local.mock.json"
        self.profile = load(self.profile_path)
        registry = patch("bcs_e2e.pool.pool_root", return_value=self.root / "registry")
        registry.start()
        self.addCleanup(registry.stop)
        self.lock_path = pool_path(pool_scope(self.profile))
        self.assertEqual(self.cli("prepare", "--run-id", "run-a")[0], 0)

    def cli(self, command, *args):
        output = io.StringIO()
        profile = [] if command == "release" else ["--profile", str(self.profile_path)]
        with contextlib.redirect_stdout(output):
            rc = main([command, *profile, "--state-dir", str(self.state), *args])
        return rc, json.loads(output.getvalue())

    def assert_reserved(self):
        self.assertTrue(self.lock_path.is_file())
        with self.assertRaises(PoolBusyError):
            PoolLease.acquire(self.lock_path, "run-b", fingerprint(self.profile))

    def test_lost_create_response_keeps_pool_and_blocks_release_or_cleanup(self):
        adapter = LocalMockAdapter(self.profile)
        with patch("bcs_e2e.pilot.adapter_for", return_value=adapter), \
             patch.object(adapter, "group_create", side_effect=WriteTransportError("lost response")):
            report = run_pre01(self.profile, self.state, "run-a")
        self.assertEqual(report["status"], "FAIL")
        self.assert_reserved()
        self.assertEqual(self.cli("release")[0], 2)
        rc, result = self.cli("cleanup", "--allow-write", "--confirm-run-id", "run-a")
        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "BLOCKED")
        self.assert_reserved()
        # Dry-run must not overwrite the real cleanup failure or free the pool.
        before = (self.state / "run.json").read_bytes()
        self.cli("cleanup", "--dry-run")
        self.assertEqual((self.state / "run.json").read_bytes(), before)

    def test_cleanup_failure_recoverable_with_same_adapter_and_exact_resources(self):
        adapter = PersistentLocalMockAdapter(self.profile, self.state, case_id="PRE-01")
        with patch("bcs_e2e.pilot.adapter_for", return_value=adapter), \
             patch.object(adapter, "group_delete", side_effect=WriteTransportError("HTTP 403")):
            report = run_pre01(self.profile, self.state, "run-a")
        self.assertEqual(report["cleanup"], "BLOCKED")
        self.assert_reserved()
        self.assertEqual(self.cli("release")[0], 2)
        with patch("bcs_e2e.cli.adapter_for", return_value=adapter):
            rc, result = self.cli("cleanup", "--allow-write", "--confirm-run-id", "run-a")
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(self.lock_path.exists())
        self.assertEqual(read(self.state / "run.json")["status"], "FAILED")

    def test_cancellation_preserves_reservation_and_unverified_activity(self):
        adapter = LocalMockAdapter(self.profile)
        with patch("bcs_e2e.pilot.adapter_for", return_value=adapter), \
             patch.object(adapter, "group_create", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_pre01(self.profile, self.state, "run-a")
        self.assertEqual(read(self.state / "run.json")["status"], "QUARANTINED")
        self.assert_reserved()
        self.assertEqual(self.cli("release")[0], 2)
        with patch("bcs_e2e.cli.adapter_for") as factory:
            self.assertEqual(self.cli("cleanup", "--allow-write", "--confirm-run-id", "run-a")[0], 2)
            factory.assert_not_called()

    def test_repeated_case_is_rejected_before_new_intent_or_network(self):
        run_pre01(self.profile, self.state, "run-a")
        report_bytes = (self.state / "report-PRE-01.json").read_bytes()
        with patch("bcs_e2e.pilot.adapter_for") as factory:
            with self.assertRaisesRegex(PilotError, "already attempted"):
                run_pre01(self.profile, self.state, "run-a")
            factory.assert_not_called()
        self.assertFalse(self.lock_path.exists())
        self.assertEqual((self.state / "report-PRE-01.json").read_bytes(), report_bytes)

    def test_concurrent_case_cannot_enter_while_first_command_is_active(self):
        entered, resume = threading.Event(), threading.Event()
        adapter = LocalMockAdapter(self.profile)
        original = adapter.preflight
        def pause():
            entered.set()
            if not resume.wait(10):
                raise AssertionError("test command not resumed")
            return original()
        outcomes = []
        def run():
            try:
                outcomes.append(run_pre01(self.profile, self.state, "run-a"))
            except BaseException as error:
                outcomes.append(error)
        with patch("bcs_e2e.pilot.adapter_for", return_value=adapter), \
             patch.object(adapter, "preflight", side_effect=pause):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises((PilotError, PoolBusyError)):
                    run_pre02(self.profile, self.state, "run-a")
                self.assertEqual(self.cli("release")[0], 2)
            finally:
                resume.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)
        self.assertEqual(outcomes[0]["status"], "PASS")
        self.assertFalse(self.lock_path.exists())


if __name__ == "__main__":
    unittest.main()
