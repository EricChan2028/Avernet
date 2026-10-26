from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.fixtures import apply_fixture_plan, build_fixture_plan
from bcs_e2e.profile import load


ROOT = Path(__file__).resolve().parents[1]


class FixtureTests(unittest.TestCase):
    def test_mock_plan_is_offline_and_declares_no_friendship_mutation(self):
        profile = load(ROOT / "profiles/local.mock.json")
        with patch("bcs_e2e.transport.build_opener") as read, patch("bcs_e2e.write_transport.build_opener") as write:
            plan = build_fixture_plan(profile)
        self.assertEqual(plan["status"], "READY")
        self.assertTrue(plan["ready_to_apply"])
        self.assertIn("friendship is not mutated", plan["required_relations"])
        read.assert_not_called()
        write.assert_not_called()

    def test_apply_mock_only_writes_fixture_declaration(self):
        profile = load(ROOT / "profiles/local.mock.json")
        with tempfile.TemporaryDirectory() as tmp:
            result = apply_fixture_plan(profile, Path(tmp), "fixture-1")
            self.assertEqual(result["status"], "APPLIED")
            self.assertFalse(result["network_contacted"])
            fixture = json.loads((Path(tmp) / "fixture.json").read_text())
            self.assertEqual(fixture["friendship"], "unchanged")

    def test_singlebox_apply_is_blocked_and_cli_requires_confirmation(self):
        profile = load(ROOT / "profiles/local.singlebox.example.json")
        plan = build_fixture_plan(profile)
        self.assertEqual(plan["status"], "BLOCKED")
        with tempfile.TemporaryDirectory() as tmp:
            output = []
            with patch("sys.stdout.write", side_effect=output.append):
                rc = main(["fixture", "apply", "--profile", str(ROOT / "profiles/local.singlebox.example.json"),
                           "--state-dir", str(Path(tmp)), "--run-id", "fixture-2"])
            self.assertEqual(rc, 2)
            self.assertFalse((Path(tmp) / "fixture.json").exists())


if __name__ == "__main__":
    unittest.main()

class FixtureApplySafetyTests(unittest.TestCase):
    def profile(self):
        return load(ROOT / "profiles/local.mock.json")

    def test_apply_is_create_once_and_does_not_overwrite_existing_declaration(self):
        profile = self.profile()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            first = apply_fixture_plan(profile, state, "fixture-once")
            self.assertEqual(first["status"], "APPLIED")
            before = (state / "fixture.json").read_bytes()
            second = apply_fixture_plan(profile, state, "fixture-once")
            self.assertEqual(second["status"], "BLOCKED")
            self.assertEqual((state / "fixture.json").read_bytes(), before)

    def test_existing_run_must_match_profile_and_run_id(self):
        profile = self.profile()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "run.json").write_text(json.dumps({
                "schema_version": 1, "status": "PREPARED", "run_id": "other-run",
                "profile_digest": "wrong", "ready_to_execute": False,
            }))
            result = apply_fixture_plan(profile, state, "fixture-once")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertFalse((state / "fixture.json").exists())

    def test_symlink_fixture_declaration_is_not_followed(self):
        profile = self.profile()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            target = state / "outside.json"
            target.write_text("outside")
            (state / "fixture.json").symlink_to(target)
            result = apply_fixture_plan(profile, state, "fixture-once")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(target.read_text(), "outside")
