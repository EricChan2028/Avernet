"""Owned loopback Provider/Judge prerequisite for the slow-Judge E2E story.

This fixture simulates only the Provider 2.0 acknowledgement boundary and one
OpenAI-compatible Judge response; an optional scripted Judge outcome sequence
arms a fixed, finite number of Judge decisions consumed in order (one per
Judge request), which fixed-Loop runs need and the slow-Judge default omits.  It does not start an engine, call BCS, send
callbacks, or expose an HTTP control plane.  The owning process is the only
party that can arm Provider authentication or release the blocked Judge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import socket
from socketserver import ThreadingMixIn
import threading
import time
import uuid

from .runtime import RuntimeContractError


_MAX_BODY_BYTES = 64 * 1024
_READ_DEADLINE_SECONDS = 1.0
_MAX_CONNECTIONS = 16
_MAX_CONCURRENT_CONNECTIONS = 3
_MAX_PROVIDER_RECEIPTS = 3
_MAX_JUDGE_OUTCOMES = 8
_MAX_PROVIDER_RECEIPT_LIMIT = 16
_MAX_INJECT_RECEIPTS = 8
_MAX_PROVIDER_WAIT_SECONDS = 20.0
_MAX_JUDGE_START_WAIT_SECONDS = 5.0
_MAX_JUDGE_HOLD_SECONDS = 20.0
_CLOSE_TIMEOUT_SECONDS = 3.0
_PROVIDER_ACK = {"ok": True}
_JUDGE_DECISION = {
    "outcome": "approved",
    "reason": "candidate satisfies the E2E criteria",
    "confidence": 0.99,
    "checked_criteria": [],
    "retry_instruction": "",
}


class LocalJudgeSandboxError(RuntimeContractError):
    """Sanitized lifecycle, ownership, or wire-contract failure."""


@dataclass(frozen=True)
class ProviderReceipt:
    """Sanitized correlation for one exact Provider 2.0 ``chat.send``."""

    delivery_request_id: str
    transport_request_id: str
    provider_id: str
    provider_bot_ref: str
    bot_id: str
    session_id: str
    bcn_group_id: str
    method: str
    protocol_version: str
    sender_actor_id: str
    body_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProviderInjectReceipt:
    """Sanitized correlation for one exact Provider 2.0 ``chat.inject``."""

    delivery_request_id: str
    transport_request_id: str
    provider_id: str
    provider_bot_ref: str
    session_id: str
    bcn_group_id: str
    body_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class JudgeReceipt:
    """Sanitized evidence that the one admitted Judge request started."""

    model: str
    message_count: int
    response_format_type: str
    body_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _identifier(value: object, label: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(char) < 0x21 or ord(char) > 0x7e for char in value)):
        raise LocalJudgeSandboxError(f"invalid {label}")
    return value


def _deadline(value: object, label: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LocalJudgeSandboxError(f"invalid {label}")
    deadline = float(value)
    now = time.monotonic()
    if not math.isfinite(deadline) or deadline <= now or deadline - now > maximum:
        raise LocalJudgeSandboxError(f"{label} must be a future monotonic deadline within {maximum:g}s")
    return deadline


def _judge_outcomes(value: object) -> tuple[str, ...] | None:
    """Validate an optional scripted Judge outcome sequence."""
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise LocalJudgeSandboxError("scripted Judge outcomes must be a sequence of strings")
    if not 1 <= len(value) <= _MAX_JUDGE_OUTCOMES:
        raise LocalJudgeSandboxError(
            f"scripted Judge outcomes must hold 1..{_MAX_JUDGE_OUTCOMES} entries")
    outcomes = []
    for item in value:
        if (not isinstance(item, str) or not item or len(item) > 512
                or any(ord(character) < 0x20 or ord(character) > 0x7e
                       for character in item)):
            raise LocalJudgeSandboxError(
                "scripted Judge outcome must be a non-empty printable ASCII string")
        outcomes.append(item)
    return tuple(outcomes)


def _receipt_limit(value: object) -> int:
    """Validate the bounded Provider receipt claim for one sandbox."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= _MAX_PROVIDER_RECEIPT_LIMIT):
        raise LocalJudgeSandboxError(
            f"Provider receipt limit must be an integer 1..{_MAX_PROVIDER_RECEIPT_LIMIT}")
    return value


