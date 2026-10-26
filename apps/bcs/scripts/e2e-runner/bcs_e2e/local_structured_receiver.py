"""Transport-only receiver for the two original Structured Story Runs."""
from __future__ import annotations

import hashlib
import time

from .local_bot_receiver import OwnedLocalBotReceiver, LocalBotReceiverError
from .profile import identifier


_MAX_DELIVERIES = 2
_MAX_FRAMES = 32
_MAX_INSTRUCTION_BYTES = 4096


def _validate_frame(frame: dict, *, run_id: str, session_id: str, group_id: str,
                    attempt: int, instruction: str, target_bot_id: str) -> dict:
    request_id = f"smnode-{run_id}-answer-{attempt}"
    params = frame.get("params") if isinstance(frame, dict) else None
    if (frame.get("type") != "req" or frame.get("id") != request_id
            or frame.get("method") != "chat.send" or not isinstance(params, dict)):
        raise LocalBotReceiverError("Structured delivery is not the exact admitted Node request")
    if (params.get("bcs_group_id") != session_id
            or params.get("bcs_session_id") is not None
            or params.get("deliver") is not True
            or params.get("idempotency_key") != request_id):
        raise LocalBotReceiverError("Structured protocol-v2 Session wire identity differs")
    channel = params.get("channel")
    context = params.get("session_context")
    message = params.get("message")
    if (not isinstance(channel, dict) or channel.get("thread_id") != group_id
            or channel.get("actor_id") != "bcs_state_machine"
            or not isinstance(context, dict) or context.get("session_id") != session_id
            or context.get("recipient") != target_bot_id
            or context.get("recipient_role") != "driver"
            or context.get("delivery_type") != "send"
            or context.get("from_bot_id") != "bcs_state_machine"
            or context.get("group_type") != "state_machine"
            or not isinstance(context.get("originator"), str)
            or not context["originator"]):
        raise LocalBotReceiverError("Structured recipient/driver/Session context differs")
    content = message.get("content") if isinstance(message, dict) else None
    if (not isinstance(content, list) or not content
            or any(not isinstance(block, dict) for block in content)):
        raise LocalBotReceiverError("Structured delivery prompt is missing")
    prompt = "\n".join(block["text"] for block in content
                       if block.get("type") == "text" and isinstance(block.get("text"), str))
    if instruction not in prompt or instruction not in context.get("message", ""):
        raise LocalBotReceiverError("Structured prompt omits the exact Node instruction")
    return {
        "status": "TRANSPORT_RECEIVED_ONLY",
        "request_id": request_id,
        "run_id": run_id,
        "session_id": session_id,
        "group_id": group_id,
        "node_id": "answer",
        "attempt": attempt,
        "target_role": "CEO",
        "target_bot_id": target_bot_id,
        "method": "chat.send",
        "instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
        "wire_group_id": session_id,
        "channel_thread_id": group_id,
        "success_ack_sent": False,
        "engine": "NOT_STARTED",
    }


class OwnedLocalStructuredReceiver:
    def __init__(self, identities):
        self.receiver = OwnedLocalBotReceiver(identities, "CEO")
        self.identities = identities
        self.deliveries: list[dict] = []
        self._entered = False

    def __enter__(self):
        if self._entered:
            raise LocalBotReceiverError("Structured receiver cannot be reopened")
        self._entered = True
        self.receiver.__enter__()
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        self.receiver.close()

    def summary(self) -> dict:
        return {
            **self.receiver.summary(),
            "scope": "ORIGINAL_STRUCTURED_STORY",
            "engine": "NOT_STARTED",
            "deliveries": [dict(row) for row in self.deliveries],
        }

    def receive_run(self, *, run_id: str, session_id: str, group_id: str,
                    attempt: int, instruction: str) -> dict:
        try:
            if (self.receiver._state != "OPEN" or self.receiver._connection is None
                    or len(self.deliveries) >= _MAX_DELIVERIES
                    or any(row["run_id"] == run_id for row in self.deliveries)):
                raise LocalBotReceiverError(
                    "Structured delivery is duplicate or outside the finite receiver lifecycle"
                )
            run_id = identifier(run_id, "Structured Run ID")
            session_id = identifier(session_id, "Structured Session ID")
            group_id = identifier(group_id, "Structured Group ID")
            if (type(attempt) is not int or attempt < 0
                    or not isinstance(instruction, str) or not instruction
                    or len(instruction.encode()) > _MAX_INSTRUCTION_BYTES):
                raise LocalBotReceiverError("Structured attempt or instruction is invalid")
            self.identities._require_roles(("CEO",))
            deadline = time.monotonic() + 5
            unrelated = 0
            for _ in range(_MAX_FRAMES):
                if time.monotonic() >= deadline:
                    raise LocalBotReceiverError("Structured delivery deadline exceeded")
                opcode, raw = self.receiver._connection.receive_frame(deadline)
                if opcode == 0x9:
                    self.receiver._connection._send_frame(0xA, raw)
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x8:
                    raise LocalBotReceiverError("Structured receiver closed before delivery")
                if opcode != 0x1:
                    raise LocalBotReceiverError("Structured delivery was not a text frame")
                frame = self.receiver._decode_frame(raw, "Structured delivery")
                if frame.get("type") != "req":
                    unrelated += 1
                    if unrelated > 8:
                        raise LocalBotReceiverError("too many unrelated Structured frames")
                    continue
                receipt = _validate_frame(
                    frame, run_id=run_id, session_id=session_id, group_id=group_id,
                    attempt=attempt, instruction=instruction,
                    target_bot_id=self.receiver.bot_id,
                )
                self.deliveries.append(receipt)
                return dict(receipt)
            raise LocalBotReceiverError("Structured delivery frame budget exceeded")
        except BaseException:
            self.close()
            raise
