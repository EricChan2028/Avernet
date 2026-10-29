"""Owned-local black-box Run lifecycle for the Nested fixed-loop collaboration Story.

The upstream story is a complete, self-contained child process against one
live owned BCS runtime: it validates two nested collaboration definitions
through the real product CLI, drives the two-level Bot loop (17/15/2) and
the three-level Human loop (31/21/10), proves terminal-callback replay
rejection, pending-node scheduling, iteration-context isolation, the Human
authentication boundary and machine-output equality, then restores every
owned scope. A second owned black-box child - the Provider/Judge HTTP mock -
runs beside the runtime to freeze the post-cleanup zero-traffic expectation:
the story performs real callouts during its window but must clear every
recorded mock surface in teardown. The owned runtime
creates and owns the state directory; the frozen binding and both black-box
child receipts land inside it, and nothing credential-bearing is ever
captured into evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .local_nested_fixed_loop_contract import (CASE_ID, MARKER_KEYS, MARKER_PATTERNS, PARENT,
                                         PROVIDER_JUDGE_MOCK, REQUIREMENTS, STATUS,
                                         STORY_SOURCE, SUITE, nested_fixed_loop_source_binding)
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import identifier
from .run_state import RunStateError, read, write
from .transport import NoRedirect

BINARY_KIND = 'bcs'
HUMAN_ID = '39464'
STORY_DEADLINE_SECONDS = 900
MOCK_READY_SECONDS = 60
MOCK_CONTROL_TIMEOUT_SECONDS = 5
MOCK_CONTROL_RETRY_SECONDS = 10
REDACTED_TOKENS = (b'bot_token', b'provider_admin_token', b'Bearer ')
STORY_LOG_NAME = 'nested-fixed-loop-story.log'
STORY_ERR_LOG_NAME = 'nested-fixed-loop-story.err.log'
MOCK_LOG_NAME = 'nested-fixed-loop-mock.log'
MOCK_ERR_LOG_NAME = 'nested-fixed-loop-mock.err.log'
MOCK_READY_FILE_NAME = 'nested-fixed-loop-mock-ready.txt'
BOUNDED_STREAM_BYTES = 8 * 1024 * 1024
ZERO_TRAFFIC_CONTROL_PATHS = ('/control/provider/requests', '/control/event-webhook/requests',
                              '/control/friend-auth-sync/requests')
JUDGE_STATUS_PATH = '/control/judge/status'


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_nested_fixed_loop_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the owned BCS server binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError('nested-fixed-loop BCS binary must be a regular frozen file')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError('nested-fixed-loop BCS binary must be executable')
    if _sha256(binary.read_bytes()) != binary_sha256:
        raise RunStateError('nested-fixed-loop BCS binary digest differs from its frozen declaration')
    return binary


def validate_nested_fixed_loop_cli_binary(binary: Path) -> Path:
    """Freeze the real product CLI before launching the story child."""
    candidate = Path(binary).absolute()
    if candidate.is_symlink() or not candidate.is_file():
        raise RunStateError('Nested fixed-loop product CLI must be a regular frozen file')
    if not os.access(candidate, os.X_OK):
        raise RunStateError('Nested fixed-loop product CLI must be executable')
    return candidate.resolve()


def _parse_story_marker(text: str) -> tuple[tuple[bool, ...], int]:
    """Return both strict marker states and the exact expected check count."""
    matches = tuple(pattern.search(text) is not None for pattern in MARKER_PATTERNS)
    return matches, sum(matches)


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass


def _launch_story(argv: list[str], cwd: Path, deadline: float) -> dict:
    """Test seam: run the story child in its own process group, bounded."""
    process = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    started = time.monotonic()
    try:
        out, err = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise RunStateError('nested-fixed-loop story exceeded its 900-second deadline') from None
    except BaseException:
        _terminate_process_group(process)
        raise
    return {'exit_code': process.returncode, 'timed_out': False,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': out, 'stderr': err, 'process': process}


def _start_mock_child(argv: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)


def _await_mock_ready(ready_path: Path, deadline_seconds: float,
                      process: subprocess.Popen) -> str:
    """Poll the frozen ready-file protocol; the mock writes its real origin."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RunStateError('nested-fixed-loop mock child exited before readiness')
        if ready_path.exists():
            origin = ready_path.read_text(encoding='utf-8').strip()
            if origin.startswith('http://') or origin.startswith('https://'):
                return origin
        time.sleep(0.1)
    _terminate_process_group(process)
    raise RunStateError('nested-fixed-loop mock child readiness timed out')


