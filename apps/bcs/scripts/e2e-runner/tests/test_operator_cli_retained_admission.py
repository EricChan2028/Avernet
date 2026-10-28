"""Retained-station admission substitution; no live request.

A stateful preprod station cannot revoke the reviewed fixture friendships
(the disclosed station revoke asymmetry stands), so an in-case partner
request can answer idempotently "Already friends" on the first and every
later Run. The runner records the station's exact answer backed by one
bounded live membership read, never fabricates a pending request ID, and
the paired accept leg records accept_invoked false without invoking the
accept route. Fresh admissions keep the strict request-ID contract; every
other shape stays fail-closed.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from bcs_e2e.operator_cli_runner import (
    OPERATOR_CLI_CHAT, OPERATOR_CLI_FRIEND_ACCEPT_CHAT,
    OPERATOR_CLI_FRIEND_ACCEPT_LIST, OPERATOR_CLI_FRIEND_LIST,
    OPERATOR_CLI_FRIEND_REQUEST_CHAT, OPERATOR_CLI_FRIEND_REQUEST_LIST,
    OperatorCliRunnerError, validate_operator_cli_profile,
)
from bcs_e2e.remote_operator_cli_adapter import RemoteOperatorCliAdapter
from bcs_e2e.run_state import RunStateError
from test_operator_cli_runner import (
    BOT_IDS, CliScript, OPERATIONS, OP_INDEX, OperatorCliHarness, REQUEST_ID,
    RUN_ID, SESSION_ID, full_environment, make_runner, profile_value,
)
from test_remote_operator_cli_adapter import (
    OperatorCliCaseTests, ScriptedRunner, preprod_environment,
    profile_value as dual_profile_value,
)

ALREADY_FRIENDS = (0, json.dumps({"success": True, "message": "Already friends",
                                  "data": None}).encode(), b"")
ACCEPT_KINDS = (OPERATOR_CLI_FRIEND_ACCEPT_CHAT, OPERATOR_CLI_FRIEND_ACCEPT_LIST)


def retained_prefix(last_operation):
    """Canonical frozen prefix; accept legs carry no request ID on the
    retained path, so the runner's substitution branch decides."""
    rows = []
    for operation, kwargs in OPERATIONS[:OP_INDEX[last_operation] + 1]:
        if operation in ACCEPT_KINDS:
            kwargs = {"request_id": None}
        rows.append((operation, kwargs))
    return rows


