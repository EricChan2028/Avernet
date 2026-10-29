"""Owned-local black-box Run lifecycle for the Bot-authority user journey.

The upstream parent Story drives the trusted registration surface and the
unified Bot-authority surface against one owned BCS runtime: two disposable
fixture Bots are registered under the strict mock-header admission of the
registration plane, every business call carries a Gateway Principal JWT in
``X-Avernet-Principal`` only, the sixteen obligations consume the frozen
24-call matrix exactly once, and both registered Bots are retained beside
the stopped runtime. Nothing credential-bearing ever lands in evidence.
This slice is promoted into the reviewed release denominator, so it never
claims release credit and keeps remote validation open.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_authority import OwnedLocalAuthority
from .local_authority_contract import (
    CASE_ID,
    EXPECTED_HTTP_CALLS,
    PARENT,
    REQUIREMENTS,
    STATUS,
    SUITE,
    authority_source_binding,
)
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write

BINARY_KIND = 'bcs'
STRICT_AUTH = {'chain': ['local', 'session'], 'require_authentication': False,
               'allow_mock_headers': True}
FORBIDDEN_JOURNAL_SUBSTRINGS = ('bot_token', 'X-Avernet-Principal', 'register?token')
EXPECTED_RESOURCES = 2


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_authority_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the owned BCS server binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError('authority BCS binary must be a regular frozen file')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError('authority BCS binary must be executable')
    if _sha256(binary.read_bytes()) != binary_sha256:
        raise RunStateError('authority BCS binary digest differs from its frozen declaration')
    return binary


def local_authority_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
                        confirm_run_id: str, allow_local_processes: bool,
                        allow_fixture_write: bool, allow_write: bool) -> dict:
    """Run the Bot-authority user journey once inside one owned runtime."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Authority requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Authority Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Authority requires a fresh state directory; existing evidence is retained')
    parent = state_dir.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RunStateError('Authority requires a non-symlink directory parent for its state')
    binary = validate_authority_binary(Path(binary), binary_sha256)
    source = executor_digest()
    original = authority_source_binding()
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in reviewed}:
        raise RunStateError('Authority parent is missing from the reviewed release denominator')
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
               'run_id': run_id, 'executor_digest': source, 'binary_kind': BINARY_KIND,
               'bcs_binary_sha256': binary_sha256, 'auto_build': False, 'store_messages': False,
               'source_binding': original, 'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': legacy, 'environment': original['environment'],
               'runtime_dir': str(state_dir.resolve())}
    error = None
    runtime = None
    case = {}
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id,
                                 allow_ambient_local_identity=False) as owned_runtime:
                # Strict admission: the registration plane mints one-time tokens
                # under explicit mock headers; business calls carry principals only.
                runtime = owned_runtime
                write(runtime.root / 'authority-binding.json', binding)
                case = OwnedLocalAuthority(runtime).run()
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Authority owned runtime was not created')
        journal = runtime.root / 'local-authority.json'
        if not case and journal.is_file():
            saved = read(journal)
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = {'credential_memory': 'NOT_CAPTURED', 'fixtures': 'NONE',
                       'role_identity': 'NONE', 'ambient_local_identity': False,
                       'strict_mock_header_admission': True}
        strict_configuration = (json.loads((runtime.root / 'bcs-config.json').read_text()).get('auth')
                                == STRICT_AUTH)
        journal_clean = journal.is_file() and not any(
            token in journal.read_text() for token in FORBIDDEN_JOURNAL_SUBSTRINGS)
        retained = isinstance(case.get('resources'), dict) and isinstance(
            case['resources'].get('created'), list) and len(case['resources']['created']) == EXPECTED_RESOURCES
        expected = [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS]
        passed = (error is None and executor_digest() == source
                  and read(runtime.root / 'authority-binding.json') == binding
                  and bool(case) and read(journal) == case
                  and case.get('status') == STATUS
                  and case.get('assertions') == expected
                  and case.get('run_id') == run_id and case.get('case_id') == CASE_ID
                  and case.get('executor_digest') == source
                  and case.get('http_call_count') == EXPECTED_HTTP_CALLS
                  and retained and strict_configuration and journal_clean
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0)
        result = {'schema_version': 1, 'status': 'BLOCKED', 'run_id': run_id,
                  'case_id': CASE_ID, 'suite': SUITE, 'executor_digest': source,
                  'binary_kind': BINARY_KIND, 'bcs_binary_sha256': binary_sha256,
                  'auto_build': False, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials, 'remote_validation': 'NOT_RUN',
                  'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}
        if error is not None:
            result['error_type'] = type(error).__name__
            result['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
        else:
            result['status'] = STATUS if passed else 'BLOCKED'
        if result['status'] == STATUS and (executor_digest() != source
                                           or read(runtime.root / 'authority-binding.json') != binding
                                           or not journal_clean):
            result['status'] = 'BLOCKED'
        write(runtime.root / 'authority-flow-report.json', result)
        from .local_authority_reporting import publish_authority_report
        canonical = publish_authority_report(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
