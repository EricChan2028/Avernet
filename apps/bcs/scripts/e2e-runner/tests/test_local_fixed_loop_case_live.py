"""Explicitly opt-in live fixed-Loop case execution.

Not a substitute for the frozen source-contract obligations: this runs
the complete ``LOCAL-FIXED-LOOP-01`` executor against a real owned
local BCS binary and one deterministic scripted Provider/Judge
sandbox, proving both fixed-Loop Runs (approved and exhausted) execute
end to end on the single-process architecture: per-Run receipt
ledgers, scripted Judge admissions, session-scoped Run start with the
purpose-bound driver Bot credential, one-shot publication, immutable
history/graph agreement, and exact cleanup.
"""
from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.case_executor import execute_fixed_loop_case
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.local_fixed_loop_case import OwnedFixedLoopCaseAdapter
from bcs_e2e.local_judge_sandbox import OwnedLocalJudgeSandbox
from bcs_e2e.local_role_identity import OwnedLocalRoleIdentity
from bcs_e2e.owned_bcs import OwnedBcsProcess, file_sha256

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'),
                     'set BCS_E2E_TEST_BINARY and SHA256 with lsof for live BCS tests')
class LiveFixedLoopCaseTests(unittest.TestCase):
    def test_owned_fixed_loop_case_executes_both_runs_on_one_process(self):
        self.assertRegex(DIGEST, r'^[0-9a-f]{64}$')
        judge = OwnedLocalJudgeSandbox(
            'live-fixed-loop-case-1',
            judge_outcomes=('revise', 'approved', 'revise', 'revise'),
            provider_receipt_limit=8)
        judge.start()
        binary = Path(BINARY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'state'
            with OwnedBcsProcess(binary=binary, binary_sha256=file_sha256(binary),
                                   root=root, run_id='live-fixed-loop-case-1',
                                   judge_sandbox=judge, store_messages=True,
                                   loop_execution_enabled=True, startup_timeout=30) as owned:
                self.assertTrue(owned.ready)
                fixtures = OwnedLocalFixtures(owned, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in fixtures.roles:
                        identities.verify(role)
                    adapter = OwnedFixedLoopCaseAdapter(owned, judge, identities)
                    execution = execute_fixed_loop_case(adapter)
                    self.assertEqual(execution.status, 'PASS')
                    self.assertEqual(execution.completed_count, 8)
                    self.assertEqual(execution.total_count, 8)
                    self.assertTrue(execution.ready_to_execute)
                    self.assertIsNone(execution.error_type)
                    methods = [step.method for step in execution.steps]
                    self.assertEqual([step.status for step in execution.steps],
                                     ['PASS'] * 8)
                    replaced = execution.snapshot()
                    self.assertEqual(replaced['case_id'], 'LOCAL-FIXED-LOOP-01')
                    self.assertEqual(replaced['parent_id'],
                                     'story_fixed_loop_human_editor_workflow')
                    self.assertTrue(all(step['status'] == 'PASS'
                                        for step in replaced['steps']))
                    # The exhausted Run's seam evidence: session-scoped
                    # start consumed the purpose-bound driver Bot credential
                    # and the one-shot Session stayed running.
                    exhausted = next(step for step in execution.steps
                                     if step.method == 'start_exhausted_loop_run')
                    self.assertEqual(exhausted.evidence['exhausted_loop']['loop_route'],
                                     'exhausted')
                    self.assertEqual(exhausted.evidence['exhausted_loop']['session_status'],
                                     'running')
                    self.assertEqual(exhausted.evidence['second_run_transport'],
                                     'session-scoped Run start with purpose-bound driver Bot credential')
                    approved = next(step for step in execution.steps
                                    if step.method == 'start_approved_loop_run')
                    self.assertEqual(approved.evidence['approved_loop']['loop_route'],
                                     'approved')
                    self.assertEqual(approved.evidence['approved_loop']['iterations'], 2)
                    replacement = next(step for step in execution.steps
                                       if step.method == 'verify_shared_reset_replacement')
                    self.assertEqual(
                        replacement.evidence['replacement']['shared_control_resets_issued'], 0)
                    counts = replacement.evidence['replacement']['per_run_receipt_counts']
                    self.assertEqual(sorted(counts.values()), [4, 4])
                    self.assertEqual(len(counts), 2)
                    self.assertEqual(replacement.evidence['replacement']
                                     ['per_run_journaled_iterations']
                                     ['{}-approved-loop'.format('live-fixed-loop-case-1')], 2)
                    self.assertEqual(
                        replacement.evidence['replacement']['deterministic_judge_admissions'], 4)
                    self.assertGreater(
                        replacement.evidence['requested_paths'], 0)
                    cleanup = next(step for step in execution.steps
                                   if step.method == 'delete_loop_groups_exactly')
                    self.assertEqual(cleanup.evidence['deletions'],
                                     {'groups': 2, 'provider_bot': 1})
                    journal = json.loads((owned.root / 'local-fixed-loop-adapter.json').read_text())
                    self.assertEqual(journal['status'], 'RUNNING')
                    self.assertEqual([row['method'] for row in journal['steps']],
                                     [step.method for step in execution.steps])
                    self.assertEqual([row['status'] for row in journal['steps']],
                                     ['PASS'] * 8)
                    self.assertTrue(all('error_type' not in row
                                        for row in journal['steps']))
                    # No secrets ever reached the journal or any evidence.
                    encoded = json.dumps(journal) + json.dumps(replaced)
                    self.assertNotIn(judge.judge_api_key, encoded)
                # The managed identity lifecycle cleared its captured Bot
                # credentials when the case completed.
                self.assertEqual(identities.summary()['credential_memory'], 'CLEARED')
            self.assertEqual(owned.child.returncode, 0)
            self.assertEqual(owned.state, 'STOPPED')
        judge.close()
        snapshot = judge.snapshot()
        self.assertEqual(snapshot['state'], 'CLOSED')
        self.assertEqual(snapshot['judge_request_count'], 4)
        self.assertEqual(len(snapshot['provider_receipts']), 8)
        self.assertNotIn('judge_api_key', json.dumps(snapshot))


if __name__ == '__main__':
    unittest.main()
