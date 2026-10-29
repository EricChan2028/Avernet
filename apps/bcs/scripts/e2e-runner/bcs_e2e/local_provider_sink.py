"""Bounded owned-loopback receiver for one Provider 2.0 delivery rejection.

This is a negative delivery fixture, not a Provider implementation. It receives
one exact ``chat.send`` for the armed principal, records sanitized correlation,
and deliberately returns HTTP 503 without starting an engine, acknowledging
success, forwarding, retrying, callback delivery, or synthetic Bot output.
"""
from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import threading
import time
import uuid

from .runtime import RuntimeContractError


_MAX_BODY_BYTES = 64 * 1024
_READ_DEADLINE_SECONDS = 1.0
_SHUTDOWN_TIMEOUT_SECONDS = 3.0
_REJECTION_BODY = b"REJECTED_NO_ENGINE\n"


class LocalProviderSinkError(RuntimeContractError):
    """Sanitized lifecycle or wire-contract failure from the local sink."""


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _identifier(value: object, label: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256
            or any(ord(char) < 0x21 or ord(char) > 0x7e for char in value)):
        raise LocalProviderSinkError(f"invalid {label}")
    return value


class OwnedLocalProviderSink:
    """Own one loopback HTTP listener and admit at most one Provider request."""

    def __init__(self, run_id: str):
        self.run_id = _identifier(run_id, "run ID")
        self._state = "NEW"
        self._server = None
        self._thread = None
        self._path = None
        self._provider_id = None
        self._bot_id = None
        self._bearer_token = None
        self._armed = False
        self._received = 0
        self._rejected = 0
        self._failed = False
        self._failure = None
        self._records = []
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._connections = set()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()

    def start(self):
        """Bind an ephemeral loopback port and start the owned receiver."""
        with self._lock:
            if self._state != "NEW":
                raise LocalProviderSinkError("provider sink lifecycle cannot be started again")
            self._path = "/provider/" + uuid.uuid4().hex
            sink = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def setup(self):
                    self.request.settimeout(_READ_DEADLINE_SECONDS)
                    with sink._lock:
                        sink._connections.add(self.request)
                    try:
                        super().setup()
                    except BaseException:
                        with sink._lock:
                            sink._connections.discard(self.request)
                        raise

                def finish(self):
                    try:
                        super().finish()
                    finally:
                        with sink._lock:
                            sink._connections.discard(self.request)

                def log_message(self, *_args):
                    pass

                def parse_request(self):
                    parsed = super().parse_request()
                    if not parsed:
                        sink._latch_failure("UNEXPECTED_REQUEST")
                        return False
                    if self.command != "POST":
                        sink._unexpected(self, 405, include_body=self.command != "HEAD")
                        return False
                    return True

                def do_POST(self):
                    sink._handle(self)

                def do_GET(self):
                    sink._unexpected(self, 405)

                def do_PUT(self):
                    sink._unexpected(self, 405)

                do_DELETE = do_PUT
                do_PATCH = do_PUT
                do_OPTIONS = do_PUT

                def do_HEAD(self):
                    sink._unexpected(self, 405, include_body=False)

            try:
                server = HTTPServer(("127.0.0.1", 0), Handler)
                server.timeout = 0.1
                thread = threading.Thread(
                    target=self._serve,
                    args=(server,),
                    name="e2e-local-provider-sink",
                    daemon=True,
                )
                self._server = server
                self._thread = thread
                self._state = "LIVE"
                thread.start()
            except BaseException:
                if self._server is not None:
                    self._server.server_close()
                self._server = None
                self._thread = None
                self._path = None
                self._state = "CLOSED"
                raise
        return self

    @property
    def url(self) -> str:
        """Return the private webhook URL only while its listener is live."""
        with self._lock:
            self._require_live_locked()
            return f"http://127.0.0.1:{self._server.server_port}{self._path}"

    def validate_live(self, run_id: str) -> None:
        """Confirm the supplied owner run still controls this live listener."""
        run_id = _identifier(run_id, "run ID")
        with self._lock:
            if not hmac.compare_digest(run_id, self.run_id):
                raise LocalProviderSinkError("provider sink run does not match")
            self._require_live_locked()

    def arm(self, provider_id: str, bot_id: str, bearer_token: str) -> None:
        """Pin the only admitted Provider, BCS Bot, and downlink credential."""
        provider_id = _identifier(provider_id, "Provider ID")
        bot_id = _identifier(bot_id, "Bot ID")
        bearer_token = _identifier(bearer_token, "Bearer token")
        with self._lock:
            self._require_live_locked()
            if self._armed or self._received or self._failed:
                raise LocalProviderSinkError("provider sink cannot be armed again")
            self._provider_id = provider_id
            self._bot_id = bot_id
            self._bearer_token = bearer_token
            self._armed = True

    def summary(self) -> dict:
        """Return safe evidence without URL capability, payload, or credential."""
        with self._lock:
            return {
                "state": self._state,
                "received": self._received,
                "rejected": self._rejected,
                "failed": self._failed,
                "engine": "NOT_STARTED",
                "records": [dict(record) for record in self._records],
                **({"failure": self._failure} if self._failure is not None else {}),
            }

    def close(self) -> None:
        """Close listener/accepted sockets, join the thread, and clear secrets."""
        with self._lock:
            if self._state == "CLOSED":
                self._clear_credentials_locked()
                return
            self._stop.set()
            connections = tuple(self._connections)
            server = self._server
            thread = self._thread
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(_SHUTDOWN_TIMEOUT_SECONDS)
        if server is not None:
            server.server_close()
        with self._lock:
            self._clear_credentials_locked()
            self._path = None
            if thread is not None and thread.is_alive():
                self._state = "CLOSE_UNCONFIRMED"
                raise LocalProviderSinkError("provider sink thread closure was not confirmed")
            self._connections.clear()
            self._server = None
            self._state = "CLOSED"

    def _serve(self, server: HTTPServer) -> None:
        try:
            while not self._stop.is_set():
                server.handle_request()
        except BaseException:
            self._latch_failure("LISTENER_FAILURE")

    def _require_live_locked(self) -> None:
        if (self._state != "LIVE" or self._stop.is_set() or self._server is None
                or self._thread is None or not self._thread.is_alive()
                or self._server.socket.fileno() < 0):
            raise LocalProviderSinkError("provider sink is not live")

    def _clear_credentials_locked(self) -> None:
        self._bearer_token = None
        self._provider_id = None
        self._bot_id = None
        self._armed = False

    def _latch_failure(self, code: str) -> None:
        with self._lock:
            self._failed = True
            if self._failure is None:
                self._failure = code
            if self._state != "CLOSED":
                self._state = "FAILED"
            self._stop.set()

    def _unexpected(self, handler, status: int, *, include_body: bool = True) -> None:
        self._latch_failure("UNEXPECTED_REQUEST")
        self._respond(handler, status, b"", include_body=include_body)

    def _handle(self, handler) -> None:
        try:
            with self._lock:
                if (self._state != "LIVE" or self._failed or not self._armed
                        or self._received >= 1):
                    raise LocalProviderSinkError("request is outside the admitted sink state")
                expected_path = self._path
                provider_id = self._provider_id
                bot_id = self._bot_id
                bearer_token = self._bearer_token
            if handler.path != expected_path:
                raise LocalProviderSinkError("request path does not match")
            if handler.headers.get_all("Transfer-Encoding"):
                raise LocalProviderSinkError("chunked request bodies are not admitted")
            lengths = handler.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise LocalProviderSinkError("request must declare one bounded body length")
            length = int(lengths[0])
            if length > _MAX_BODY_BYTES:
                self._latch_failure("BODY_TOO_LARGE")
                self._respond(handler, 413, b"")
                return
            if handler.headers.get_all("Authorization") != ["Bearer " + bearer_token]:
                raise LocalProviderSinkError("request principal does not match")
            if handler.headers.get("Content-Type", "").lower() != "application/json; charset=utf-8":
                raise LocalProviderSinkError("request content type does not match")
            if handler.headers.get("Accept") != "text/event-stream, application/json":
                raise LocalProviderSinkError("request transport negotiation does not match")
            if handler.headers.get("X-BCN-Protocol-Version") != "2.0":
                raise LocalProviderSinkError("request protocol version does not match")
            if handler.headers.get("X-BCN-Transport") != "sse":
                raise LocalProviderSinkError("request transport does not match")
            request_id = _identifier(
                handler.headers.get("X-BCN-Message-Id"), "Provider request ID"
            )
            timestamp = handler.headers.get("X-BCN-Timestamp", "")
            if not timestamp.isdigit() or int(timestamp) <= 0:
                raise LocalProviderSinkError("request timestamp is invalid")
            raw = self._read_body(handler, length)
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            record = self._validate_body(body, provider_id, bot_id, request_id, raw)
            with self._lock:
                if self._failed or self._received >= 1:
                    raise LocalProviderSinkError("request lost the single-delivery claim")
                self._received = 1
                self._rejected = 1
                self._records.append(record)
            self._respond(handler, 503, _REJECTION_BODY)
        except (LocalProviderSinkError, UnicodeError, ValueError, OSError, socket.timeout):
            self._latch_failure("UNEXPECTED_REQUEST")
            self._respond(handler, 400, b"")
        except BaseException:
            self._latch_failure("HANDLER_FAILURE")
            self._respond(handler, 500, b"")

    def _read_body(self, handler, length: int) -> bytes:
        deadline = time.monotonic() + _READ_DEADLINE_SECONDS
        value = bytearray()
        while len(value) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalProviderSinkError("request body read deadline exceeded")
            handler.request.settimeout(remaining)
            chunk = handler.rfile.read1(min(4096, length - len(value)))
            if not chunk:
                raise LocalProviderSinkError("request body ended early")
            value.extend(chunk)
        return bytes(value)

    def _validate_body(self, body, provider_id: str, bot_id: str,
                       request_id: str, raw: bytes) -> dict:
        if not isinstance(body, dict):
            raise LocalProviderSinkError("Provider request body is not an object")
        provider_run_id = _identifier(body.get("id"), "Provider run ID")
        target = body.get("to_bot")
        sender = body.get("from")
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (body.get("type") != "req" or body.get("method") != "chat.send"
                or not isinstance(body.get("session_id"), str) or not body["session_id"]
                or body.get("bcn_group_id") != body["session_id"]
                or not isinstance(target, dict)
                or target.get("provider_id") != provider_id
                or target.get("provider_bot_ref") != self.run_id
                or not isinstance(sender, dict) or sender.get("actor_id") != bot_id
                or not isinstance(message, dict) or message.get("role") != "user"
                or not isinstance(content, list) or not content
                or not all(isinstance(item, dict) for item in content)
                or type(body.get("timeout_ms")) is not int
                or body["timeout_ms"] <= 0):
            raise LocalProviderSinkError("Provider request body does not match")
        return {
            "status": "REJECTED_NO_ENGINE",
            "http_status": 503,
            "owner_run_id": self.run_id,
            "provider_run_id": provider_run_id,
            "provider_id": provider_id,
            "bot_id": bot_id,
            "provider_bot_ref": target["provider_bot_ref"],
            "request_id": request_id,
            "session_id": body["session_id"],
            "bcn_group_id": body["bcn_group_id"],
            "method": "chat.send",
            "protocol_version": "2.0",
            "body_sha256": hashlib.sha256(raw).hexdigest(),
            "engine": "NOT_STARTED",
        }

    @staticmethod
    def _respond(handler, status: int, payload: bytes, *, include_body: bool = True) -> None:
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.send_header("Content-Length", str(len(payload)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            if include_body and payload:
                handler.wfile.write(payload)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        handler.close_connection = True
