"""Owned-local Edge read adapter live and static safety contracts."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[3]
sys.path.insert(0, str(ROOT))
from bcs_e2e.edge_read_case import (
    ADMISSION_CASE_ID,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_CONNECTIONS_CASE_ID,
)
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256
from bcs_e2e.owned_edge_read_adapter import OwnedEdgeReadAdapter
from bcs_e2e.run_state import RunStateError
from bcs_e2e.edge_read_case import execute_edge_read_case
import tempfile

BCS_BINARY = ROOT.parents[1] / 'target' / 'debug' / 'bcs'
GUARD = REPO_ROOT / 'scripts/ci/e2e_runner_case_guard.py'


class OwnedEdgeReadAdapterGuardTests(unittest.TestCase):
    def test_case_guard_accepts_reviewed_read_adapter(self):
        result = subprocess.run(
            [sys.executable, str(GUARD),
             str(ROOT / 'bcs_e2e/owned_edge_read_adapter.py')],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_adapter_requires_exact_case_and_identity_objects(self):
        with self.assertRaises(RunStateError):
            OwnedEdgeReadAdapter(object(), object(), ADMISSION_CASE_ID)


@unittest.skipUnless(BCS_BINARY.is_file(), 'owned BCS debug binary is not available')
class OwnedEdgeReadAdapterLiveTests(unittest.TestCase):
    def test_both_read_cases_use_one_owned_runtime_without_drift(self):
        run_id = 'edge-read-flow-1'
        with tempfile.TemporaryDirectory(prefix='edge-read-flow-') as tmp:
            state_dir = Path(tmp) / 'runtime'
            with OwnedBcsProcess(binary=BCS_BINARY, binary_sha256=file_sha256(BCS_BINARY),
                                 root=state_dir, run_id=run_id) as runtime:
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=('CEO',)) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    identities.verify('CEO')
                    admission = execute_edge_read_case(
                        ADMISSION_CASE_ID, OwnedEdgeReadAdapter(runtime, identities, ADMISSION_CASE_ID))
                    requests = execute_edge_read_case(
                        FRIEND_REQUESTS_CASE_ID,
                        OwnedEdgeReadAdapter(runtime, identities, FRIEND_REQUESTS_CASE_ID))
                    connections = execute_edge_read_case(
                        FRIEND_CONNECTIONS_CASE_ID,
                        OwnedEdgeReadAdapter(runtime, identities, FRIEND_CONNECTIONS_CASE_ID))

            for execution, count in ((admission, 2), (requests, 3), (connections, 2)):
                with self.subTest(case_id=execution.case_id):
                    self.assertEqual(execution.status, 'PASS')
                    self.assertEqual(execution.completed_count, count)
                    self.assertEqual(execution.total_count, count)
                    self.assertEqual(execution.error_type, None)
                    self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
                    journal = execution.snapshot()
                    self.assertFalse(any('Bearer' in str(step.evidence)
                                         for step in execution.steps))
                    self.assertIn(journal['steps'][0]['evidence']['transport'],
                                  'owned-local-http')
            self.assertEqual(runtime.state, 'STOPPED')
            self.assertEqual(runtime.child.returncode, 0)
            self.assertEqual(identities.summary()['credential_memory'], 'CLEARED')
            admission_journal = (state_dir / f'local-edge-read-{ADMISSION_CASE_ID}.json').is_file()
            request_journal = (state_dir / f'local-edge-read-{FRIEND_REQUESTS_CASE_ID}.json').is_file()
            connections_journal = (state_dir / f'local-edge-read-{FRIEND_CONNECTIONS_CASE_ID}.json').is_file()
            self.assertTrue(admission_journal)
            self.assertTrue(request_journal)
            self.assertTrue(connections_journal)


if __name__ == '__main__':
    unittest.main()
