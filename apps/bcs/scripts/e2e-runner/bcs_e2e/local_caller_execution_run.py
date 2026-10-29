"""Owned-local black-box Run lifecycle for the caller-execution Story.

The upstream story is a complete, self-contained child process: it owns the
mocked provider, spawns the frozen test-utils BCS binary, drives the durable
queue, loses the in-memory caller cache on restart and redacts itself. This
module launches exactly that child once, freezes a shared child receipt, and
never captures raw credential-bearing streams into evidence.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_caller_execution_contract import (CASE_ID, MARKER_KEYS, MARKER_PATTERNS, PARENT,
                                               REQUIREMENTS, STATUS, STORY_SOURCE, SUITE,
                                               caller_execution_source_binding)
from .profile import identifier
from .run_state import RunStateError, read, write

BINARY_KIND = 'bcs-caller-e2e-test-utils'
CHILD_DEADLINE_SECONDS = 900
REDACTED_TOKEN = b'test-token'
STORY_LOG_NAME = 'caller-execution-story.log'
STORY_ERR_LOG_NAME = 'caller-execution-story.err.log'
BOUNDED_STREAM_BYTES = 8 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_caller_execution_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the out-of-tree test-utils binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError('caller-execution test-utils binary must be a regular frozen file')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError('caller-execution test-utils binary must be executable')
    if _sha256(binary.read_bytes()) != binary_sha256:
        raise RunStateError('caller-execution test-utils binary digest differs from its frozen declaration')
    return binary


def _parse_story_markers(text: str) -> tuple[tuple[bool, bool, bool], int]:
    """Strictly ordered markers; the HTTP assertion count proves real assertions."""
    flags: list[bool] = []
    position = 0
    http_assertions = 0
    failed = False
    for index, pattern in enumerate(MARKER_PATTERNS):
        if failed:
            flags.append(False)
            continue
        match = pattern.search(text, position)
        if match is None:
            failed = True
            flags.append(False)
            continue
        if index == 0:
            http_assertions = int(match.group(1))
        flags.append(True)
        position = match.end()
    return tuple(flags), http_assertions


def _terminate_story_group(process: subprocess.Popen) -> None:
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
        _terminate_story_group(process)
        raise RunStateError('caller-execution story exceeded its 900-second deadline') from None
    return {'exit_code': process.returncode, 'timed_out': False, 'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': out, 'stderr': err}


def _redaction_gate(output: bytes, error: bytes) -> bool:
    return REDACTED_TOKEN not in output and REDACTED_TOKEN not in error


def _bounded_write(path: Path, data: bytes) -> None:
    if len(data) > BOUNDED_STREAM_BYTES:
        raise RunStateError('caller-execution story stream exceeded its bounded evidence limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def local_caller_execution_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
                               confirm_run_id: str, allow_local_processes: bool,
                               allow_fixture_write: bool, allow_write: bool) -> dict:
    """Run the story once; freeze one shared black-box child receipt."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Caller execution requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Caller execution Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Caller execution requires a fresh state directory; existing evidence is retained')
    parent = state_dir.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RunStateError('Caller execution requires a non-symlink directory parent for its state')
    binary = validate_caller_execution_binary(Path(binary), binary_sha256)
    source = executor_digest()
    original = caller_execution_source_binding()
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if PARENT in {row['id'] for row in reviewed}:
        raise RunStateError('Caller execution slice must stay independent of the reviewed release denominator')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
               'run_id': run_id, 'executor_digest': source, 'binary_kind': BINARY_KIND,
               'bcs_binary_sha256': binary_sha256, 'auto_build': False,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'environment': original['environment'],
               'runtime_dir': str(state_dir.resolve())}
    root = state_dir.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(root / 'caller-execution-binding.json', binding)
    argv = [sys.executable, str(STORY_SOURCE), '--binary', str(binary)]
    error = None
    child = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            child = _launch_story(argv, root, CHILD_DEADLINE_SECONDS)
        except BaseException as failure:
            error = failure
        redaction_ok = False
        if child is not None:
            redaction_ok = _redaction_gate(child['stdout'], child['stderr'])
            if redaction_ok:
                _bounded_write(root / STORY_LOG_NAME, child['stdout'])
                _bounded_write(root / STORY_ERR_LOG_NAME, child['stderr'])
        markers = (False, False, False)
        http_assertions = 0
        if child is not None:
            markers, http_assertions = _parse_story_markers(
                child['stdout'].decode('utf-8', errors='replace'))
        receipt = None
        if child is not None:
            receipt = {'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID,
                       'argv': [str(item) for item in argv], 'exit_code': child['exit_code'],
                       'timed_out': child['timed_out'], 'duration_seconds': child['duration_seconds'],
                       'markers': dict(zip(MARKER_KEYS, markers)),
                       'http_assertions': http_assertions, 'redaction_gate': redaction_ok,
                       'stdout_sha256': _sha256(child['stdout']), 'stdout_bytes': len(child['stdout']),
                       'stderr_sha256': _sha256(child['stderr']), 'stderr_bytes': len(child['stderr'])}
            write(root / 'caller-execution-child.json', receipt)
        child_pass = (error is None and child is not None and receipt is not None
                      and receipt['exit_code'] == 0 and not receipt['timed_out']
                      and all(receipt['markers'].values()) and receipt['http_assertions'] > 0
                      and receipt['redaction_gate'] is True)
        case = {}
        if child_pass:
            case = {'schema_version': 1, 'status': STATUS, 'run_id': run_id, 'case_id': CASE_ID,
                    'executor_digest': source, 'binary_kind': BINARY_KIND,
                    'bcs_binary_sha256': binary_sha256, 'child_receipt': receipt,
                    'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
                    'story_lines': {row['id']: list(row['story_lines']) for row in original['requirements']},
                    'story_markers': {'observed': list(markers), 'http_assertions': http_assertions},
                    'resource_disposition': 'STORY_SCOPED_TEMPORARY_DIRECTORY',
                    'credentials': {'credential_memory': 'NOT_CAPTURED',
                                    'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'}}
            write(root / 'caller-execution.json', case)
        result = {'schema_version': 1, 'status': 'BLOCKED', 'run_id': run_id, 'case_id': CASE_ID,
                  'executor_digest': source, 'binary_kind': BINARY_KIND,
                  'bcs_binary_sha256': binary_sha256, 'auto_build': False,
                  'child_receipt': receipt if receipt is not None else {'status': 'NOT_CREATED'},
                  'case': case,
                  'credentials': {'credential_memory': 'NOT_CAPTURED',
                                  'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'},
                  'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
                  'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
            result['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
        else:
            result['status'] = STATUS if child_pass else 'BLOCKED'
        # Drift fence: the executor source must be unchanged while the child ran.
        if result['status'] == STATUS and (executor_digest() != source
                                            or read(root / 'caller-execution-binding.json') != binding):
            result['status'] = 'BLOCKED'
        write(root / 'caller-execution-flow-report.json', result)
        from .local_caller_execution_reporting import publish_caller_execution
        canonical = publish_caller_execution(root, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