class RetainedAdmissionRunnerTests(OperatorCliHarness):
    def run_ops(self, last_operation, script):
        profile = validate_operator_cli_profile(profile_value(preprod=True))
        runner, _state = make_runner(profile, self.root, script)
        with patch.dict(os.environ, full_environment(preprod=True)), \
             patch("bcs_e2e.operator_cli_runner.capture", side_effect=script):
            results = [runner.execute(operation, **kwargs)
                       for operation, kwargs in retained_prefix(last_operation)]
        return runner, script, results

    def test_retained_chat_pair_records_verified_substitution(self):
        script = CliScript()
        script.overrides = {"friend_request": ALREADY_FRIENDS}
        runner, script, results = self.run_ops(OPERATOR_CLI_CHAT, script)
        request = results[OP_INDEX[OPERATOR_CLI_FRIEND_REQUEST_CHAT]]
        accept = results[OP_INDEX[OPERATOR_CLI_FRIEND_ACCEPT_CHAT]]
        chat = results[OP_INDEX[OPERATOR_CLI_CHAT]]
        self.assertIsNone(request["data"]["request_id"])
        self.assertEqual(request["data"]["admission"], "ALREADY_ADMITTED")
        self.assertTrue(request["data"]["friends_verified"])
        self.assertEqual(request["data"]["target_bot"], BOT_IDS["member"])
        self.assertEqual(accept["data"], {"request_id": None,
                                          "admission": "ALREADY_ADMITTED",
                                          "accept_invoked": False,
                                          "accepted": True})
        self.assertEqual(chat["data"]["run_id"], RUN_ID)
        self.assertEqual(chat["data"]["session_id"], SESSION_ID)
        self.assertFalse(chat["data"]["detach_fallback"])
        self.assertEqual(runner.chat_admission, "ALREADY_ADMITTED")
        self.assertEqual(
            runner.executed,
            [operation for operation, _kwargs
             in OPERATIONS[:OP_INDEX[OPERATOR_CLI_CHAT] + 1]])
        tails = script.tails()
        self.assertNotIn(["friend", "accept", "--request-id", REQUEST_ID], tails)
        self.assertIn(["friend", "request", "--bot-uuid", BOT_IDS["member"]], tails)
        self.assertIn(["friend", "list", "--json"], tails)
        self.assertIn(["chat", "--bot-uuid", BOT_IDS["member"], "--message",
                       "operator e2e chat", "--detach"], tails)

    def test_retained_eng_pair_substitutes_accept_and_lists_friends(self):
        script = CliScript()
        script.overrides = {"friend_request": ALREADY_FRIENDS}
        runner, script, results = self.run_ops(OPERATOR_CLI_FRIEND_LIST, script)
        request = results[OP_INDEX[OPERATOR_CLI_FRIEND_REQUEST_LIST]]
        accept = results[OP_INDEX[OPERATOR_CLI_FRIEND_ACCEPT_LIST]]
        listing = results[OP_INDEX[OPERATOR_CLI_FRIEND_LIST]]
        self.assertEqual(request["data"]["admission"], "ALREADY_ADMITTED")
        self.assertIsNone(request["data"]["request_id"])
        self.assertEqual(request["data"]["target_bot"], BOT_IDS["eng"])
        self.assertFalse(accept["data"]["accept_invoked"])
        self.assertTrue(listing["data"]["friend_list_verified"])
        tails = script.tails()
        self.assertNotIn(["friend", "accept", "--request-id", REQUEST_ID], tails)
        self.assertIn(["friend", "list"], tails)
        self.assertIn(["friend", "list", "--json"], tails)

    def test_already_friends_answer_without_live_membership_blocks(self):
        script = CliScript()
        script.overrides = {
            "friend_request": ALREADY_FRIENDS,
            "friend_list_json": (0, json.dumps(
                [{"bot_uuid": BOT_IDS["eng"]}]).encode(), b"")}
        with self.assertRaisesRegex(
                OperatorCliRunnerError, "not backed by the live friend list"):
            self.run_ops(OPERATOR_CLI_CHAT, script)

    def test_already_friends_answer_with_hidden_id_stays_fail_closed(self):
        script = CliScript()
        script.overrides = {"friend_request": (
            0, json.dumps({"success": True, "message": "Already friends",
                           "data": {"id": ""}}).encode(), b"")}
        with self.assertRaisesRegex(OperatorCliRunnerError, "friend request ID"):
            self.run_ops(OPERATOR_CLI_FRIEND_REQUEST_CHAT, script)

    def test_fresh_admission_keeps_the_strict_request_id_contract(self):
        script = CliScript()
        runner, script, results = self.run_ops(
            OPERATOR_CLI_FRIEND_ACCEPT_CHAT, script)
        request = results[OP_INDEX[OPERATOR_CLI_FRIEND_REQUEST_CHAT]]
        accept = results[OP_INDEX[OPERATOR_CLI_FRIEND_ACCEPT_CHAT]]
        self.assertEqual(request["data"]["admission"], "ACCEPTED")
        self.assertEqual(request["data"]["request_id"], REQUEST_ID)
        self.assertEqual(accept["data"], {"request_id": REQUEST_ID,
                                          "admission": "ACCEPTED",
                                          "accept_invoked": True,
                                          "accepted": True})
        self.assertIn(["friend", "accept", "--request-id", REQUEST_ID],
                      script.tails())
        self.assertEqual(runner.chat_admission, "ACCEPTED")


