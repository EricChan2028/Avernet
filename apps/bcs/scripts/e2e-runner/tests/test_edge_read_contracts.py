"""Read-only Edge source maps are migration inputs, not execution or release credit."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.edge_read_contract import (
    ADMISSION_PARENT,
    ADMISSION_REQUIREMENTS,
    FRIEND_REQUESTS_PARENT,
    FRIEND_REQUESTS_REQUIREMENTS,
    FRIEND_CONNECTIONS_PARENT,
    FRIEND_CONNECTIONS_REQUIREMENTS,
    admission_source_binding,
    edge_read_adaptability,
    friend_requests_list_source_binding,
    friend_connections_list_source_binding,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError


class EdgeReadContractTests(unittest.TestCase):
    def test_admission_maps_both_original_read_calls(self):
        value = admission_source_binding()
        self.assertEqual(value['parent'], ADMISSION_PARENT)
        self.assertEqual([row['id'] for row in value['requirements']], list(ADMISSION_REQUIREMENTS))
        self.assertEqual([row['method'] for row in value['requirements']], ['GET', 'GET'])
        self.assertEqual(value['requirements'][0]['path'],
                         '/bots/$BOT_CEO_UUID/admission?actor=human_88001')
        self.assertEqual(value['requirements'][1]['path'],
                         '/bots/$BOT_CEO_UUID/admission?actor=88001&actor_kind=human')
        self.assertEqual([row['actor'] for row in value['requirements']], ['UNAUTHENTICATED', 'UNAUTHENTICATED'])

    def test_friend_requests_list_maps_all_three_direction_reads(self):
        value = friend_requests_list_source_binding()
        self.assertEqual(value['parent'], FRIEND_REQUESTS_PARENT)
        self.assertEqual([row['id'] for row in value['requirements']], list(FRIEND_REQUESTS_REQUIREMENTS))
        self.assertEqual([row['path'] for row in value['requirements']], [
            '/collaboration/friend-connections/requests',
            '/collaboration/friend-connections/requests?direction=sent',
            '/collaboration/friend-connections/requests?direction=all',
        ])
        self.assertEqual([row['actor'] for row in value['requirements']], ['CEO_BOT'] * 3)

    def test_friend_connections_slice_maps_the_two_original_list_reads(self):
        value = friend_connections_list_source_binding()
        expected_path = '/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot'
        self.assertEqual(value['parent'], FRIEND_CONNECTIONS_PARENT)
        self.assertEqual([row['id'] for row in value['requirements']],
                          list(FRIEND_CONNECTIONS_REQUIREMENTS))
        self.assertEqual([row['path'] for row in value['requirements']],
                         [expected_path, expected_path])
        self.assertEqual([row['actor'] for row in value['requirements']], ['CEO_BOT'] * 2)

    def test_all_source_maps_reject_drift_and_invented_fields(self):
        originals = (admission_source_binding(), friend_connections_list_source_binding(),
                     friend_requests_list_source_binding())
        functions = (admission_source_binding, friend_connections_list_source_binding,
                     friend_requests_list_source_binding)
        for original, function, index in zip(originals, functions, (0, 0, 2)):
            for mutation in ('missing', 'reverse', 'path', 'actor', 'expectation', 'hash'):
                value = copy.deepcopy(original)
                if mutation == 'missing':
                    value['requirements'].pop()
                elif mutation == 'reverse':
                    value['requirements'].reverse()
                elif mutation == 'path':
                    value['requirements'][index]['path'] = '/invented'
                elif mutation == 'actor':
                    value['requirements'][index]['actor'] = 'HUMAN'
                elif mutation == 'expectation':
                    value['requirements'][index]['expectation'] = ''
                else:
                    value['sources']['edge_permission.sh'] = '0' * 64
                with self.subTest(parent=original['parent'], mutation=mutation), \
                     patch(f'bcs_e2e.edge_read_contract.read', return_value=value):
                    with self.assertRaises(RunStateError):
                        function()

    def test_contract_fields_reject_invalid_values(self):
        value = friend_requests_list_source_binding()
        for edit in ('kind', 'method', 'source_revision', 'parent'):
            mutated = copy.deepcopy(value)
            if edit == 'kind':
                mutated['requirements'][0]['kind'] = 'http '
            elif edit == 'method':
                mutated['requirements'][0]['method'] = 'POST'
            elif edit == 'source_revision':
                mutated['source_revision'] = 'not-a-revision'
            else:
                mutated['parent'] = 'other-parent'
            with self.subTest(edit=edit), patch('bcs_e2e.edge_read_contract.read', return_value=mutated):
                with self.assertRaises(RunStateError):
                    friend_requests_list_source_binding()

    def test_plan_exposes_source_candidates_without_execution_or_release_credit(self):
        plan = migration_plan()
        parents = {row['parent'] for row in plan['additional_source_maps']}
        self.assertIn(ADMISSION_PARENT, parents)
        self.assertIn(FRIEND_REQUESTS_PARENT, parents)
        candidates = plan['cross_environment_source_candidates']
        self.assertEqual([row['legacy_id'] for row in candidates['cases']],
                         [ADMISSION_PARENT, FRIEND_REQUESTS_PARENT, FRIEND_CONNECTIONS_PARENT,
                          'test_ep_bot_search'])
        self.assertEqual([row['effect_scope'] for row in candidates['cases']],
                         ['READ_ONLY', 'READ_ONLY',
                          'READ_ONLY_SLICE_PARENT_CONTAINS_WRITES',
                          'READ_SPINE_WITH_SEPARATE_FIXTURE_MUTATION'])
        self.assertTrue(all(row['status'] == 'PENDING' for row in candidates['cases']))
        self.assertTrue(all(row['environments'] == ['owned-local', 'singlebox', 'preprod']
                            for row in candidates['cases']))
        self.assertFalse(any(row['grants_release_credit'] for row in candidates['cases']))
        self.assertFalse(candidates['ready_to_execute'])
        executors = [row for row in plan['local_registered_executors']
                     if row['legacy_id'] in {ADMISSION_PARENT, FRIEND_REQUESTS_PARENT,
                                             FRIEND_CONNECTIONS_PARENT}]
        self.assertEqual([row['case_id'] for row in executors],
                         ['LOCAL-EDGE-ADMISSION-01', 'LOCAL-EDGE-FRIEND-REQUESTS-LIST-01',
                          'LOCAL-EDGE-FRIEND-CONNECTIONS-LIST-01'])
        self.assertTrue(all(row['status'] in ('LOCAL_EXECUTOR_ONLY', 'LOCAL_READ_SLICE_ONLY')
                            and row['remote_validation'] == 'NOT_RUN'
                            and not row['grants_release_credit'] for row in executors))
        self.assertEqual(plan['legacy_inventory_count'], 41)
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertEqual(plan['passed_assertion_count'], 0)

    def test_adaptability_helper_never_infers_remote_admission(self):
        candidates = edge_read_adaptability()
        self.assertFalse(candidates['ready_to_execute'])
        self.assertTrue(all(row['blocker'] for row in candidates['cases']))
        self.assertIn('route', candidates['cases'][0]['blocker'])
        self.assertIn('identity', candidates['cases'][1]['blocker'])


if __name__ == '__main__':
    unittest.main()