def _terminate_mock_child(process: subprocess.Popen) -> dict:
    started = time.monotonic()
    _terminate_process_group(process)
    try:
        out, err = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        out, err = b'', b''
    return {'exit_code': process.returncode, 'timed_out': False,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': out, 'stderr': err}


def _redaction_gate(*streams: bytes) -> bool:
    return all(token not in stream for stream in streams for token in REDACTED_TOKENS)


def _bounded_write(path: Path, data: bytes) -> None:
    if len(data) > BOUNDED_STREAM_BYTES:
        raise RunStateError('nested-fixed-loop child stream exceeded its bounded evidence limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _mock_open_get(origin: str, path: str, opener) -> dict:
    with opener.open(origin + path, timeout=MOCK_CONTROL_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RunStateError('nested-fixed-loop mock control plane rejected the probe')
        return json.loads(response.read().decode('utf-8'))


def _mock_control_counts(origin: str) -> dict:
    """Freeze zero-webhook evidence from the mock's control plane after the story."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + MOCK_CONTROL_RETRY_SECONDS
    failure: Exception | None = None
    while time.monotonic() < deadline:
        try:
            counts = {}
            for path in ZERO_TRAFFIC_CONTROL_PATHS:
                counts[path] = len(_mock_open_get(origin, path, opener)['requests'])
            judge = _mock_open_get(origin, JUDGE_STATUS_PATH, opener)
            return {'observed_webhook_requests': counts['/control/provider/requests'],
                    'observed_event_webhook_requests': counts['/control/event-webhook/requests'],
                    'observed_friend_auth_sync_requests': counts['/control/friend-auth-sync/requests'],
                    'judge_started': bool(judge.get('started')),
                    'judge_released': bool(judge.get('released'))}
        except (OSError, ValueError, KeyError, TypeError, RunStateError) as failure_error:
            failure = failure_error
            time.sleep(0.1)
    raise RunStateError('nested-fixed-loop mock control plane unreachable: ' + repr(failure))


def _mock_argv(ready_path: Path) -> list[str]:
    return [sys.executable, str(PROVIDER_JUDGE_MOCK), '--host', '127.0.0.1', '--port', '0',
            '--ready-file', str(ready_path)]


def _story_argv(base_url: str, mock_origin: str, cli_binary: Path,
                driver: str) -> list[str]:
    return [sys.executable, str(STORY_SOURCE), '--base-url', base_url,
            '--mock-url', mock_origin, '--cli', str(cli_binary), '--driver', driver,
            '--human', HUMAN_ID]


def local_nested_fixed_loop_run(*, binary: Path, binary_sha256: str,
                               cli_binary: Path, driver: str, state_dir: Path,
                               run_id: str, confirm_run_id: str, allow_local_processes: bool,
                               allow_fixture_write: bool, allow_write: bool) -> dict:
    """Run the story once beside the owned runtime and the frozen Provider mock."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Nested fixed-loop requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Nested fixed-loop Run ID')
    driver = identifier(driver, 'Nested fixed-loop driver Bot ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Nested fixed-loop requires a fresh state directory; existing evidence is retained')
    parent = state_dir.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RunStateError('Nested fixed-loop requires a non-symlink directory parent for its state')
    binary = validate_nested_fixed_loop_binary(Path(binary), binary_sha256)
    cli_binary = validate_nested_fixed_loop_cli_binary(Path(cli_binary))
    cli_binary_sha256 = file_sha256(cli_binary)
    source = executor_digest()
    original = nested_fixed_loop_source_binding()
    legacy = current_manifest()['legacy_inventory']
    if len(legacy) != 34 or PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Nested fixed-loop slice must stay bound to its unmapped parent in the frozen legacy inventory')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
               'run_id': run_id, 'executor_digest': source, 'binary_kind': BINARY_KIND,
               'bcs_binary_sha256': binary_sha256, 'auto_build': False,
               'cli_binary': str(cli_binary), 'cli_binary_sha256': cli_binary_sha256,
               'driver_bot_id': driver,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'environment': original['environment'],
               'human_id': HUMAN_ID, 'mock_child': original['mock_child'],
               'runtime_dir': str(state_dir.resolve())}
    error = None
    runtime = None
    root = None
    story = None
    story_argv = None
    mock_process = None
    mock_argv = None
    mock_origin = None
    mock_started = None
    counts = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id,
                                 allow_ambient_local_identity=False) as owned_runtime:
                runtime = owned_runtime
                root = owned_runtime.root
                write(root / 'nested-fixed-loop-binding.json', binding)
                ready_path = root / MOCK_READY_FILE_NAME
                mock_argv = _mock_argv(ready_path)
                mock_started = time.monotonic()
                mock_process = _start_mock_child(mock_argv, cwd=root)
                try:
                    mock_origin = _await_mock_ready(ready_path, MOCK_READY_SECONDS, mock_process)
                except BaseException:
                    _terminate_process_group(mock_process)
                    raise
                story_argv = _story_argv(owned_runtime.inputs.origin, mock_origin,
                                         cli_binary, driver)
                story = _launch_story(story_argv, cwd=root, deadline=STORY_DEADLINE_SECONDS)
                counts = _mock_control_counts(mock_origin)
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Nested fixed-loop owned runtime was not created')
        mock_finished = None
        if mock_process is not None:
            mock_finished = _terminate_mock_child(mock_process)
            mock_process = None
        story_stdout = story['stdout'] if story is not None else b''
        story_stderr = story['stderr'] if story is not None else b''
        mock_stdout = mock_finished['stdout'] if mock_finished is not None else b''
        mock_stderr = mock_finished['stderr'] if mock_finished is not None else b''
        redaction_ok = _redaction_gate(story_stdout, story_stderr, mock_stdout, mock_stderr)
        if redaction_ok:
            if story is not None:
                _bounded_write(root / STORY_LOG_NAME, story_stdout)
                _bounded_write(root / STORY_ERR_LOG_NAME, story_stderr)
            if mock_finished is not None:
                _bounded_write(root / MOCK_LOG_NAME, mock_stdout)
                _bounded_write(root / MOCK_ERR_LOG_NAME, mock_stderr)
        marker_matches, observed_checks = ((False, False), 0)
        if story is not None:
            marker_matches, observed_checks = _parse_story_marker(
                story_stdout.decode('utf-8', errors='replace'))
        mock_receipt = None
        if mock_finished is not None:
            mock_receipt = {'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID,
                            'argv': [str(item) for item in (mock_argv or [])],
                            'origin': mock_origin, 'ready': mock_origin is not None,
                            'ready_file': MOCK_READY_FILE_NAME,
                            'exit_code': mock_finished['exit_code'],
                            'timed_out': mock_finished['timed_out'],
                            'duration_seconds': (round(time.monotonic() - mock_started, 3)
                                                 if mock_started is not None else 0.0),
                            'redaction_gate': redaction_ok,
                            'stdout_sha256': _sha256(mock_stdout), 'stdout_bytes': len(mock_stdout),
                            'stderr_sha256': _sha256(mock_stderr), 'stderr_bytes': len(mock_stderr),
                            'observed_webhook_requests': (counts['observed_webhook_requests']
                                                            if counts is not None else None),
                            'observed_event_webhook_requests': (counts['observed_event_webhook_requests']
                                                                if counts is not None else None),
                            'observed_friend_auth_sync_requests': (counts['observed_friend_auth_sync_requests']
                                                                   if counts is not None else None),
                            'judge_started': bool(counts and counts['judge_started']),
                            'judge_released': bool(counts and counts['judge_released'])}
            write(root / 'nested-fixed-loop-mock-child.json', mock_receipt)
        receipt = None
        if story is not None:
            receipt = {'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID,
                       'argv': [str(item) for item in (story_argv or [])],
                       'exit_code': story['exit_code'], 'timed_out': story['timed_out'],
                       'duration_seconds': story['duration_seconds'],
                       'markers': dict(zip(MARKER_KEYS, marker_matches)),
                       'observed_checks': observed_checks, 'redaction_gate': redaction_ok,
                       'stdout_sha256': _sha256(story_stdout), 'stdout_bytes': len(story_stdout),
                       'stderr_sha256': _sha256(story_stderr), 'stderr_bytes': len(story_stderr)}
            write(root / 'nested-fixed-loop-child.json', receipt)
        owned_record = read(root / 'owned-bcs.json')
        zero_traffic = (counts is not None
                        and counts['observed_webhook_requests'] == 0
                        and counts['observed_event_webhook_requests'] == 0
                        and counts['judge_started'] is False)
        child_pass = (error is None and story is not None and receipt is not None
                      and mock_receipt is not None and mock_origin is not None
                      and receipt['exit_code'] == 0 and not receipt['timed_out']
                      and tuple(receipt['markers'][name] for name in MARKER_KEYS) == (True, True)
                      and receipt['observed_checks'] == len(MARKER_KEYS)
                      and receipt['redaction_gate'] is True
                      and mock_receipt['ready'] is True and mock_receipt['exit_code'] == 0
                      and mock_receipt['redaction_gate'] is True
                      and zero_traffic
                      and executor_digest() == source
                      and read(root / 'nested-fixed-loop-binding.json') == binding
                      and file_sha256(Path(binary)) == binary_sha256
                      and file_sha256(Path(cli_binary)) == cli_binary_sha256
                      and owned_record.get('status') == 'STOPPED'
                      and owned_record.get('returncode') == 0 and owned_record.get('ready') is True)
        case = {}
        if child_pass:
            case = {'schema_version': 1, 'status': STATUS, 'run_id': run_id, 'case_id': CASE_ID,
                    'executor_digest': source, 'binary_kind': BINARY_KIND,
                    'bcs_binary_sha256': binary_sha256, 'auto_build': False, 'human_id': HUMAN_ID,
                    'cli_binary_sha256': cli_binary_sha256, 'driver_bot_id': driver,
                    'child_receipt': receipt, 'mock_child_receipt': mock_receipt,
                    'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
                    'story_lines': {row['id']: list(row['story_lines'])
                                    for row in original['requirements']},
                    'story_markers': {'observed': list(marker_matches), 'checks': observed_checks},
                    'zero_traffic': counts,
                    'runtime_shutdown': {'status': owned_record.get('status'),
                                         'returncode': owned_record.get('returncode')},
                    'resource_disposition': 'STORY_SCOPED_TEMPORARY_DIRECTORY',
                    'credentials': {'credential_memory': 'NOT_CAPTURED',
                                    'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'}}
            write(root / 'nested-fixed-loop.json', case)
        result = {'schema_version': 1, 'status': 'BLOCKED', 'run_id': run_id, 'case_id': CASE_ID,
                  'suite': SUITE, 'executor_digest': source, 'binary_kind': BINARY_KIND,
                  'bcs_binary_sha256': binary_sha256, 'auto_build': False, 'human_id': HUMAN_ID,
                  'cli_binary_sha256': cli_binary_sha256, 'driver_bot_id': driver,
                  'child_receipt': receipt if receipt is not None else {'status': 'NOT_CREATED'},
                  'mock_child_receipt': mock_receipt if mock_receipt is not None else {'status': 'NOT_CREATED'},
                  'case': case, 'zero_traffic': zero_traffic,
                  'credentials': {'credential_memory': 'NOT_CAPTURED',
                                  'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'},
                  'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
                  'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
            result['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
        else:
            result['status'] = STATUS if child_pass else 'BLOCKED'
        # Drift fence: executor, frozen binding and frozen binary while children ran.
        if result['status'] == STATUS and (executor_digest() != source
                                            or read(root / 'nested-fixed-loop-binding.json') != binding
                                            or file_sha256(Path(binary)) != binary_sha256
                                            or file_sha256(Path(cli_binary)) != cli_binary_sha256):
            result['status'] = 'BLOCKED'
        write(root / 'nested-fixed-loop-flow-report.json', result)
        from .local_nested_fixed_loop_reporting import publish_nested_fixed_loop_report
        canonical = publish_nested_fixed_loop_report(root, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
        story_process = story.get('process') if isinstance(story, dict) else None
        if story_process is not None and story_process.poll() is None:
            _terminate_process_group(story_process)
        if mock_process is not None and mock_process.poll() is None:
            _terminate_process_group(mock_process)
