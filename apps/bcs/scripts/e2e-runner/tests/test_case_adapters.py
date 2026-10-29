"""The first slow-Judge adapter interface must stay bound to the case rewrite."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.case_adapters import (
    ADAPTER_METHOD_NAMES,
    FIXED_LOOP_ADAPTER_METHOD_NAMES,
    AdapterCall,
    SlowJudgeCaseAdapter,
    validate_adapter_contract,
)
from bcs_e2e.case_rewrite import fixed_loop_case_rewrite, slow_judge_case_rewrite
from bcs_e2e.edge_friend_mutation_case import (
    CASE_IDS as EDGE_FRIEND_MUTATION_CASE_IDS, edge_friend_mutation_case_plan)
from bcs_e2e.run_state import RunStateError


class CaseAdapterTests(unittest.TestCase):
    def test_adapter_method_names_match_the_full_22_requirement_plan(self):
        plan = slow_judge_case_rewrite()
        expected = tuple(plan.adapters[name] for name in plan.requirement_ids)
        self.assertEqual(len(expected), 22)
        self.assertEqual(expected, ADAPTER_METHOD_NAMES)
        self.assertEqual(len(set(ADAPTER_METHOD_NAMES)), len(ADAPTER_METHOD_NAMES))

    def test_concrete_adapter_must_implement_every_environmental_neutral_method(self):
        class Complete:
            pass
        for name in ADAPTER_METHOD_NAMES:
            setattr(Complete, name, lambda self: AdapterCall({}, {}))
        validate_adapter_contract(Complete())

    def test_missing_adapter_method_is_rejected_without_partial_success(self):
        class Partial:
            pass
        for name in ADAPTER_METHOD_NAMES[:-1]:
            setattr(Partial, name, lambda self: AdapterCall({}, {}))
        with self.assertRaisesRegex(RunStateError, 'case adapter is missing methods'):
            validate_adapter_contract(Partial())

    def test_adapter_call_requires_evidence_dictionary(self):
        call = AdapterCall({}, {})
        self.assertIsInstance(call.evidence, dict)

    def test_fixed_loop_adapter_method_names_match_the_eight_requirement_plan(self):
        plan = fixed_loop_case_rewrite()
        expected = tuple(plan.adapters[name] for name in plan.requirement_ids)
        self.assertEqual(len(expected), 8)
        self.assertEqual(expected, FIXED_LOOP_ADAPTER_METHOD_NAMES)
        self.assertEqual(len(set(FIXED_LOOP_ADAPTER_METHOD_NAMES)),
                         len(FIXED_LOOP_ADAPTER_METHOD_NAMES))

    def test_fixed_loop_adapter_must_implement_every_environmental_neutral_method(self):
        class Complete:
            pass
        for name in FIXED_LOOP_ADAPTER_METHOD_NAMES:
            setattr(Complete, name, lambda self: AdapterCall({}, {}))
        validate_adapter_contract(Complete(), fixed_loop_case_rewrite())

    def test_fixed_loop_missing_adapter_method_is_rejected_without_partial_success(self):
        class Partial:
            pass
        for name in FIXED_LOOP_ADAPTER_METHOD_NAMES[:-1]:
            setattr(Partial, name, lambda self: AdapterCall({}, {}))
        with self.assertRaisesRegex(RunStateError, 'case adapter is missing methods'):
            validate_adapter_contract(Partial(), fixed_loop_case_rewrite())

    def test_edge_friend_mutation_adapter_method_names_match_the_single_requirement_plans(self):
        for case_id in EDGE_FRIEND_MUTATION_CASE_IDS:
            with self.subTest(case_id=case_id):
                plan = edge_friend_mutation_case_plan(case_id)
                expected = tuple(plan.adapters[name] for name in plan.requirement_ids)
                self.assertEqual(len(expected), 1)
                self.assertEqual(len(set(expected)), 1)

    def test_edge_friend_mutation_adapter_must_implement_every_environmental_neutral_method(self):
        class Complete:
            pass
        names = []
        for case_id in EDGE_FRIEND_MUTATION_CASE_IDS:
            plan = edge_friend_mutation_case_plan(case_id)
            names.extend(plan.adapters[name] for name in plan.requirement_ids)
        self.assertEqual(len(names), 9)
        self.assertEqual(len(set(names)), 9)
        for name in names:
            setattr(Complete, name, lambda self: AdapterCall({}, {}))
        for case_id in EDGE_FRIEND_MUTATION_CASE_IDS:
            validate_adapter_contract(Complete(), edge_friend_mutation_case_plan(case_id))

    def test_validate_adapter_contract_defaults_to_the_slow_judge_plan(self):
        class Complete:
            pass
        for name in ADAPTER_METHOD_NAMES:
            setattr(Complete, name, lambda self: AdapterCall({}, {}))
        validate_adapter_contract(Complete())
