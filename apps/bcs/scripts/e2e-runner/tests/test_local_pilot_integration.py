from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.cli import main
from bcs_e2e.profile import load


ROOT = Path(__file__).resolve().parents[1]


class LocalPilotIntegrationTests(unittest.TestCase):
    def call(self, arguments: list[str], expected: int) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            actual = main(arguments)
        self.assertEqual(actual, expected, msg=output.getvalue())
        return json.loads(output.getvalue())

    def test_three_pilot_cases_report_local_surrogate_and_gate_stays_unqualified(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            tmp = Path(tmp)
            profile = load(ROOT / "profiles/local.mock.json")
            profile["policy"]["fixture_pool"] = "integration-test-pool"
            profile_path = tmp / "profile.json"
            profile_path.write_text(json.dumps(profile))
            state = tmp / "state"
            run_id = "integration-run"
            with patch("bcs_e2e.pool.pool_root", return_value=tmp / "pools"):
                self.assertEqual(self.call([
                    "prepare", "--profile", str(profile_path), "--run-id", run_id,
                    "--state-dir", str(state),
                ], 0)["status"], "PREPARED")
                for case_id in ("PRE-01", "PRE-02", "PRE-03"):
                    result = self.call([
                        "run", "--profile", str(profile_path), "--state-dir", str(state),
                        "--run-id", run_id, "--case", case_id,
                        "--allow-write", "--confirm-run-id", run_id,
                    ], 0)
                    self.assertEqual(result["status"], "PASS")
                    self.assertEqual(result["evidence_status"], "LOCAL_SURROGATE")
                    self.assertEqual(result["cleanup"], "PASS")
                report = self.call([
                    "report", "--profile", str(profile_path), "--state-dir", str(state),
                    "--format", "json",
                ], 0)["summary"]
                self.assertEqual(report["status"], "PILOT_PASS")
                self.assertEqual(report["scope"]["full_release_status"], "NOT_QUALIFIED")
                self.assertEqual([case["status"] for case in report["cases"]],
                                 ["PASS", "PASS", "PASS"])
                self.assertGreater(len(report["correlations"]), 0)
                gate = self.call([
                    "gate", "--profile", str(profile_path), "--state-dir", str(state),
                ], 2)
                self.assertEqual(gate["status"], "NOT_QUALIFIED")
                self.assertIn("frozen release inventory is not qualified", gate["reasons"])


if __name__ == "__main__":
    unittest.main()
