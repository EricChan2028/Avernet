"""Bounded transport-only receiver for one verified owned-local Workspace Bot."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import time
import uuid
from urllib.parse import urlsplit

from .local_role_identity import OwnedLocalRoleIdentity
from .profile import identifier, unique_keys
from .ws_transport import WebSocketConnection, WebSocketTransportError, _read_headers


_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_TIMEOUT_SECONDS = 5
_MAX_UNRELATED_FRAMES = 16
_MAX_CONTENT_BLOCKS = 32
_MAX_TEXT_BYTES = 262_144


class _DeadlineReader:
    """Apply one absolute deadline while reusing the shared header parser."""

    def __init__(self, sock, deadline: float):
        self.sock = sock
        self.deadline = deadline

    def recv(self, size: int) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout()
        self.sock.settimeout(remaining)
        return self.sock.recv(size)


class LocalBotReceiverError(RuntimeError):
    """A sanitized failure from the owned-local Bot transport receiver."""


class OwnedLocalBotReceiver:
    """Open one existing Workspace Bot connection and observe one delivery.

    This receiver proves only that BCS wrote a matching request frame to the
    verified Bot WebSocket. It never runs an engine, emits Bot output, or sends
    a success response for the delivery request.
    """

    def __init__(self, identities: OwnedLocalRoleIdentity, role: str):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != "workspace"
                or not isinstance(role, str) or role not in identities.roles
                or role not in identities._verified):
            raise LocalBotReceiverError(
                "receiver requires one verified owned-local Workspace Bot role"
            )
        try:
            identities._require_bound(role)
            bot_id, token = identities._entries[role]
            origin = identities.runtime.inputs.origin
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                    or parsed.port is None or not 1024 <= parsed.port <= 65535
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                    or parsed.username is not None or parsed.password is not None):
                raise LocalBotReceiverError(
                    "receiver requires the exact owned loopback HTTP origin"
                )
            identities.fixtures._require_owned()
        except LocalBotReceiverError:
            raise
        except Exception:
            raise LocalBotReceiverError(
                "verified owned-local Bot binding is no longer usable"
            ) from None
        self.identities = identities
        self.role = role
        self.bot_id = identifier(bot_id, "receiver Bot ID")
        self._token = token
        self._host = "127.0.0.1"
        self._port = parsed.port
        self._connection: WebSocketConnection | None = None
        self._state = "NEW"
        self._connect_request_id: str | None = None

    def __enter__(self):
        if self._state != "NEW":
            raise LocalBotReceiverError("receiver lifecycle cannot be entered again")
        try:
            self.identities._require_bound(self.role)
            self.identities.fixtures._require_owned()
            self._open()
            self._state = "OPEN"
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_args):
        self.close()

    def summary(self) -> dict:
        return {
            "state": self._state,
            "role": self.role,
            "bot_id": self.bot_id,
            "endpoint": "/ws/bot",
            "protocol_version": 2,
            "delivery_semantics": "TRANSPORT_RECEIVED_ONLY",
            "success_ack_sent": False,
        }

    def close(self) -> None:
        connection = self._connection
        self._token = ""
        if connection is None:
            if self._state != "CLOSE_UNCONFIRMED":
                self._state = "CLOSED"
            return
        try:
            connection.close()
        except BaseException as error:
            try:
                connection.sock.close()
            except BaseException:
                self._state = "CLOSE_UNCONFIRMED"
                raise LocalBotReceiverError("owned Bot socket closure was not confirmed") from None
            self._connection = None
            self._state = "CLOSED"
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
        else:
            self._connection = None
            self._state = "CLOSED"

    def _open(self) -> None:
        deadline = time.monotonic() + _MAX_TIMEOUT_SECONDS
        raw = None
        connection = None
        try:
            remaining = self._remaining(deadline, "WebSocket handshake")
            raw = socket.create_connection((self._host, self._port), timeout=remaining)
            raw.settimeout(remaining)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host = f"{self._host}:{self._port}"
            request = (
                f"GET /ws/bot HTTP/1.1\r\nHost: {host}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii")
            raw.sendall(request)
            status, headers, initial = _read_headers(_DeadlineReader(raw, deadline))
            expected = base64.b64encode(
                hashlib.sha1((key + _GUID).encode("ascii")).digest()
            ).decode("ascii")
            if status != "HTTP/1.1 101 Switching Protocols":
                raise LocalBotReceiverError("WebSocket Upgrade did not return HTTP 101")
            if headers.get("upgrade", "").lower() != "websocket":
                raise LocalBotReceiverError("WebSocket Upgrade omitted the Upgrade header")
            connection_tokens = {
                item.strip().lower()
                for item in headers.get("connection", "").split(",")
            }
            if "upgrade" not in connection_tokens:
                raise LocalBotReceiverError("WebSocket Upgrade omitted the Connection header")
            if not hmac.compare_digest(headers.get("sec-websocket-accept", ""), expected):
                raise LocalBotReceiverError("WebSocket Upgrade returned an invalid accept value")
            connection = WebSocketConnection(
                raw, max_frame_bytes=_MAX_TEXT_BYTES, initial_buffer=initial
            )
            request_id = "e2e-local-bot-connect-" + uuid.uuid4().hex
            self._connect_request_id = request_id
            connection.send_text(json.dumps({
                "type": "req",
                "id": request_id,
                "method": "bot.connect",
                "params": {
                    "token": self._token,
                    "bot_id": self.bot_id,
                    "protocol_version": 2,
                },
            }, separators=(",", ":")))
            self._await_connect(connection, request_id, deadline)
            self._connection = connection
            connection = None
            raw = None
        except LocalBotReceiverError:
            raise
        except (OSError, UnicodeError, ValueError, WebSocketTransportError):
            raise LocalBotReceiverError("owned-local Bot WebSocket handshake failed") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    try:
                        connection.sock.close()
                    except BaseException:
                        pass
            elif raw is not None:
                try:
                    raw.close()
                except BaseException:
                    pass

    def _await_connect(self, connection, request_id: str, deadline: float) -> None:
        unrelated = 0
        frames = 0
        while True:
            self._remaining(deadline, "Bot connection")
            frames += 1
            if frames > 64:
                raise LocalBotReceiverError("Bot connection frame budget exceeded")
            opcode, payload = connection.receive_frame(deadline)
            if opcode == 0x9:
                connection._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise LocalBotReceiverError("WebSocket closed before bot.connect completed")
            if opcode != 0x1:
                raise LocalBotReceiverError("bot.connect response was not a text frame")
            frame = self._decode_frame(payload, "bot.connect response")
            if frame.get("type") == "req":
                raise LocalBotReceiverError(
                    "unexpected request frame arrived before bot.connect completed"
                )
            if frame.get("type") != "res" or frame.get("id") != request_id:
                unrelated += 1
                if unrelated > _MAX_UNRELATED_FRAMES:
                    raise LocalBotReceiverError(
                        "too many unrelated frames before bot.connect response"
                    )
                continue
            payload_value = frame.get("payload")
            if (frame.get("ok") is not True or not isinstance(payload_value, dict)
                    or payload_value.get("bot_uuid") != self.bot_id
                    or payload_value.get("is_new") is not False
                    or type(payload_value.get("protocol_version")) is not int
                    or payload_value.get("protocol_version") != 2):
                raise LocalBotReceiverError(
                    "bot.connect did not confirm the existing protocol-v2 Bot identity"
                )
            return

    def receive_delivery(self, *, group_id: str, text: str, method: str,
                         timeout_seconds: float = 5,
                         session_id: str | None = None) -> dict:
        """Receive and verify one BCS delivery without acknowledging execution."""
        try:
            if self._state != "OPEN" or self._connection is None:
                raise LocalBotReceiverError("receiver is not open")
            self.identities._require_bound(self.role)
            self.identities.fixtures._require_owned()
            group_id = identifier(group_id, "expected Group ID")
            if session_id is not None:
                session_id = identifier(session_id, "expected initial Session ID")
            if method not in ("chat.send", "chat.inject"):
                raise LocalBotReceiverError("delivery method must be chat.send or chat.inject")
            if (not isinstance(text, str) or not text
                    or len(text.encode("utf-8")) > _MAX_TEXT_BYTES):
                raise LocalBotReceiverError("expected delivery text is invalid or oversized")
            if (type(timeout_seconds) not in (int, float)
                    or isinstance(timeout_seconds, bool)
                    or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS):
                raise LocalBotReceiverError("delivery timeout must be within 5 seconds")
            deadline = time.monotonic() + timeout_seconds
            unrelated = 0
            frames = 0
            while True:
                self._remaining(deadline, "Bot delivery")
                frames += 1
                if frames > 64:
                    raise LocalBotReceiverError("Bot delivery frame budget exceeded")
                opcode, raw = self._connection.receive_frame(deadline)
                if opcode == 0x9:
                    self._connection._send_frame(0xA, raw)
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x8:
                    raise LocalBotReceiverError("WebSocket closed before the delivery arrived")
                if opcode != 0x1:
                    raise LocalBotReceiverError("delivery was not a text WebSocket frame")
                frame = self._decode_frame(raw, "delivery frame")
                if frame.get("type") != "req":
                    unrelated += 1
                    if unrelated > _MAX_UNRELATED_FRAMES:
                        raise LocalBotReceiverError("too many unrelated delivery frames")
                    continue
                if frame.get("method") != method:
                    raise LocalBotReceiverError("received an unexpected delivery method")
                return self._verify_delivery(
                    frame, group_id=group_id, session_id=session_id, text=text
                )
        except BaseException:
            self.close()
            raise

    def _verify_delivery(self, frame: dict, *, group_id: str,
                         session_id: str | None, text: str) -> dict:
        request_id = identifier(frame.get("id"), "delivery request ID")
        params = frame.get("params")
        if not isinstance(params, dict):
            raise LocalBotReceiverError("delivery params are missing or invalid")
        wire_group_id = params.get("bcs_group_id")
        allowed_wire_ids = {group_id}
        if session_id is not None:
            allowed_wire_ids.add(session_id)
        if wire_group_id not in allowed_wire_ids:
            raise LocalBotReceiverError("delivery belongs to a foreign Group or Session")
        message = params.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (not isinstance(content, list) or not 1 <= len(content) <= _MAX_CONTENT_BLOCKS
                or any(not isinstance(block, dict) for block in content)):
            raise LocalBotReceiverError("delivery message content is invalid or oversized")
        texts = []
        total = 0
        for block in content:
            if block.get("type") != "text" or not isinstance(block.get("text"), str):
                continue
            value = block["text"]
            total += len(value.encode("utf-8"))
            if total > _MAX_TEXT_BYTES:
                raise LocalBotReceiverError("delivery text content exceeds its byte budget")
            texts.append(value)
        combined = "\n".join(texts)
        if text not in combined:
            raise LocalBotReceiverError("delivery does not contain the expected literal text")
        channel = params.get("channel")
        thread_id = channel.get("thread_id") if isinstance(channel, dict) else None
        session_key = params.get("session_key")
        bcs_session_id = params.get("bcs_session_id")
        for value, label in ((session_key, "session_key"), (thread_id, "thread_id"),
                             (bcs_session_id, "bcs_session_id")):
            if value is not None and not isinstance(value, str):
                raise LocalBotReceiverError(f"delivery {label} is invalid")
        if (thread_id not in (None, group_id, session_id)
                or bcs_session_id not in (None, session_id)
                or not isinstance(session_key, str) or not 1 <= len(session_key) <= 256
                or any(ord(c) < 32 for c in session_key)):
            raise LocalBotReceiverError("delivery metadata has a foreign or invalid scope")
        return {
            "status": "TRANSPORT_RECEIVED_ONLY",
            "target_role": self.role,
            "target_bot_id": self.bot_id,
            "request_id": request_id,
            "method": frame["method"],
            "group_id": group_id,
            "wire_group_id": wire_group_id,
            "expected_session_id": session_id,
            "session_key": session_key,
            "bcs_session_id": bcs_session_id,
            "channel_thread_id": thread_id,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "success_ack_sent": False,
            "connection_state": "OPEN",
        }

    @staticmethod
    def _decode_frame(raw: bytes, label: str) -> dict:
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        except (UnicodeError, ValueError):
            raise LocalBotReceiverError(f"{label} is not valid JSON") from None
        if not isinstance(value, dict):
            raise LocalBotReceiverError(f"{label} is not an object")
        return value

    @staticmethod
    def _remaining(deadline: float, label: str) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocalBotReceiverError(f"{label} exceeded its 5 second budget")
        return remaining
