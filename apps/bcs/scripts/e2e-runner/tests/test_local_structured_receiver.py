"""Focused protocol-v2 transport tests for the original Structured Story."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_bot_receiver import LocalBotReceiverError
from bcs_e2e.local_structured_receiver import OwnedLocalStructuredReceiver, _validate_frame


INSTRUCTION = "Draft and verify a concise release recommendation."


def frame(run_id="run-1", session_id="session-1", group_id="group-1", attempt=0):
    request_id = f"smnode-{run_id}-answer-{attempt}"
    prompt = f"[BCS Group Context]\n\n[消息内容]\n{INSTRUCTION}\nQuestion"
    return {"type": "req", "id": request_id, "method": "chat.send", "params": {
        "bcs_group_id": session_id, "deliver": True, "idempotency_key": request_id,
        "session_key": "group:session-", "channel": {"thread_id": group_id,
        "actor_id": "bcs_state_machine", "actor_name": "BCS State Machine"},
        "session_context": {"session_id": session_id, "recipient": "ceo",
        "recipient_role": "driver", "delivery_type": "send", "originator": "CEO(ceo)",
        "from_bot_id": "bcs_state_machine", "group_type": "state_machine",
        "message": INSTRUCTION},
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
        "tags": [], "attachments": []}}


class StructuredReceiverTests(unittest.TestCase):
    def test_exact_frame_is_transport_only_without_success_ack(self):
        result = _validate_frame(frame(), run_id="run-1", session_id="session-1",
                                 group_id="group-1", attempt=0,
                                 instruction=INSTRUCTION, target_bot_id="ceo")
        self.assertEqual(result["request_id"], "smnode-run-1-answer-0")
        self.assertFalse(result["success_ack_sent"])
        self.assertEqual(result["engine"], "NOT_STARTED")
        self.assertNotIn(INSTRUCTION, json.dumps(result))

    def test_request_session_group_recipient_and_instruction_are_exact(self):
        edits = [
            lambda value: value.update(id="smnode-foreign-answer-0"),
            lambda value: value.update(method="chat.inject"),
            lambda value: value["params"].update(bcs_group_id="group-1"),
            lambda value: value["params"].update(bcs_session_id="session-1"),
            lambda value: value["params"]["channel"].update(thread_id="session-1"),
            lambda value: value["params"]["session_context"].update(recipient="pm"),
            lambda value: value["params"]["session_context"].update(recipient_role="consultant"),
            lambda value: value["params"]["session_context"].update(session_id="foreign"),
            lambda value: value["params"]["message"].update(content=[{"type": "text", "text": "other"}]),
        ]
        for edit in edits:
            value = copy.deepcopy(frame())
            edit(value)
            with self.subTest(value=value), self.assertRaises(LocalBotReceiverError):
                _validate_frame(value, run_id="run-1", session_id="session-1",
                                group_id="group-1", attempt=0,
                                instruction=INSTRUCTION, target_bot_id="ceo")

    def test_receiver_accepts_at_most_two_distinct_runs_and_sends_no_ack(self):
        receiver = object.__new__(OwnedLocalStructuredReceiver)
        connection = Mock()
        connection.receive_frame.side_effect = [
            (1, json.dumps(frame("run-1", "session-1")).encode()),
            (1, json.dumps(frame("run-2", "session-2")).encode()),
        ]
        raw = SimpleNamespace(_state="OPEN", _connection=connection, bot_id="ceo",
                              _decode_frame=lambda data, _: json.loads(data), close=Mock(),
                              summary=lambda: {"state": "OPEN"})
        receiver.receiver = raw
        receiver.identities = SimpleNamespace(_require_roles=Mock())
        receiver.deliveries = []
        receiver.receive_run(run_id="run-1", session_id="session-1", group_id="group-1",
                             attempt=0, instruction=INSTRUCTION)
        receiver.receive_run(run_id="run-2", session_id="session-2", group_id="group-1",
                             attempt=0, instruction=INSTRUCTION)
        connection._send_frame.assert_not_called()
        with self.assertRaises(LocalBotReceiverError):
            receiver.receive_run(run_id="run-3", session_id="session-3", group_id="group-1",
                                 attempt=0, instruction=INSTRUCTION)
        raw.close.assert_called_once()

    def test_foreign_or_duplicate_request_is_not_discarded(self):
        receiver = object.__new__(OwnedLocalStructuredReceiver)
        connection = Mock()
        connection.receive_frame.return_value = (
            1, json.dumps(frame("foreign", "session-1")).encode()
        )
        raw = SimpleNamespace(_state="OPEN", _connection=connection, bot_id="ceo",
                              _decode_frame=lambda data, _: json.loads(data), close=Mock())
        receiver.receiver = raw
        receiver.identities = SimpleNamespace(_require_roles=Mock())
        receiver.deliveries = []
        with self.assertRaises(LocalBotReceiverError):
            receiver.receive_run(run_id="run-1", session_id="session-1", group_id="group-1",
                                 attempt=0, instruction=INSTRUCTION)
        self.assertEqual(connection.receive_frame.call_count, 1)
        self.assertEqual(receiver.deliveries, [])
        raw.close.assert_called_once()