class _BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, address, handler, sandbox):
        self.sandbox = sandbox
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.sandbox._admit_connection(request):
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.sandbox._release_connection(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.sandbox._release_connection(request)


class OwnedLocalJudgeSandbox:
    """Own one finite Provider/Judge loopback listener for one local run."""

    def __init__(self, run_id: str, *,
                 judge_outcomes: object = None,
                 provider_receipt_limit: object = _MAX_PROVIDER_RECEIPTS):
        self.run_id = _identifier(run_id, "run ID")
        outcomes = _judge_outcomes(judge_outcomes)
        self._judge_admissions = 1 if outcomes is None else len(outcomes)
        # Legacy callers keep the fixed single decision; a scripted
        # sequence is consumed in order, exactly one decision per
        # admitted Judge request, so multi-iteration loops can be
        # driven without any shared control-plane reset.
        self._judge_decisions = None if outcomes is None else outcomes
        self._provider_receipt_limit = _receipt_limit(provider_receipt_limit)
        self._state = "NEW"
        self._server = None
        self._thread = None
        self._provider_path = None
        self._judge_path = None
        self._judge_api_key = None
        self._provider_id = None
        self._bot_id = None
        self._provider_token = None
        self._armed = False
        self._provider_receipts: dict[str, ProviderReceipt] = {}
        self._provider_order: list[str] = []
        self._inject_receipts: dict[str, ProviderInjectReceipt] = {}
        self._inject_order: list[str] = []
        # Per-request Judge state; index zero preserves the legacy
        # single-Judge behavior exactly.
        self._judge_receipts: list[JudgeReceipt] = []
        self._judge_release_events: list[threading.Event] = []
        self._judge_released_flags: list[bool] = []
        self._judge_finished_flags: list[bool] = []
        self._failed = False
        self._failure = None
        self._connection_total = 0
        self._active_connections: set[socket.socket] = set()
        self._slots = threading.BoundedSemaphore(_MAX_CONCURRENT_CONNECTIONS)
        self._close_event = threading.Event()
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()

    def start(self):
        """Bind an ephemeral loopback listener and create private capabilities."""
        with self._lock:
            if self._state != "NEW":
                raise LocalJudgeSandboxError("Judge sandbox lifecycle cannot be started again")
            self._provider_path = "/provider/" + uuid.uuid4().hex
            self._judge_path = "/judge/" + uuid.uuid4().hex
            self._judge_api_key = secrets.token_urlsafe(32)
            sandbox = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def setup(self):
                    self.request.settimeout(_READ_DEADLINE_SECONDS)
                    super().setup()

                def log_message(self, *_args):
                    pass

                def parse_request(self):
                    parsed = super().parse_request()
                    if not parsed:
                        sandbox._latch_failure("INVALID_HTTP_REQUEST")
                        return False
                    if self.command != "POST":
                        sandbox._reject(self, 405, "METHOD_NOT_ALLOWED", include_body=self.command != "HEAD")
                        return False
                    return True

                def do_POST(self):
                    sandbox._handle_post(self)

                def do_GET(self):
                    sandbox._reject(self, 405, "METHOD_NOT_ALLOWED")

                do_PUT = do_GET
                do_DELETE = do_GET
                do_PATCH = do_GET
                do_OPTIONS = do_GET
                do_TRACE = do_GET

                def do_HEAD(self):
                    sandbox._reject(self, 405, "METHOD_NOT_ALLOWED", include_body=False)

            try:
                server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, self)
                thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    name="e2e-local-judge-sandbox",
                    daemon=True,
                )
                self._server = server
                self._thread = thread
                self._state = "LIVE"
                thread.start()
            except BaseException:
                if self._server is not None:
                    self._server.server_close()
                self._clear_secrets_locked()
                self._server = None
                self._thread = None
                self._provider_path = None
                self._judge_path = None
                self._state = "CLOSED"
                raise
        return self

    @property
    def provider_url(self) -> str:
        with self._lock:
            self._require_live_locked()
            return f"http://127.0.0.1:{self._server.server_port}{self._provider_path}"

    @property
    def judge_base_url(self) -> str:
        """Base URL to configure; the OpenAI client appends ``/chat/completions``."""
        with self._lock:
            self._require_live_locked()
            return f"http://127.0.0.1:{self._server.server_port}{self._judge_path}"

    @property
    def judge_api_key(self) -> str:
        """Return the in-memory Judge credential only while this owner is live."""
        with self._lock:
            self._require_live_locked()
            return self._judge_api_key

    @property
    def judge_admissions(self) -> int:
        """Total Judge requests admitted for the scripted set (legacy: one)."""
        with self._lock:
            self._require_live_locked()
            return self._judge_admissions

    @property
    def judge_request_count(self) -> int:
        """Judge requests admitted so far."""
        with self._lock:
            self._require_live_locked()
            return len(self._judge_receipts)

    def validate_live(self, run_id: str) -> None:
        run_id = _identifier(run_id, "run ID")
        with self._lock:
            if not hmac.compare_digest(run_id, self.run_id):
                raise LocalJudgeSandboxError("Judge sandbox run does not match")
            self._require_live_locked()

    def arm_provider(self, provider_id: str, bot_id: str, bearer_token: str) -> None:
        """Pin the registered Provider, target Bot, and BCS downlink credential."""
        provider_id = _identifier(provider_id, "Provider ID")
        bot_id = _identifier(bot_id, "Bot ID")
        bearer_token = _identifier(bearer_token, "Provider Bearer token")
        with self._lock:
            self._require_live_locked()
            if self._armed or self._provider_receipts:
                raise LocalJudgeSandboxError("Provider sandbox cannot be armed again")
            self._provider_id = provider_id
            self._bot_id = bot_id
            self._provider_token = bearer_token
            self._armed = True

    def wait_provider_request(self, delivery_request_id: str, deadline: float) -> ProviderReceipt:
        """Wait for one exact ``body.id``; never substitute the latest request."""
        delivery_request_id = _identifier(delivery_request_id, "delivery request ID")
        deadline = _deadline(deadline, "Provider wait deadline", _MAX_PROVIDER_WAIT_SECONDS)
        with self._changed:
            while True:
                if self._failed:
                    raise LocalJudgeSandboxError(f"Judge sandbox failed: {self._failure}")
                receipt = self._provider_receipts.get(delivery_request_id)
                if receipt is not None:
                    return receipt
                if self._state != "LIVE" or self._close_event.is_set():
                    raise LocalJudgeSandboxError("Judge sandbox closed before Provider delivery")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalJudgeSandboxError("timed out waiting for exact Provider delivery")
                self._changed.wait(remaining)

    def wait_provider_delivery(self, delivery_request_id: str, deadline: float) -> ProviderReceipt:
        """Readable alias for :meth:`wait_provider_request`."""
        return self.wait_provider_request(delivery_request_id, deadline)

    def wait_judge_started(self, deadline: float, index: int = 0) -> JudgeReceipt:
        """Wait at most five seconds for the ``index``-th authenticated Judge request."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise LocalJudgeSandboxError("Judge start index must be a non-negative integer")
        deadline = _deadline(deadline, "Judge start deadline", _MAX_JUDGE_START_WAIT_SECONDS)
        with self._changed:
            if index >= self._judge_admissions:
                raise LocalJudgeSandboxError("Judge start index exceeds the admitted finite set")
            while True:
                if self._failed:
                    raise LocalJudgeSandboxError(f"Judge sandbox failed: {self._failure}")
                if index < len(self._judge_receipts):
                    return self._judge_receipts[index]
                if self._state != "LIVE" or self._close_event.is_set():
                    raise LocalJudgeSandboxError("Judge sandbox closed before Judge start")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalJudgeSandboxError("timed out waiting for Judge start")
                self._changed.wait(remaining)

    def release_judge(self, index: int = 0) -> None:
        """Release one blocked Judge request from this owning object."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise LocalJudgeSandboxError("Judge release index must be a non-negative integer")
        with self._changed:
            self._require_live_locked()
            if index >= len(self._judge_receipts):
                raise LocalJudgeSandboxError("Judge has not started")
            if self._judge_released_flags[index] or self._judge_finished_flags[index]:
                raise LocalJudgeSandboxError("Judge can be released only once")
            self._judge_released_flags[index] = True
            self._judge_release_events[index].set()
            self._changed.notify_all()

    def snapshot(self) -> dict:
        """Return finite sanitized evidence without URLs, payloads, or credentials."""
        with self._lock:
            first = self._judge_receipts[0] if self._judge_receipts else None
            return {
                "state": self._state,
                "failed": self._failed,
                "provider_receipts": [
                    self._provider_receipts[item].to_dict() for item in self._provider_order
                ],
                "provider_receipt_limit": self._provider_receipt_limit,
                "judge_started": first is not None,
                "judge_released": any(self._judge_released_flags),
                "judge_finished": any(self._judge_finished_flags),
                "judge_receipt": first.to_dict() if first is not None else None,
                "judge_admissions": self._judge_admissions,
                "judge_request_count": len(self._judge_receipts),
                "judge_outcomes": (
                    list(self._judge_decisions) if self._judge_decisions is not None else None
                ),
                "provider_inject_count": len(self._inject_receipts),
                "connection_total": self._connection_total,
                **({"failure": self._failure} if self._failure is not None else {}),
            }

    def close(self) -> None:
        """Unblock requests as errors, stop owned threads, and clear all secrets."""
        with self._changed:
            if self._state == "CLOSED":
                self._clear_secrets_locked()
                return
            self._close_event.set()
            self._changed.notify_all()
            server = self._server
            thread = self._thread
            connections = tuple(self._active_connections)
        if server is not None:
            server.shutdown()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if server is not None:
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(_CLOSE_TIMEOUT_SECONDS)
        with self._changed:
            self._clear_secrets_locked()
            self._provider_path = None
            self._judge_path = None
            self._server = None
            self._active_connections.clear()
            if thread is not None and thread.is_alive():
                self._state = "CLOSE_UNCONFIRMED"
                raise LocalJudgeSandboxError("Judge sandbox listener closure was not confirmed")
            self._state = "CLOSED"
            self._changed.notify_all()

    def _require_live_locked(self) -> None:
        if self._failed:
            raise LocalJudgeSandboxError(f"Judge sandbox failed: {self._failure}")
        if (self._state != "LIVE" or self._close_event.is_set() or self._server is None
                or self._thread is None or not self._thread.is_alive()
                or self._server.socket.fileno() < 0):
            raise LocalJudgeSandboxError("Judge sandbox is not live")

    def _clear_secrets_locked(self) -> None:
        self._judge_api_key = None
        self._provider_token = None
        self._provider_id = None
        self._bot_id = None
        self._armed = False

    def _admit_connection(self, connection: socket.socket) -> bool:
        with self._changed:
            self._connection_total += 1
            if self._connection_total > _MAX_CONNECTIONS or not self._slots.acquire(blocking=False):
                self._latch_failure_locked("CONNECTION_LIMIT_EXCEEDED")
                try:
                    connection.close()
                except OSError:
                    pass
                return False
            self._active_connections.add(connection)
            return True

    def _release_connection(self, connection: socket.socket) -> None:
        with self._changed:
            if connection in self._active_connections:
                self._active_connections.remove(connection)
                self._slots.release()
            self._changed.notify_all()

    def _latch_failure_locked(self, code: str) -> None:
        self._failed = True
        if self._failure is None:
            self._failure = code
        if self._state != "CLOSED":
            self._state = "FAILED"
        self._changed.notify_all()

    def _latch_failure(self, code: str) -> None:
        with self._changed:
            self._latch_failure_locked(code)

    def _reject(self, handler, status: int, code: str, *, include_body: bool = True) -> None:
        self._latch_failure(code)
        self._respond_json(handler, status, {"error": "request rejected"}, include_body=include_body)

    def _handle_post(self, handler) -> None:
        try:
            with self._lock:
                if self._state != "LIVE" or self._failed or self._close_event.is_set():
                    raise LocalJudgeSandboxError("request is outside the admitted sandbox state")
                provider_path = self._provider_path
                judge_path = self._judge_path + "/chat/completions"
            if handler.path == provider_path:
                self._handle_provider(handler)
            elif handler.path == judge_path:
                self._handle_judge(handler)
            else:
                raise LocalJudgeSandboxError("request path does not match an owned capability")
        except (LocalJudgeSandboxError, UnicodeError, ValueError, OSError, socket.timeout):
            self._latch_failure("INVALID_OR_FOREIGN_REQUEST")
            self._respond_json(handler, 400, {"error": "request rejected"})
        except BaseException:
            self._latch_failure("HANDLER_FAILURE")
            self._respond_json(handler, 500, {"error": "handler failure"})

    def _request_json(self, handler) -> tuple[object, bytes]:
        if handler.headers.get_all("Transfer-Encoding"):
            raise LocalJudgeSandboxError("chunked bodies are not admitted")
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise LocalJudgeSandboxError("request must declare one body length")
        length = int(lengths[0])
        if length <= 0 or length > _MAX_BODY_BYTES:
            raise LocalJudgeSandboxError("request body length is outside the finite bound")
        if handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise LocalJudgeSandboxError("request content type does not match")
        raw = self._read_body(handler, length)
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object), raw

    @staticmethod
    def _read_body(handler, length: int) -> bytes:
        deadline = time.monotonic() + _READ_DEADLINE_SECONDS
        value = bytearray()
        while len(value) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalJudgeSandboxError("request body read deadline exceeded")
            handler.request.settimeout(remaining)
            chunk = handler.rfile.read1(min(4096, length - len(value)))
            if not chunk:
                raise LocalJudgeSandboxError("request body ended early")
            value.extend(chunk)
        return bytes(value)

    def _handle_provider(self, handler) -> None:
        with self._lock:
            if not self._armed:
                raise LocalJudgeSandboxError("Provider request is outside the armed finite set")
            provider_id = self._provider_id
            bot_id = self._bot_id
            token = self._provider_token
        if handler.headers.get_all("Authorization") != ["Bearer " + token]:
            raise LocalJudgeSandboxError("Provider principal does not match")
        if handler.headers.get("X-BCN-Protocol-Version") != "2.0":
            raise LocalJudgeSandboxError("Provider protocol version does not match")
        negotiated_transport = handler.headers.get("X-BCN-Transport")
        if negotiated_transport not in ("sse", "callback"):
            raise LocalJudgeSandboxError("Provider transport does not match")
        transport_request_id = _identifier(
            handler.headers.get("X-BCN-Message-Id"), "transport request ID"
        )
        timestamp = handler.headers.get("X-BCN-Timestamp", "")
        if not timestamp.isdigit() or int(timestamp) <= 0:
            raise LocalJudgeSandboxError("Provider timestamp is invalid")
        body, raw = self._request_json(handler)
        method = body.get("method") if isinstance(body, dict) else None
        if method == "chat.send":
            if (negotiated_transport != "sse"
                    or handler.headers.get("Accept") != "text/event-stream, application/json"):
                raise LocalJudgeSandboxError("Provider transport negotiation does not match")
            self._admit_provider_delivery(
                self._provider_receipt(body, raw, provider_id, bot_id, transport_request_id))
        elif method == "chat.inject":
            # Session and group context is delivered to every non-human
            # participant; a Provider endpoint must accept the system
            # context frame as a separate finite capability.
            if (negotiated_transport != "callback"
                    or handler.headers.get("Accept") != "application/json"):
                raise LocalJudgeSandboxError(
                    "System context transport negotiation does not match")
            self._admit_provider_inject(
                self._provider_inject_receipt(body, raw, provider_id, bot_id,
                                               transport_request_id))
        else:
            raise LocalJudgeSandboxError("Provider request method is outside the owned capability set")
        self._respond_json(handler, 200, _PROVIDER_ACK)

    def _admit_provider_delivery(self, receipt: ProviderReceipt) -> None:
        with self._changed:
            if receipt.delivery_request_id in self._provider_receipts:
                raise LocalJudgeSandboxError("duplicate Provider delivery request ID")
            if self._failed or len(self._provider_receipts) >= self._provider_receipt_limit:
                raise LocalJudgeSandboxError("Provider receipt lost its finite claim")
            self._provider_receipts[receipt.delivery_request_id] = receipt
            self._provider_order.append(receipt.delivery_request_id)
            self._changed.notify_all()

    def _admit_provider_inject(self, receipt: ProviderInjectReceipt) -> None:
        with self._changed:
            if receipt.delivery_request_id in self._inject_receipts:
                raise LocalJudgeSandboxError("duplicate System context delivery request ID")
            if self._failed or len(self._inject_receipts) >= _MAX_INJECT_RECEIPTS:
                raise LocalJudgeSandboxError("System context receipt lost its finite claim")
            self._inject_receipts[receipt.delivery_request_id] = receipt
            self._inject_order.append(receipt.delivery_request_id)
            self._changed.notify_all()

    def _provider_inject_receipt(self, body, raw: bytes, provider_id: str, bot_id: str,
                                 transport_request_id: str) -> ProviderInjectReceipt:
        target = body.get("to_bot")
        sender = body.get("from")
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        session_id = body.get("session_id")
        group_id = body.get("bcn_group_id")
        sender_actor_id = sender.get("actor_id") if isinstance(sender, dict) else None
        if (body.get("type") != "req" or body.get("method") != "chat.inject"
                or not isinstance(session_id, str) or not session_id
                or not isinstance(group_id, str) or not group_id
                or not isinstance(target, dict)
                or target.get("provider_id") != provider_id
                or target.get("provider_bot_ref") != self.run_id
                or not isinstance(target.get("tags", []), list)
                or not isinstance(sender, dict) or sender.get("kind") != "bot"
                or not isinstance(sender.get("name"), str) or not sender["name"]
                or not isinstance(sender_actor_id, str) or not sender_actor_id
                or not isinstance(message, dict) or message.get("role") != "user"
                or not isinstance(content, list) or not content
                or not all(isinstance(item, dict) for item in content)
                or not any(item.get("type") == "text" and isinstance(item.get("text"), str)
                           for item in content)
                or type(body.get("timeout_ms")) is not int or body["timeout_ms"] <= 0):
            raise LocalJudgeSandboxError("System context request does not match Provider 2.0")
        return ProviderInjectReceipt(
            delivery_request_id=_identifier(body.get("id"), "delivery request ID"),
            transport_request_id=transport_request_id,
            provider_id=provider_id,
            provider_bot_ref=self.run_id,
            session_id=session_id,
            bcn_group_id=group_id,
            body_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _provider_receipt(self, body, raw: bytes, provider_id: str, bot_id: str,
                          transport_request_id: str) -> ProviderReceipt:
        if not isinstance(body, dict):
            raise LocalJudgeSandboxError("Provider request body is not an object")
        delivery_request_id = _identifier(body.get("id"), "delivery request ID")
        target = body.get("to_bot")
        sender = body.get("from")
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        session_id = body.get("session_id")
        group_id = body.get("bcn_group_id")
        sender_actor_id = sender.get("actor_id") if isinstance(sender, dict) else None
        if (body.get("type") != "req" or body.get("method") != "chat.send"
                or not isinstance(session_id, str) or not session_id
                or not isinstance(group_id, str) or not group_id
                or not isinstance(target, dict)
                or target.get("provider_id") != provider_id
                or target.get("provider_bot_ref") != self.run_id
                or not isinstance(target.get("tags", []), list)
                or not isinstance(sender, dict) or sender.get("kind") != "bot"
                or not isinstance(sender.get("name"), str) or not sender["name"]
                or not isinstance(sender_actor_id, str) or not sender_actor_id
                or not isinstance(message, dict) or message.get("role") != "user"
                or not isinstance(content, list) or not content
                or not all(isinstance(item, dict) for item in content)
                or not any(item.get("type") == "text" and isinstance(item.get("text"), str)
                           for item in content)
                or type(body.get("timeout_ms")) is not int or body["timeout_ms"] <= 0):
            raise LocalJudgeSandboxError("Provider request body does not match Provider 2.0")
        return ProviderReceipt(
            delivery_request_id=delivery_request_id,
            transport_request_id=transport_request_id,
            provider_id=provider_id,
            provider_bot_ref=self.run_id,
            bot_id=bot_id,
            session_id=session_id,
            bcn_group_id=group_id,
            method="chat.send",
            protocol_version="2.0",
            sender_actor_id=sender_actor_id,
            body_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _handle_judge(self, handler) -> None:
        with self._lock:
            key = self._judge_api_key
            if len(self._judge_receipts) >= self._judge_admissions:
                raise LocalJudgeSandboxError(
                    f"only {self._judge_admissions} Judge request(s) are admitted")
        if handler.headers.get_all("Authorization") != ["Bearer " + key]:
            raise LocalJudgeSandboxError("Judge principal does not match")
        body, raw = self._request_json(handler)
        receipt = self._validate_judge_body(body, raw)
        with self._changed:
            if len(self._judge_receipts) >= self._judge_admissions or self._failed:
                raise LocalJudgeSandboxError("Judge request lost its finite claim")
            index = len(self._judge_receipts)
            self._judge_receipts.append(receipt)
            self._judge_release_events.append(threading.Event())
            self._judge_released_flags.append(False)
            self._judge_finished_flags.append(False)
            self._changed.notify_all()
        hold_deadline = time.monotonic() + _MAX_JUDGE_HOLD_SECONDS
        release_event = self._judge_release_events[index]
        while True:
            if self._close_event.wait(0.05):
                self._respond_json(handler, 503, {"error": "sandbox_closed"})
                return
            if release_event.is_set():
                break
            if time.monotonic() >= hold_deadline:
                self._latch_failure("JUDGE_RELEASE_TIMEOUT")
                self._respond_json(handler, 504, {"error": "judge_release_timeout"})
                return
        with self._changed:
            if (self._close_event.is_set() or self._failed
                    or not self._judge_released_flags[index]):
                self._respond_json(handler, 503, {"error": "judge_not_approved"})
                return
            self._judge_finished_flags[index] = True
            self._changed.notify_all()
        if self._judge_decisions is None:
            content = json.dumps(_JUDGE_DECISION, separators=(",", ":"))
        else:
            # Keep the slow-Judge wire shape; substitute only the scripted
            # outcome so fixed-Loop iteration and exhaustion boundaries are
            # deterministic without a shared mock control plane.
            content = json.dumps({
                "outcome": self._judge_decisions[index],
                "reason": "scripted E2E Judge outcome",
                "confidence": 1.0,
                "checked_criteria": [],
                "retry_instruction": "",
            }, separators=(",", ":"))
        self._respond_json(handler, 200, {"choices": [{"message": {"content": content}}]})

    @staticmethod
    def _validate_judge_body(body, raw: bytes) -> JudgeReceipt:
        if not isinstance(body, dict):
            raise LocalJudgeSandboxError("Judge request body is not an object")
        model = body.get("model")
        messages = body.get("messages")
        response_format = body.get("response_format")
        response_format_type = (
            response_format.get("type") if isinstance(response_format, dict) else None
        )
        if (not isinstance(model, str) or not model
                or not isinstance(messages, list) or not messages
                or not all(isinstance(item, dict)
                           and item.get("role") in {"system", "user", "assistant"}
                           and isinstance(item.get("content"), (str, dict, list))
                           for item in messages)
                or body.get("stream") is not False
                or not isinstance(body.get("temperature"), (int, float))
                or isinstance(body.get("temperature"), bool)
                or type(body.get("max_tokens")) is not int or body["max_tokens"] <= 0
                or response_format_type != "json_schema"):
            raise LocalJudgeSandboxError("Judge request body does not match OpenAI-compatible wire")
        return JudgeReceipt(
            model=model,
            message_count=len(messages),
            response_format_type=response_format_type,
            body_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _respond_json(handler, status: int, value: object, *, include_body: bool = True) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            if include_body:
                handler.wfile.write(payload)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        handler.close_connection = True
