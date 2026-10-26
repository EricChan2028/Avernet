"""The seven edge friend-mutation source maps freeze behavior before
any executor exists, and register one local executor per legacy
entrypoint without release credit."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.edge_friend_mutation_case import (
    CASE_IDS,
    PARENTS,
    SUITE,
    _function_body,
    edge_friend_mutation_case_plan,
    edge_friend_mutation_case_spec,
    edge_friend_mutation_source_binding,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError

MAPPED = (
    ('LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01',
     'test_ep_friend_connections_friend_accept_reject_cancel',
     'EDGE_FRIEND_MUTATIONS.accept-reject-cancel',
     'run_accept_reject_cancel'),
    ('LOCAL-EDGE-FRIEND-REVOKE-01',
     'test_ep_friend_connections_friend_revoke',
     'EDGE_FRIEND_MUTATIONS.revoke',
     'run_revoke'),
    ('LOCAL-EDGE-ENSURE-BOT-01',
     'test_ep_ensure_bot',
     'EDGE_FRIEND_MUTATIONS.ensure-bot',
     'run_ensure_bot'),
    ('LOCAL-EDGE-FRIEND-FULL-LIFECYCLE-01',
     'test_ep_friend_connections_full_lifecycle',
     'EDGE_FRIEND_MUTATIONS.full-lifecycle',
     'run_full_lifecycle'),
    ('LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01',
     'test_ep_friend_connections_mutual_auto_approve',
     'EDGE_FRIEND_MUTATIONS.mutual-auto-approve',
     'run_mutual_auto_approve'),
    ('LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01',
     'test_ep_error_and_admission_branches',
     'EDGE_FRIEND_MUTATIONS.error-admission-branches',
     'run_error_admission_branches'),
    ('LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01',
     'test_ep_connect_private_and_discover_branches',
     'EDGE_FRIEND_MUTATIONS.connect-private-discover',
     'run_connect_private_discover'),
)


class EdgeFriendMutationContractTests(unittest.TestCase):

    def test_seven_plans_freeze_exactly_one_obligation_each(self):
        self.assertEqual(len(CASE_IDS), 7)
        self.assertEqual(PARENTS, tuple(row[1] for row in MAPPED))
        self.assertEqual(CASE_IDS, tuple(row[0] for row in MAPPED))
        self.assertEqual(len(set(PARENTS)), 7)
        for case_id, parent, requirement, adapter_method in MAPPED:
            with self.subTest(case_id=case_id):
                plan = edge_friend_mutation_case_plan(case_id)
                spec = edge_friend_mutation_case_spec(case_id)
                self.assertEqual(plan.case_id, case_id)
                self.assertEqual(plan.parent_id, parent)
                self.assertEqual(len(plan.requirements), 1)
                self.assertEqual(plan.requirement_ids, (requirement,))
                row = plan.requirements[0]
                self.assertEqual(row.requirement_id, requirement)
                self.assertEqual(row.adapter_method, adapter_method)
                self.assertEqual(
                    plan.adapters, {requirement: adapter_method})
                self.assertEqual(
                    {environment.name for environment in plan.environments},
                    {'owned-local', 'singlebox', 'preprod'})
                binding = edge_friend_mutation_source_binding(case_id)
                self.assertEqual(binding['schema_version'], 1)
                self.assertEqual(binding['parent'], parent)
                self.assertIn('edge_permission.sh', binding['sources'])
                self.assertEqual(
                    [entry['id'] for entry in binding['requirements']],
                    [requirement])
                self.assertEqual(spec.adapter_method, adapter_method)

    def test_function_body_extraction_rejects_missing_or_unterminated_drift(self):
        with self.assertRaises(RunStateError):
            _function_body('unrelated() {\n  echo ok\n}\n', 'test_x')
        with self.assertRaises(RunStateError):
            _function_body('test_x() {\n  echo hi\n', 'test_x')
        body = _function_body(
            'test_x() {\n  echo "/collaboration/friend-connections"\n}\n',
            'test_x')
        self.assertIn('/collaboration/friend-connections', body)

    def test_registered_executors_map_each_parent_without_release_credit(self):
        plan = migration_plan()
        mapped = {row['parent']: row
                  for row in plan['additional_source_maps']}
        for parent in PARENTS:
            with self.subTest(parent=parent):
                self.assertIn(parent, mapped)
                binding = mapped[parent]
                self.assertEqual(binding['schema_version'], 1)
                self.assertIn('edge_permission.sh', binding['sources'])
                self.assertEqual(
                    len(binding['requirements']), 1)
        rows = [row for row in plan['local_registered_executors']
                if row['legacy_id'] in set(PARENTS)]
        self.assertEqual([row['case_id'] for row in rows], list(CASE_IDS))
        for row, (_, parent, requirement, _) in zip(rows, MAPPED):
            with self.subTest(case_id=row['case_id']):
                self.assertEqual(row['legacy_id'], parent)
                self.assertEqual(row['suite'], SUITE)
                self.assertEqual(row['case_version'], 1)
                self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY')
                self.assertEqual(row['required_assertion_count'], 1)
                self.assertEqual(row['requirement_ids'], [requirement])
                self.assertEqual(row['canonical_reporting'], 'INTEGRATED_LOCAL')
                self.assertEqual(
                    row['execution_command'], 'local-edge-friend-mutations-run')
                self.assertEqual(
                    row['report_command'], 'local-edge-friend-mutations-report')
                self.assertEqual(row['remote_validation'], 'NOT_RUN')
                self.assertFalse(row['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'], 34)
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertEqual(plan['passed_assertion_count'], 0)


if __name__ == '__main__':
    unittest.main()
