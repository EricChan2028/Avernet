"""Dependency-free, bounded WebSocket Upgrade client for PRE-02."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import time
import uuid
from urllib.parse import quote, urlsplit

from .correlation import RequestFailure, record_response_id
from .transport import tls_context


_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketTransportError(RequestFailure):
    pass


def _read_headers(sock: socket.socket, limit: int = 16_384) -> tuple[str, dict[str, str], bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketTransportError("WebSocket Upgrade response was incomplete")
        data.extend(chunk)
        if len(data) > limit:
            raise WebSocketTransportError("WebSocket Upgrade headers exceed the safety limit")
    delimiter = b"\r\n\r\n"
    header_end = data.index(delimiter) + len(delimiter)
    lines = bytes(data[:header_end - len(delimiter)]).decode("iso-8859-1").split("\r\n")
    status = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator or not name.strip():
            raise WebSocketTransportError("WebSocket Upgrade response contains an invalid header")
        headers[name.strip().lower()] = value.strip()
    return status, headers, bytes(data[header_end:])


def _request_id(value: str | None, prefix: str) -> str:
    """Allow callers to persist actual correlation IDs before transport dispatch."""
    if value is None:
        return prefix + uuid.uuid4().hex
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or not value.isascii() or any(not (c.isalnum() or c in '-_') for c in value)):
        raise WebSocketTransportError("WebSocket request ID must be bounded ASCII identifier text")
    return value


class WebSocketConnection:
    def __init__(self, sock: socket.socket, *, max_frame_bytes: int = 1_048_576,
                 initial_buffer: bytes = b""):
        self.sock = sock
        self.closed = False
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray(initial_buffer)
        self.connect_request_id: str | None = None

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if opcode >= 0x8 and len(payload) > 125:
            raise WebSocketTransportError("outgoing WebSocket control frame is too large")
        if len(payload) < 126:
            length = bytes([0x80 | len(payload)])
        elif len(payload) < 65_536:
            length = b"\xfe" + struct.pack("!H", len(payload))
        elif len(payload) <= self.max_frame_bytes:
            length = b"\xff" + struct.pack("!Q", len(payload))
        else:
            raise WebSocketTransportError("outgoing WebSocket frame is too large")
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode]) + length + mask + masked)

    def send_text(self, value: str) -> None:
        self._send_frame(0x1, value.encode("utf-8"))

    def _recv_exact(self, size: int, deadline: float | None = None) -> bytes:
        data = bytearray()
        while len(data) < size:
            if self._buffer:
                take = min(size - len(data), len(self._buffer))
                data.extend(self._buffer[:take])
                del self._buffer[:take]
                continue
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WebSocketTransportError("WebSocket frame deadline exceeded")
                self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(size - len(data))
            except socket.timeout:
                raise WebSocketTransportError("WebSocket frame deadline exceeded") from None
            if not chunk:
                raise WebSocketTransportError("WebSocket closed before a complete frame arrived")
            data.extend(chunk)
        return bytes(data)

    def receive_frame(self, deadline: float | None = None) -> tuple[int, bytes]:
        first, second = self._recv_exact(2, deadline)
        if first & 0x70:
            raise WebSocketTransportError("WebSocket frame has reserved bits set")
        if not first & 0x80:
            raise WebSocketTransportError("fragmented WebSocket frames are not supported by this pilot")
        opcode = first & 0x0F
        if second & 0x80:
            raise WebSocketTransportError("server WebSocket frames must not be masked")
        if opcode not in {0x1, 0x2, 0x8, 0x9, 0xA}:
            raise WebSocketTransportError("WebSocket frame has an unsupported opcode")
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2, deadline))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8, deadline))[0]
        if opcode >= 0x8 and length > 125:
            raise WebSocketTransportError("WebSocket control frame is invalid")
        if length > self.max_frame_bytes:
            raise WebSocketTransportError("incoming WebSocket frame exceeds the safety limit")
        return opcode, self._recv_exact(length, deadline)

    def request_connect(self, group_id: str, session_id: str, *, request_id: str | None = None) -> dict:
        request_id = _request_id(request_id, "e2e-pre02-connect-")
        self.connect_request_id = request_id
        self.send_text(json.dumps({
            "type": "req", "id": request_id, "method": "connect",
            "params": {"group_id": group_id, "session_id": session_id},
        }, separators=(",", ":")))
        deadline = time.monotonic() + 10
        frame_count = 0
        while time.monotonic() < deadline:
            frame_count += 1
            if frame_count > 64:
                raise WebSocketTransportError("too many unrelated WebSocket frames before connect response")
            opcode, payload = self.receive_frame(deadline)
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise WebSocketTransportError("WebSocket closed before the connect response")
            if opcode != 0x1:
                raise WebSocketTransportError("WebSocket connect response was not a text frame")
            try:
                frame = json.loads(payload.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                raise WebSocketTransportError("WebSocket connect response was not valid JSON") from None
            if not isinstance(frame, dict) or frame.get("type") != "res" or frame.get("id") != request_id:
                continue
            if frame.get("ok") is not True:
                # Server error fields are arbitrary payload, not safe diagnostics.
                raise WebSocketTransportError("WebSocket connect request was rejected")
            result = frame.get("payload")
            if not isinstance(result, dict) or result.get("group_id") != group_id:
                raise WebSocketTransportError("WebSocket connect response has the wrong group binding")
            return {"request_id": request_id, "group_id": group_id, "session_id": session_id,
                    "view_actor_id": result.get("view_actor_id"),
                    "message_view_scope": result.get("message_view_scope")}
        raise WebSocketTransportError("timed out waiting for the WebSocket connect response")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._send_frame(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self.sock.close()


class SessionWebSocketClient:
    def __init__(self, origin: str, *, timeout_seconds: int = 10,
                 request_headers: dict[str, str] | None = None):
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or not origin.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in origin)):
            raise WebSocketTransportError("WebSocket origin must be an HTTP(S) origin")
        headers = {} if request_headers is None else request_headers
        if (not isinstance(headers, dict) or set(headers) - {'Cookie', 'Origin', 'Referer'}
                or any(not isinstance(v, str) or not v or not v.isascii()
                       or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in headers.values())
                or sum(len(k) + len(v) + 4 for k, v in headers.items()) > 16_384):
            raise WebSocketTransportError('invalid bounded WebSocket browser headers')
        # Credentials apply only to this fixed origin. Upgrade never follows a
        # redirect, resolves a returned target or falls back to another host.
        self._request_headers = dict(headers)
        self.scheme = "wss" if parsed.scheme == "https" else "ws"
        self.host = parsed.hostname
        self.port = parsed.port or (443 if self.scheme == "wss" else 80)
        self.timeout = timeout_seconds

    def connect(self, token: str, group_id: str, session_id: str, *,
                upgrade_request_id: str | None = None,
                connect_request_id: str | None = None) -> tuple[WebSocketConnection, dict]:
        if not isinstance(token, str) or not token:
            raise WebSocketTransportError("WebSocket connection token is missing")
        path = "/openapi/v1/collaboration/messages/ws?token=" + quote(token, safe="")
        host_header = self.host if self.port in {80, 443} else f"{self.host}:{self.port}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        upgrade_id = _request_id(upgrade_request_id, "e2e-ws-upgrade-")
        control_id = _request_id(connect_request_id, "e2e-pre02-connect-")
        context_headers = "".join(f"{name}: {value}\r\n" for name, value in self._request_headers.items())
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"X-Request-ID: {upgrade_id}\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{context_headers}\r\n"
        ).encode("ascii")
        started = time.monotonic()
        raw: socket.socket | None = None
        connection: WebSocketConnection | None = None
        handed_off = False
        record = {
            "operation": "session.ws.connect", "method": "WS", "protocol": "websocket",
            "path": "/openapi/v1/collaboration/messages/ws?token=[REDACTED]",
            "http_status": None, "submitted_request_id": None, "request_id_echoed": False,
            "connected": False, "phase": "connection", "connect_request_id": None,
        }
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
            raw.settimeout(self.timeout)
            if self.scheme == "wss":
                raw = tls_context().wrap_socket(raw, server_hostname=self.host)
            record.update(phase="upgrade", submitted_request_id=upgrade_id)
            raw.sendall(request)
            status, headers, initial_buffer = _read_headers(raw)
            status_parts = status.split(" ", 2)
            if (len(status_parts) >= 2 and status_parts[0] == "HTTP/1.1"
                    and len(status_parts[1]) == 3 and status_parts[1].isascii()
                    and status_parts[1].isdigit() and 100 <= int(status_parts[1]) <= 599):
                record["http_status"] = int(status_parts[1])
            # Retain only bounded correlation IDs, never arbitrary server headers.
            record["request_id_echoed"] = headers.get("x-request-id") == upgrade_id
            record_response_id(record, headers.get('x-request-id'), source='HEADER', sensitive_values=(token, *self._request_headers.values()))
            if len(status_parts) < 2 or status_parts[0] != "HTTP/1.1" or status_parts[1] != "101":
                raise WebSocketTransportError("WebSocket Upgrade did not return HTTP 101")
            expected = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
            if headers.get("upgrade", "").lower() != "websocket":
                raise WebSocketTransportError("WebSocket Upgrade omitted the Upgrade header")
            if "upgrade" not in headers.get("connection", "").lower():
                raise WebSocketTransportError("WebSocket Upgrade omitted the Connection header")
            if not hmac.compare_digest(headers.get("sec-websocket-accept", ""), expected):
                raise WebSocketTransportError("WebSocket Upgrade returned an invalid accept value")
            connection = WebSocketConnection(raw, initial_buffer=initial_buffer)
            record["phase"] = "connect"
            connected = connection.request_connect(group_id, session_id, request_id=control_id)
            record.update(connected=True, phase="connected", group_id=group_id, session_id=session_id,
                          connect_request_id=connected["request_id"])
            handed_off = True
            return connection, record
        except WebSocketTransportError as error:
            raise error.with_record(record)
        except (OSError, UnicodeError, ValueError) as error:
            raise WebSocketTransportError(
                f"WebSocket connection failed: {type(error).__name__}").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            if connection is not None:
                record["connect_request_id"] = connection.connect_request_id
            # Also release the held socket on interruption; never reconnect or
            # follow a rejected upgrade, and do not replace the primary error.
            if not handed_off:
                record['socket_close_status'] = 'NOT_OPENED' if raw is None else 'UNCONFIRMED'
                if raw is not None:
                    try:
                        raw.close()
                    except OSError:
                        pass
                    else:
                        record['socket_close_status'] = 'CLOSED'
