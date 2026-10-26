"""Contract tests for the complete owned-local Session Service parent case."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_executor import CaseExecution, StepOutcome
from bcs_e2e.local_session_service_parent_case import (
    PARENT_CASE_ID,
    execute_local_session_service_parent_case,
    session_service_parent_adaptability,
    session_service_parent_case_plan,
)

PARENT = "story_cli_operator_runs_sessions_and_services"
REQUIREMENTS = (
    "CLI-SESSION-SERVICE.session-lifecycle",
    "CLI-SESSION-SERVICE.member-chat-lifecycle",
    "CLI-SESSION-SERVICE.session-complete",
    "CLI-SESSION-SERVICE.service-roundtrip",
)
METHODS = (
    "execute_local_session_service_lifecycle_case",
    "execute_local_session_service_member_chat_case",
    "execute_local_session_service_complete_case",
    "execute_local_session_service_roundtrip_case",
)


def pass_execution(case_id: str, requirement_id: str, method: str) -> CaseExecution:
    execution = CaseExecution(
        case_id=case_id,
        parent_id=PARENT,
        status="PASS",
        steps=(StepOutcome(requirement_id=requirement_id, method=method,
                           status="PASS", data={"exact": True}, evidence={},
                           error_type=None),),
        completed_count=1,
        total_count=1,
        error_type=None,
        ready_to_execute=True,
    )
    execution.validate()
    return execution


class LocalSessionServiceParentCaseTests(unittest.TestCase):
    def test_parent_case_plan_and_adaptability_remain_local_credit_only(self):
        plan = session_service_parent_case_plan()
        self.assertEqual(plan["case_id"], PARENT_CASE_ID)
        self.assertEqual(plan["legacy_id"], PARENT)
        self.assertEqual(plan["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(plan["parent_requirement_count"], 4)
        adaptability = session_service_parent_adaptability()
        self.assertEqual(adaptability["local_executor_status"], "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        self.assertIn("preprod", adaptability["blocker"])

    def test_success_combines_all_four_exact_session_service_slices(self):
        passes = tuple(pass_execution(*spec) for spec in zip(
            ("LOCAL-SESSION-SERVICE-LIFECYCLE-01",
             "LOCAL-SESSION-SERVICE-MEMBER-CHAT-01",
             "LOCAL-SESSION-SERVICE-COMPLETE-01",
             "LOCAL-SESSION-SERVICE-ROUNDTRIP-01"),
            REQUIREMENTS, METHODS,
        ))
        adapters = (Mock(), Mock(), Mock(), Mock())
        constructors = [Mock(side_effect=value) for value in adapters]
        children = [Mock(return_value=case) for case in passes]
        with patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceLifecycleAdapter", constructors[0]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceMemberChatAdapter", constructors[1]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceCompleteAdapter", constructors[2]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceRoundtripAdapter", constructors[3]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_lifecycle_case", children[0]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_member_chat_case", children[1]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_complete_case", children[2]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_roundtrip_case", children[3]):
            execution = execute_local_session_service_parent_case({}, Mock())
        self.assertEqual(execution.status, "PASS")
        self.assertEqual(execution.case_id, PARENT_CASE_ID)
        self.assertEqual(execution.parent_id, PARENT)
        self.assertEqual(execution.completed_count, 4)
        self.assertEqual(execution.total_count, 4)
        self.assertEqual([step.requirement_id for step in execution.steps], list(REQUIREMENTS))
        self.assertEqual([step.status for step in execution.steps], ["PASS"]*4)
        self.assertFalse(execution.error_type)

    def test_a_child_failure_blocks_later_sibling_slices(self):
        passing = pass_execution(
            "LOCAL-SESSION-SERVICE-LIFECYCLE-01", REQUIREMENTS[0], METHODS[0])
        blocked_step = StepOutcome(
            requirement_id=REQUIREMENTS[1], method=METHODS[1],
            status="NOT_RUN", data=None, evidence={}, error_type="RunStateError",
        )
        blocked = CaseExecution(
            case_id="LOCAL-SESSION-SERVICE-MEMBER-CHAT-01", parent_id=PARENT,
            status="BLOCKED", steps=(blocked_step,), completed_count=0,
            total_count=1, error_type="RunStateError", ready_to_execute=False,
        )
        later = [Mock(return_value=passing) for _ in (0, 1)]
        with patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceLifecycleAdapter", return_value=Mock()), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceMemberChatAdapter", return_value=Mock()), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceCompleteAdapter", return_value=Mock()), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceRoundtripAdapter", return_value=Mock()), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_lifecycle_case", return_value=passing), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_member_chat_case", return_value=blocked), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_complete_case", later[0]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_roundtrip_case", later[1]):
            execution = execute_local_session_service_parent_case({}, Mock())
        self.assertEqual(execution.status, "BLOCKED")
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(execution.total_count, 4)
        self.assertEqual([step.status for step in execution.steps], ["PASS", "NOT_RUN", "NOT_RUN", "NOT_RUN"])
        self.assertEqual(execution.error_type, "RunStateError")
        for child in later:
            child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
