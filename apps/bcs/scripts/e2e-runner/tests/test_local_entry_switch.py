"""Offline 6.6 switch review preconditions and exact blockers."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from bcs_e2e.local_entry_switch import review_local_entry_switch
from bcs_e2e.migration_inventory import migration_plan


def plan():
    return migration_plan()


# This A tree still has 29 registered executors: three reviewed parents await
# registration and one registered row is still known backlog. Pin those 29
# rows here so the 6.6 review regressions test the actual local migration
# floor while the runtime roster keeps its honest 31/10/41 facts.
A_TREE_LOCAL_EXECUTOR_ROSTER = tuple(
    {'id': row['legacy_id']}
    for row in plan()['local_registered_executors']
)


def partial_equivalent(value):
    result = copy.deepcopy(value)
    absorbed = result["local_registered_executors"].pop(0)
    result["local_partial_executors"] = [{
        "case_id": absorbed["case_id"], "legacy_id": absorbed["legacy_id"]
    }]
    return result


class LocalEntrySwitchReviewTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        roster_patch = patch(
            'bcs_e2e.local_entry_switch.reviewed_release_inventory',
            return_value=list(A_TREE_LOCAL_EXECUTOR_ROSTER),
        )
        roster_patch.start()
        self.addCleanup(roster_patch.stop)

    def test_complete_review_is_backed_by_performed_local_shell_default(self):
        with patch("bcs_e2e.local_entry_switch.migration_plan", return_value=plan()):
            result = review_local_entry_switch()
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["ready_to_execute"])
        self.assertEqual(result["executor_count"], 29)
        self.assertEqual(result["partial_executor_count"], 0)
        self.assertEqual(result["blockers"], [])
        self.assertEqual(
            [row["status"] for row in result["executors"]],
            ["READY"] * 29,
        )
        self.assertEqual(result["entry_switch"], "PERFORMED_LOCAL_SHELL_DEFAULT")
        self.assertEqual(result["remote_validation"], "NOT_RUN")
        self.assertFalse(result["grants_release_credit"])
        self.assertEqual(result["rust_unit_test_semantics"], "UNCHANGED")
        self.assertEqual(result["legacy_entry"],
                         "LEGACY_SINGLEBOX_E2E_ENTRY_EXPLICIT_ROLLBACK_ONLY")
        self.assertEqual(result["new_core"], "OWNED_LOCAL_BCS_E2E_RUNNER_CORE")
        self.assertEqual(result["rollback"]["db_migration"], "NONE")
        self.assertEqual(result["ledger_recovery"]["automatic_replay"], "NOT_ALLOWED")

    def test_absorbing_one_parent_executor_to_partial_blocks_the_review(self):
        with patch(
            "bcs_e2e.local_entry_switch.migration_plan",
            return_value=partial_equivalent(plan()),
        ):
            result = review_local_entry_switch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["ready_to_execute"])
        self.assertEqual(result["executor_count"], 28)
        self.assertEqual(result["partial_executor_count"], 1)
        self.assertIn("executor.count:28 expected 29", result["blockers"])
        self.assertIn(
            "parents.missing:story_user_builds_trusted_team",
            result["blockers"],
        )
        self.assertIn(
            "partial.executors.present:LOCAL-TRUST-API-CLI-01",
            result["blockers"],
        )

    def test_wrong_parent_keeps_the_review_from_claiming_the_original_29(self):
        mutated = plan()
        mutated["local_registered_executors"][0]["legacy_id"] = "unknown-parent"
        with patch("bcs_e2e.local_entry_switch.migration_plan", return_value=mutated):
            result = review_local_entry_switch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "parents.missing:story_user_builds_trusted_team",
            result["blockers"],
        )
        self.assertIn("parents.unexpected:unknown-parent", result["blockers"])
        self.assertNotIn("executor.count:29 expected 29", result["blockers"])

    def test_non_integrated_reporting_is_an_exact_blocker(self):
        mutated = plan()
        mutated["local_registered_executors"][1]["canonical_reporting"] = "NOT_INTEGRATED"
        with patch("bcs_e2e.local_entry_switch.migration_plan", return_value=mutated):
            result = review_local_entry_switch()
        self.assertEqual(result["status"], "BLOCKED")
        row = result["executors"][1]
        self.assertEqual(row["status"], "BLOCKED")
        self.assertIn(
            "canonical.reporting:LOCAL-CLI-FILE-01:"
            "'NOT_INTEGRATED' must contain INTEGRATED_LOCAL",
            row["blockers"],
        )

    def test_release_credit_is_an_exact_blocker(self):
        mutated = plan()
        mutated["local_registered_executors"][1]["grants_release_credit"] = True
        with patch("bcs_e2e.local_entry_switch.migration_plan", return_value=mutated):
            result = review_local_entry_switch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "release.credit:LOCAL-CLI-FILE-01:True expected False",
            result["blockers"],
        )

    def test_missing_command_and_contract_is_an_exact_blocker(self):
        mutated = plan()
        row = mutated["local_registered_executors"][1]
        row.pop("execution_command", None)
        row.pop("programmatic_execution_contract", None)
        with patch("bcs_e2e.local_entry_switch.migration_plan", return_value=mutated):
            result = review_local_entry_switch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "execution.binding:LOCAL-CLI-FILE-01:"
            "missing execution_command or programmatic_execution_contract",
            result["blockers"],
        )

    def test_current_frozen_plan_is_ready_with_the_trust_contract_registered(self):
        result = review_local_entry_switch()
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["ready_to_execute"])
        self.assertEqual(result["blockers"], [])
        row = result["executors"][0]
        self.assertEqual(row["case_id"], "LOCAL-TRUST-API-CLI-01")
        self.assertEqual(
            row["execution_binding"],
            {
                "kind": "PROGRAMMATIC_EXECUTION_CONTRACT",
                "value": "owned-local-trust-api-cli-v1",
            },
        )

    def test_remote_validation_changes_are_not_swallowed_by_local_readiness(self):
        mutated = plan()
        mutated["local_registered_executors"][1]["remote_validation"] = "RUN"
        with patch("bcs_e2e.local_entry_switch.migration_plan", return_value=mutated):
            result = review_local_entry_switch()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "remote.validation:LOCAL-CLI-FILE-01:'RUN' expected NOT_RUN",
            result["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
