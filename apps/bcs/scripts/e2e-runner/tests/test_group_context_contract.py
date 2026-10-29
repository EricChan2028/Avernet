"""Offline source-binding and execution contract tests; no live request."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_adapters import AdapterCall
from bcs_e2e.group_context_contract import (
    ADAPTER_METHODS,
    CASE_ID,
    PARENT,
    SUITE,
    execute_group_context_case,
    group_context_adaptability,
    group_context_case_plan,
    group_context_source_binding,
    validate_group_context_adapter,
)
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.run_state import RunStateError

REQUIREMENT_IDS = (
    "CLI-GROUP-CONTEXT.workspace",
    "CLI-GROUP-CONTEXT.add-group-entry",
    "CLI-GROUP-CONTEXT.add-session-entry",
    "CLI-GROUP-CONTEXT.add-round-entry",
    "CLI-GROUP-CONTEXT.get-initial-entry",
    "CLI-GROUP-CONTEXT.update-entry",
    "CLI-GROUP-CONTEXT.list-entries",
    "CLI-GROUP-CONTEXT.get-updated-entry",
    "CLI-GROUP-CONTEXT.cleanup-workspace",
)

SOURCE = ROOT.parent / "e2e-test" / "cli-stories.sh"


def contract_value() -> dict:
    return json.loads(
        (ROOT / "group-context-migration-contract.json").read_text("utf-8"))


def obligation_evidence() -> dict:
    return {
        "transport": "PRODUCT_CLI",
        "operation": "group-context obligation",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


class FakeGroupContextAdapter:
    """Credential-free double standing in for the product CLI adapter."""

    def __init__(self, *, fail_at="", invalid_evidence_at="", wrong_type_at=""):
        self.fail_at = fail_at
        self.invalid_evidence_at = invalid_evidence_at
        self.wrong_type_at = wrong_type_at
        self.calls: list[str] = []

    def _dispatch(self, method: str) -> AdapterCall:
        self.calls.append(method)
        if self.wrong_type_at == method:
            return {"not": "an AdapterCall"}
        if self.invalid_evidence_at == method:
            return AdapterCall(
                data={"obligation": method},
                evidence={"transport": "UNDECLARED"})
        if self.fail_at == method:
            raise RunStateError(f"fake adapter failure at {method}")
        return AdapterCall(
            data={"obligation": method}, evidence=obligation_evidence())

    def create_workspace(self):
        return self._dispatch("create_workspace")

    def add_group_context_entry(self):
        return self._dispatch("add_group_context_entry")

    def add_session_context_entry(self):
        return self._dispatch("add_session_context_entry")

    def add_round_context_entry(self):
        return self._dispatch("add_round_context_entry")

    def get_initial_context(self):
        return self._dispatch("get_initial_context")

    def update_group_context_entry(self):
        return self._dispatch("update_group_context_entry")

    def list_context_entries(self):
        return self._dispatch("list_context_entries")

    def get_updated_context(self):
        return self._dispatch("get_updated_context")

    def cleanup_group_context(self):
        return self._dispatch("cleanup_group_context")


class GroupContextSourceBindingTests(unittest.TestCase):
    def test_binding_freezes_nine_obligations_against_shell_source(self):
        binding = group_context_source_binding()
        self.assertEqual(set(binding), {
            "schema_version", "source_revision", "parent", "sources",
            "requirements"})
        self.assertEqual(binding["schema_version"], 1)
        self.assertEqual(binding["parent"], PARENT)
        self.assertTrue(re.fullmatch("[0-9a-f]{40}", binding["source_revision"]))
        self.assertEqual(set(binding["sources"]), {"cli-stories.sh"})
        self.assertEqual(binding["sources"]["cli-stories.sh"],
                         hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        requirements = binding["requirements"]
        self.assertEqual([row["id"] for row in requirements],
                         list(REQUIREMENT_IDS))
        self.assertEqual(len(ADAPTER_METHODS), len(requirements))
        for row in requirements:
            self.assertEqual(set(row), {"id", "command", "expectation"})
            self.assertIsInstance(row["expectation"], str)
            self.assertTrue(row["expectation"])

    def test_binding_rejects_a_missing_shell_source(self):
        prefix = Path(tempfile.mkdtemp())
        try:
            contract_root = prefix / "contract-root"
            contract_root.mkdir()
            shutil.copy(ROOT / "group-context-migration-contract.json",
                        contract_root / "group-context-migration-contract.json")
            (prefix / "e2e-test").mkdir()
            with patch("bcs_e2e.group_context_contract.ROOT", contract_root), \
                    self.assertRaises(RunStateError):
                group_context_source_binding()
        finally:
            shutil.rmtree(prefix)

    def test_binding_rejects_contract_mutations(self):
        for mutation in (
                "remove-requirement", "duplicate-requirement-id", "foreign-parent",
                "non-hex-revision", "extra-top-level-key", "missing-sources-key",
                "extra-source", "wrong-source-hash", "non-string-expectation",
                "empty-expectation", "extra-row-key", "schema-version-two"):
            value = json.loads(json.dumps(contract_value()))
            requirements = value["requirements"]
            with self.subTest(mutation=mutation):
                if mutation == "remove-requirement":
                    requirements.pop()
                elif mutation == "duplicate-requirement-id":
                    requirements[0]["id"] = requirements[1]["id"]
                elif mutation == "foreign-parent":
                    value["parent"] = "story_cli_operator_foreign"
                elif mutation == "non-hex-revision":
                    value["source_revision"] = "not-a-hex-revision"
                elif mutation == "extra-top-level-key":
                    value["extra"] = 1
                elif mutation == "missing-sources-key":
                    del value["sources"]
                elif mutation == "extra-source":
                    value["sources"]["other.sh"] = "0" * 64
                elif mutation == "wrong-source-hash":
                    value["sources"]["cli-stories.sh"] = "0" * 64
                elif mutation == "non-string-expectation":
                    requirements[0]["expectation"] = 1
                elif mutation == "empty-expectation":
                    requirements[0]["expectation"] = ""
                elif mutation == "extra-row-key":
                    requirements[0]["extra"] = 1
                else:
                    value["schema_version"] = 2
                with patch("bcs_e2e.group_context_contract.read",
                           return_value=value), \
                        self.assertRaises(RunStateError):
                    group_context_source_binding()


class GroupContextPlanTests(unittest.TestCase):
    def test_case_plan_pairs_nine_requirements_with_adapter_methods(self):
        plan = group_context_case_plan()
        self.assertEqual(plan["case_id"], CASE_ID)
        self.assertEqual(plan["parent_id"], PARENT)
        self.assertEqual(plan["requirement_ids"], list(REQUIREMENT_IDS))
        self.assertEqual(plan["adapter_methods"], list(ADAPTER_METHODS))
        self.assertEqual(plan["source_revision"],
                         contract_value()["source_revision"])
        self.assertEqual(plan["remote_validation"], "NOT_RUN")
        self.assertFalse(plan["grants_release_credit"])

    def test_adaptability_declares_both_environments_without_release_credit(self):
        value = group_context_adaptability()
        self.assertEqual(value["case_id"], CASE_ID)
        self.assertEqual(value["selected_obligations"],
                         ["group-context-full-parent"])
        self.assertEqual(value["environments"],
                         ["singlebox-product-cli", "preprod-product-cli"])
        self.assertEqual(value["local_executor_status"],
                         "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertEqual(value["cli_surface"], "product-bcs-cli-group-context")
        self.assertEqual(value["remote_validation"], "NOT_RUN")
        self.assertFalse(value["grants_release_credit"])
        self.assertIn("preprod", " ".join(value["blockers"]).lower())

    def test_group_context_is_a_separate_slice_outside_release_local_executors(self):
        result = migration_plan()
        rows = [row for row in result["cross_environment_product_cli_case_slices"]
                if row.get("parent_id") == PARENT]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["case_id"], CASE_ID)
        self.assertEqual(rows[0]["adapter_methods"], list(ADAPTER_METHODS))
        legacy = {row["legacy_id"]
                  for row in result["local_registered_executors"]}
        self.assertNotIn(PARENT, legacy)


class GroupContextExecutionTests(unittest.TestCase):
    def test_all_nine_obligations_pass_in_frozen_order(self):
        adapter = FakeGroupContextAdapter()
        execution = execute_group_context_case(adapter)
        self.assertEqual(execution.case_id, CASE_ID)
        self.assertEqual(execution.parent_id, PARENT)
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.completed_count, 9)
        self.assertEqual(execution.total_count, 9)
        self.assertTrue(execution.ready_to_execute)
        self.assertIsNone(execution.error_type)
        self.assertEqual(adapter.calls, list(ADAPTER_METHODS))
        for step, requirement_id, method in zip(
                execution.steps, REQUIREMENT_IDS, ADAPTER_METHODS):
            self.assertEqual(step.requirement_id, requirement_id)
            self.assertEqual(step.method, method)
            self.assertEqual(step.status, "PASS")
            self.assertIsNone(step.error_type)
            self.assertEqual(step.data, {"obligation": method})
            self.assertEqual(step.evidence["transport"], "PRODUCT_CLI")
            self.assertEqual(step.evidence["remote_validation"], "NOT_RUN")
            self.assertFalse(step.evidence["grants_release_credit"])

    def test_first_failure_blocks_the_case_and_skips_remaining_obligations(self):
        execution = execute_group_context_case(
            FakeGroupContextAdapter(fail_at="add_round_context_entry"))
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 3)
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertFalse(execution.ready_to_execute)
        for step in execution.steps[:3]:
            self.assertEqual(step.status, "PASS")
        failed = execution.steps[3]
        self.assertEqual(failed.status, "NOT_RUN")
        self.assertEqual(failed.error_type, "RunStateError")
        self.assertEqual(failed.requirement_id, REQUIREMENT_IDS[3])
        for step in execution.steps[4:]:
            self.assertEqual(step.status, "NOT_RUN")
            self.assertIsNone(step.error_type)

    def test_evidence_outside_contract_blocks_the_case(self):
        execution = execute_group_context_case(
            FakeGroupContextAdapter(invalid_evidence_at="list_context_entries"))
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 6)
        self.assertEqual(execution.error_type, "RunStateError")
        self.assertEqual(execution.steps[6].status, "NOT_RUN")

    def test_non_adapter_call_result_blocks_the_case(self):
        execution = execute_group_context_case(
            FakeGroupContextAdapter(wrong_type_at="cleanup_group_context"))
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 8)
        self.assertEqual(execution.error_type, "RunStateError")

    def test_missing_adapter_method_is_rejected_before_execution(self):
        class IncompleteAdapter:
            def __init__(self):
                for method in ADAPTER_METHODS:
                    if method != "list_context_entries":
                        setattr(self, method, lambda: None)

        with self.assertRaisesRegex(RunStateError, "list_context_entries"):
            validate_group_context_adapter(IncompleteAdapter())
        with self.assertRaises(RunStateError):
            execute_group_context_case(IncompleteAdapter())

    def test_suite_and_case_identifiers_are_exported(self):
        self.assertEqual(SUITE, "dual-group-context-cli-v1")
        self.assertEqual(REQUIREMENT_IDS[-1],
                         "CLI-GROUP-CONTEXT.cleanup-workspace")


if __name__ == "__main__":
    unittest.main()
