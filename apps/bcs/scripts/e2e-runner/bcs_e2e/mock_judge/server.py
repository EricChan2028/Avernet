"""Owned-local Provider/Judge mock receiver (dual viewpoint: harness + BCS sink).

Incremental byte-faithful port of the community coverage mock
``apps/bcs/scripts/e2e-test/http_provider_judge_mock.py`` (frozen binding
sha256 ``fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4``
per ``webhook-migration-contract.json`` / ``judge-migration-contract.json``)
onto the owned-local executor face for the story #27
(``story_user_receives_group_event_webhooks``) and story #28
(``story_provider_callback_survives_slow_judge``) owned-local legs.

The receiver owns the complete community endpoint surface, one process, two
viewpoints (see ``common.sh`` ``mock_view_base_url`` / ``mock_sink_base_url``):

* harness view — the owned executor curls ``/health`` and ``/control/*``;
* BCS sink view — URLs registered into BCS that the BCS server calls back
  (``/provider/webhook``, ``/event-webhook``, ``/v1/chat/completions`` judge
  downlink, friend-auth-sync and work-order event ports).

Owned-local deltas from the community process (all recorded in
``port-inventory.md``):

* loopback-only binding (community ``--host`` accepted any interface);
* per-instance ``MockJudgeState`` instead of one module-global ``STATE``, so
  each Run owns a fresh receiver (``WEBHOOK.receiver-empty``,
  ``JUDGE.sandbox-empty``);
* machine-readable JSONL request journal carrying metadata only — method,
  path, status, client, duration — never headers or bodies, so recorded
  ``authorization`` values live solely in the in-memory control views;
* graceful SIGTERM/SIGINT shutdown for owned lifecycle teardown;
* zero credential literals. The community shell exported a literal Judge API
  key; this receiver performs no bearer validation (the community server did
  not either) and a future owner band injects any expected presentation via
  the ``BCS_E2E_MOCK_JUDGE_API_KEY`` environment name only.
"""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .page_bank import (
    CONTROL_ACK_BYTES,
    FRIEND_AUTH_SYNCED_BYTES,
    JUDGE_RELEASE_TIMEOUT_BYTES,
    JUDGE_UNEXPECTED_INVOCATION_BYTES,
    NOT_FOUND_BYTES,
    OUTCOMES_INVALID_BYTES,
    chat_completions_view,
    judge_status_view,
    requests_view,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
JUDGE_RELEASE_TIMEOUT_SECONDS = 30.0

GET_HEALTH = "/health"
GET_PROVIDER_REQUESTS = "/control/provider/requests"
GET_EVENT_WEBHOOK_REQUESTS = "/control/event-webhook/requests"
GET_FRIEND_AUTH_SYNC_REQUESTS = "/control/friend-auth-sync/requests"
GET_JUDGE_STATUS = "/control/judge/status"
POST_CONTROL_RESET = "/control/reset"
POST_PROVIDER_CLEAR = "/control/provider/clear"
POST_EVENT_WEBHOOK_CLEAR = "/control/event-webhook/clear"
POST_FRIEND_AUTH_SYNC_CLEAR = "/control/friend-auth-sync/clear"
POST_JUDGE_RELEASE = "/control/judge/release"
POST_JUDGE_OUTCOMES = "/control/judge/outcomes"
POST_PROVIDER_WEBHOOK = "/provider/webhook"
POST_EVENT_WEBHOOK = "/event-webhook"
POST_WORK_ORDER_EVENTS = "/api/v1/work-orders/events"
POST_BOT_FRIEND_AUTH_SYNC = "/api/internal/bot-friend-auth/sync"
POST_CHAT_COMPLETIONS = "/v1/chat/completions"

PRINCIPAL_HEADER = "x-avernet-principal"


class MockJudgeConfigError(RuntimeError):
    """Raised when the receiver is configured outside its loopback contract."""


class MockJudgeState:
    """Per-instance port of the community module-global ``MockState``.

    Record item shapes are byte-semantic ports of the community handler:

    * ``provider_requests``: ``{"authorization", "body"}``
    * ``event_webhook_requests``: ``{"authorization", "content_type", "body"}``
    * ``friend_auth_sync_requests`` / ``work_order_event_requests``:
      ``{"principal", "body"}``
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.provider_requests: list[dict[str, Any]] = []
        self.event_webhook_requests: list[dict[str, Any]] = []
        self.friend_auth_sync_requests: list[dict[str, Any]] = []
        self.work_order_event_requests: list[dict[str, Any]] = []
        self.judge_started = threading.Event()
        self.judge_release = threading.Event()
        self.judge_outcomes: list[str] | None = None

    def reset(self) -> None:
        with self.lock:
            self.provider_requests.clear()
            self.event_webhook_requests.clear()
            self.friend_auth_sync_requests.clear()
            self.work_order_event_requests.clear()
            self.judge_outcomes = None
        self.judge_started.clear()
        self.judge_release.clear()


class MockJudgeHTTPServer(ThreadingHTTPServer):
    """Carrier binding the ported state and the JSONL request journal."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type,
                 state: MockJudgeState, journal: Any) -> None:
        super().__init__(address, handler)
        self.mock_state = state
        self.request_journal = journal


