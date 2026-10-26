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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.pool import PoolLeaseError, bound_pool_path, pool_path, pool_scope
from bcs_e2e.profile import load


class PoolScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = patch("bcs_e2e.pool.pool_root", return_value=self.root / "registry")
        registry.start()
        self.addCleanup(registry.stop)
        self.profile = load(ROOT / "profiles/local.mock.json")

    def prepare(self, directory, run_id, profile):
        profile_path = self.root / (directory + "-profile.json")
        profile_path.write_text(json.dumps(profile))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(["prepare", "--profile", str(profile_path), "--run-id", run_id,
                       "--state-dir", str(self.root / directory)])
        return rc, json.loads(output.getvalue())

    def test_same_pool_different_run_directories_contend(self):
        self.assertEqual(self.prepare("a", "run-a", self.profile)[0], 0)
        rc, result = self.prepare("nested-b", "run-b", self.profile)
        self.assertEqual(rc, 2)
        self.assertIn("already leased", result["reason"])
        self.assertFalse((self.root / "nested-b" / "run.sqlite").exists())
        state = json.loads((self.root / "a" / "run.json").read_text())
        self.assertEqual(Path(state["lock_path"]).parent, self.root / "registry")
        self.assertEqual(json.loads(Path(state["lock_path"]).read_text())["run_id"], "run-a")

    def test_changed_credential_or_timeout_cannot_bypass_pool_lease(self):
        self.assertEqual(self.prepare("a", "run-a", self.profile)[0], 0)
        changed = copy.deepcopy(self.profile)
        changed["auth"]["header_env"]["Cookie"] = "OTHER_SECRET_REF"
        changed["policy"]["timeout_seconds"] = 20
        self.assertEqual(self.prepare("b", "run-b", changed)[0], 2)
        self.assertEqual(pool_scope(self.profile), pool_scope(changed))

    def test_separate_pools_and_environment_bindings_are_independent(self):
        self.assertEqual(self.prepare("a", "run-a", self.profile)[0], 0)
        changed = copy.deepcopy(self.profile)
        changed["policy"]["fixture_pool"] = "independent-pool"
        self.assertEqual(self.prepare("b", "run-b", changed)[0], 0)
        changed = copy.deepcopy(self.profile)
        changed["environment"]["id"] = "another-local-environment"
        self.assertEqual(self.prepare("c", "run-c", changed)[0], 0)

    def test_release_then_another_directory_can_prepare(self):
        self.assertEqual(self.prepare("a", "run-a", self.profile)[0], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["release", "--state-dir", str(self.root / "a")]), 0)
        self.assertEqual(self.prepare("b", "run-b", self.profile)[0], 0)

    def test_legacy_or_relocated_lock_binding_is_rejected(self):
        scope = pool_scope(self.profile)
        path = pool_path(scope)
        self.assertEqual(bound_pool_path({"pool_scope": scope, "lock_path": str(path)}), path)
        for state in ({"lock_path": str(self.root / "old.lock")},
                      {"pool_scope": scope, "lock_path": str(self.root / "elsewhere.lock")}):
            with self.assertRaises(PoolLeaseError):
                bound_pool_path(state)


if __name__ == "__main__":
    unittest.main()
