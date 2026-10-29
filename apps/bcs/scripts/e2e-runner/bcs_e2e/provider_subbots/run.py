"""Owned-local lifecycle for the Provider subbots migration slice."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ..errors import has_cancellation
from ..inventory import (KNOWN_UNMAPPED_LEGACY_IDS, current_manifest, executor_digest,
                         reviewed_release_inventory)
from ..owned_bcs import OwnedBcsProcess, file_sha256
from ..profile import identifier
from ..run_state import RunStateError, read, write
from .checks import scan_redaction, verify_all_obligations
from .contract import (CASE_ID, MARKER_PATTERNS, PARENT, REQUIREMENTS, STATUS,
                       STORY_CHECKS, SUITE, provider_subbots_source_binding)
from .reporting import (BINDING_NAME, CASE_NAME, FLOW_NAME,
                        build_provider_subbots_summary,
                        publish_provider_subbots_report)
from .runtime import provider_subbots_config_overlay

BINARY_KIND = 'bcs'
STORY_DEADLINE_SECONDS = 900
BOUNDED_STREAM_BYTES = 8 * 1024 * 1024
EVIDENCE_NAME = 'provider-subbots-evidence.json'
STORY_LOG_NAME = 'provider-subbots-story.log'
STORY_ERR_LOG_NAME = 'provider-subbots-story.err.log'
PRINCIPAL_ENV = 'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'
STORY_SOURCE = Path(__file__).with_name('story.py')


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_provider_subbots_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the owned BCS server binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError('provider-subbots BCS binary must be an executable regular file')
    if not isinstance(binary_sha256, str) or len(binary_sha256) != 64:
        raise RunStateError('provider-subbots BCS binary digest is invalid')
    if file_sha256(binary) != binary_sha256:
        raise RunStateError('provider-subbots BCS binary digest differs from its frozen declaration')
    return binary


def _parse_story_marker(text: str) -> tuple[bool, int]:
    matches = MARKER_PATTERNS[0].findall(text)
    if len(matches) != 1:
        return False, 0
    return True, int(matches[0])


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


def _story_environment(owned_runtime, root: Path) -> dict[str, str]:
    """Minimal child environment with only the owned per-run signing key."""
    key = owned_runtime.inputs.environment.get(PRINCIPAL_ENV)
    if (not isinstance(key, str) or not key
            or any(ord(char) <= 32 or ord(char) == 127 for char in key)):
        raise RunStateError('provider-subbots owned runtime principal signing key is missing')
    return {
        'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        'HOME': str(Path(root) / 'home'),
        'TMPDIR': str(Path(root) / 'tmp'),
        'LANG': 'C.UTF-8',
        'TZ': 'UTC',
        'PYTHONIOENCODING': 'utf-8',
        PRINCIPAL_ENV: key,
    }


def _launch_story(argv: list[str], cwd: Path, deadline: float,
                  environment: dict[str, str]) -> dict:
    """Test seam: one bounded black-box child in its own process group."""
    process = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, start_new_session=True)
    started = time.monotonic()
    try:
        out, err = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise RunStateError('provider-subbots story exceeded its 900-second deadline') from None
    except BaseException:
        _terminate_process_group(process)
        raise
    return {'exit_code': process.returncode, 'timed_out': False,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': out, 'stderr': err, 'process': process}


def _bounded_write(path: Path, data: bytes) -> None:
    if len(data) > BOUNDED_STREAM_BYTES:
        raise RunStateError('provider-subbots child stream exceeded its bounded evidence limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _read_optional(path: Path) -> bytes:
    if not path.exists() or path.is_symlink():
        return b''
    return path.read_bytes()


def _redaction_entries(stream_items: list[tuple[str, bytes]],
                       artifact_items: list[tuple[str, bytes]]) -> tuple[list[dict], list[dict], bool]:
    streams = [scan_redaction(name, payload) for name, payload in stream_items]
    artifacts = [scan_redaction(name, payload) for name, payload in artifact_items]
    return streams, artifacts, all(row['clean'] is True for row in streams + artifacts)


def local_provider_subbots_run(*, binary: Path, binary_sha256: str, state_dir: Path,
                               run_id: str, confirm_run_id: str,
                               allow_local_processes: bool, allow_fixture_write: bool,
                               allow_write: bool) -> dict:
    """Run the frozen Provider subbots journey once beside an owned BCS child."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Provider subbots requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Provider subbots Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Provider subbots requires a fresh state directory; existing evidence is retained')
    parent_dir = state_dir.parent
    if parent_dir.is_symlink() or (parent_dir.exists() and not parent_dir.is_dir()):
        raise RunStateError('Provider subbots requires a non-symlink directory parent for its state')
    binary = validate_provider_subbots_binary(Path(binary), binary_sha256)
    source = executor_digest()
    original = provider_subbots_source_binding()
    legacy = current_manifest()['legacy_inventory']
    if PARENT not in KNOWN_UNMAPPED_LEGACY_IDS:
        raise RunStateError('Provider subbots parent must remain registered backlog')
    if PARENT not in {row['id'] for row in legacy}:
        raise RunStateError('Provider subbots parent is missing from the legacy inventory')
    if PARENT in {row['id'] for row in reviewed_release_inventory()}:
        raise RunStateError('Provider subbots slice must stay independent of the reviewed release denominator')
    binding = {
        'schema_version': 1,
        'contract': SUITE,
        'suite': SUITE,
        'case_id': CASE_ID,
        'run_id': run_id,
        'executor_digest': source,
        'binary_kind': BINARY_KIND,
        'bcs_binary_sha256': binary_sha256,
        'auto_build': False,
        'source_binding': original,
        'selected_requirement_ids': list(REQUIREMENTS),
        'legacy_inventory': legacy,
        'environment': original['environment'],
        'config_fragment': original['config_fragment'],
        'runtime_dir': str(state_dir.resolve()),
    }
    error = None
    root = None
    startup_record = None
    story = None
    story_argv = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with provider_subbots_config_overlay():
                with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id,
                                     allow_ambient_local_identity=True) as owned_runtime:
                    root = owned_runtime.root
                    startup_record = read(root / 'owned-bcs.json')
                    binding['human_id'] = owned_runtime.inputs.human_id
                    write(root / BINDING_NAME, binding)
                    evidence_path = root / EVIDENCE_NAME
                    story_argv = [sys.executable, str(STORY_SOURCE),
                                  '--base-url', owned_runtime.inputs.origin,
                                  '--human', owned_runtime.inputs.human_id,
                                  '--evidence-file', str(evidence_path)]
                    story = _launch_story(
                        story_argv, cwd=root, deadline=STORY_DEADLINE_SECONDS,
                        environment=_story_environment(owned_runtime, root))
        except BaseException as failure:
            error = failure
        if root is None:
            if error is not None:
                raise error
            raise RunStateError('Provider subbots owned runtime was not created')
        owned_record = read(root / 'owned-bcs.json')
        story_stdout = story['stdout'] if story is not None else b''
        story_stderr = story['stderr'] if story is not None else b''
        console = _read_optional(root / 'console.log')
        evidence_raw = _read_optional(root / EVIDENCE_NAME)
        marker_ok, observed_checks = (False, 0)
        if story is not None:
            marker_ok, observed_checks = _parse_story_marker(
                story_stdout.decode('utf-8', errors='replace'))
        evidence = None
        try:
            evidence = json.loads(evidence_raw) if evidence_raw else None
        except (UnicodeError, json.JSONDecodeError):
            evidence = None
        child_exit_ok = (story is not None and story['exit_code'] == 0
                         and story['timed_out'] is False and marker_ok
                         and observed_checks == STORY_CHECKS)
        provisional_case = {}
        if child_exit_ok and isinstance(evidence, dict):
            provisional_case = {
                'schema_version': 1, 'status': STATUS, 'run_id': run_id,
                'case_id': CASE_ID, 'executor_digest': source,
                'binary_kind': BINARY_KIND, 'bcs_binary_sha256': binary_sha256,
                'auto_build': False,
                'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
                'story_lines': {row['id']: list(row['story_lines'])
                                for row in original['requirements']},
                'story_markers': {'observed': marker_ok,
                                  'live_http_assertions': observed_checks},
                'config_fragment': original['config_fragment'],
                'resource_disposition': 'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED',
                'remote_validation': 'NOT_RUN',
                'grants_release_credit': False,
            }
        child_receipt = {
            'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID,
            'argv': [str(item) for item in (story_argv or [])],
            'exit_code': story['exit_code'] if story is not None else None,
            'timed_out': story['timed_out'] if story is not None else True,
            'duration_seconds': story['duration_seconds'] if story is not None else 0.0,
            'markers': {'owned-local-provider-subbots-compliance': marker_ok},
            'observed_checks': observed_checks,
            'stdout_sha256': _sha256(story_stdout), 'stdout_bytes': len(story_stdout),
            'stderr_sha256': _sha256(story_stderr), 'stderr_bytes': len(story_stderr),
        }
        provisional_result = {
            'schema_version': 1, 'status': STATUS if child_exit_ok else 'BLOCKED',
            'run_id': run_id, 'case_id': CASE_ID, 'suite': SUITE,
            'executor_digest': source, 'binary_kind': BINARY_KIND,
            'bcs_binary_sha256': binary_sha256, 'auto_build': False,
            'child_receipt': child_receipt, 'case': provisional_case,
            'checks': {'status': 'PASS' if child_exit_ok else 'BLOCKED', 'checked': []},
            'redaction': {'status': 'PASS' if story is not None else 'BLOCKED'},
            'runtime_shutdown': {'status': owned_record.get('status'),
                                 'returncode': owned_record.get('returncode'),
                                 'ready': owned_record.get('ready')},
            'credentials': {'credential_memory': 'NOT_CAPTURED',
                            'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'},
            'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False,
            'resource_disposition': 'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED',
        }
        candidate_summary = build_provider_subbots_summary(binding, provisional_result)
        stream_items = [('story.stdout', story_stdout), ('story.stderr', story_stderr),
                        ('owned.console', console), ('child.evidence', evidence_raw)]
        artifact_items = [('child.receipt', json.dumps(child_receipt, sort_keys=True).encode()),
                          ('flow.report', json.dumps(provisional_result, sort_keys=True).encode()),
                          ('canonical.report', json.dumps(candidate_summary, sort_keys=True).encode())]
        streams, artifacts, redaction_ok = _redaction_entries(stream_items, artifact_items)
        redaction = {'token_material_captured': False, 'streams': streams,
                     'artifacts': artifacts, 'status': 'PASS' if redaction_ok else 'BLOCKED'}
        checks = {'status': 'BLOCKED', 'checked': []}
        if child_exit_ok and isinstance(evidence, dict):
            try:
                checks = verify_all_obligations(evidence)
                checks = {'status': checks['status'], 'checked': checks['checked']}
            except RunStateError as failure:
                checks = {'status': 'BLOCKED', 'checked': [],
                          'error_type': type(failure).__name__}
        child_pass = (error is None and child_exit_ok and isinstance(evidence, dict)
                      and redaction_ok and checks.get('status') == 'PASS'
                      and startup_record is not None
                      and startup_record.get('status') == 'RUNNING'
                      and startup_record.get('ready') is True
                      and owned_record.get('status') == 'STOPPED'
                      and owned_record.get('returncode') == 0
                      and executor_digest() == source
                      and read(root / BINDING_NAME) == binding
                      and file_sha256(binary) == binary_sha256)
        case = dict(provisional_case) if child_pass else {}
        if child_pass:
            case['checks'] = checks
            case['redaction'] = redaction
        result = dict(provisional_result)
        result.update({
            'status': STATUS if child_pass else ('CANCELLED' if error is not None and has_cancellation(error)
                                                else 'BLOCKED'),
            'child_receipt': child_receipt,
            'case': case,
            'checks': checks,
            'redaction': redaction,
            'runtime_shutdown': {'status': owned_record.get('status'),
                                 'returncode': owned_record.get('returncode'),
                                 'ready': owned_record.get('ready')},
        })
        if not redaction_ok:
            result['error_type'] = 'RedactionGateError'
        elif error is not None:
            result['error_type'] = type(error).__name__
        if redaction_ok and story is not None:
            _bounded_write(root / STORY_LOG_NAME, story_stdout)
            _bounded_write(root / STORY_ERR_LOG_NAME, story_stderr)
        if child_pass:
            write(root / CASE_NAME, case)
        write(root / FLOW_NAME, result)
        binding_unchanged = read(root / BINDING_NAME) == binding
        artifact_unchanged = (executor_digest() == source
                              and file_sha256(binary) == binary_sha256)
        if not (binding_unchanged and artifact_unchanged):
            result['status'] = 'BLOCKED'
            result['error_type'] = 'DriftFenceError'
            write(root / FLOW_NAME, result)
            result['canonical_summary'] = build_provider_subbots_summary(binding, result)
            result['outputs'] = {}
            return result
        canonical = publish_provider_subbots_report(root, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] != 'LOCAL_SUITE_PASS':
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
        story_process = story.get('process') if isinstance(story, dict) else None
        if story_process is not None and story_process.poll() is None:
            _terminate_process_group(story_process)