class MockJudgeHandler(BaseHTTPRequestHandler):
    """Byte-faithful port of the community ``Handler`` routing table."""

    server: MockJudgeHTTPServer
    server_version = "BcsE2eHttpMock/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # The JSONL journal replaces the community human-readable stdout log.
        return

    def _read_json(self) -> Any:
        length = int(self.headers.get("content-length", "0"))
        payload = self.rfile.read(length) if length else b"{}"
        return json.loads(payload)

    def _send_bytes(self, status: int, body: bytes) -> None:
        started = getattr(self, "_request_started", None)
        duration_ms = None if started is None else round((time.monotonic() - started) * 1000, 3)
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.server.request_journal(
            method=self.command,
            path=self.path,
            status=status,
            client=self.client_address[0],
            duration_ms=duration_ms,
        )

    def _send_json(self, status: int, payload: Any) -> None:
        self._send_bytes(status, json.dumps(payload, separators=(",", ":")).encode())

    def _snapshot(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.server.mock_state.lock:
            return list(records)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._request_started = time.monotonic()
        state = self.server.mock_state
        if self.path == GET_HEALTH:
            self._send_bytes(200, b'{"ok":true}')
            return
        if self.path == GET_PROVIDER_REQUESTS:
            self._send_bytes(200, requests_view(self._snapshot(state.provider_requests)))
            return
        if self.path == GET_EVENT_WEBHOOK_REQUESTS:
            self._send_bytes(200, requests_view(self._snapshot(state.event_webhook_requests)))
            return
        if self.path == GET_FRIEND_AUTH_SYNC_REQUESTS:
            self._send_bytes(200, requests_view(self._snapshot(state.friend_auth_sync_requests)))
            return
        if self.path == GET_JUDGE_STATUS:
            self._send_bytes(
                200,
                judge_status_view(state.judge_started.is_set(), state.judge_release.is_set()),
            )
            return
        self._send_bytes(404, NOT_FOUND_BYTES)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._request_started = time.monotonic()
        state = self.server.mock_state
        if self.path == POST_CONTROL_RESET:
            state.reset()
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_PROVIDER_CLEAR:
            with state.lock:
                state.provider_requests.clear()
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_EVENT_WEBHOOK_CLEAR:
            with state.lock:
                state.event_webhook_requests.clear()
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_FRIEND_AUTH_SYNC_CLEAR:
            with state.lock:
                state.friend_auth_sync_requests.clear()
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_JUDGE_RELEASE:
            state.judge_release.set()
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_JUDGE_OUTCOMES:
            outcomes = self._read_json().get("outcomes")
            if not isinstance(outcomes, list) or not all(
                isinstance(item, str) and item for item in outcomes
            ):
                self._send_bytes(400, OUTCOMES_INVALID_BYTES)
                return
            with state.lock:
                state.judge_outcomes = list(outcomes)
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_PROVIDER_WEBHOOK:
            body = self._read_json()
            with state.lock:
                state.provider_requests.append(
                    {"authorization": self.headers.get("authorization"), "body": body},
                )
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_EVENT_WEBHOOK:
            body = self._read_json()
            with state.lock:
                state.event_webhook_requests.append(
                    {
                        "authorization": self.headers.get("authorization"),
                        "content_type": self.headers.get("content-type"),
                        "body": body,
                    },
                )
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_WORK_ORDER_EVENTS:
            # BCS friend-connect notification port: any 2xx means delivered.
            body = self._read_json()
            with state.lock:
                state.work_order_event_requests.append(
                    {
                        "principal": self.headers.get(PRINCIPAL_HEADER),
                        "body": body,
                    },
                )
            self._send_bytes(200, CONTROL_ACK_BYTES)
            return
        if self.path == POST_BOT_FRIEND_AUTH_SYNC:
            # TC friend grant sync point: BCS arrives with the inbound
            # principal re-addressed as iss=bcs. BCS treats {"synced": true}
            # as confirmation; anything else is reported as not applied.
            body = self._read_json()
            with state.lock:
                state.friend_auth_sync_requests.append(
                    {"principal": self.headers.get(PRINCIPAL_HEADER), "body": body},
                )
            self._send_bytes(200, FRIEND_AUTH_SYNCED_BYTES)
            return
        if self.path == POST_CHAT_COMPLETIONS:
            self._read_json()
            state.judge_started.set()
            with state.lock:
                scripted = state.judge_outcomes is not None
                outcome = state.judge_outcomes.pop(0) if state.judge_outcomes else None
            if scripted and outcome is None:
                self._send_bytes(500, JUDGE_UNEXPECTED_INVOCATION_BYTES)
                return
            if not scripted and not state.judge_release.wait(timeout=JUDGE_RELEASE_TIMEOUT_SECONDS):
                self._send_bytes(504, JUDGE_RELEASE_TIMEOUT_BYTES)
                return
            self._send_bytes(200, chat_completions_view(outcome if scripted else "approved"))
            return
        self._send_bytes(404, NOT_FOUND_BYTES)


class MockJudgeReceiver:
    """Loopback-owned lifecycle wrapper around the ported receiver."""

    def __init__(self, *, host: str = "127.0.0.1",
                 request_log_path: Path | None = None) -> None:
        if host not in LOOPBACK_HOSTS:
            raise MockJudgeConfigError(
                f"Provider/Judge mock binds loopback only; refused host={host!r} "
                "(BCS_E2E_MOCK loopback contract)",
            )
        self._host = host
        self.state = MockJudgeState()
        self.request_log_path = request_log_path
        self._server: MockJudgeHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._journal_lock = threading.Lock()
        self._journal_file: Any = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise MockJudgeConfigError("receiver is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def port(self) -> int:
        if self._server is None:
            raise MockJudgeConfigError("receiver is not started")
        return int(self._server.server_address[1])

    def start(self) -> None:
        if self._server is not None:
            raise MockJudgeConfigError("receiver already started")
        if self.request_log_path is not None:
            self.request_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._journal_file = open(self.request_log_path, "a", encoding="utf-8")
        self._server = MockJudgeHTTPServer((self._host, 0), MockJudgeHandler, self.state, self._journal)
        self._thread = threading.Thread(target=self._server.serve_forever, name="mock-judge-receiver")
        self._thread.start()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=10)
        self._server = None
        self._thread = None
        if self._journal_file is not None:
            self._journal_file.close()
            self._journal_file = None

    def _journal(self, *, method: str, path: str, status: int, client: str,
                 duration_ms: float | None) -> None:
        record = {
            "received_at": datetime.now(UTC).isoformat(),
            "remote_address": client,
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": duration_ms,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with self._journal_lock:
            if self._journal_file is not None:
                self._journal_file.write(line)
                self._journal_file.flush()
            else:
                print(line, end="", flush=True)

    def __enter__(self) -> "MockJudgeReceiver":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Owned-local Provider/Judge mock receiver (loopback only).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--request-log", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receiver = MockJudgeReceiver(host=args.host, request_log_path=args.request_log)
    except MockJudgeConfigError as error:
        print(str(error), flush=True)
        return 2
    stop = threading.Event()

    def _request_stop(*_signal_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    receiver.start()
    host, port = receiver._server.server_address[:2]
    ready_file = Path(args.ready_file)
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file.write_text(f"http://{host}:{port}\n", encoding="utf-8")
    try:
        stop.wait()
    finally:
        receiver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
