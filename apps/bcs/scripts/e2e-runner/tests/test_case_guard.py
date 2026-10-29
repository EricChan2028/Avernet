from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
GUARD = ROOT / "scripts/ci/e2e_runner_case_guard.py"
RUNNER = ROOT / "apps/bcs/scripts/e2e-runner/bcs_e2e/pilot.py"


class CaseGuardTests(unittest.TestCase):
    def run_guard(self, path: Path):
        return subprocess.run([sys.executable, str(GUARD), str(path)],
                              capture_output=True, text=True, check=False)

    def test_migrated_pilot_has_no_raw_network_sql_process_or_identity_bypass(self):
        for path in (RUNNER, RUNNER.with_name('control_steps.py'), RUNNER.with_name('file_steps.py')):
            with self.subTest(path=path.name):
                result = self.run_guard(path)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_trust_api_business_steps_use_only_governed_adapter_calls(self):
        result = self.run_guard(RUNNER.with_name('trust_api_steps.py'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_guard_rejects_raw_transport_process_sql_and_principal_literals(self):
        bad_sources = (
            "import requests\nrequests.get('https://example.test')\n",
            "import subprocess\nsubprocess.run(['kill', '-9', '1'])\n",
            "import sqlite3\nconn.execute('DELETE FROM bcs_groups')\n",
            "headers = {'X-Avernet-Principal': 'fake'}\n",
            "token = 'Authorization: Bearer secret'\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            for index, source in enumerate(bad_sources):
                path = Path(tmp) / f"bad-{index}.py"
                path.write_text(source)
                with self.subTest(index=index):
                    result = self.run_guard(path)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("E2E CASE GUARD FAILED", result.stdout)

    def test_guard_reports_syntax_errors_and_allows_reviewed_adapter_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.py"
            good.write_text("adapter.group_create(body)\nledger.intent('PRE-01', 1, 'group', 'req')\n")
            result = self.run_guard(good)
            self.assertEqual(result.returncode, 0)
            broken = Path(tmp) / "broken.py"
            broken.write_text("def broken(:\n")
            result = self.run_guard(broken)
            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot parse", result.stdout)


if __name__ == "__main__":
    unittest.main()
