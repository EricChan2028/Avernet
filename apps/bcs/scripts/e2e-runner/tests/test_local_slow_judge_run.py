"""Focused real-runtime integration for the owned-local slow-Judge Story command."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.local_slow_judge_reporting import build_slow_judge_report
from bcs_e2e.local_slow_judge_run import local_slow_judge_run
from bcs_e2e.owned_bcs import file_sha256
from bcs_e2e.local_judge_contract import CASE_ID, SUITE

BCS_BINARY = ROOT.parents[1] / 'target' / 'debug' / 'bcs'


@unittest.skipUnless(BCS_BINARY.is_file(), 'owned BCS debug binary is not available')
class OwnedLocalSlowJudgeRunTests(unittest.TestCase):
    def test_story_command_executes_publishes_and_offline_report_passes(self):
        run_id = 'slow-judge-flow-1'
        with tempfile.TemporaryDirectory(prefix='slow-judge-flow-') as tmp:
            state_dir = Path(tmp) / 'runtime'
            summary = local_slow_judge_run(
                binary=BCS_BINARY, binary_sha256=file_sha256(BCS_BINARY),
                state_dir=state_dir, run_id=run_id, confirm_run_id=run_id,
                allow_local_processes=True, allow_fixture_write=True, allow_write=True,
            )

            self.assertEqual(summary['status'], 'LOCAL_SLOW_JUDGE_PASS')
            self.assertEqual(summary['case_id'], CASE_ID)
            self.assertEqual(summary['full_release_status'], 'NOT_QUALIFIED')
            self.assertEqual(summary['canonical_summary']['scope']['suite'], SUITE)
            self.assertEqual(summary['canonical_summary']['status'], 'LOCAL_SUITE_PASS')
            self.assertEqual(summary['case']['completed_count'], 22)
            self.assertEqual(summary['case']['total_count'], 22)
            self.assertTrue(summary['case']['ready_to_execute'])
            self.assertEqual(summary['shutdown']['status'], 'STOPPED')
            self.assertEqual(summary['shutdown']['returncode'], 0)
            self.assertEqual(summary['judge_sandbox']['state'], 'CLOSED')
            self.assertFalse(summary['judge_sandbox']['failed'])

            offline = build_slow_judge_report(state_dir)
            self.assertEqual(offline['status'], 'LOCAL_SUITE_PASS')
            self.assertEqual(offline['run_id'], run_id)
            self.assertEqual(offline['reports']['observed_assertion_count'], 22)
            self.assertEqual(len(offline['requirements']), 22)
            self.assertTrue(all(row['status'] == 'PASS' for row in offline['requirements']))
            self.assertEqual(offline['scope']['full_release_status'], 'NOT_QUALIFIED')
            self.assertFalse(offline['ready_to_execute'])


if __name__ == '__main__':
    unittest.main()
