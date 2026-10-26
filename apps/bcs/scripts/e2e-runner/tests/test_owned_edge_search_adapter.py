"""Owned-local Bot-search adapter and separate fixture mutation contracts."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[3]
sys.path.insert(0, str(ROOT))
from bcs_e2e.edge_search_case import execute_edge_search_case
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256
from bcs_e2e.owned_edge_read_adapter import (
    OwnedBotSearchFixtureAdmission,
    OwnedEdgeSearchAdapter,
)
from bcs_e2e.run_state import RunStateError

BCS_BINARY = ROOT.parents[1] / 'target' / 'debug' / 'bcs'
GUARD = REPO_ROOT / 'scripts/ci/e2e_runner_case_guard.py'


class OwnedEdgeSearchAdapterGuardTests(unittest.TestCase):
    def test_case_guard_accepts_reviewed_search_adapter(self):
        result = subprocess.run(
            [sys.executable, str(GUARD),
             str(ROOT / 'bcs_e2e/owned_edge_read_adapter.py')],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_adapter_requires_exact_owned_objects(self):
        with self.assertRaises(RunStateError):
            OwnedEdgeSearchAdapter(object(), object(), object())
        with self.assertRaises(RunStateError):
            OwnedBotSearchFixtureAdmission(object())


@unittest.skipUnless(BCS_BINARY.is_file(), 'owned BCS debug binary is not available')
class OwnedEdgeSearchAdapterLiveTests(unittest.TestCase):
    def test_no_ambient_runtime_preserves_anonymous_private_or_friendship_fields(self):
        with tempfile.TemporaryDirectory(prefix='edge-bot-search-') as tmp:
            state_dir = pathlib.Path(tmp) / 'runtime'
            with OwnedBcsProcess(binary=BCS_BINARY, binary_sha256=file_sha256(BCS_BINARY),
                                 root=state_dir, run_id='edge-bot-search-1',
                                 allow_ambient_local_identity=False) as runtime:
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=('CEO',)) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    identities.verify('CEO')
                    fixture = OwnedBotSearchFixtureAdmission(runtime)
                    admission = fixture.admit(allow_write=True)
                    execution = execute_edge_search_case(
                        OwnedEdgeSearchAdapter(runtime, identities, fixture))

            self.assertEqual(admission['status'], 'PASS')
            self.assertEqual(admission['bot_uuid'], 'tc_cov_bot:85020')
            self.assertEqual(admission['ensured'], True)
            self.assertTrue(admission['created'])
            self.assertNotIn(fixture._service_key, fixture.path.read_text())

            # This runtime intentionally does not establish a default local user
            # identity. A request without Authorization therefore cannot infer a
            # viewer and must omit is_friend instead of merely serializing false.
            self.assertEqual(execution.status, 'PASS')
            self.assertEqual(execution.error_type, None)
            self.assertEqual(execution.completed_count, 12)
            self.assertEqual(execution.total_count, 12)
            self.assertEqual([step.status for step in execution.steps], ['PASS'] * 12)

            journal = (state_dir / 'local-edge-bot-search-read.json').read_text()
            self.assertNotIn(fixture._service_key, journal)
            self.assertNotIn('Bearer', journal)
            self.assertIn('"included_in_read_case": false', journal)
            self.assertEqual(runtime.state, 'STOPPED')
            self.assertEqual(runtime.child.returncode, 0)
            self.assertEqual(identities.summary()['credential_memory'], 'CLEARED')



if __name__ == '__main__':
    unittest.main()
