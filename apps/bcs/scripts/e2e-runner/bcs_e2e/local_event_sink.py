"""Owned loopback sink for one Run's Group Event Webhook story.

The sink exposes one unguessable POST target. It has no health, reset, query,
proxy, or control endpoint and never starts an engine. Events that can race the
Group-create response are held under a fixed quota until the caller binds the
returned Group and Subscription IDs.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import math
import socket
import threading
import time
import uuid

from .runtime import RuntimeContractError


_MAX_RECORDS = 32
_MAX_CONNECTIONS = 64
_MAX_BODY_BYTES = 64 * 1024
_READ_DEADLINE_SECONDS = 1.0
_MAX_WAIT_SECONDS = 5.0
_SHUTDOWN_TIMEOUT_SECONDS = 3.0
_ALLOWED_EVENT_TYPES = {
    "group.created",
    "session.created",
    "message.created",
    "event_subscription.test",
}


class LocalEventSinkError(RuntimeContractError):
    """Sanitized lifecycle, admission, or Event-contract failure."""


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
        raise LocalEventSinkError(f"invalid {label}")
    return value


def _nonempty_text(value: object, label: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LocalEventSinkError(f"invalid {label}")
    return value


def _require_keys(value: dict, required: set[str], label: str) -> None:
    if not required.issubset(value):
        raise LocalEventSinkError(f"incomplete {label}")


class OwnedLocalEventSink:
    """Own one per-Run loopback Event Webhook listener with bounded evidence."""

    def __init__(self, run_id: str):
        self.run_id = _identifier(run_id, "run ID")
        self._state = "NEW"
        self._server = None
        self._thread = None
        self._path = None
        self._armed = False
        self._group_id = None
        self._subscription_id = None
        self._received = 0
        self._connections_seen = 0
        self._failed = False
        self._failure = None
        self._pending = []
        self._records = []
        self._stream_sequences = {}
        self._event_ids = set()
        self._stop = threading.Event()
        self._condition = threading.Condition(threading.RLock())
        self._connections = set()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()

    def start(self):
        """Bind an ephemeral IPv4 loopback port and start the listener."""
        with self._condition:
            if self._state != "NEW":
                raise LocalEventSinkError("event sink lifecycle cannot be started again")
            self._path = "/events/" + uuid.uuid4().hex
            sink = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def setup(self):
                    self.request.settimeout(_READ_DEADLINE_SECONDS)
                    with sink._condition:
                        sink._connections.add(self.request)
                    try:
                        super().setup()
                    except BaseException:
                        with sink._condition:
                            sink._connections.discard(self.request)
                        raise

                def finish(self):
                    try:
                        super().finish()
                    finally:
                        with sink._condition:
                            sink._connections.discard(self.request)

                def log_message(self, *_args):
                    pass

                def parse_request(self):
                    parsed = super().parse_request()
                    if not parsed:
                        sink._latch_failure("MALFORMED_REQUEST")
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

            class BoundedServer(HTTPServer):
                def get_request(self):
                    connection, address = super().get_request()
                    with sink._condition:
                        sink._connections_seen += 1
                        if sink._connections_seen > _MAX_CONNECTIONS:
                            sink._latch_failure_locked("CONNECTION_QUOTA")
                            connection.close()
                            raise OSError("owned Event receiver connection budget exhausted")
                    return connection, address

            try:
                server = BoundedServer(("127.0.0.1", 0), Handler)
                server.timeout = 0.1
                thread = threading.Thread(
                    target=self._serve,
                    args=(server,),
                    name="e2e-local-event-sink",
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
        """Return the capability URL only while this listener is live."""
        with self._condition:
            self._require_live_locked()
            return f"http://127.0.0.1:{self._server.server_port}{self._path}"

    def validate_live(self, run_id: str) -> None:
        run_id = _identifier(run_id, "run ID")
        with self._condition:
            if not hmac.compare_digest(run_id, self.run_id):
                raise LocalEventSinkError("event sink run does not match")
            self._require_live_locked()

    def arm_create_intent(self, run_id: str) -> None:
        """Admit the one imminent Group-create flow owned by ``run_id``."""
        self.validate_live(run_id)
        with self._condition:
            if self._armed or self._received or self._failed:
                raise LocalEventSinkError("event sink create intent cannot be armed again")
            self._armed = True

    def bind_group(self, group_id: str, subscription_id: str) -> None:
        """Bind buffered and future deliveries to exact create-response IDs."""
        group_id = _identifier(group_id, "Group ID")
        subscription_id = _identifier(subscription_id, "Subscription ID")
        with self._condition:
            self._require_live_locked()
            if not self._armed or self._group_id is not None or self._failed:
                raise LocalEventSinkError("event sink is not awaiting one Group binding")
            try:
                validated = [
                    self._bind_record(record, group_id, subscription_id)
                    for record in self._pending
                ]
                stream_sequences = {}
                event_ids = set()
                for record in validated:
                    self._admit_order(record, stream_sequences, event_ids)
            except LocalEventSinkError:
                self._latch_failure_locked("BINDING_MISMATCH")
                raise
            self._group_id = group_id
            self._subscription_id = subscription_id
            self._records.extend(validated)
            self._pending.clear()
            self._stream_sequences = stream_sequences
            self._event_ids = event_ids
            self._condition.notify_all()

    def wait_for(self, event_type: str, group_id: str, deadline: float, *, subject_id: str | None = None) -> dict:
        """Wait for one bound receipt; ``deadline`` is monotonic and at most 5s away."""
        if event_type not in _ALLOWED_EVENT_TYPES:
            raise LocalEventSinkError("unsupported Event type")
        group_id = _identifier(group_id, "Group ID")
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise LocalEventSinkError("invalid wait deadline")
        if subject_id is not None:
            subject_id = _identifier(subject_id, "expected Event subject")
        now = time.monotonic()
        if not math.isfinite(deadline) or deadline <= now or deadline - now > _MAX_WAIT_SECONDS:
            raise LocalEventSinkError("wait deadline must be within five seconds")
        with self._condition:
            while True:
                if self._failed or self._state != "LIVE":
                    raise LocalEventSinkError("event sink failed or closed")
                if self._group_id is None:
                    raise LocalEventSinkError("event sink Group is not bound")
                if not hmac.compare_digest(group_id, self._group_id):
                    raise LocalEventSinkError("wait Group does not match")
                match = next(
                    (record for record in self._records
                     if record["event_type"] == event_type
                     and (subject_id is None or record["subject_id"] == subject_id)),
                    None,
                )
                if match is not None:
                    return dict(match)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalEventSinkError("Event wait deadline exceeded")
                self._condition.wait(remaining)

    def snapshot(self) -> dict:
        """Return finite, payload-free evidence suitable for a story assertion."""
        with self._condition:
            return {
                "state": self._state,
                "armed": self._armed,
                "bound": self._group_id is not None,
                "received": self._received,
                "validated": len(self._records),
                "buffered": len(self._pending),
                "failed": self._failed,
                "records": [dict(record) for record in self._records],
                **({"failure": self._failure} if self._failure is not None else {}),
            }

    summary = snapshot

    def close(self) -> None:
        """Boundedly stop the listener and all accepted connections."""
        with self._condition:
            if self._state == "CLOSED":
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
        with self._condition:
            self._path = None
            self._connections.clear()
            self._server = None
            if thread is not None and thread.is_alive():
                self._state = "CLOSE_UNCONFIRMED"
                raise LocalEventSinkError("event sink thread closure was not confirmed")
            self._state = "CLOSED"
            self._condition.notify_all()

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
            raise LocalEventSinkError("event sink is not live")

    def _latch_failure_locked(self, code: str) -> None:
        self._failed = True
        if self._failure is None:
            self._failure = code
        if self._state != "CLOSED":
            self._state = "FAILED"
        self._stop.set()
        self._condition.notify_all()

    def _latch_failure(self, code: str) -> None:
        with self._condition:
            self._latch_failure_locked(code)

    def _unexpected(self, handler, status: int, *, include_body: bool = True) -> None:
        self._latch_failure("UNEXPECTED_REQUEST")
        self._respond(handler, status, include_body=include_body)

    def _handle(self, handler) -> None:
        try:
            with self._condition:
                if self._state != "LIVE" or self._failed or not self._armed:
                    raise LocalEventSinkError("request is outside the admitted sink state")
                expected_path = self._path
            if handler.path != expected_path:
                raise LocalEventSinkError("request path does not match")
            names = [name.lower() for name, _ in handler.headers.raw_items()]
            if len(names) != len(set(names)):
                raise LocalEventSinkError("duplicate request header")
            if handler.headers.get_all("Transfer-Encoding"):
                raise LocalEventSinkError("transfer encoding is not admitted")
            if handler.headers.get_all("Authorization"):
                raise LocalEventSinkError("Authorization is not part of the Event Webhook contract")
            lengths = handler.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise LocalEventSinkError("request must declare one bounded body length")
            length = int(lengths[0])
            if length > _MAX_BODY_BYTES:
                self._latch_failure("BODY_TOO_LARGE")
                self._respond(handler, 413)
                return
            if handler.headers.get("Content-Type", "").lower() != "application/json; charset=utf-8":
                raise LocalEventSinkError("request content type does not match")
            raw = self._read_body(handler, length)
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            record = self._validate_envelope(body)
            record["authorization"] = None
            with self._condition:
                if self._failed or self._received >= _MAX_RECORDS:
                    raise LocalEventSinkError("record quota exceeded")
                self._received += 1
                if self._group_id is None:
                    self._pending.append(record)
                else:
                    bound = self._bind_record(record, self._group_id, self._subscription_id)
                    self._admit_order(bound, self._stream_sequences, self._event_ids)
                    self._records.append(bound)
                    self._condition.notify_all()
            self._respond(handler, 204)
        except (LocalEventSinkError, UnicodeError, ValueError, OSError, socket.timeout):
            self._latch_failure("UNEXPECTED_REQUEST")
            self._respond(handler, 400)
        except BaseException:
            self._latch_failure("HANDLER_FAILURE")
            self._respond(handler, 500)

    def _read_body(self, handler, length: int) -> bytes:
        deadline = time.monotonic() + _READ_DEADLINE_SECONDS
        value = bytearray()
        while len(value) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalEventSinkError("request body read deadline exceeded")
            handler.request.settimeout(remaining)
            chunk = handler.rfile.read1(min(4096, length - len(value)))
            if not chunk:
                raise LocalEventSinkError("request body ended early")
            value.extend(chunk)
        return bytes(value)

    def _validate_envelope(self, body: object) -> dict:
        if not isinstance(body, dict):
            raise LocalEventSinkError("Event body is not an object")
        _require_keys(body, {
            "spec_version", "event_id", "event_type", "schema_version", "source",
            "occurred_at", "recorded_at", "subject", "scope", "stream", "data",
        }, "Event envelope")
        event_id = _identifier(body["event_id"], "Event ID")
        event_type = body["event_type"]
        if (body["spec_version"] != "1.0" or body["schema_version"] != "1.0"
                or body["source"] != "bcs" or event_type not in _ALLOWED_EVENT_TYPES):
            raise LocalEventSinkError("Event envelope version or type does not match")
        _nonempty_text(body["occurred_at"], "occurred_at", 128)
        _nonempty_text(body["recorded_at"], "recorded_at", 128)
        subject = body["subject"]
        scope = body["scope"]
        stream = body["stream"]
        data = body["data"]
        if not all(isinstance(item, dict) for item in (subject, scope, stream, data)):
            raise LocalEventSinkError("Event envelope object shape does not match")
        subject_type = _identifier(subject.get("type"), "subject type")
        subject_id = _identifier(subject.get("id"), "subject ID")
        group_id = _identifier(scope.get("group_id"), "scope Group ID")
        stream_key = _nonempty_text(stream.get("key"), "stream key", 512)
        sequence = stream.get("sequence")
        if type(sequence) is not int or sequence < 1:
            raise LocalEventSinkError("stream sequence is invalid")
        self._validate_event_shape(
            event_type, subject_type, subject_id, scope, stream_key, sequence, data
        )
        delivery_id = body.get("delivery_id")
        if delivery_id is not None:
            delivery_id = _identifier(delivery_id, "Delivery ID")
        return {
            "group_id": group_id,
            "event_type": event_type,
            "stream_key": stream_key,
            "sequence": sequence,
            "event_id": event_id,
            **({"delivery_id": delivery_id} if delivery_id is not None else {}),
            "_subject_id": subject_id,
        }

    def _validate_event_shape(self, event_type: str, subject_type: str,
                              subject_id: str, scope: dict, stream_key: str,
                              sequence: int, data: dict) -> None:
        group_id = scope["group_id"]
        if event_type == "group.created":
            if subject_type != "group" or subject_id != group_id or stream_key != f"group:{group_id}":
                raise LocalEventSinkError("group.created envelope does not match")
            _require_keys(data, {"group_kind", "strategy", "name", "status", "version"}, "group.created data")
            return
        if event_type in {"session.created", "message.created"}:
            session_id = _identifier(scope.get("session_id"), "scope Session ID")
            expected_subject = "session" if event_type == "session.created" else "message"
            if subject_type != expected_subject or stream_key != f"session:{session_id}":
                raise LocalEventSinkError(f"{event_type} envelope does not match")
            if event_type == "session.created":
                if subject_id != session_id:
                    raise LocalEventSinkError("session.created subject does not match scope")
                _require_keys(data, {"session_kind", "status", "initial"}, "session.created data")
                if type(data["initial"]) is not bool:
                    raise LocalEventSinkError("session.created initial flag is invalid")
            else:
                _require_keys(data, {"logical_message_id", "message_type", "sender", "content", "attachments"}, "message.created data")
                if data["message_type"] != "chat" or data["logical_message_id"] != subject_id:
                    raise LocalEventSinkError("message.created data does not match")
            return
        if (subject_type != "event_subscription" or sequence != 1
                or data != {"test": True}):
            raise LocalEventSinkError("Event Subscription test envelope does not match")

    def _bind_record(self, record: dict, group_id: str, subscription_id: str) -> dict:
        if not hmac.compare_digest(record["group_id"], group_id):
            raise LocalEventSinkError("Event Group does not match create response")
        if record["event_type"] == "event_subscription.test":
            if (not hmac.compare_digest(record["_subject_id"], subscription_id)
                    or record["stream_key"] != f"event-subscription-test:{subscription_id}"):
                raise LocalEventSinkError("Event Subscription test does not match binding")
        public = dict(record)
        public["subject_id"] = public.pop("_subject_id")
        return public

    @staticmethod
    def _admit_order(record: dict, streams: dict, event_ids: set) -> None:
        event_id = record["event_id"]
        if event_id in event_ids:
            raise LocalEventSinkError("duplicate Event ID")
        event_ids.add(event_id)
        key = record["stream_key"]
        previous = streams.get(key)
        if previous is not None and record["sequence"] <= previous:
            raise LocalEventSinkError("Event stream order is not strictly increasing")
        streams[key] = record["sequence"]

    @staticmethod
    def _respond(handler, status: int, *, include_body: bool = True) -> None:
        payload = b"" if status == 204 else b"REJECTED\n"
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
