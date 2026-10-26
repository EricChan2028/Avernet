"""Focused real-runtime integration for the owned-local slow-Judge adapter."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_adapters import ADAPTER_METHOD_NAMES
from bcs_e2e.case_executor import execute_slow_judge_case
from bcs_e2e.case_rewrite import slow_judge_case_rewrite
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_judge_sandbox import OwnedLocalJudgeSandbox
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256
from bcs_e2e.owned_local_case_adapter import OwnedLocalCaseAdapter
from bcs_e2e.run_state import RunStateError

BCS_BINARY = ROOT.parents[1] / 'target' / 'debug' / 'bcs'


@unittest.skipUnless(BCS_BINARY.is_file(), 'owned BCS debug binary is not available')
class OwnedLocalSlowJudgeAdapterTests(unittest.TestCase):
    def test_live_runtime_executes_all_slow_judge_obligations(self):
        run_id = 'slow-judge-adapter-1'
        with tempfile.TemporaryDirectory(prefix='slow-judge-adapter-') as tmp:
            root = Path(tmp)
            binary_sha256 = file_sha256(BCS_BINARY)
            with OwnedLocalJudgeSandbox(run_id) as sandbox:
                with OwnedBcsProcess(binary=BCS_BINARY, binary_sha256=binary_sha256,
                                     root=root / 'runtime', run_id=run_id,
                                     store_messages=True, judge_sandbox=sandbox) as runtime:
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        fixtures.apply(allow_write=True, role_identity=identities)
                        identities.verify('CEO')
                        adapter = OwnedLocalCaseAdapter(runtime, sandbox, identities)
                        execution = execute_slow_judge_case(adapter)

        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 22)
        self.assertEqual(execution.total_count, 22)
        self.assertTrue(execution.ready_to_execute)
        self.assertEqual([step.method for step in execution.steps], list(ADAPTER_METHOD_NAMES))
        self.assertEqual([step.requirement_id for step in execution.steps],
                         [row.requirement_id for row in slow_judge_case_rewrite().requirements])
        self.assertTrue(all(step.status == 'PASS' for step in execution.steps))
        self.assertEqual(execution.error_type, None)

    def test_constructor_rejects_a_non_owned_or_unverified_identity_context(self):
        run_id = 'slow-judge-adapter-2'
        with tempfile.TemporaryDirectory(prefix='slow-judge-adapter-invalid-') as tmp:
            root = Path(tmp)
            binary_sha256 = file_sha256(BCS_BINARY)
            with OwnedLocalJudgeSandbox(run_id) as sandbox:
                with OwnedBcsProcess(binary=BCS_BINARY, binary_sha256=binary_sha256,
                                     root=root / 'runtime', run_id=run_id,
                                     store_messages=True, judge_sandbox=sandbox) as runtime:
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                        with self.assertRaisesRegex(RunStateError, 'not ready'):
                            OwnedLocalCaseAdapter(runtime, sandbox, identities)


if __name__ == '__main__':
    unittest.main()
