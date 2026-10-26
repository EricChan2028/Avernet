"""Run/profile mismatch must fail before credentials, network or state mutation."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_pre01, run_pre02, run_pre03
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import RunStateError, read, write
from bcs_e2e.runtime import RuntimeContractError

ROOT = Path(__file__).resolve().parents[1]


class RunBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.profile = load(ROOT / "profiles/local.mock.json")
        self.digest = fingerprint(self.profile)
        self.state_path = self.state_dir / "run.json"
        self.ledger_path = self.state_dir / "run.sqlite"
        self.lock_path = self.state_dir / "fixture-pool.lock"
        write(self.state_path, {
            "schema_version": 1, "status": "PREPARED", "run_id": "bound-run",
            "profile_digest": self.digest, "ledger_path": str(self.ledger_path),
            "lock_path": str(self.lock_path),
        })

    def changed_profiles(self):
        for section, key, value in (
            ("environment", "id", "another-env"),
            ("entrypoint", "origin", "mock://another"),
            ("human", "user_id", "another-human"),
            ("policy", "fixture_pool", "another-pool"),
        ):
            profile = copy.deepcopy(self.profile)
            profile[section][key] = value
            yield section, profile

    def test_all_story_entrypoints_reject_changed_profile_before_state_or_adapter(self):
        original = self.state_path.read_bytes()
        for execute in (run_pre01, run_pre02, run_pre03):
            for field, profile in self.changed_profiles():
                with self.subTest(case=execute.__name__, field=field), \
                     patch("bcs_e2e.pilot.adapter_for") as adapter, \
                     patch("bcs_e2e.pilot._case_lease") as lease, \
                     patch("bcs_e2e.pilot.Ledger") as ledger:
                    with self.assertRaisesRegex(RunStateError, "profile does not match"):
                        execute(profile, self.state_dir, "bound-run")
                    adapter.assert_not_called()
                    lease.assert_not_called()
                    ledger.assert_not_called()
                    self.assertEqual(self.state_path.read_bytes(), original)

    def test_all_stories_refuse_unverified_legacy_runtime_before_leasing(self):
        (self.state_dir / "runtime.json").write_text("unverified legacy state")
        for execute in (run_pre01, run_pre02, run_pre03):
            with self.subTest(case=execute.__name__), \
                 patch("bcs_e2e.pilot.adapter_for") as adapter, \
                 patch("bcs_e2e.pilot._case_lease") as lease:
                with self.assertRaisesRegex(RuntimeContractError, "legacy runtime"):
                    execute(self.profile, self.state_dir, "bound-run")
                adapter.assert_not_called()
                lease.assert_not_called()

    def call_cleanup(self, profile, *flags):
        profile_path = self.state_dir / "profile.json"
        profile_path.write_text(json.dumps(profile))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(["cleanup", "--profile", str(profile_path),
                       "--state-dir", str(self.state_dir), *flags])
        return rc, json.loads(output.getvalue())

    def test_cleanup_mismatch_blocks_before_ledger_adapter_or_lease(self):
        profile = copy.deepcopy(self.profile)
        profile["environment"]["id"] = "another-env"
        for flags in (("--dry-run",), ("--allow-write", "--confirm-run-id", "bound-run")):
            with self.subTest(flags=flags), patch("bcs_e2e.cli.adapter_for") as adapter, \
                 patch("bcs_e2e.cli.Ledger") as ledger, \
                 patch("bcs_e2e.cli.PoolLease.acquire") as lease:
                rc, result = self.call_cleanup(profile, *flags)
                self.assertEqual(rc, 2)
                self.assertIn("profile does not match", result["reason"])
                adapter.assert_not_called()
                ledger.assert_not_called()
                lease.assert_not_called()
                self.assertFalse((self.state_dir / "cleanup.json").exists())

    def test_remote_dry_run_needs_no_credentials_or_adapter(self):
        profile = load(ROOT / "profiles/preprod.example.json")
        digest = fingerprint(profile)
        state = read(self.state_path)
        state["profile_digest"] = digest
        write(self.state_path, state)
        with Ledger(self.ledger_path, "bound-run", digest, create=True) as ledger:
            handle = ledger.intent("PRE-01", 1, "group", "request-1")
            ledger.observed(handle, "group-1", origin="created")
        with patch("bcs_e2e.cli.adapter_for", side_effect=AssertionError("must stay offline")), \
             patch("bcs_e2e.cli.PoolLease.acquire") as lease:
            rc, result = self.call_cleanup(profile, "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual([r["resource_id"] for r in result["resources"]], ["group-1"])
        lease.assert_not_called()
        with Ledger(self.ledger_path, "bound-run", digest) as ledger:
            self.assertEqual(ledger.cleanup_plan()["resources"][0]["state"], "live")

    def test_mismatching_ledger_returns_blocked_not_a_traceback(self):
        with Ledger(self.ledger_path, "different-run", self.digest, create=True):
            pass
        with patch("bcs_e2e.cli.adapter_for") as adapter:
            rc, result = self.call_cleanup(self.profile, "--dry-run")
        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "BLOCKED")
        adapter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
