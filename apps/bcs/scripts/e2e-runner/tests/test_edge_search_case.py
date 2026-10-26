"""Bot-search read spine remains environment-neutral and mutation-free."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.edge_search_case import (
    ADAPTER_METHODS,
    CASE_ID,
    READ_REQUIREMENTS,
    edge_search_adaptability,
    edge_search_case_plan,
    execute_edge_search_case,
    validate_edge_search_adapter,
)
from bcs_e2e.edge_search_contract import bot_search_source_binding
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError


class _SearchAdapter:
    def __init__(self, fail_on: int | None = None, mutate_evidence: bool = False):
        self.fail_on = fail_on
        self.mutate_evidence = mutate_evidence
        self.calls = 0
        self.plan = edge_search_case_plan()

    def _call(self, requirement):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError('injected failure')
        evidence = {
            'transport': 'test-owned-transport',
            'identity': 'RUN_OWNED_CEO_BOT' if requirement.actor != 'UNAUTHENTICATED' else 'ANONYMOUS',
            'http_method': 'GET',
            'path': requirement.path,
            'http_status': requirement.expected_http_status,
            'response_contract': requirement.response_contract,
        }
        if self.mutate_evidence and self.calls == 1:
            evidence['http_status'] = 500
        return AdapterCall(data={'sequence': self.calls}, evidence=evidence)

    def __getattr__(self, name):
        requirement = next(row for row in self.plan.requirements if row.adapter_method == name)
        return lambda: self._call(requirement)


class EdgeSearchCaseTests(unittest.TestCase):
    def test_plan_contains_only_twelve_reads_and_separates_the_fixture_mutation(self):
        plan = edge_search_case_plan()
        self.assertEqual(plan.case_id, CASE_ID)
        self.assertEqual(plan.parent_id, 'test_ep_bot_search')
        self.assertEqual(plan.requirement_ids, READ_REQUIREMENTS)
        self.assertEqual(tuple(plan.adapters.values()), ADAPTER_METHODS)
        self.assertEqual(len(plan.requirements), 12)
        self.assertEqual(plan.fixture_admission.requirement_id,
                         'EDGE-BOT-SEARCH.ensure-tc-fixture')
        self.assertEqual(plan.fixture_admission.actor, 'SERVICE_KEY')
        self.assertEqual(plan.fixture_admission.effect, 'MUTATION')
        self.assertEqual(plan.fixture_admission.ownership_scope, 'SEPARATE_ADMISSION')
        self.assertNotIn('ensure', ' '.join(ADAPTER_METHODS))
        self.assertEqual([row.name for row in plan.environments],
                         ['owned-local', 'singlebox', 'preprod'])
        self.assertEqual([row.status for row in plan.environments],
                         ['PENDING', 'PENDING', 'PENDING'])
        self.assertNotIn('validation currently fails', plan.environments[0].blocker)

    def test_reviewed_status_contracts_are_strict_and_fail_closed_for_anonymous_fields(self):
        plan = edge_search_case_plan()
        by_id = {row.requirement_id: row for row in plan.requirements}
        self.assertEqual(by_id['EDGE-BOT-SEARCH.name-fuzzy'].expected_http_status, 200)
        self.assertEqual(by_id['EDGE-BOT-SEARCH.public-online-not-friend'].expected_http_status, 400)
        self.assertEqual(by_id['EDGE-BOT-SEARCH.friend-true'].expected_http_status, 400)
        self.assertEqual(by_id['EDGE-BOT-SEARCH.bad-limit'].expected_http_status, 400)
        self.assertEqual(by_id['EDGE-BOT-SEARCH.bad-visibility'].expected_http_status, 400)
        self.assertEqual(by_id['EDGE-BOT-SEARCH.bad-status'].expected_http_status, 400)
        self.assertEqual(by_id['EDGE-BOT-SEARCH.anonymous'].response_contract,
                         'NO_PRIVATE_OR_FRIENDSHIP_FIELDS')
        self.assertTrue(all(row.expected_http_status in (200, 400)
                            for row in plan.requirements))

    def test_execution_spine_accepts_the_exact_twelve_method_contract(self):
        adapter = _SearchAdapter()
        result = execute_edge_search_case(adapter)
        self.assertEqual(result.status, 'PASS')
        self.assertEqual(adapter.calls, 12)
        self.assertEqual(result.completed_count, 12)
        self.assertEqual(result.total_count, 12)
        self.assertEqual(result.error_type, None)
        self.assertEqual([step.method for step in result.steps], list(ADAPTER_METHODS))

    def test_failure_contract_is_never_retried_and_blocks_later_reads(self):
        adapter = _SearchAdapter(fail_on=2)
        result = execute_edge_search_case(adapter)
        self.assertEqual(result.status, 'BLOCKED')
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(result.completed_count, 1)
        self.assertEqual([step.status for step in result.steps],
                         ['PASS'] + ['NOT_RUN'] * 11)
        self.assertEqual(result.error_type, 'RuntimeError')

    def test_adapter_evidence_must_match_the_frozen_response_contract(self):
        adapter = _SearchAdapter(mutate_evidence=True)
        result = execute_edge_search_case(adapter)
        self.assertEqual(result.status, 'BLOCKED')
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result.error_type, 'RunStateError')

    def test_missing_adapter_method_is_rejected_before_any_transport_call(self):
        class Missing: pass
        plan = edge_search_case_plan()
        with self.assertRaises(RunStateError) as raised:
            validate_edge_search_adapter(Missing(), plan)
        self.assertIn('read_bot_search_name_fuzzy', str(raised.exception))

    def test_source_drift_cannot_be_laundered_through_the_read_spine(self):
        original = bot_search_source_binding()
        value = copy.deepcopy(original)
        value['requirements'][0]['path'] = '/invented/search'
        with patch('bcs_e2e.edge_search_case.bot_search_source_binding', return_value=value):
            with self.assertRaises(RunStateError):
                edge_search_case_plan()

    def test_migration_plan_exposes_the_spine_without_executor_or_release_credit(self):
        migration = migration_plan()
        rewrite = next(row for row in migration['cross_environment_case_rewrites']
                       if row['case_id'] == CASE_ID)
        self.assertEqual(rewrite['parent_id'], 'test_ep_bot_search')
        self.assertEqual(rewrite['requirement_ids'], list(READ_REQUIREMENTS))
        self.assertEqual(rewrite['adapter_methods'], list(ADAPTER_METHODS))
        self.assertEqual(rewrite['status'], 'PENDING')
        self.assertFalse(rewrite['ready_to_execute'])
        self.assertFalse(rewrite['grants_release_credit'])
        executor = next(row for row in migration['local_registered_executors']
                        if row['legacy_id'] == 'test_ep_bot_search')
        self.assertEqual(executor['case_id'], CASE_ID)
        self.assertEqual(executor['status'], 'LOCAL_EXECUTOR_ONLY')
        self.assertEqual(executor['required_assertion_count'], 12)
        self.assertEqual(executor['requirement_ids'], list(READ_REQUIREMENTS))
        self.assertEqual(executor['canonical_reporting'], 'INTEGRATED_LOCAL_SHARED_RUNTIME')
        self.assertEqual(executor['remote_validation'], 'NOT_RUN')
        self.assertFalse(executor['grants_release_credit'])
        candidate = next(row for row in migration['cross_environment_source_candidates']['cases']
                         if row['legacy_id'] == 'test_ep_bot_search')
        self.assertEqual(candidate['status'], 'PENDING')
        self.assertEqual(candidate['excluded_mutation_ids'],
                         ['EDGE-BOT-SEARCH.ensure-tc-fixture'])
        self.assertFalse(candidate['grants_release_credit'])
        self.assertFalse(edge_search_adaptability()['ready_to_execute'])


if __name__ == '__main__':
    unittest.main()
