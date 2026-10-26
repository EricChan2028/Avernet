from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.pilot import evaluate
from bcs_e2e.reporting import build_report
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import write

ROOT = Path(__file__).resolve().parents[1]


class QualificationTests(unittest.TestCase):
    def test_pilot_pass_is_not_full_qualification(self):
        summary = {"status": "PILOT_PASS", "run_id": "r", "integrity": {"status": "PASS"},
                   "scope": {"full_release_status": "NOT_QUALIFIED"},
                   "cases": [{"status": "PASS", "evidence_status": "LOCAL_SURROGATE"}]}
        result = evaluate(summary)
        self.assertEqual(result["status"], "NOT_QUALIFIED")
        self.assertIn("frozen release inventory", " ".join(result["reasons"]))

    def test_blocked_evidence_and_case_failures_are_not_qualified(self):
        result = evaluate({"status": "BLOCKED", "integrity": {"status": "BLOCKED"},
                           "scope": {"full_release_status": "NOT_QUALIFIED"},
                           "cases": [{"status": "BLOCKED", "evidence_status": "BLOCKED"}]})
        self.assertEqual(result["status"], "NOT_QUALIFIED")
        self.assertGreaterEqual(len(result["reasons"]), 3)

    def test_gate_cli_uses_run_profile_binding_and_returns_nonzero_for_pilot(self):
        profile = load(ROOT / "profiles/local.mock.json")
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            write(state / "run.json", {"schema_version": 1, "status": "COMPLETED", "run_id": "r",
                                       "profile_digest": fingerprint(profile)})
            output = []
            with patch("sys.stdout.write", side_effect=output.append):
                rc = main(["gate", "--profile", str(ROOT / "profiles/local.mock.json"),
                           "--state-dir", str(state)])
            self.assertEqual(rc, 2)
            self.assertIn("NOT_QUALIFIED", "".join(output))


if __name__ == "__main__":
    unittest.main()
