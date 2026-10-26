"""Owned-local fixture Bot streaming connection for chat delivery.

The owned runtime is provider-free: fixture Bots have no engine,
so a Bot is only effectively online while a live streaming
connection occupies the Bot WebSocket registry. This seam holds
exactly one such connection for one fixture Bot during the
parent story's chat step. It upgrades through the product
WebSocket endpoint, presents the Bot's stored session token on
the product bot.connect frame, absorbs unrelated server frames
while it is held, and releases the connection on exit so the
registry drops the Bot again. Correlation IDs are the only
values exposed; credentials never leave the process.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import threading
import time
import uuid

from .run_state import RunStateError
from .ws_transport import (
    WebSocketConnection,
    _GUID,
    _read_headers,
)

BOT_ENDPOINT = '/ws/bot'
PROTOCOL_VERSION = 2
BUDGET_SECONDS = 10


def require(value, message='owned-local Bot WebSocket contract differs'):
    if not value:
        raise RunStateError(message)


class OwnedLocalBotWebSocket:
    """One fixture Bot reconnection scoped to one story step."""

    def __init__(self, port: int, bot_id: str, token: str):
        require(type(port) is int and 1 <= port <= 65_535,
                'owned local Bot WebSocket requires a bounded port')
        require(type(bot_id) is str and bot_id
                and bot_id == bot_id.strip()
                and len(bot_id) <= 128,
                'owned local Bot WebSocket requires a fixture Bot UUID')
        require(type(token) is str and token
                and len(token) <= 4096,
                'owned local Bot WebSocket requires the stored fixture token')
        self.port = port
        self.bot_id = bot_id
        self.token = token
        self.endpoint = BOT_ENDPOINT
        self.request_id = 'e2e-bot-ws-'+uuid.uuid4().hex
        self.upgrade_request_id = 'e2e-bot-ws-upgrade-'+uuid.uuid4().hex
        self.reconnected = False
        self._connection: WebSocketConnection | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *_exc):
        self._release()

    def observation(self):
        """The frozen journal observes the correlation ID only."""
        require(self._connection is not None
                and self.reconnected,
                'the fixture Bot connection is not live')
        return {'kind': 'bot_ws', 'endpoint': self.endpoint,
                'request_id': self.request_id}

    def _connect(self):
        require(self._connection is None,
                'the fixture Bot connection cannot repeat')
        raw = None
        try:
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            request = (
                f'GET {BOT_ENDPOINT} HTTP/1.1\r\n'
                f'Host: 127.0.0.1:{self.port}\r\n'
                'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                f'X-Request-ID: {self.upgrade_request_id}\r\n'
                f'Sec-WebSocket-Key: {key}\r\n'
                'Sec-WebSocket-Version: 13\r\n\r\n'
            ).encode('ascii')
            raw = socket.create_connection(
                ('127.0.0.1', self.port), timeout=BUDGET_SECONDS)
            raw.settimeout(BUDGET_SECONDS)
            raw.sendall(request)
            status, headers, initial_buffer = _read_headers(raw)
            parts = status.split(' ', 2)
            if (len(parts) < 2 or parts[0] != 'HTTP/1.1'
                    or parts[1] != '101'):
                raise RunStateError(
                    'owned Bot WebSocket upgrade did not return HTTP 101')
            if headers.get('upgrade', '').lower() != 'websocket':
                raise RunStateError(
                    'owned Bot WebSocket upgrade omitted the Upgrade header')
            if 'upgrade' not in headers.get('connection', '').lower():
                raise RunStateError(
                    'owned Bot WebSocket upgrade omitted the Connection header')
            expected = base64.b64encode(hashlib.sha1(
                (key+_GUID).encode('ascii')).digest()).decode('ascii')
            if not hmac.compare_digest(
                    headers.get('sec-websocket-accept', ''), expected):
                raise RunStateError(
                    'owned Bot WebSocket upgrade returned an invalid accept')
            connection = WebSocketConnection(raw, initial_buffer=initial_buffer)
            raw = None
            frame = {'type': 'req', 'id': self.request_id,
                     'method': 'bot.connect',
                     'params': {'bot_id': self.bot_id,
                                'token': self.token,
                                'protocol_version': PROTOCOL_VERSION}}
            try:
                connection.send_text(
                    json.dumps(frame, separators=(',', ':')))
                self._await_connect(connection)
            except (OSError, UnicodeError, ValueError, RunStateError):
                connection.close()
                raise
            self._connection = connection
            self._start_reader()
        except (OSError, UnicodeError, ValueError) as error:
            self._close_socket(raw)
            raise RunStateError('owned Bot WebSocket connection failed: '
                                + type(error).__name__) from None

    def _await_connect(self, connection):
        deadline = time.monotonic() + BUDGET_SECONDS
        frames = 0
        while time.monotonic() < deadline:
            frames += 1
            if frames > 64:
                raise RunStateError(
                    'too many unrelated frames before the Bot connect response')
            opcode, payload = connection.receive_frame(deadline)
            if opcode == 0x9:
                connection._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise RunStateError(
                    'owned Bot WebSocket closed before the connect response')
            if opcode != 0x1:
                raise RunStateError(
                    'owned Bot connect response was not a text frame')
            try:
                frame = json.loads(payload.decode('utf-8'))
            except (UnicodeError, json.JSONDecodeError):
                raise RunStateError(
                    'owned Bot connect response was not valid JSON') from None
            if (not isinstance(frame, dict) or frame.get('type') != 'res'
                    or frame.get('id') != self.request_id):
                continue
            if frame.get('ok') is not True:
                # Server error payloads are arbitrary; the journal must
                # never adopt them.
                raise RunStateError('owned Bot connect request was rejected')
            result = frame.get('payload')
            if (not isinstance(result, dict)
                    or result.get('is_new') is not False
                    or result.get('bot_uuid') != self.bot_id
                    or result.get('token') != self.token
                    or result.get('protocol_version') != PROTOCOL_VERSION):
                raise RunStateError(
                    'owned Bot connect response does not rebind the fixture')
            self.reconnected = True
            return
        raise RunStateError('owned Bot connect response timed out')

    def _start_reader(self):
        connection = self._connection

        def _absorb():
            while not self._stop.is_set():
                try:
                    opcode, payload = connection.receive_frame(
                        deadline=time.monotonic()+BUDGET_SECONDS)
                except Exception:
                    return
                if opcode == 0x9:
                    try:
                        connection._send_frame(0xA, payload)
                    except Exception:
                        return
                elif opcode == 0x8:
                    return

        self._reader = threading.Thread(
            target=_absorb, name='owned-local-bot-ws-reader',
            daemon=True)
        self._reader.start()

    def _release(self):
        self._stop.set()
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=BUDGET_SECONDS)

    @staticmethod
    def _close_socket(raw):
        if raw is None:
            return
        try:
            raw.close()
        except OSError:
            pass
