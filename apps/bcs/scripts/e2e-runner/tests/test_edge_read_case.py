"""Edge read case rewrites and execution spine are adapter-only until admitted."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.edge_read_case import (
    ADMISSION_ADAPTER_METHODS,
    ADMISSION_CASE_ID,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_CONNECTIONS_CASE_ID,
    edge_read_case_plans,
    execute_edge_read_case,
    validate_edge_read_adapter,
)
from bcs_e2e.edge_read_contract import admission_source_binding
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError


class _ReadAdapter:
    def __init__(self, fail_on: int | None = None):
        self.fail_on = fail_on
        self.calls = 0

    def _call(self, evidence):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError('injected failure')
        return AdapterCall(data={'sequence': self.calls}, evidence=dict(evidence))

    def read_admission_human_actor(self):
        return self._call({'actor': 'human'})

    def read_admission_actor_kind(self):
        return self._call({'actor_kind': 'human'})

    def read_friend_requests_received(self):
        return self._call({'direction': 'received'})

    def read_friend_requests_sent(self):
        return self._call({'direction': 'sent'})

    def read_friend_requests_all(self):
        return self._call({'direction': 'all'})

    def read_friend_connections_list(self):
        return self._call({'actor_kind': 'bot'})

    def read_friend_connections_list_repeat(self):
        return self._call({'actor_kind': 'bot', 'repeat': True})


class EdgeReadCaseTests(unittest.TestCase):
    def test_edge_read_cases_have_environment_neutral_method_seams(self):
        admission, requests, connections = edge_read_case_plans()
        self.assertEqual(admission.case_id, ADMISSION_CASE_ID)
        self.assertEqual(tuple(admission.adapters.values()), ADMISSION_ADAPTER_METHODS)
        self.assertEqual(admission.requirements[0].method, 'GET')
        self.assertEqual(admission.requirements[0].actor, 'UNAUTHENTICATED')
        self.assertEqual(requests.case_id, FRIEND_REQUESTS_CASE_ID)
        self.assertEqual([row.actor for row in requests.requirements], ['CEO_BOT'] * 3)
        self.assertEqual(tuple(requests.adapters.values()),
                         tuple(row for row in ('read_friend_requests_received',
                                              'read_friend_requests_sent', 'read_friend_requests_all')))
        self.assertEqual(connections.case_id, FRIEND_CONNECTIONS_CASE_ID)
        self.assertEqual([row.actor for row in connections.requirements], ['CEO_BOT'] * 2)
        self.assertEqual(tuple(connections.adapters.values()),
                         ('read_friend_connections_list', 'read_friend_connections_list_repeat'))
        for plan in (admission, requests, connections):
            with self.subTest(case_id=plan.case_id):
                self.assertTrue(all(row.method == 'GET' for row in plan.requirements))
                self.assertEqual([row.name for row in plan.environments],
                                  ['owned-local', 'singlebox', 'preprod'])
                self.assertTrue(all(row.status == 'PENDING' and row.blocker for row in plan.environments))

    def test_execution_spine_accepts_adapters_and_completes_the_exact_case(self):
        admission = execute_edge_read_case(ADMISSION_CASE_ID, _ReadAdapter())
        self.assertEqual(admission.status, 'PASS')
        self.assertEqual(admission.completed_count, 2)
        self.assertEqual(admission.total_count, 2)
        self.assertEqual(admission.error_type, None)
        requests = execute_edge_read_case(FRIEND_REQUESTS_CASE_ID, _ReadAdapter())
        self.assertEqual(requests.status, 'PASS')
        connections = execute_edge_read_case(FRIEND_CONNECTIONS_CASE_ID, _ReadAdapter())
        self.assertEqual(connections.status, 'PASS')
        self.assertEqual([step.method for step in connections.steps],
                         ['read_friend_connections_list', 'read_friend_connections_list_repeat'])
        self.assertEqual([step.method for step in requests.steps],
                         ['read_friend_requests_received', 'read_friend_requests_sent',
                          'read_friend_requests_all'])

    def test_execution_failure_blocks_later_steps_without_retry(self):
        adapter = _ReadAdapter(fail_on=2)
        result = execute_edge_read_case(FRIEND_REQUESTS_CASE_ID, adapter)
        self.assertEqual(result.status, 'BLOCKED')
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(result.error_type, 'RuntimeError')
        self.assertEqual([step.status for step in result.steps], ['PASS', 'NOT_RUN', 'NOT_RUN'])
        self.assertEqual(result.completed_count, 1)

    def test_unknown_case_or_missing_method_is_rejected_before_transport(self):
        with self.assertRaises(RunStateError):
            execute_edge_read_case('UNKNOWN-CASE', _ReadAdapter())
        plan = edge_read_case_plans()[0]

        class Missing: pass
        with self.assertRaises(RunStateError) as raised:
            validate_edge_read_adapter(Missing(), plan)
        self.assertIn('read_admission_human_actor', str(raised.exception))

    def test_source_drift_cannot_be_laundered_through_the_case_plan(self):
        original = admission_source_binding()
        value = copy.deepcopy(original)
        value['requirements'][0]['path'] = '/invented'
        with patch('bcs_e2e.edge_read_contract.read', return_value=value):
            with self.assertRaises(RunStateError):
                edge_read_case_plans()

    def test_plan_exposes_rewrites_without_admitting_preprod_or_release_credit(self):
        plan = migration_plan()
        rows = {
            row['case_id']: row
            for row in plan['cross_environment_case_rewrites']
        }
        self.assertEqual(rows[ADMISSION_CASE_ID]['parent_id'], 'test_ep_admission')
        self.assertEqual(rows[FRIEND_REQUESTS_CASE_ID]['parent_id'],
                         'test_ep_friend_connections_friend_requests_list')
        for row in rows.values():
            self.assertEqual(row['status'], 'PENDING')
            self.assertFalse(row['ready_to_execute'])
            self.assertFalse(row['grants_release_credit'])
            self.assertEqual([env['name'] for env in row['environments']],
                             ['owned-local', 'singlebox', 'preprod'])
            self.assertTrue(all(env['status'] == 'PENDING' for env in row['environments']))


if __name__ == '__main__':
    unittest.main()
