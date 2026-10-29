"""Explicit local-mock cleanup recovery from already complete terminal evidence.

Never settles effects, renews deadlines, repairs ownership or reacquires a missing
reservation. Unknown activity requires a different, independently authorized
reconciliation contract; this entry point cannot turn it into terminal proof.
"""
from __future__ import annotations

from pathlib import Path
import uuid

from .cleanup import CleanupError, execute_cleanup, verify_scope
from .execution_safety import require_execution_safe
from .inventory import executor_digest, frozen_manifest
from .ledger import Ledger
from .operation_authority import AuthorizedAdapter
from .persistent_mock import PersistentLocalMockAdapter
from .pool import PoolLease, bound_pool_path
from .profile import require_resolved
from .run_state import RunStateError, read, require_profile_binding, write
from .runtime import require_no_legacy_runtime


RECOVERABLE = {"RUNNING", "QUARANTINED", "FAILED", "RECOVERING"}


def recover_local_mock(profile: dict, state_dir: Path, *, allow_write: bool,
                       confirm_run_id: str) -> dict:
    if (profile['entrypoint']['kind'] != 'mock' or profile['environment']['tier'] != 'local'):
        raise RunStateError('recovery is restricted to local mock Runs; remote recovery is not admitted')
    require_resolved(profile)
    if not state_dir.is_dir() or state_dir.is_symlink():
        raise RunStateError('recovery requires an existing regular Run directory')
    require_no_legacy_runtime(state_dir)
    state = read(state_dir / 'run.json')
    digest = require_profile_binding(state, profile)
    if allow_write is not True or confirm_run_id != state.get('run_id'):
        raise RunStateError('recovery requires --allow-write and exact --confirm-run-id')
    if state.get('status') not in RECOVERABLE or state.get('adapter_kind') != 'mock':
        raise RunStateError('only interrupted or failed local mock Runs can be recovered')
    if state.get('execution_contract_required') is not True:
        raise RunStateError('recovery requires the persisted execution contract')
    frozen_manifest(state_dir, state)
    ledger_path = state_dir / 'run.sqlite'
    if ledger_path.is_symlink() or Path(state['ledger_path']).resolve() != ledger_path.resolve():
        raise RunStateError('recovery ledger must be the current Run journal')
    run_id = state['run_id']
    # from_file obtains the command guard without stealing, renewing or creating
    # a reservation. An active command, missing lock or foreign owner blocks.
    with PoolLease.from_file(bound_pool_path(state), run_id, digest) as lease:
        current = read(state_dir / 'run.json')
        if current != state:
            raise RunStateError('Run changed while acquiring recovery command ownership')
        lease.require_held()
        with Ledger(ledger_path, run_id, digest, read_only=True) as ledger:
            require_execution_safe(ledger, required=True)
            adapter = AuthorizedAdapter(PersistentLocalMockAdapter(profile, state_dir), lease)
            try:
                verify_scope(ledger, adapter)
            except CleanupError:
                raise RunStateError("recovery independent scope verification failed") from None
            plan = ledger.cleanup_plan()
            if plan['status'] != 'PLANNED' or plan['retained_handles']:
                raise RunStateError('recovery resource scope remains unresolved')
        recovery_id = uuid.uuid4().hex
        path = state_dir / f'recovery-{recovery_id}.json'
        receipt = {'schema_version': 1, 'status': 'RECOVERING', 'run_id': run_id,
                   'profile_digest': digest, 'recovery_id': recovery_id,
                   'original_status': state.get('recovery_origin_status', state['status']),
                   'original_executor_digest': state['executor_digest'],
                   'recovery_executor_digest': executor_digest(),
                   'scope': 'LOCAL_MOCK_TERMINAL_RESOURCE_CLEANUP',
                   'functional_result': 'UNCHANGED', 'full_release_status': 'NOT_QUALIFIED',
                   'pool': 'RETAINED', 'ready_to_execute': False}
        started = False
        try:
            write(path, receipt)
            state.update(status='RECOVERING', recovery_origin_status=receipt['original_status'],
                         last_recovery=path.name)
            started = True
            write(state_dir / 'run.json', state)
            with Ledger(ledger_path, run_id, digest) as ledger:
                require_execution_safe(ledger, required=True)
                cleanup = execute_cleanup(ledger, adapter, dry_run=False)
                receipt['cleanup'] = cleanup
                if cleanup['status'] != 'PASS':
                    receipt['status'] = 'BLOCKED'
                    write(path, receipt)
                    state.update(status='QUARANTINED', quiescence='UNKNOWN', cleanup_status='BLOCKED')
                    write(state_dir / 'run.json', state)
                    return receipt
                require_execution_safe(ledger, required=True)
                adapter.require_active()
                # Neither old report-*.json nor the original cleanup.json is
                # overwritten. Recovery success never repairs a functional FAIL.
                receipt.update(status='CLEANUP_VERIFIED', pool='RELEASE_PENDING')
                write(path, receipt)
                state.update(status='RECOVERING', recovery_phase='RELEASE_PENDING',
                             quiescence='CONFIRMED', cleanup_status='PASS')
                write(state_dir / 'run.json', state)
                require_execution_safe(ledger, required=True)
                adapter.require_active()
                lease.release()
            receipt.update(status='RECOVERED', pool='RELEASED')
            write(path, receipt)
            state.update(status='RECOVERED', recovery_phase='COMPLETED')
            write(state_dir / 'run.json', state)
            return receipt
        except BaseException:
            if started:
                state.update(status='QUARANTINED', quiescence='UNKNOWN')
                write(state_dir / 'run.json', state)
            raise
