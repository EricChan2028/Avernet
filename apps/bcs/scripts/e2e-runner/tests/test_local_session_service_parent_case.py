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
from bcs_e2e.local_session_service_cli import (
    LIFECYCLE_ASSERTION_IDS,
    MEMBER_CHAT_ASSERTION_IDS,
    SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
    SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
)
from bcs_e2e.local_session_service_completion import (
    COMPLETE_ASSERTION_IDS,
    ROUNDTRIP_ASSERTION_IDS,
    SESSION_SERVICE_ASSERTION_IDS,
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
)
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
ASSERTION_GROUPS = (
    LIFECYCLE_ASSERTION_IDS,
    MEMBER_CHAT_ASSERTION_IDS,
    COMPLETE_ASSERTION_IDS,
    ROUNDTRIP_ASSERTION_IDS,
)


def execution(case_id: str, assertion_ids: tuple[str, ...], method: str,
              *, status: str = "PASS") -> CaseExecution:
    steps = []
    completed = 0
    for assertion_id in assertion_ids:
        passed = status == "PASS" and completed == 0
        if status == "PASS":
            passed = True
        steps.append(StepOutcome(
            requirement_id=assertion_id, method=method,
            status="PASS" if passed else "NOT_RUN",
            data={"exact": True} if passed else None,
            evidence={}, error_type=None if passed else "RunStateError",
        ))
        if passed:
            completed += 1
    value = CaseExecution(
        case_id=case_id, parent_id=PARENT, status=status,
        steps=tuple(steps), completed_count=completed,
        total_count=len(steps), error_type=None if status == "PASS" else "RunStateError",
        ready_to_execute=status == "PASS",
    )
    value.validate()
    return value


def child_executions(*, member_chat_status: str = "PASS") -> tuple[CaseExecution, ...]:
    return (
        execution("LOCAL-SESSION-SERVICE-LIFECYCLE-01",
                  LIFECYCLE_ASSERTION_IDS, SESSION_SERVICE_LIFECYCLE_CASE_METHOD),
        execution("LOCAL-SESSION-SERVICE-MEMBER-CHAT-01",
                  MEMBER_CHAT_ASSERTION_IDS, SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
                  status=member_chat_status),
        execution("LOCAL-SESSION-SERVICE-COMPLETE-01",
                  COMPLETE_ASSERTION_IDS, SESSION_SERVICE_COMPLETE_CASE_METHOD),
        execution("LOCAL-SESSION-SERVICE-ROUNDTRIP-01",
                  ROUNDTRIP_ASSERTION_IDS, SESSION_SERVICE_ROUNDTRIP_CASE_METHOD),
    )


class LocalSessionServiceParentCaseTests(unittest.TestCase):
    def test_parent_case_plan_and_adaptability_remain_local_credit_only(self):
        plan = session_service_parent_case_plan()
        self.assertEqual(plan["case_id"], PARENT_CASE_ID)
        self.assertEqual(plan["legacy_id"], PARENT)
        self.assertEqual(plan["requirement_ids"], list(REQUIREMENTS))
        self.assertEqual(plan["parent_requirement_count"], 4)
        self.assertEqual(plan["assertion_ids"], list(SESSION_SERVICE_ASSERTION_IDS))
        self.assertEqual(plan["assertion_count"], 44)
        adaptability = session_service_parent_adaptability()
        self.assertEqual(adaptability["local_executor_status"], "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertEqual(adaptability["assertion_count"], 44)
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        self.assertIn("preprod", adaptability["blocker"])

    def test_success_combines_all_four_slices_at_44_assertion_points(self):
        children = [Mock(return_value=value) for value in child_executions()]
        adapters = (Mock(), Mock(), Mock(), Mock())
        constructors = [Mock(side_effect=value) for value in adapters]
        with patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceLifecycleAdapter", constructors[0]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceMemberChatAdapter", constructors[1]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceCompleteAdapter", constructors[2]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceRoundtripAdapter", constructors[3]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_lifecycle_case", children[0]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_member_chat_case", children[1]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_complete_case", children[2]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_roundtrip_case", children[3]):
            value = execute_local_session_service_parent_case({}, Mock())
        self.assertEqual(value.status, "PASS")
        self.assertEqual(value.case_id, PARENT_CASE_ID)
        self.assertEqual(value.parent_id, PARENT)
        self.assertEqual(value.completed_count, 44)
        self.assertEqual(value.total_count, 44)
        self.assertEqual([step.requirement_id for step in value.steps],
                         list(SESSION_SERVICE_ASSERTION_IDS))
        self.assertEqual([step.status for step in value.steps], ["PASS"] * 44)
        self.assertFalse(value.error_type)

    def test_a_child_failure_blocks_later_sibling_slices(self):
        children = [Mock(return_value=value) for value in child_executions(
            member_chat_status="BLOCKED")]
        adapters = (Mock(), Mock(), Mock(), Mock())
        constructors = [Mock(side_effect=value) for value in adapters]
        with patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceLifecycleAdapter", constructors[0]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceMemberChatAdapter", constructors[1]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceCompleteAdapter", constructors[2]), \
             patch("bcs_e2e.local_session_service_parent_case.RemoteLocalSessionServiceRoundtripAdapter", constructors[3]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_lifecycle_case", children[0]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_member_chat_case", children[1]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_complete_case", children[2]), \
             patch("bcs_e2e.local_session_service_parent_case.execute_local_session_service_roundtrip_case", children[3]):
            value = execute_local_session_service_parent_case({}, Mock())
        self.assertEqual(value.status, "BLOCKED")
        self.assertEqual(value.completed_count, 12)
        self.assertEqual(value.total_count, 44)
        self.assertEqual([step.status for step in value.steps],
                         ["PASS"] * 12 + ["NOT_RUN"] * 32)
        self.assertEqual(value.error_type, "RunStateError")


if __name__ == "__main__":
    unittest.main()
