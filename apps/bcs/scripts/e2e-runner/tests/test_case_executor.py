"""The slow-Judge execution spine must honor the reviewed obligation order."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.case_adapters import (
    ADAPTER_METHOD_NAMES,
    FIXED_LOOP_ADAPTER_METHOD_NAMES,
    AdapterCall,
)
from bcs_e2e.case_executor import (
    execute_fixed_loop_case,
    execute_slow_judge_case,
)
from bcs_e2e.case_rewrite import fixed_loop_case_rewrite, slow_judge_case_rewrite
from bcs_e2e.run_state import RunStateError


class FakeAdapter:
    def __init__(self, fail_method: str | None = None):
        self.fail_method = fail_method
        self.executed = []

    def _run(self, name):
        self.executed.append(name)
        if name == self.fail_method:
            raise RuntimeError('runtime failure')
        return AdapterCall({'method': name}, {'history': name, 'status': 'PASS'})

    def __getattr__(self, name):
        if name in ADAPTER_METHOD_NAMES:
            return lambda: self._run(name)
        raise AttributeError(name)


class CaseExecutorTests(unittest.TestCase):
    def test_successful_case_hover_completes_only_after_all_22_steps(self):
        plan = slow_judge_case_rewrite()
        adapter = FakeAdapter()
        execution = execute_slow_judge_case(adapter)
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 22)
        self.assertEqual(execution.total_count, 22)
        self.assertIsNone(execution.error_type)
        self.assertTrue(execution.ready_to_execute)
        self.assertEqual([step.method for step in execution.steps], list(plan.adapters[name] for name in plan.requirement_ids))
        self.assertEqual([step.status for step in execution.steps], ['PASS'] * 22)

    def test_a_failing_method_blocks_the_case_and_keeps_later_steps_not_run(self):
        fail_method = ADAPTER_METHOD_NAMES[12]
        adapter = FakeAdapter(fail_method)
        execution = execute_slow_judge_case(adapter)
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 12)
        self.assertEqual(execution.error_type, 'RuntimeError')
        self.assertFalse(execution.ready_to_execute)
        self.assertEqual([step.status for step in execution.steps[:12]], ['PASS'] * 12)
        self.assertEqual([step.status for step in execution.steps[12:]], ['NOT_RUN'] * 10)

    def test_non_adapter_return_value_blocks_before_progress_credit(self):
        class BadAdapter(FakeAdapter):
            def _run(self, name):
                return {'wrong': True}
        execution = execute_slow_judge_case(BadAdapter())
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.error_type, 'RunStateError')
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')
        self.assertEqual(execution.steps[-1].status, 'NOT_RUN')

    def test_snapshot_is_a_stable_executable_summary(self):
        execution = execute_slow_judge_case(FakeAdapter())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['case_id'], 'LOCAL-SLOW-JUDGE-01')
        self.assertEqual(snapshot['status'], 'PASS')
        self.assertEqual(snapshot['completed_count'], 22)
        self.assertEqual(snapshot['total_count'], 22)
        self.assertTrue(snapshot['ready_to_execute'])
        self.assertEqual([step['method'] for step in snapshot['steps']], list(ADAPTER_METHOD_NAMES))


    def test_a_structured_service_gap_reaches_the_blocked_receipt(self):
        gap = {'gap': 'PREPROD_SERVICE_SCOPE_GAP', 'singlebox_closure': True,
               'method': ADAPTER_METHOD_NAMES[0]}

        class GapAdapter(FakeAdapter):
            def _run(self, name):
                error = RunStateError('PREPROD_SERVICE_SCOPE_GAP')
                error.gap = gap
                raise error

        execution = execute_slow_judge_case(GapAdapter())
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.error_type, 'RunStateError')
        self.assertEqual(execution.steps[0].error_gap, gap)
        self.assertIsNone(execution.steps[1].error_gap)
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['steps'][0]['error_gap'], gap)
        self.assertIsNone(snapshot['steps'][1]['error_gap'])


class FakeFixedLoopAdapter:
    def __init__(self, fail_method: str | None = None):
        self.fail_method = fail_method
        self.executed = []

    def _run(self, name):
        self.executed.append(name)
        if name == self.fail_method:
            raise RuntimeError('runtime failure')
        return AdapterCall({'method': name}, {'history': name, 'status': 'PASS'})

    def __getattr__(self, name):
        if name in FIXED_LOOP_ADAPTER_METHOD_NAMES:
            return lambda: self._run(name)
        raise AttributeError(name)


class FixedLoopCaseExecutorTests(unittest.TestCase):
    def test_successful_fixed_loop_case_completes_all_eight_steps(self):
        plan = fixed_loop_case_rewrite()
        execution = execute_fixed_loop_case(FakeFixedLoopAdapter())
        self.assertEqual(execution.status, 'PASS')
        self.assertEqual(execution.completed_count, 8)
        self.assertEqual(execution.total_count, 8)
        self.assertIsNone(execution.error_type)
        self.assertTrue(execution.ready_to_execute)
        self.assertEqual([step.method for step in execution.steps],
                         [plan.adapters[name] for name in plan.requirement_ids])
        self.assertEqual([step.status for step in execution.steps], ['PASS'] * 8)

    def test_a_failing_fixed_loop_method_blocks_the_case_and_keeps_later_steps_not_run(self):
        fail_method = FIXED_LOOP_ADAPTER_METHOD_NAMES[2]
        execution = execute_fixed_loop_case(FakeFixedLoopAdapter(fail_method))
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.error_type, 'RuntimeError')
        self.assertFalse(execution.ready_to_execute)
        self.assertEqual([step.status for step in execution.steps[:2]], ['PASS'] * 2)
        self.assertEqual([step.status for step in execution.steps[2:]], ['NOT_RUN'] * 6)

    def test_fixed_loop_non_adapter_return_value_blocks_before_progress_credit(self):
        class BadAdapter(FakeFixedLoopAdapter):
            def _run(self, name):
                return {'wrong': True}
        execution = execute_fixed_loop_case(BadAdapter())
        self.assertEqual(execution.status, 'BLOCKED')
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.error_type, 'RunStateError')
        self.assertEqual(execution.steps[0].status, 'NOT_RUN')
        self.assertEqual(execution.steps[-1].status, 'NOT_RUN')

    def test_fixed_loop_snapshot_is_a_stable_executable_summary(self):
        execution = execute_fixed_loop_case(FakeFixedLoopAdapter())
        snapshot = execution.snapshot()
        self.assertEqual(snapshot['case_id'], 'LOCAL-FIXED-LOOP-01')
        self.assertEqual(snapshot['parent_id'],
                         'story_fixed_loop_human_editor_workflow')
        self.assertEqual(snapshot['status'], 'PASS')
        self.assertEqual(snapshot['completed_count'], 8)
        self.assertEqual(snapshot['total_count'], 8)
        self.assertTrue(snapshot['ready_to_execute'])
        self.assertEqual([step['method'] for step in snapshot['steps']],
                         list(FIXED_LOOP_ADAPTER_METHOD_NAMES))
