"""Read-only diagnostics from one held local BCS and one Case's request ledger.

The product's dedicated JSON sink has timezone-less timestamps. Only a runtime
explicitly started with TZ=UTC can admit this provider. Raw lines never leave it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from .ledger import Ledger
from .log_observer import BoundedLogObserver, LogObservationError, LogQuery
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import identifier, unique_keys


_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
_EVENTS = {'http.request.started', 'http.request.response_ready', 'http.request.cancelled'}


def _event(line: bytes) -> dict:
    try:
        value = json.loads(line.decode('utf-8'), object_pairs_hook=unique_keys)
        if (not isinstance(value, dict) or value.get('target') != 'bcs_http_access'
                or value.get('message') not in _EVENTS or value.get('level') not in {'INFO', 'WARN'}
                or not isinstance(value.get('request_id'), str) or _ID.fullmatch(value['request_id']) is None):
            raise ValueError
        timestamp = value.get('timestamp')
        if not isinstance(timestamp, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', timestamp) is None:
            raise ValueError
        epoch = int(datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())
        if epoch < 0 or not isinstance(value.get('route'), str) or not value['route']:
            raise ValueError
        event = value['message']
        if event != 'http.request.cancelled' and value.get('method') not in {'GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'}:
            raise ValueError
        if event != 'http.request.started':
            duration = value.get('duration_ms')
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError
            if event == 'http.request.response_ready':
                status = value.get('status')
                if type(status) is not int or not 100 <= status <= 599:
                    raise ValueError
                expected = 'upgraded' if status == 101 else 'success' if 200 <= status < 300 else 'http_error'
            else:
                expected = 'cancelled'
            if value.get('outcome') != expected:
                raise ValueError
        return {'environment': 'local', 'timestamp': epoch, 'request_id': value['request_id']}
    except (ValueError, TypeError, OverflowError):
        raise LogObservationError('owned access log record does not match the supported event contract') from None


class OwnedLocalLogClient:
    def __init__(self, runtime: OwnedBcsProcess, *, ledger_path: Path, profile_digest: str,
                 case_id: str, max_scan_bytes: int = 2_097_152, max_line_bytes: int = 65_536):
        if not isinstance(runtime, OwnedBcsProcess) or runtime.inputs is None:
            raise LogObservationError('local log observation requires a held runtime')
        identifier(case_id, 'log Case ID')
        if (type(max_scan_bytes) is not int or not 1024 <= max_scan_bytes <= 8 * 1024 * 1024
                or type(max_line_bytes) is not int or not 256 <= max_line_bytes <= 65_536):
            raise LogObservationError('local log scan budget is invalid')
        self.runtime, self.root, self.run_id = runtime, runtime.root, runtime.run_id
        self.path = self.root / 'logs' / 'e2e-access.jsonl'
        self.config_digest = file_sha256(runtime.inputs.config_path)
        self.ledger_path, self.profile_digest, self.case_id = ledger_path, profile_digest, case_id
        self.max_scan_bytes, self.max_line_bytes = max_scan_bytes, max_line_bytes
        self._snapshot: list[dict] | None = None
        self._truncated, self._size = False, 0
        self.scan_count = 0

    def _owned(self, deadline: float) -> None:
        runtime = self.runtime
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LogObservationError('local log observation deadline exceeded')
        if (runtime.state != 'RUNNING' or not runtime.ready or runtime.run_id != self.run_id
                or runtime.root != self.root or self.root.is_symlink()
                or runtime.inputs.root != self.root or runtime.inputs.environment.get('TZ') != 'UTC'
                or runtime.inputs.environment.get('SERVER_ENV') != 'local'
                or runtime.child is None or runtime.child.poll() is not None
                or not runtime._owns_listener(min(1, remaining))
                or runtime.inputs.config_path.is_symlink()
                or file_sha256(runtime.inputs.config_path) != self.config_digest
                or self.path.parent.is_symlink() or runtime.access_log_identity is None):
            raise LogObservationError('local log runtime or source binding changed')
        config = json.loads(runtime.inputs.config_path.read_text())
        outputs = config.get('logging', {}).get('outputs', [])
        expected = {'name': 'e2e-access', 'path': str(self.path.parent), 'file': self.path.name,
                    'level': 'info', 'rotation': 'daily', 'format': 'json',
                    'targets': ['bcs_http_access'], 'max_keep_days': 0}
        if outputs != [expected]:
            raise LogObservationError('local log sink is not the configured owned source')
        metadata = self.path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino) != runtime.access_log_identity
                or self._snapshot is not None and metadata.st_size < self._size):
            raise LogObservationError('local access log was replaced or truncated')

    def admit(self) -> None:
        self._owned(time.monotonic() + 2)
        with Ledger(self.ledger_path, self.run_id, self.profile_digest, read_only=True):
            pass

    def _scan(self, deadline: float) -> None:
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or (metadata.st_dev, metadata.st_ino) != self.runtime.access_log_identity):
                raise LogObservationError('local access log handle is not owned')
            size = metadata.st_size
            data = stream.read(min(size, self.max_scan_bytes))
        if len(data) != min(size, self.max_scan_bytes):
            raise LogObservationError('local access log changed during snapshot')
        records = []
        truncated = size > len(data) or bool(data and not data.endswith(b'\n'))
        for line in data.splitlines(keepends=True):
            if time.monotonic() >= deadline:
                raise LogObservationError('local log observation deadline exceeded')
            if not line.endswith(b'\n'):
                break
            if len(line) > self.max_line_bytes:
                raise LogObservationError('local access log line exceeds its budget')
            records.append(_event(line))
        self._snapshot, self._truncated, self._size = records, truncated, size
        self.scan_count += 1

    def query(self, request: LogQuery, *, timeout_seconds: float) -> dict:
        request.validate('local')
        if request.request_id is None:
            raise LogObservationError('owned access logs do not establish traceparent correlation')
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise LogObservationError('local log observation timeout is invalid')
        deadline = time.monotonic() + timeout_seconds
        with Ledger(self.ledger_path, self.run_id, self.profile_digest, read_only=True) as ledger:
            allowed = {row.get('response_request_id') or row['request_id'] for row in ledger.correlations()
                       if row['case_id'] == self.case_id and row['attempt'] == 1}
        if request.request_id not in allowed:
            raise LogObservationError('log selector is not in the current Case attempt ledger')
        self._owned(deadline)
        if self._snapshot is None:
            self._scan(deadline)
        rows = [row for row in self._snapshot if row['request_id'] == request.request_id
                and request.start_epoch <= row['timestamp'] <= request.end_epoch]
        self._owned(deadline)
        if time.monotonic() >= deadline:
            raise LogObservationError('local log observation deadline exceeded')
        return {'success': True, 'records': rows[:request.limit],
                'truncated': self._truncated or len(rows) > request.limit}


class OwnedLocalLogObserver(BoundedLogObserver):
    def __init__(self, runtime: OwnedBcsProcess, *, ledger_path: Path, profile_digest: str, case_id: str):
        client = OwnedLocalLogClient(runtime, ledger_path=ledger_path, profile_digest=profile_digest, case_id=case_id)
        super().__init__(client=client, environment='local', environment_binding_confirmed=True)

    def admit(self) -> dict:
        self.client.admit()
        return {**super().admit(), 'backend': 'owned-local-access-json'}
