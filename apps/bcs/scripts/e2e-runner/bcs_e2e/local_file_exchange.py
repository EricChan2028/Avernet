"""Finite loopback capture for the actual owned-local BCS file CLI.

The broker admits one declared file operation and forwards every admitted step
only to the verified owned BCS origin. It never fabricates product replies,
follows redirects, consults proxy environment variables, or persists request
bodies, credentials, upload URLs, or share material.
"""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import socket
import threading
import time
from urllib.parse import quote, urlsplit

from .correlation import record_response_id
from .profile import identifier, unique_keys


class LocalFileExchangeError(RuntimeError):
    pass


_OPERATIONS = {'upload', 'list', 'capabilities', 'download', 'share', 'delete'}
_DENIED_HEADERS = (
    'Cookie', 'Proxy-Authorization', 'X-BCS-Service-Key', 'X-BCS-Bot-Token',
    'X-Avernet-Principal', 'X-Authenticated-User', 'X-Forwarded-User',
    'X-Request-ID',
)
_HEX = re.compile(rb'^[0-9A-Fa-f]+$')


class LocalFileExchange:
    def __init__(self, case, event: dict, *, token: str, operation: str,
                 session_id: str, file_id: str = '', payload: bytes = b'',
                 filename: str = '', on_prepared=None):
        if operation not in _OPERATIONS:
            raise LocalFileExchangeError('unsupported local file operation')
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in token):
            raise LocalFileExchangeError('local file exchange requires one bounded Bot token')
        if not isinstance(payload, bytes) or len(payload) > 4096:
            raise LocalFileExchangeError('local file payload exceeds the finite contract')
        self.case, self.event, self.token = case, event, token
        self.operation, self.session_id, self.file_id = operation, session_id, file_id
        self.payload, self.filename, self.on_prepared = payload, filename, on_prepared
        identifier(session_id, 'local file Session ID')
        if operation in {'download', 'share', 'delete'}:
            identifier(file_id, 'local file ID')
        elif file_id:
            raise LocalFileExchangeError('file ID is not admitted for this operation')
        if operation == 'upload':
            if not isinstance(filename, str) or not filename or len(filename.encode('utf-8')) > 512:
                raise LocalFileExchangeError('upload requires one bounded filename')
            if any(ord(c) < 32 for c in filename):
                raise LocalFileExchangeError('upload filename contains control characters')
        elif filename or payload:
            raise LocalFileExchangeError('payload and filename are admitted only for upload')
        if on_prepared is not None and (operation != 'upload' or not callable(on_prepared)):
            raise LocalFileExchangeError('prepared callback is admitted only for upload')

        target = urlsplit(case.identities.origin)
        try:
            upstream_port = target.port
        except ValueError:
            upstream_port = None
        if (target.scheme != 'http' or target.hostname != '127.0.0.1'
                or not upstream_port or target.username is not None or target.password is not None
                or target.path or target.query or target.fragment
                or target.netloc != '127.0.0.1:' + str(upstream_port)):
            raise LocalFileExchangeError('file exchange requires the exact owned loopback BCS origin')
        self._upstream_port = upstream_port
        self._upstream_origin = 'http://127.0.0.1:' + str(upstream_port)
        self._session_path = quote(session_id, safe='')
        self._file_path = quote(file_id, safe='') if file_id else ''
        self.attempts = 0
        self.forwarded = 0
        self.response = None
        self.failure = None
        self._phase = 0
        self._terminal = False
        self._stop = threading.Event()
        self._dispatch_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._incoming = None
        self._upstream = None
        self._upstream_socket = None
        self.deadline = time.monotonic() + 12
        event.setdefault('wire', [])
        if not isinstance(event['wire'], list) or event['wire']:
            raise LocalFileExchangeError('file exchange requires a fresh wire record list')
        exchange = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                self.request.settimeout(.25)
                with exchange._io_lock:
                    exchange._incoming = self.request
                    if exchange._stop.is_set():
                        self.request.close()
                        raise LocalFileExchangeError('file exchange is stopped')
                super().setup()

            def log_message(self, *_args):
                pass

            def parse_request(self):
                parsed = super().parse_request()
                exchange.attempts += 1
                if not parsed:
                    exchange._fail(LocalFileExchangeError('malformed local file CLI request'))
                    return False
                return True

            def do_GET(self):
                self._exchange()

            def do_POST(self):
                self._exchange()

            def do_PUT(self):
                self._exchange()

            def do_DELETE(self):
                self._exchange()

            def do_HEAD(self):
                self._reject(405)

            def do_OPTIONS(self):
                self._reject(405)

            def _reject(self, status=502):
                exchange._fail(LocalFileExchangeError('unexpected or repeated local file CLI request'))
                try:
                    self.send_response(status)
                    self.send_header('Content-Length', '0')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                except (OSError, ValueError):
                    pass
                self.close_connection = True

            def _exchange(self):
                try:
                    phase = exchange._admit(self.command, self.path, self.headers)
                    raw, framing = exchange._read_request_body(self.rfile, self.headers)
                    exchange._validate_body(phase, raw)
                    status, response_headers, upstream_body, parsed = exchange._forward(
                        phase, self.command, self.path, raw, framing, self.headers)
                    reply = upstream_body
                    if phase == 'prepare' and status == 201:
                        reply = exchange._prepare_reply(parsed)
                    exchange._advance(phase, status)
                    self.send_response(status)
                    content_type = response_headers.get('content-type')
                    if content_type:
                        self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(reply)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    if reply:
                        self.wfile.write(reply)
                    self.close_connection = True
                except BaseException as error:
                    exchange._fail(error)
                    self._reject()

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = .1
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(
            target=self._serve, name='e2e-local-file-exchange', daemon=True)

    def _serve(self):
        try:
            while not self._stop.is_set():
                self.server.handle_request()
        except BaseException as error:
            self._fail(error)

    def __enter__(self):
        try:
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise
        return self

    def _require_running(self):
        if self._stop.is_set() or time.monotonic() >= self.deadline:
            raise LocalFileExchangeError('file exchange was cancelled or exceeded its absolute deadline')

    def _fail(self, error):
        if self.failure is None:
            self.failure = error

    def _expected(self):
        base = '/sessions/' + self._session_path + '/files'
        if self.operation == 'upload':
            if self._phase == 0:
                return 'prepare', 'POST', base
            prepared = quote(self.file_id, safe='')
            if self._phase == 1:
                return 'upload-content', 'PUT', base + '/' + prepared + '/content'
            if self._phase == 2:
                return 'complete', 'POST', base + '/' + prepared + '/complete'
            return 'done', '', ''
        if self.operation == 'list':
            return 'list', 'GET', base
        if self.operation == 'capabilities':
            return 'capabilities', 'GET', base + '/capabilities'
        suffix = '/' + self._file_path
        if self.operation == 'download':
            suffix += '/content'
        elif self.operation == 'share':
            suffix += '/share'
        return self.operation, {'download': 'GET', 'share': 'POST', 'delete': 'DELETE'}[self.operation], base + suffix

    def _admit(self, method, path, headers):
        self._require_running()
        phase, expected_method, expected_path = self._expected()
        if (self.failure is not None or self._terminal or method != expected_method
                or path != expected_path or '?' in path or '#' in path
                or headers.get_all('Authorization') != ['Bearer ' + self.token]
                or any(headers.get_all(name) for name in _DENIED_HEADERS)):
            raise LocalFileExchangeError('local file CLI request differs from the finite operation')
        return phase

    def _read_request_body(self, stream, headers):
        lengths = headers.get_all('Content-Length', [])
        transfers = headers.get_all('Transfer-Encoding', [])
        if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
            raise LocalFileExchangeError('ambiguous local file request framing')
        if transfers:
            if transfers != ['chunked']:
                raise LocalFileExchangeError('unsupported local file request transfer encoding')
            return self._read_chunked(stream), 'chunked'
        if lengths:
            if not lengths[0].isdigit():
                raise LocalFileExchangeError('invalid local file request content length')
            length = int(lengths[0])
            if length > 4096:
                raise LocalFileExchangeError('local file request body exceeds 4096 bytes')
            return self._read_exact(stream, length), 'content-length'
        return b'', 'none'

    def _read_exact(self, stream, length):
        data = bytearray()
        while len(data) < length:
            self._require_running()
            try:
                chunk = stream.read(min(4096, length - len(data)))
            except socket.timeout:
                continue
            if not chunk:
                raise LocalFileExchangeError('local file request body ended before its declared length')
            data.extend(chunk)
        self._require_running()
        return bytes(data)

    def _readline(self, stream, limit):
        line = bytearray()
        while len(line) <= limit:
            self._require_running()
            try:
                byte = stream.read(1)
            except socket.timeout:
                continue
            if not byte:
                raise LocalFileExchangeError('chunked local file request ended early')
            line.extend(byte)
            if line.endswith(b'\r\n'):
                return bytes(line)
        raise LocalFileExchangeError('chunked local file line exceeds its finite limit')

    def _read_chunked(self, stream):
        body = bytearray()
        chunks = 0
        while True:
            line = self._readline(stream, 128)
            token = line[:-2]
            if not token or b';' in token or _HEX.fullmatch(token) is None:
                raise LocalFileExchangeError('invalid chunk size or forbidden chunk extension')
            size = int(token, 16)
            if size == 0:
                if self._readline(stream, 2) != b'\r\n':
                    raise LocalFileExchangeError('chunked trailers are forbidden')
                return bytes(body)
            chunks += 1
            if chunks > 4096 or size > 4096 - len(body):
                raise LocalFileExchangeError('chunked local file body exceeds 4096 bytes')
            body.extend(self._read_exact(stream, size))
            if self._read_exact(stream, 2) != b'\r\n':
                raise LocalFileExchangeError('chunk data lacks its required terminator')

    def _json(self, raw):
        try:
            return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise LocalFileExchangeError('local file request is not strict JSON') from None

    def _validate_body(self, phase, raw):
        if phase == 'prepare':
            expected = {'file_name': self.filename, 'size': len(self.payload), 'mime_type': 'text/plain'}
            if not raw or self._json(raw) != expected:
                raise LocalFileExchangeError('upload prepare body differs from the admitted file')
        elif phase == 'upload-content':
            if raw != self.payload:
                raise LocalFileExchangeError('uploaded bytes differ from the admitted payload')
        elif phase == 'complete':
            if not raw or self._json(raw) != {}:
                raise LocalFileExchangeError('upload complete body must be the exact empty object')
        elif phase == 'share':
            if raw and self._json(raw) != {}:
                raise LocalFileExchangeError('share body must be absent or the exact empty object')
        elif raw:
            raise LocalFileExchangeError('this local file operation does not admit a request body')

    def _wire_record(self, phase, method, path, framing, raw):
        record = {
            'sequence': len(self.event['wire']) + 1,
            'phase': phase,
            'method': method,
            'path': path,
            'dispatch_status': 'INTENDED',
            'transport_only_original_framing': framing,
            'decoded_body_bytes': len(raw),
        }
        request_id = self._request_id(record)
        if request_id is not None:
            record['submitted_request_id'] = request_id
        self.event['wire'].append(record)
        self.case._active()
        self.case._save()
        return record

    def _request_id(self, record):
        base = self.event.get('submitted_request_id')
        if not isinstance(base, str) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,96}', base):
            return None
        return base + '-' + str(record['sequence'])

    def _forward(self, phase, method, path, raw, framing, incoming_headers):
        record = self._wire_record(phase, method, path, framing, raw)
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'}
        if raw:
            content_type = incoming_headers.get('Content-Type')
            if phase == 'upload-content':
                if content_type != 'text/plain':
                    raise LocalFileExchangeError('upload content type differs from the prepared text file')
                headers['Content-Type'] = 'text/plain'
            else:
                if content_type is None or content_type.split(';', 1)[0].strip().lower() != 'application/json':
                    raise LocalFileExchangeError('JSON local file request lacks application/json')
                headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(raw))
        request_id = self._request_id(record)
        if request_id is not None:
            headers['X-Request-ID'] = request_id
            record['submitted_request_id'] = request_id
        client_name = incoming_headers.get('X-BCS-Client')
        if client_name is not None and len(client_name) <= 128 and all(ord(c) >= 32 for c in client_name):
            headers['X-BCS-Client'] = client_name
        self.case._active()
        upstream = HTTPConnection('127.0.0.1', self._upstream_port, timeout=.25)
        with self._io_lock:
            self._upstream = upstream
        try:
            upstream.connect()
            with self._io_lock:
                self._upstream_socket = upstream.sock
            with self._dispatch_lock:
                self._require_running()
                self.case._active()
                self.forwarded += 1
                upstream.request(method, path, body=raw if raw else None, headers=headers)
            while True:
                self._require_running()
                try:
                    response = upstream.getresponse()
                    break
                except socket.timeout:
                    continue
            with response:
                status = response.status
                response_headers = {key.lower(): value for key, value in response.getheaders()}
                if 300 <= status < 400 or response_headers.get('location') is not None:
                    raise LocalFileExchangeError('local file exchange never follows or returns redirects')
                limit = 4096 if phase == 'download' else 65_536
                body = self._read_response(response, limit)
                record['http_status'] = status
                record_response_id(record, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=(self.token,))
        finally:
            upstream.close()
            with self._io_lock:
                self._upstream = None
                self._upstream_socket = None
        parsed = None
        if phase != 'download' and body:
            try:
                parsed = json.loads(body.decode('utf-8'), object_pairs_hook=unique_keys)
            except (UnicodeError, json.JSONDecodeError, ValueError):
                raise LocalFileExchangeError('upstream local file response is not strict JSON') from None
            if isinstance(parsed, dict):
                record_response_id(record, parsed.get('request_id'), source='ENVELOPE',
                                   sensitive_values=(self.token,))
        self.response = body if phase == 'download' else parsed
        record['dispatch_status'] = 'RESPONSE_RECEIVED'
        expected = {
            'prepare': {201}, 'upload-content': {202}, 'complete': {200},
            'list': {200}, 'capabilities': {200}, 'download': {200},
            'share': {201}, 'delete': {200, 204},
        }[phase]
        if status not in expected:
            record['dispatch_status'] = 'UPSTREAM_REJECTED'
            self._fail(LocalFileExchangeError('upstream local file operation returned an unexpected status'))
        if phase == 'delete' and status == 204 and body:
            raise LocalFileExchangeError('204 file delete response must be empty')
        self.case._active()
        self.case._save()
        return status, response_headers, body, parsed

    def _read_response(self, response, limit):
        body = bytearray()
        while len(body) <= limit:
            self._require_running()
            try:
                chunk = response.read(min(4096, limit + 1 - len(body)))
            except socket.timeout:
                continue
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > limit:
            raise LocalFileExchangeError('upstream local file response exceeds its byte budget')
        if response.length not in (None, 0):
            raise LocalFileExchangeError('upstream local file response ended before Content-Length')
        self._require_running()
        return bytes(body)

    def _prepare_reply(self, parsed):
        if not isinstance(parsed, dict):
            raise LocalFileExchangeError('upload prepare response is not an object')
        if parsed.get('mode') != 'single' or parsed.get('method') != 'PUT' or 'parts' in parsed or 'part_count' in parsed or 'part_size' in parsed:
            raise LocalFileExchangeError('multipart local CLI upload is outside the finite contract')
        prepared_id = identifier(parsed.get('file_id'), 'prepared local file ID')
        upload_url = parsed.get('upload_url')
        if not isinstance(upload_url, str):
            raise LocalFileExchangeError('upload prepare response lacks a single upload URL')
        target = urlsplit(upload_url)
        expected_path = ('/sessions/' + self._session_path + '/files/'
                         + quote(prepared_id, safe='') + '/content')
        try:
            port = target.port
        except ValueError:
            port = None
        if (target.scheme != 'http' or target.hostname != '127.0.0.1'
                or port != self._upstream_port or target.netloc != '127.0.0.1:' + str(self._upstream_port)
                or target.username is not None or target.password is not None
                or target.path != expected_path or target.query or target.fragment):
            raise LocalFileExchangeError('upload prepare target is not the exact owned BCS content route')
        if self.on_prepared is not None:
            self._require_running()
            self.case._active()
            self.on_prepared(prepared_id)
            self.case._active()
        self.file_id = prepared_id
        record = self.event['wire'][-1]
        record['response_resource_id'] = prepared_id
        record['transport_only_upload_url_rewrite'] = True
        reply = dict(parsed)
        reply['upload_url'] = self.origin + expected_path
        self.case._save()
        return json.dumps(reply, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

    def _advance(self, phase, status):
        if self.failure is not None:
            return
        if self.operation == 'upload':
            self._phase += 1
            if phase == 'complete':
                self._terminal = True
        else:
            self._terminal = True

    def __exit__(self, error_type, *_args):
        with self._dispatch_lock:
            self._stop.set()
            if error_type is not None:
                self.case.invalidated = True
        with self._io_lock:
            connections = (self._incoming, self._upstream_socket)
        for connection in connections:
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.thread.join(timeout=10)
        self.server.server_close()
        self.token = ''
        self.payload = b''
        if self.thread.is_alive():
            self.case.invalidated = True
            error = LocalFileExchangeError('owned local file exchange shutdown is unconfirmed')
            error.child_shutdown_status = 'UNCONFIRMED'
            raise error
