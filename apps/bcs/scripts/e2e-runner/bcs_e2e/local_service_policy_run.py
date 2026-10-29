"""Owned-local black-box Run lifecycle for the retired service policy Story.

The upstream #3173 retirement regression is one self-contained child process
against a single live owned BCS runtime: it bootstraps the local Human
principal through the loopback mock-admission headers (X-Mock-User-Id),
registers one disposable driver Bot, persists no legacy service_spec payload
on either legacy encoding, keeps the Gone settings surface, the service
invocation lifecycle, the running-reactivation guard and the completed-chat
conversion guard under live HTTP, and deletes every resource on teardown. The
owned runtime creates and owns the state directory; the frozen binding and the
black-box child receipt land inside it, and nothing credential-bearing is ever
captured into evidence. This is the 30th promoted reviewed-release parent
slice, not an independent slice.
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

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_service_policy_contract import (CASE_ID, MARKER_KEYS, MARKER_PATTERNS,
                                             PARENT, REQUIREMENTS, STORY_CHECKS, STATUS,
                                             STORY_SOURCE, SUITE, service_policy_source_binding)
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import identifier
from .run_state import RunStateError, read, write

BINARY_KIND = 'bcs'
STORY_DEADLINE_SECONDS = 900
REDACTED_TOKENS = (b'bot_token', b'Bearer ')
STORY_LOG_NAME = 'service-policy-story.log'
STORY_ERR_LOG_NAME = 'service-policy-story.err.log'
BOUNDED_STREAM_BYTES = 8 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_service_policy_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the owned BCS server binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError('service-policy BCS binary must be a regular frozen file')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError('service-policy BCS binary must be executable')
    if _sha256(binary.read_bytes()) != binary_sha256:
        raise RunStateError('service-policy BCS binary digest differs from its frozen declaration')
    return binary


def _parse_story_marker(text: str) -> tuple[bool, int]:
    """Single strict marker; the live HTTP checks count proves real checks."""
    match = MARKER_PATTERNS[0].search(text)
    if match is None:
        return False, 0
    return True, int(match.group(1))


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
        raise RunStateError('service-policy story exceeded its 900-second deadline') from None
    except BaseException:
        _terminate_process_group(process)
        raise
    return {'exit_code': process.returncode, 'timed_out': False,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': out, 'stderr': err, 'process': process}


def _redaction_gate(*streams: bytes) -> bool:
    return all(token not in stream for stream in streams for token in REDACTED_TOKENS)


def _bounded_write(path: Path, data: bytes) -> None:
    if len(data) > BOUNDED_STREAM_BYTES:
        raise RunStateError('service-policy child stream exceeded its bounded evidence limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _story_argv(base_url: str) -> list[str]:
    return [sys.executable, str(STORY_SOURCE), '--base-url', base_url]


def local_service_policy_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
                             confirm_run_id: str, allow_local_processes: bool,
                             allow_fixture_write: bool, allow_write: bool) -> dict:
    """Run the retired service policy story once beside the owned runtime."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Service policy requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Service policy Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Service policy requires a fresh state directory; existing evidence is retained')
    parent = state_dir.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RunStateError('Service policy requires a non-symlink directory parent for its state')
    binary = validate_service_policy_binary(Path(binary), binary_sha256)
    source = executor_digest()
    original = service_policy_source_binding()
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in reviewed}:
        raise RunStateError('Service policy slice must stay inside the reviewed release denominator')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
               'run_id': run_id, 'executor_digest': source, 'binary_kind': BINARY_KIND,
               'bcs_binary_sha256': binary_sha256, 'auto_build': False,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'environment': original['environment'],
               'runtime_dir': str(state_dir.resolve())}
    error = None
    runtime = None
    root = None
    story = None
    story_argv = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id,
                                 allow_ambient_local_identity=True) as owned_runtime:
                # The frozen fixture drives the loopback mock-admission headers
                # (X-Mock-User-Id); ambient local identity admission is required.
                runtime = owned_runtime
                root = owned_runtime.root
                write(root / 'service-policy-binding.json', binding)
                story_argv = _story_argv(owned_runtime.inputs.origin)
                story = _launch_story(story_argv, cwd=root, deadline=STORY_DEADLINE_SECONDS)
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Service policy owned runtime was not created')
        story_stdout = story['stdout'] if story is not None else b''
        story_stderr = story['stderr'] if story is not None else b''
        redaction_ok = _redaction_gate(story_stdout, story_stderr)
        if redaction_ok and story is not None:
            _bounded_write(root / STORY_LOG_NAME, story_stdout)
            _bounded_write(root / STORY_ERR_LOG_NAME, story_stderr)
        marker_ok, observed_checks = (False, 0)
        if story is not None:
            marker_ok, observed_checks = _parse_story_marker(story_stdout.decode('utf-8', errors='replace'))
        receipt = None
        if story is not None:
            receipt = {'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID,
                       'argv': [str(item) for item in (story_argv or [])],
                       'exit_code': story['exit_code'], 'timed_out': story['timed_out'],
                       'duration_seconds': story['duration_seconds'],
                       'markers': dict(zip(MARKER_KEYS, (marker_ok,))),
                       'observed_checks': observed_checks, 'redaction_gate': redaction_ok,
                       'stdout_sha256': _sha256(story_stdout), 'stdout_bytes': len(story_stdout),
                       'stderr_sha256': _sha256(story_stderr), 'stderr_bytes': len(story_stderr)}
            write(root / 'service-policy-child.json', receipt)
        owned_record = read(root / 'owned-bcs.json')
        child_pass = (error is None and story is not None and receipt is not None
                      and receipt['exit_code'] == 0 and not receipt['timed_out']
                      and receipt['markers'].get(MARKER_KEYS[0]) is True
                      and receipt['observed_checks'] == STORY_CHECKS
                      and receipt['redaction_gate'] is True
                      and executor_digest() == source
                      and read(root / 'service-policy-binding.json') == binding
                      and file_sha256(Path(binary)) == binary_sha256
                      and owned_record.get('status') == 'STOPPED' and owned_record.get('returncode') == 0)
        case = {}
        if child_pass:
            case = {'schema_version': 1, 'status': STATUS, 'run_id': run_id, 'case_id': CASE_ID,
                    'executor_digest': source, 'binary_kind': BINARY_KIND,
                    'bcs_binary_sha256': binary_sha256, 'auto_build': False,
                    'child_receipt': receipt,
                    'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
                    'story_lines': {row['id']: list(row['story_lines'])
                                    for row in original['requirements']},
                    'story_markers': {'observed': [marker_ok], 'http_checks': observed_checks},
                    'runtime_shutdown': {'status': owned_record.get('status'),
                                         'returncode': owned_record.get('returncode')},
                    'resource_disposition': 'STORY_SCOPED_TEMPORARY_DIRECTORY',
                    'credentials': {'credential_memory': 'NOT_CAPTURED',
                                    'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'}}
            write(root / 'service-policy.json', case)
        result = {'schema_version': 1, 'status': 'BLOCKED', 'run_id': run_id, 'case_id': CASE_ID,
                  'suite': SUITE, 'executor_digest': source, 'binary_kind': BINARY_KIND,
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
        # Drift fence: executor, frozen binding and frozen binary while the child ran.
        if result['status'] == STATUS and (executor_digest() != source
                                            or read(root / 'service-policy-binding.json') != binding
                                            or file_sha256(Path(binary)) != binary_sha256):
            result['status'] = 'BLOCKED'
        write(root / 'service-policy-flow-report.json', result)
        from .local_service_policy_reporting import publish_service_policy_report
        canonical = publish_service_policy_report(root, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
        story_process = story.get('process') if isinstance(story, dict) else None
        if story_process is not None and story_process.poll() is None:
            _terminate_process_group(story_process)
