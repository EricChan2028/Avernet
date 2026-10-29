"""Bot-search source map preserves its one fixture mutation; no execution credit."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.edge_search_case import CASE_ID
from bcs_e2e.edge_search_contract import PARENT, REQUIREMENTS, bot_search_source_binding
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError
from bcs_e2e.inventory import current_manifest


class BotSearchContractTests(unittest.TestCase):
    def test_original_case_contains_twelve_reads_and_one_owned_fixture_mutation(self):
        value = bot_search_source_binding()
        self.assertEqual(value['parent'], PARENT)
        self.assertEqual(len(REQUIREMENTS), 13)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['requirements'][6]['id'], 'EDGE-BOT-SEARCH.ensure-tc-fixture')
        self.assertEqual(value['requirements'][6]['method'], 'POST')
        self.assertEqual(value['requirements'][6]['effect'], 'MUTATION')
        self.assertEqual(value['requirements'][6]['actor'], 'SERVICE_KEY')
        self.assertEqual(value['requirements'][6]['path'], '/admin/bots/$tc_uuid/ensure')
        self.assertEqual(value['requirements'][9]['actor'], 'UNAUTHENTICATED')
        self.assertEqual(sum(row['effect'] == 'READ' for row in value['requirements']), 12)

    def test_body_query_and_validation_paths_remain_exclusive(self):
        value = bot_search_source_binding()
        paths = {row['path'] for row in value['requirements']}
        self.assertIn('/bots/search?q=CEO&offset=0&limit=20', paths)
        self.assertIn('/bots/search?visibility=public&status=online&is_friend=false&limit=50', paths)
        self.assertIn('/bots/search?limit=999', paths)
        self.assertIn('/bots/search?visibility=bogus', paths)
        self.assertIn('/bots/search?status=bogus', paths)
        self.assertEqual(sum(row['path'] == '/bots/search?limit=10' for row in value['requirements']), 1)

    def test_drift_reordering_and_false_readonly_effect_rejected(self):
        original = bot_search_source_binding()
        for mutation in ('missing', 'reverse', 'effect', 'actor', 'path', 'hash', 'expectation'):
            value = copy.deepcopy(original)
            if mutation == 'missing':
                value['requirements'].pop(6)
            elif mutation == 'reverse':
                value['requirements'].reverse()
            elif mutation == 'effect':
                value['requirements'][6]['effect'] = 'READ'
            elif mutation == 'actor':
                value['requirements'][6]['actor'] = 'CEO_BOT'
            elif mutation == 'path':
                value['requirements'][6]['path'] = '/admin/bots/invented/ensure'
            elif mutation == 'hash':
                value['sources']['edge_permission.sh'] = '0' * 64
            else:
                value['requirements'][0]['expectation'] = ''
            with self.subTest(mutation=mutation), patch(
                    'bcs_e2e.edge_search_contract.read', return_value=value):
                with self.assertRaises(RunStateError):
                    bot_search_source_binding()

    def test_plan_registers_the_source_map_without_release_or_preprod_credit(self):
        plan = migration_plan()
        mapped = next(row for row in plan['additional_source_maps'] if row['parent'] == PARENT)
        self.assertEqual([row['id'] for row in mapped['requirements']], list(REQUIREMENTS))
        executor = next(row for row in plan['local_registered_executors']
                        if row['legacy_id'] == PARENT)
        self.assertEqual(executor['case_id'], CASE_ID)
        self.assertEqual(executor['required_assertion_count'], len(REQUIREMENTS) - 1)
        self.assertEqual(executor['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(executor['remote_validation'], 'NOT_RUN')
        self.assertFalse(executor['grants_release_credit'])
        candidate = next(row for row in plan['cross_environment_source_candidates']['cases']
                         if row['legacy_id'] == PARENT)
        self.assertEqual(candidate['status'], 'PENDING')
        self.assertFalse(candidate['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'], len(current_manifest()['legacy_inventory']))
        self.assertEqual(plan['migrated_story_count'], 0)
        self.assertEqual(plan['passed_assertion_count'], 0)
        self.assertFalse(plan['ready_to_execute'])


if __name__ == '__main__':
    unittest.main()
