"""The fixed-Loop source map fixes behavior before any executor exists."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_fixed_loop_contract import (
    PARENT, REQUIREMENTS, fixed_loop_method, fixed_loop_source_binding)
from bcs_e2e.run_state import RunStateError


class FixedLoopContractTests(unittest.TestCase):
    def test_original_eight_obligations_freeze_both_loop_and_chat_boundaries(self):
        binding = fixed_loop_source_binding()
        rows = binding['requirements']
        self.assertEqual([row['id'] for row in rows], list(REQUIREMENTS))
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(row['kind'] == 'http' for row in rows), 5)
        self.assertEqual(sum(row['kind'] == 'fixture' for row in rows), 2)
        self.assertEqual(rows[2]['path'], '/groups/${group_id}/state-machine-runs')
        self.assertEqual(rows[3]['path'], '/groups/${group_id}/sessions')
        self.assertIn('exactly once', rows[4]['expectation'])
        self.assertEqual(rows[6]['method'], 'DELETE')
        self.assertIn('Shared /control/provider/clear', rows[7]['expectation'])

    def test_tampered_source_map_or_source_hash_is_rejected(self):
        original = fixed_loop_source_binding()
        for change in ('missing', 'reverse', 'actor', 'kind', 'method', 'path', 'source'):
            value = copy.deepcopy(original)
            if change == 'missing':
                value['requirements'].pop(3)
            elif change == 'reverse':
                value['requirements'].reverse()
            elif change == 'actor':
                value['requirements'][2]['actor'] = 'PROVIDER_ADMIN'
            elif change == 'kind':
                value['requirements'][5]['kind'] = 'http'
            elif change == 'method':
                value['requirements'][6]['method'] = 'POST'
            elif change == 'path':
                value['requirements'][5]['path'] = '/runs'
            else:
                value['sources']['fixed_loop_story.py'] = '0' * 64
            with self.subTest(change=change), patch(
                    'bcs_e2e.local_fixed_loop_contract.read', return_value=value):
                with self.assertRaises(RunStateError):
                    fixed_loop_source_binding()

    def test_python_method_boundary_keeps_all_iterations_and_one_shot_details(self):
        text = (Path(__file__).resolve().parents[2] / 'e2e-test/fixed_loop_story.py').read_text()
        setup = fixed_loop_method(text, 'setup')
        exercise = fixed_loop_method(text, 'exercise')
        verify = fixed_loop_method(text, 'verify')
        self.assertIn('X-BCS-Bot-Token', setup)
        self.assertIn('self.bot["bot_runtime_token"]', setup)
        self.assertIn('iteration"] == 1', exercise)
        self.assertIn('iteration"] == 2', exercise)
        self.assertIn('stale input', exercise)
        self.assertIn('status=409', exercise)
        self.assertIn('len(outputs) == 6', verify)
        self.assertIn('assert len(published) == 1', verify)
        for changed in (text.replace('def setup(self) -> None:', 'def other_setup(self) -> None:', 1),
                        text.replace('def pending(self) -> dict:', 'def setup(self) -> dict:', 1),
                        text.replace('def verify(self, approved: bool) -> None:', 'def missing(self):', 1)):
            with self.assertRaises(ValueError):
                fixed_loop_method(changed, 'setup')
                fixed_loop_method(changed, 'verify')

    def test_source_map_registers_executor_without_release_credit(self):
        from bcs_e2e.local_fixed_loop_contract import CASE_ID, SUITE
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        mapped = next(row for row in plan['additional_source_maps'] if row['parent'] == PARENT)
        self.assertEqual([row['id'] for row in mapped['requirements']], list(REQUIREMENTS))
        row = next(row for row in plan['local_registered_executors']
                   if row['legacy_id'] == PARENT)
        self.assertEqual(row['case_id'], CASE_ID)
        self.assertEqual(row['suite'], SUITE)
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(row['case_version'], 1)
        self.assertEqual(row['required_assertion_count'], 8)
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(row['execution_command'], 'local-fixed-loop-run')
        self.assertEqual(row['report_command'], 'local-fixed-loop-report')
        self.assertEqual(row['remote_validation'], 'NOT_RUN')
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertEqual(plan['legacy_inventory_count'], 34)
        self.assertFalse(plan['ready_to_execute'])


if __name__ == '__main__':
    unittest.main()
