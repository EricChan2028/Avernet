from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import subprocess
import os
import unittest
from unittest.mock import patch
import contextlib
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.pool import PoolBusyError, PoolLease, PoolLeaseError, inspect_pool, pool_path, pool_scope
from bcs_e2e.profile import fingerprint, load


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pool.lock"
        self.digest = "a" * 64

    def tearDown(self):
        self.tmp.cleanup()

    def test_acquire_is_exclusive_and_metadata_is_redacted_from_public_view(self):
        lease = PoolLease.acquire(self.path, "run-one", self.digest)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(lease.public_view()["status"], "ACQUIRED")
        self.assertNotIn("lease_token", lease.public_view())
        with self.assertRaises(PoolBusyError):
            PoolLease.acquire(self.path, "run-two", self.digest)
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["run_id"], "run-one")
        self.assertEqual(payload["profile_digest"], self.digest)
        lease.release()
        self.assertFalse(self.path.exists())

    def test_cli_pool_inspect_is_read_only_and_reports_lease_state(self):
        profile = load(Path(__file__).resolve().parents[1] / "profiles/local.mock.json")
        digest = fingerprint(profile)
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp)
            with patch("bcs_e2e.pool.pool_root", return_value=registry):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(["pool", "inspect", "--profile",
                                           str(Path(__file__).resolve().parents[1] / "profiles/local.mock.json")]), 0)
                self.assertEqual(json.loads(output.getvalue())["status"], "AVAILABLE")
                path = pool_path(pool_scope(profile))
                lease = PoolLease.acquire(path, "inspect-run", digest)
                lease.close()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(["pool", "inspect", "--profile",
                                           str(Path(__file__).resolve().parents[1] / "profiles/local.mock.json")]), 2)
                self.assertEqual(json.loads(output.getvalue())["status"], "LEASED")
                PoolLease.from_file(path, "inspect-run", digest).release()

    def test_inspect_rejects_dangling_symlink_instead_of_reporting_available(self):
        target = Path(self.tmp.name) / "missing"
        self.path.symlink_to(target)
        result = inspect_pool(self.path)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("symlink", result["reason"])

    def test_inspect_reports_dead_lease_without_reclaiming_or_exposing_token(self):
        lease = PoolLease.acquire(self.path, "run-one", self.digest)
        payload = json.loads(self.path.read_text())
        payload["pid"] = -1
        self.path.write_text(json.dumps(payload))
        lease.close()
        result = inspect_pool(self.path)
        self.assertEqual(result["status"], "LEASED")
        self.assertFalse(result["process_alive"])
        self.assertTrue(result["guard_present"])
        self.assertNotIn("lease_token", result)
        self.assertTrue(self.path.exists())
        PoolLease.from_file(self.path, "run-one", self.digest).release()

    def test_inspect_missing_pool_is_available(self):
        result = inspect_pool(self.path)
        self.assertEqual(result, {"status": "AVAILABLE", "lock_path": str(self.path),
                                  "process_alive": False})

    def test_existing_lock_is_not_stolen_even_when_pid_is_dead(self):
        lease = PoolLease.acquire(self.path, "run-one", self.digest)
        payload = json.loads(self.path.read_text())
        payload["pid"] = -1
        self.path.write_text(json.dumps(payload))
        with self.assertRaises(PoolBusyError):
            PoolLease.acquire(self.path, "run-two", self.digest)
        lease.close()
        lease = PoolLease.from_file(self.path, "run-one", self.digest)
        lease.release()

    def test_wrong_run_or_profile_cannot_release(self):
        lease = PoolLease.acquire(self.path, "run-one", self.digest)
        lease.close()
        with self.assertRaises(PoolLeaseError):
            PoolLease.from_file(self.path, "run-two", self.digest)
        with self.assertRaises(PoolLeaseError):
            PoolLease.from_file(self.path, "run-one", "b" * 64)
        PoolLease.from_file(self.path, "run-one", self.digest).release()

    def test_corrupt_or_symlink_lock_fails_closed(self):
        self.path.write_text("not-json")
        with self.assertRaises(PoolLeaseError):
            PoolLease.from_file(self.path, "run-one", self.digest)
        self.path.unlink()
        target = Path(self.tmp.name) / "target"
        target.write_text("{}")
        self.path.symlink_to(target)
        with self.assertRaises(PoolLeaseError):
            PoolLease.from_file(self.path, "run-one", self.digest)
        with self.assertRaises(PoolLeaseError):
            PoolLease.acquire(self.path, "run-one", self.digest)

    def test_same_run_cannot_be_adopted_twice(self):
        lease = PoolLease.acquire(self.path, "run-one", self.digest)
        try:
            with self.assertRaises(PoolBusyError):
                PoolLease.from_file(self.path, "run-one", self.digest)
            lease.close()
            resumed = PoolLease.from_file(self.path, "run-one", self.digest)
            try:
                with self.assertRaises(PoolBusyError):
                    PoolLease.from_file(self.path, "run-one", self.digest)
                with self.assertRaises(PoolLeaseError):
                    lease.release()
            finally:
                resumed.release()
        finally:
            lease.close()

    def test_process_exit_releases_guard_but_not_durable_reservation(self):
        # The child is our isolated test process, not a product service.
        code = """
import sys
from pathlib import Path
from bcs_e2e.pool import PoolLease
lease = PoolLease.acquire(Path(sys.argv[1]), "run-one", "a" * 64)
print("ACQUIRED", flush=True)
sys.stdin.readline()
"""
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        child = subprocess.Popen([sys.executable, "-c", code, str(self.path)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=environment)
        try:
            import select
            readable, _, _ = select.select([child.stdout], [], [], 10)
            self.assertTrue(readable, "child did not acquire its guard in time")
            self.assertEqual(child.stdout.readline().strip(), "ACQUIRED")
            with self.assertRaises(PoolBusyError):
                PoolLease.from_file(self.path, "run-one", self.digest)
            child.terminate()
            child.communicate(timeout=10)
            self.assertTrue(self.path.exists())
            with self.assertRaises(PoolBusyError):
                PoolLease.acquire(self.path, "run-two", self.digest)
            PoolLease.from_file(self.path, "run-one", self.digest).release()
            self.assertTrue(self.path.with_suffix(".guard").exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
