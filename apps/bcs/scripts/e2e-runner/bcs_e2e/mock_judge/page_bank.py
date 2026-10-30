"""Canned response pages for the owned-local Provider/Judge mock receiver.

Every byte literal below is ported from the community coverage mock
``apps/bcs/scripts/e2e-test/http_provider_judge_mock.py`` (binding sha256
``fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4`` frozen
in ``webhook-migration-contract.json`` and
``judge-migration-contract.json``). The community mock serialises with
``json.dumps(payload, separators=(",", ":"))``; fixed pages are stored as
exact bytes and the two dynamic views (recorded-request views, judge
decision envelope) reproduce the community key order and separator policy
so downstream ``json_path`` assertions in stories #27/#28 observe identical
payloads.

Naming: the community process is one server with two viewpoints (see
``common.sh`` ``mock_view_base_url`` / ``mock_sink_base_url``). "Views" here
are the harness-facing ``/control/*`` GET surfaces; "pages" are every other
canned response page the receiver serves.
"""

from __future__ import annotations

import json
from typing import Any

# --- Fixed pages (byte-exact ports of the community send_json payloads) ---

HEALTH_OK_BYTES = b'{"ok":true}'
CONTROL_ACK_BYTES = b'{"ok":true}'
NOT_FOUND_BYTES = b'{"error":"not_found"}'
OUTCOMES_INVALID_BYTES = b'{"error":"expected nonempty outcome strings"}'
JUDGE_UNEXPECTED_INVOCATION_BYTES = b'{"error":"unexpected_judge_invocation"}'
JUDGE_RELEASE_TIMEOUT_BYTES = b'{"error":"judge_release_timeout"}'
FRIEND_AUTH_SYNCED_BYTES = b'{"synced":true,"reason":"created","auth_id":1}'

# --- Judge decision page (faithful field order and default separators) ---

DECISION_REASON = "candidate satisfies the E2E criteria"
DECISION_CONFIDENCE = 0.99
DECISION_CHECKED_CRITERIA: list[Any] = []
DECISION_RETRY_INSTRUCTION = ""
APPROVED_OUTCOME = "approved"


def judge_content_view(outcome: str) -> str:
    """Render the Judge decision exactly as the community mock embeds it.

    The community handler builds ``decision`` as a dict with the field order
    ``outcome, reason, confidence, checked_criteria, retry_instruction`` and
    serialises it with *default* separators (spaces after ``:`` and ``,``),
    then nests that string into a compact-separator envelope. Both dumps
    policies are reproduced verbatim.
    """

    decision = {
        "outcome": outcome,
        "reason": DECISION_REASON,
        "confidence": DECISION_CONFIDENCE,
        "checked_criteria": list(DECISION_CHECKED_CRITERIA),
        "retry_instruction": DECISION_RETRY_INSTRUCTION,
    }
    return json.dumps(decision)


def chat_completions_view(outcome: str) -> bytes:
    """``/v1/chat/completions`` 200 page: OpenAI-style ``choices`` envelope."""

    content = judge_content_view(outcome)
    payload = {"choices": [{"message": {"content": content}}]}
    return json.dumps(payload, separators=(",", ":")).encode()


# --- Harness-facing record views (GET /control/*) ---


def requests_view(records: list[dict[str, Any]]) -> bytes:
    """Render ``{"requests": [...]}`` for the recorded-request control views.

    Snapshots are taken under the state lock by the caller; serialization
    uses the community compact separators with the wrapper key first, which
    is also the order the community ``json.dumps`` produces.
    """

    return json.dumps({"requests": list(records)}, separators=(",", ":")).encode()


def judge_status_view(started: bool, released: bool) -> bytes:
    """Render the ``/control/judge/status`` harness view."""

    return json.dumps(
        {"started": bool(started), "released": bool(released)},
        separators=(",", ":"),
    ).encode()