class RetainedAdmissionCaseTests(OperatorCliCaseTests):
    RETAINED_OUTPUTS = {
        OPERATOR_CLI_FRIEND_REQUEST_CHAT: {"request_id": None,
                                           "admission": "ALREADY_ADMITTED",
                                           "friends_verified": True,
                                           "target_bot": BOT_IDS["member"]},
        OPERATOR_CLI_FRIEND_ACCEPT_CHAT: {"request_id": None,
                                          "admission": "ALREADY_ADMITTED",
                                          "accept_invoked": False,
                                          "accepted": True},
        OPERATOR_CLI_FRIEND_REQUEST_LIST: {"request_id": None,
                                           "admission": "ALREADY_ADMITTED",
                                           "friends_verified": True,
                                           "target_bot": BOT_IDS["eng"]},
        OPERATOR_CLI_FRIEND_ACCEPT_LIST: {"request_id": None,
                                          "admission": "ALREADY_ADMITTED",
                                          "accept_invoked": False,
                                          "accepted": True},
    }

    def _execute(self, outputs=None):
        return self.run_scripted_case(preprod=True, outputs=outputs)

    def test_retained_admission_closes_thirteen_obligations_on_preprod(self):
        execution, _runner, _calls, _station, _reads = self._execute(
            outputs=self.RETAINED_OUTPUTS)
        self.assertEqual(execution.status, "PASS")
        chat = execution.steps[10]
        prerequisite = chat.data["prerequisite_admission"]
        self.assertIsNone(prerequisite["request_id"])
        self.assertEqual(prerequisite["admission"], "ALREADY_ADMITTED")
        self.assertTrue(prerequisite["friends_verified"])
        self.assertFalse(prerequisite["accept_invoked"])
        self.assertEqual(chat.data["run_id"], RUN_ID)
        self.assertEqual(len(chat.evidence["operator_cli_commands"]), 2)
        self.assertNotIn(["friend", "accept", "--request-id", REQUEST_ID],
                         chat.evidence["operator_cli_commands"])
        friends = execution.steps[12]
        listing = friends.data["prerequisite_admission"]
        self.assertIsNone(listing["request_id"])
        self.assertEqual(listing["admission"], "ALREADY_ADMITTED")
        self.assertEqual(len(friends.evidence["operator_cli_commands"]), 2)

    def test_fresh_preprod_admission_keeps_the_accept_command(self):
        execution, _runner, _calls, _station, _reads = self._execute()
        self.assertEqual(execution.status, "PASS")
        chat = execution.steps[10]
        self.assertEqual(chat.data["prerequisite_admission"]["admission"],
                         "ACCEPTED")
        self.assertEqual(len(chat.evidence["operator_cli_commands"]), 3)
        self.assertEqual(
            chat.evidence["operator_cli_commands"][1],
            ["friend", "accept", "--request-id", REQUEST_ID])

    def test_substitution_without_membership_projection_is_rejected(self):
        profile = dual_profile_value(preprod=True)
        runner = ScriptedRunner(outputs={
            OPERATOR_CLI_FRIEND_REQUEST_CHAT: {
                "request_id": None, "admission": "ALREADY_ADMITTED",
                "target_bot": BOT_IDS["member"]}})
        with patch.dict(os.environ, preprod_environment()):
            adapter = RemoteOperatorCliAdapter(profile, runner)
            with self.assertRaisesRegex(
                    RunStateError, "admission substitution is not verified"):
                adapter.operator_chat_with_pm()

    def test_case_failure_reports_the_unverified_substitution(self):
        execution, _runner, _calls, _station, _reads = self._execute(outputs={
            OPERATOR_CLI_FRIEND_REQUEST_CHAT: {
                "request_id": None, "admission": "ALREADY_ADMITTED",
                "target_bot": BOT_IDS["member"]}})
        self.assertEqual(execution.status, "BLOCKED")
        chat = execution.steps[10]
        self.assertEqual(chat.status, "NOT_RUN")
        self.assertEqual(chat.error_type, "RunStateError")


if __name__ == "__main__":
    unittest.main()
