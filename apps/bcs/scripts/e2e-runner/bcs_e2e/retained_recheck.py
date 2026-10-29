"""Read-only continuation of evidence collection, never continuation of a Case."""
from __future__ import annotations

from pathlib import Path
import signal
import time

from .case_scope import CaseScopedAdapter
from .errors import has_cancellation, primary_error
from .inventory import executor_digest, frozen_manifest
from .ledger import Ledger
from .pool import PoolLease, bound_pool_path
from .profile import ConfigError, fingerprint
from .recheck_review import PLAN_CONTRACT, RetainedRecheckReview
from .remote_observer import remote_check_dependencies
from .retained_remote import RetainedMcpClient
from .run_state import read, write


def retained_recheck(profile: dict, *, state_dir: Path, run_id: str, case_id: str,
                     review_path: Path, observer_binding: Path | None, executable: Path,
                     allow_observer_read: bool, confirm_run_id: str, mapping_confirmation: Path | None = None) -> dict:
    if allow_observer_read is not True or confirm_run_id != run_id:
        raise ConfigError('retained recheck requires explicit observer reads and exact Run confirmation')
    review = RetainedRecheckReview(profile, state_dir, run_id, case_id, review_path)
    initial_review = review.validate()
    planned = initial_review['contract'] == PLAN_CONTRACT
    if planned:
        if mapping_confirmation is None or observer_binding is not None:
            raise ConfigError('v2 read plan requires a mapping confirmation, not a legacy query receipt')
        from .mapping_recheck import mapping_dependencies
        client, binding = mapping_dependencies(profile, mapping_confirmation, review,
                                               executable=executable, allow_read=allow_observer_read)
        binding_key = 'mapping_confirmation_digest'
    else:
        if observer_binding is None or mapping_confirmation is not None:
            raise ConfigError('legacy recheck requires its expiring observer binding; no implicit migration')
        client, binding = remote_check_dependencies(profile, observer_binding, executable=executable, allow_read=True)
        binding_key = 'observer_binding_digest'
    if fingerprint(binding.validate()) != initial_review[binding_key]:
        raise ConfigError('environment binding differs from the selected recheck plan')
    state = read(state_dir / 'run.json')
    guard = bound_pool_path(state).with_suffix('.guard')
    if not guard.is_file() or guard.is_symlink():
        raise ConfigError('recheck requires the existing command guard; never recreate it')
    guard_identity = (guard.stat().st_dev, guard.stat().st_ino)
    with PoolLease.from_file(bound_pool_path(state), run_id, fingerprint(profile), existing_guard_only=True) as lease:
        lease.require_held()
        if (guard.stat().st_dev, guard.stat().st_ino) != guard_identity:
            raise ConfigError('recheck command guard changed during acquisition')
        review.bind_authority(lambda: (lease.require_held(), binding.validate()))
        review.validate()
        directory = state_dir / 'observations'
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ConfigError('recheck evidence directory must not be a symlink or file')
        directory.mkdir(mode=0o700, exist_ok=True)
        output = directory / review.observation_id
        # One receipt/observation ID is one attempt; failure leaves its directory
        # reserved. No overwrite, implicit retry or original artifact replacement.
        output.mkdir(mode=0o700, exist_ok=False)
        output_identity = (output.stat().st_dev, output.stat().st_ino)
        report = {'schema_version': 1, 'status': 'BLOCKED', 'ready_to_execute': False,
                  'run_id': run_id, 'case_id': case_id, 'observation_id': review.observation_id,
                  'kind': 'RETAINED_EXECUTION_READ_ONLY', 'started_at': int(time.time()),
                  'source': initial_review['source'], 'profile_digest': fingerprint(profile),
                  'observer_binding': binding.summary,
                  ('read_plan_digest' if planned else 'recheck_review_digest'): review.digest,
                  'observer_executor_digest': executor_digest(), 'original_executor_digest': state['executor_digest'],
                  'original_result_unchanged': True, 'execution_status': 'UNCONFIRMED',
                  'pool_disposition': 'QUARANTINED', 'pool_release_authorized': False,
                  'read_authorization_basis': 'EXPLICIT_COMMAND_FLAGS_AND_SOURCE_PLAN' if planned else 'EXPIRING_REVIEW',
                  'business_writes': 0, 'execution_observation': {'status': 'NOT_RUN', 'scope_status': 'UNCONFIRMED'},
                  'persistence': 'PENDING', 'errors': [], 'output_dir': str(output)}
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        cancel_state = {'requested': False, 'finalizing': False}

        def cancel(_sig, _frame):
            first = not cancel_state['requested']
            cancel_state['requested'] = True
            if first and not cancel_state['finalizing']:
                raise KeyboardInterrupt()

        def check_output():
            if (directory.is_symlink() or output.is_symlink()
                    or (output.stat().st_dev, output.stat().st_ino) != output_identity):
                raise ConfigError('recheck evidence directory changed')

        def active():
            check_output()
            lease.require_held()
            review.validate()
            binding.validate()

        try:
            for sig in previous:
                signal.signal(sig, cancel)
            write(output / ('read-plan.json' if planned else 'recheck-review.json'), initial_review)
            write(output / ('mapping-confirmation.json' if planned else 'observer-binding.json'), binding.validate())
            write(output / 'report.json', report)
            with Ledger(state_dir / 'run.sqlite', run_id, fingerprint(profile), read_only=True) as ledger:
                definition = next(c for c in frozen_manifest(state_dir, state)['cases'] if c['id'] == case_id)
                scoped = CaseScopedAdapter(object(), ledger, fingerprint(profile), definition, attempt=1)
                # This client exposes only bounded reads, not the product adapter.
                observer = RetainedMcpClient(client, review).execution_observer(profile)
                observer.admit()
                active()
                report['execution_observation'] = observer.observe(
                    ledger, scoped.observation_scope, active, lease=lease, case_id=case_id,
                    timeout_seconds=profile['policy']['timeout_seconds'])
                if report['execution_observation'].get('child_shutdown_status') == 'UNCONFIRMED':
                    report['child_shutdown_status'] = 'UNCONFIRMED'
                active()
        except (Exception, KeyboardInterrupt) as error:
            cancel_state['requested'] = cancel_state['requested'] or has_cancellation(error)
            partial = getattr(primary_error(error), 'execution_observation', None)
            if isinstance(partial, dict):
                report['execution_observation'] = partial
            report['errors'].append({'error_type': type(primary_error(error)).__name__})
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                report['child_shutdown_status'] = 'UNCONFIRMED'
        finally:
            cancel_state['finalizing'] = True
            try:
                # Local publication remains possible after remote authority loss.
                # No source file, reservation or original report is changed.
                report['status'] = 'CANCELLED' if cancel_state['requested'] else 'BLOCKED'
                check_output()
                report['persistence'] = 'SAVED'
                report['finished_at'] = int(time.time())
                write(output / 'report.json', report)
                if cancel_state['requested'] and report['status'] != 'CANCELLED':
                    report['status'] = 'CANCELLED'
                    write(output / 'report.json', report)
            except (Exception, KeyboardInterrupt) as error:
                cancel_state['requested'] = cancel_state['requested'] or has_cancellation(error)
                report['persistence'] = 'FAILED'
                report['errors'].append({'error_type': type(error).__name__})
            finally:
                if cancel_state['requested']:
                    report['status'] = 'CANCELLED'
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        return report
