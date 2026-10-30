"""Complete provider source map is a prerequisite, not execution evidence."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_provider_contract import REQUIREMENTS, PARENT, HELPER, provider_source_binding
from bcs_e2e.run_state import RunStateError


class ProviderContractTests(unittest.TestCase):
    def test_whole_parent_includes_all_fifteen_organization_operations(self):
        value = provider_source_binding()
        self.assertEqual(value['parent'], PARENT)
        self.assertEqual(len(REQUIREMENTS), 32)
        self.assertEqual([r['id'] for r in value['requirements']], list(REQUIREMENTS))
        self.assertEqual([r['function'] for r in value['requirements']], [PARENT]*8+[HELPER]*15+[PARENT]*9)
        self.assertEqual(value['requirements'][20]['path'], '/organizations/${organization_code}/admin-runs')
        self.assertEqual(value['requirements'][22]['method'], 'DELETE')
        self.assertEqual(value['requirements'][-2]['method'], 'DELETE')

    def test_source_hash_and_missing_reordered_invented_operations_are_rejected(self):
        original = provider_source_binding()
        for mutation in ('missing', 'duplicate', 'reorder', 'method', 'path', 'function', 'source', 'empty_expectation'):
            value = copy.deepcopy(original)
            rows = {r['id']: r for r in value['requirements']}
            if mutation == 'missing': value['requirements'].pop(1)
            elif mutation == 'duplicate': value['requirements'][0] = value['requirements'][1]
            elif mutation == 'reorder': value['requirements'].reverse()
            elif mutation == 'method': rows['PROVIDER.admin-start']['method'] = 'GET'
            elif mutation == 'path': rows['PROVIDER.admin-start']['path'] = '/invented'
            elif mutation == 'function': rows['PROVIDER.org-create']['function'] = PARENT
            elif mutation == 'source': value['sources']['stories.sh'] = '0'*64
            else: value['requirements'][0]['expectation'] = ''
            with self.subTest(mutation=mutation), patch('bcs_e2e.local_provider_contract.read', return_value=value):
                with self.assertRaises(RunStateError): provider_source_binding()

    def test_map_has_no_secret_no_execution_credit_and_no_removed_endpoint(self):
        value = provider_source_binding()
        self.assertNotIn('status', value)
        self.assertNotIn('ready_to_execute', value)
        self.assertFalse(any('/bot/events/coordination' in r['path'] for r in value['requirements']))
        self.assertTrue(all(r['method'] in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') for r in value['requirements']))

    def test_plan_lists_complete_local_executor_without_release_credit(self):
        from bcs_e2e.migration_inventory import migration_plan
        plan = migration_plan()
        mapped = next(row for row in plan['additional_source_maps'] if row['parent'] == PARENT)
        self.assertEqual([r['id'] for r in mapped['requirements']], list(REQUIREMENTS))
        executor = next(r for r in plan['local_registered_executors'] if r['legacy_id'] == PARENT)
        self.assertEqual(executor['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(executor['required_assertion_count'], 32)
        self.assertFalse(executor['grants_release_credit'])
        self.assertEqual(plan['legacy_inventory_count'], 41)
        self.assertEqual(plan['migrated_story_count'], 0)
