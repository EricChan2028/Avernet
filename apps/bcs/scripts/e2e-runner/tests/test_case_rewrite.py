"""The first case rewrite must preserve every slow-Judge obligation and seam."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.case_rewrite import slow_judge_case_rewrite
from bcs_e2e.local_judge_contract import CASE_ID, PARENT, REQUIREMENTS, judge_source_binding
from bcs_e2e.run_state import RunStateError


class CaseRewriteTests(unittest.TestCase):
    def test_slow_judge_case_rewrite_preserves_all_22_original_obligations(self):
        plan = slow_judge_case_rewrite()
        binding = judge_source_binding()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, PARENT)
        self.assertEqual(plan.requirement_ids, tuple(row['id'] for row in binding['requirements']))
        self.assertEqual(plan.requirement_ids, REQUIREMENTS)
        self.assertEqual(len(plan.requirements), 22)
        self.assertEqual(set(plan.adapters), set(REQUIREMENTS))
        self.assertEqual(len(set(plan.adapters.values())), len(REQUIREMENTS))

    def test_every_obligation_keeps_original_kind_and_transport_projection(self):
        plan = slow_judge_case_rewrite()
        binding = judge_source_binding()
        for requirement, source in zip(plan.requirements, binding['requirements']):
            with self.subTest(requirement_id=requirement.requirement_id):
                self.assertEqual(requirement.kind, source['kind'])
                self.assertEqual(requirement.method, source['method'])
                self.assertEqual(requirement.path, source['path'])
                self.assertEqual(requirement.actor, source['actor'])
                self.assertEqual(requirement.expectation, source['expectation'])

    def test_every_obligation_has_one_environment_independent_adapter_method(self):
        plan = slow_judge_case_rewrite()
        for requirement in plan.requirements:
            with self.subTest(requirement_id=requirement.requirement_id):
                self.assertIsInstance(requirement.adapter_method, str)
                self.assertTrue(requirement.adapter_method)
                self.assertNotIn('/', requirement.adapter_method)
        self.assertEqual(plan.adapters[REQUIREMENTS[12]], 'submit_review_callback_before_deadline')
        self.assertEqual(plan.adapters[REQUIREMENTS[19]], 'await_judged_run_completion')

    def test_case_plan_declares_local_singlebox_and_preprod_without_claiming_readiness(self):
        plan = slow_judge_case_rewrite()
        self.assertEqual([row.name for row in plan.environments],
                         ['owned-local', 'singlebox', 'preprod'])
        self.assertEqual([row.adapter_family for row in plan.environments],
                         ['OwnedLocalCaseAdapter', 'SingleboxCaseAdapter', 'RemoteCaseAdapter'])
        self.assertEqual([row.status for row in plan.environments], ['LOCAL_FULL_EXECUTOR', 'PENDING', 'PENDING'])
        self.assertTrue(all(row.blocker for row in plan.environments))
        self.assertIn('run-owned process ownership', plan.environments[1].blocker)
        self.assertIn('callback', plan.environments[2].blocker)

    def test_case_rewrite_rejects_drift_between_contract_and_plan(self):
        plan = slow_judge_case_rewrite()
        with self.assertRaises(RunStateError):
            object.__setattr__(plan, 'requirements', plan.requirements[:-1])
            plan.validate()



class FixedLoopCaseRewriteTests(unittest.TestCase):
    def test_fixed_loop_case_rewrite_preserves_all_eight_obligations(self):
        from bcs_e2e.case_rewrite import fixed_loop_case_rewrite
        from bcs_e2e.local_fixed_loop_contract import (
            CASE_ID as FIXED_LOOP_CASE_ID,
            PARENT as FIXED_LOOP_PARENT,
            REQUIREMENTS as FIXED_LOOP_REQUIREMENTS,
            fixed_loop_source_binding,
        )
        plan = fixed_loop_case_rewrite()
        binding = fixed_loop_source_binding()
        self.assertEqual(plan.case_id, FIXED_LOOP_CASE_ID)
        self.assertEqual(plan.parent_id, FIXED_LOOP_PARENT)
        self.assertEqual(plan.requirement_ids, tuple(FIXED_LOOP_REQUIREMENTS))
        self.assertEqual(len(plan.requirements), 8)
        self.assertEqual(set(plan.adapters), set(FIXED_LOOP_REQUIREMENTS))
        self.assertEqual(len(set(plan.adapters.values())), len(FIXED_LOOP_REQUIREMENTS))
        for requirement, source in zip(plan.requirements, binding['requirements']):
            self.assertEqual(requirement.kind, source['kind'])
            self.assertEqual(requirement.method, source['method'])
            self.assertEqual(requirement.path, source['path'])
            self.assertEqual(requirement.actor, source['actor'])
            self.assertEqual(requirement.expectation, source['expectation'])
        self.assertEqual([row.name for row in plan.environments],
                         ['owned-local', 'singlebox', 'preprod'])
        self.assertEqual([row.adapter_family for row in plan.environments],
                         ['OwnedLocalCaseAdapter', 'SingleboxCaseAdapter', 'RemoteCaseAdapter'])
        self.assertEqual([row.status for row in plan.environments], ['LOCAL_FULL_EXECUTOR', 'PENDING', 'PENDING'])
        self.assertTrue(all(row.blocker for row in plan.environments))
        self.assertEqual(list(plan.binding.requirement_ids),
                         list(FIXED_LOOP_REQUIREMENTS))

    def test_fixed_loop_plan_keeps_obligation_transport_and_seam_projection(self):
        from bcs_e2e.case_rewrite import fixed_loop_case_rewrite
        plan = fixed_loop_case_rewrite()
        requirements = plan.binding.requirement_ids
        self.assertEqual(plan.adapters[requirements[0]], 'provision_per_run_sandbox')
        self.assertEqual(plan.adapters[requirements[2]], 'start_approved_loop_run')
        self.assertEqual(plan.adapters[requirements[3]], 'start_exhausted_loop_run')
        self.assertEqual(plan.adapters[requirements[7]], 'verify_shared_reset_replacement')
        self.assertEqual(plan.requirements[0].kind, 'fixture')
        self.assertEqual(plan.requirements[0].path, '${OWNED_FIXED_LOOP_SANDBOX}')
        self.assertEqual(plan.requirements[2].method, 'POST')
        self.assertEqual(plan.requirements[3].path, '/groups/${group_id}/sessions')
        self.assertEqual(plan.requirements[5].kind, 'poll')
        self.assertEqual(plan.requirements[6].method, 'DELETE')

    def test_fixed_loop_plan_rejects_drift_between_contract_and_plan(self):
        from bcs_e2e.case_rewrite import fixed_loop_case_rewrite
        plan = fixed_loop_case_rewrite()
        with self.assertRaises(RunStateError):
            object.__setattr__(plan, 'requirements', plan.requirements[:-1])
            plan.validate()


class MigrationInventoryCaseRewriteTests(unittest.TestCase):
    def test_plan_exposes_slow_judge_case_rewrite_without_execution_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        rows = plan.get('case_rewrite_plans', [])
        self.assertEqual(len(rows), 2)
        row = rows[0]
        self.assertEqual(row['case_id'], CASE_ID)
        self.assertEqual(row['parent_id'], PARENT)
        self.assertEqual(row['status'], 'PENDING')
        self.assertEqual(row['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(row['adapter_method_count'], 22)
        self.assertEqual(len(row['requirements']), 22)
        self.assertEqual([item['id'] for item in row['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(item['adapter_method'] for item in row['requirements']))
        self.assertEqual([item['name'] for item in row['environments']],
                         ['owned-local', 'singlebox', 'preprod'])
        self.assertFalse(row['ready_to_execute'])
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['migrated_story_count'], 0)

    def test_inventory_exposes_fixed_loop_case_rewrite_without_execution_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        from bcs_e2e.local_fixed_loop_contract import (
            CASE_ID as FIXED_LOOP_CASE_ID,
            REQUIREMENTS as FIXED_LOOP_REQUIREMENTS,
        )
        plan = migration_plan()
        rows = plan.get('case_rewrite_plans', [])
        self.assertEqual(len(rows), 2)
        row = next(item for item in rows if item['case_id'] == FIXED_LOOP_CASE_ID)
        self.assertEqual(row['parent_id'], 'story_fixed_loop_human_editor_workflow')
        self.assertEqual(row['status'], 'PENDING')
        self.assertEqual(row['requirement_ids'], list(FIXED_LOOP_REQUIREMENTS))
        self.assertEqual(row['adapter_method_count'], 8)
        self.assertEqual(len(row['requirements']), 8)
        self.assertEqual([item['id'] for item in row['requirements']],
                         list(FIXED_LOOP_REQUIREMENTS))
        self.assertTrue(all(item['adapter_method'] for item in row['requirements']))
        self.assertEqual([item['name'] for item in row['environments']],
                         ['owned-local', 'singlebox', 'preprod'])
        self.assertFalse(row['ready_to_execute'])
        self.assertFalse(row['grants_release_credit'])
