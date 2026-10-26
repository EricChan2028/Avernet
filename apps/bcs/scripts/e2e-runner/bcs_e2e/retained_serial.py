"""Single-host retained scheduling, separate from remote quiescence and cleanup.

The persistent pool guard spans fresh preflight, reservation transfer and business
execution. Transfers append an intent before replacing the scheduling reservation;
old Run artifacts and quota accounting are never modified. An interrupted transfer
is not retried automatically. This is not cross-host or server-side fencing.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import time
import uuid

from .inventory import frozen_manifest
from .pool import PoolBusyError, PoolLease, PoolLeaseError, bound_pool_path, pool_path, pool_scope
from .profile import ConfigError, fingerprint, identifier
from .retained_contract import retained_cases, retained_suites
from .profile_transition import ProfileTransition, FILENAME as TRANSITION_FILE, frozen_transition
from .run_state import read, require_profile_binding, write

CONTRACT = 'retained-serial-preflight-v1'


def rejected_before_ws_connect(report: dict) -> bool:
    """Historical rejection evidence is not an open WS or remote quiescence.

    Old transport reports omitted local-close metadata. A finite rejected
    Upgrade with no connect frame is sufficient for scheduling handoff after
    the process guard is acquired; do not rewrite the old failed result.
    """
    evidence = report.get('evidence', [])
    if not isinstance(evidence, list):
        return False
    attempts = [r for r in evidence if isinstance(r, dict) and r.get('operation') == 'session.ws.connect']
    return (len(attempts) == 1 and attempts[0].get('outcome') == 'ERROR'
            and attempts[0].get('phase') == 'upgrade'
            and type(attempts[0].get('http_status')) is int
            and attempts[0]['http_status'] in (301, 302, 303, 307, 308, 401, 403)
            and attempts[0].get('connected') is False
            and 'connect_request_id' in attempts[0] and attempts[0]['connect_request_id'] is None)


class SerialAdmission:
    def __init__(self, profile: dict, run_id: str, state_dir: Path, previous_state_dir: Path | None,
                 *, transition: ProfileTransition | None = None):
        identifier(run_id, 'serial Run ID')
        if profile['policy']['exclusive_use_confirmed'] is not True:
            raise ConfigError('serial retained admission requires designated-runner exclusive use')
        self.profile, self.run_id = profile, run_id
        self.transition = transition
        if transition is not None and previous_state_dir is None:
            raise ConfigError('profile transition requires the exact previous state directory')
        self.state_dir, self.previous_state_dir = state_dir, previous_state_dir
        self.path = pool_path(pool_scope(profile))
        self.guard = -1
        self.lease = None
        self.previous = None
        self.previous_state = None
        self.previous_report = None

    def __enter__(self):
        self.guard = PoolLease._guard(self.path)
        try:
            self.require_guard()
            if self.path.exists() or self.path.is_symlink():
                if self.previous_state_dir is None:
                    raise PoolBusyError('retained reservation exists; supply its exact previous state directory for serial handoff')
                state = read(self.previous_state_dir / 'run.json')
                previous_profile = self.profile if self.transition is None else self.transition.previous_profile
                if self.transition is not None:
                    self.transition.check(self.profile, self.run_id, state.get('run_id'))
                require_profile_binding(state, previous_profile)
                if bound_pool_path(state) != self.path or state.get('run_id') == self.run_id:
                    raise ConfigError('serial handoff requires the same pool and a distinct new Run')
                manifest = frozen_manifest(self.previous_state_dir, state)
                cases = state.get('attempted_cases', [])
                if manifest['suite'] not in retained_suites() or state.get('status') not in ('QUARANTINED', 'RETAINED'):
                    raise ConfigError('previous Run is not a retained scheduling reservation')
                if not cases:
                    report_path = 'retained-run.json'
                    report = read(self.previous_state_dir / report_path)
                    if (report.get('local_trigger_status') != 'NOT_STARTED'
                            or report.get('functional') != 'NOT_RUN' or report.get('persistence') != 'PERSISTED'
                            or report.get('failure') != state.get('preparation_failure')):
                        raise ConfigError('previous Run lacks confirmed pre-execution failure evidence')
                else:
                    if (state.get('finalization_policy') != 'retain' or not isinstance(cases, list)
                            or len(cases) != 1 or cases[0] not in retained_cases(manifest['suite'])
                            or state.get('last_report') != f'report-{cases[0]}.json'):
                        raise ConfigError('previous Run has no completed retained command record; inspect local trigger ownership')
                    report_path = state['last_report']
                    report = read(self.previous_state_dir / report_path)
                    if (report.get('case_id') != cases[0]
                            or (report.get('socket_status') not in ('NOT_OPENED', 'CLOSED')
                                and not rejected_before_ws_connect(report))
                            or report.get('cleanup') != 'RETAINED'):
                        raise ConfigError('previous retained command or local connection finalization is unconfirmed')
                serial_policy = state.get('admission_policy') == CONTRACT
                if serial_policy:
                    policy_view(previous_profile, state, self.previous_state_dir, require_finalized=bool(cases))
                if (report.get('run_id') != state['run_id']
                        or report.get('profile_digest') != state['profile_digest']
                        or report.get('manifest_digest') != state['manifest_digest']
                        or report.get('executor_digest') != state['executor_digest']
                        or report.get('status') not in (('PASS', 'BLOCKED', 'FAIL', 'CANCELLED') if serial_policy
                                                     else ('BLOCKED', 'FAIL', 'CANCELLED'))):
                    raise ConfigError('previous retained command evidence differs from its Run')
                self.previous_report_path = report_path
                # No test of remote execution_status/quiescence here: unknown
                # historical remote activity is explicitly admitted by D40.
                self.previous = PoolLease._read_payload(self.path, state['run_id'], state['profile_digest'])
                self.previous_state, self.previous_report = state, report
            elif self.previous_state_dir is not None:
                raise ConfigError('previous state supplied but no matching retained reservation exists')
            return self
        except BaseException:
            self.close()
            raise

    def require_guard(self):
        if self.guard < 0:
            raise PoolLeaseError('serial trigger guard is not held')
        held, current = os.fstat(self.guard), self.path.with_suffix('.guard').lstat()
        if (not stat.S_ISREG(current.st_mode)
                or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
            raise PoolLeaseError('serial trigger guard was replaced')
        if self.lease is not None:
            self.lease.require_held()

    def reserve(self, discovery: dict) -> PoolLease:
        self.require_guard()
        if self.lease is not None:
            raise PoolLeaseError('serial reservation cannot be replayed')
        if (discovery.get('status') != 'DISCOVERY_PASSED'
                or discovery.get('profile_digest') != fingerprint(self.profile)):
            raise ConfigError('fresh dependency discovery did not pass for this profile')
        if self.state_dir.exists() or self.state_dir.is_symlink():
            raise ConfigError('serial Run requires a fresh state directory')
        if self.previous is not None:
            if (read(self.previous_state_dir / 'run.json') != self.previous_state
                    or read(self.previous_state_dir / self.previous_report_path) != self.previous_report
                    or PoolLease._read_payload(self.path, self.previous['run_id'], self.previous['profile_digest']) != self.previous):
                raise PoolLeaseError('previous retained reservation changed during preflight')
        elif self.path.exists() or self.path.is_symlink():
            raise PoolLeaseError('reservation appeared during serial preflight')
        transition_snapshot = None
        if self.transition is not None:
            if self.previous is None:
                raise ConfigError('profile transition requires an existing retained reservation')
            transition_snapshot = self.transition.check(self.profile, self.run_id, self.previous['run_id'])
        payload = {'schema_version': 1, 'run_id': self.run_id, 'profile_digest': fingerprint(self.profile),
                   'lease_token': uuid.uuid4().hex, 'acquired_at': int(time.time()),
                   'pid': os.getpid(), 'host': socket.gethostname()}
        # Exclusive immutable intent in the pool registry survives Run-directory
        # failures. Never overwrite a transfer for the same new Run identifier.
        directory = self.path.with_suffix('.serial-history')
        if directory.is_symlink():
            raise PoolLeaseError('serial history directory cannot be a symlink')
        directory.mkdir(mode=0o700, exist_ok=True)
        import hashlib
        receipt = directory / (hashlib.sha256(self.run_id.encode()).hexdigest() + '.json')
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        intent = {'schema_version': 1, 'contract': CONTRACT, 'phase': 'INTENDED',
                  'run_id': self.run_id, 'profile_digest': payload['profile_digest'],
                  'state_dir': str(self.state_dir.resolve()), 'pool_scope': pool_scope(self.profile),
                  'previous_run_id': None if self.previous is None else self.previous['run_id'],
                  'previous_ws_rejected_before_connect': (self.previous_report is not None
                                                         and rejected_before_ws_connect(self.previous_report)),
                  'previous_reservation_digest': None if self.previous is None else fingerprint(self.previous),
                  'next_reservation_digest': fingerprint(payload), 'preflight_digest': fingerprint(discovery),
                  'remote_termination_confirmed': False, 'cleanup_authorized': False}
        if transition_snapshot is not None:
            intent['profile_transition_digest'] = fingerprint(transition_snapshot)
        write(receipt, intent)
        self.require_guard()
        if self.transition is not None:
            self.transition.check(self.profile, self.run_id, self.previous['run_id'])
        # Atomic scheduling transfer, not deletion or reclamation. Any exception
        # leaves the intent/reservation for inspection; no rollback/replay occurs.
        write(self.path, payload)
        self.lease = PoolLease(self.path, self.run_id, payload['profile_digest'], payload['lease_token'],
                               payload['acquired_at'], payload['host'], self.guard)
        self.lease.require_held()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        if transition_snapshot is not None:
            write(self.state_dir / TRANSITION_FILE, transition_snapshot)
        write(self.state_dir / 'serial-admission.json', {
            **intent, 'phase': 'RESERVED_AFTER_PREFLIGHT', 'history_receipt': str(receipt),
            'history_receipt_digest': fingerprint(intent), 'next_run_admission': 'FRESH_CHECK_REQUIRED'})
        write(self.state_dir / 'preflight.json', discovery)
        return self.lease

    def close(self):
        if self.lease is not None:
            self.lease.close()
        elif self.guard >= 0:
            os.close(self.guard)
        self.guard = -1

    def __exit__(self, *_args):
        self.close()


def policy_view(profile: dict, state: dict, state_dir: Path, *, require_finalized: bool = False) -> dict:
    """Validate the versioned admission receipt for offline reporting, not reuse."""
    if state.get('admission_policy') != CONTRACT:
        raise ConfigError('unsupported serial retained admission policy')
    require_profile_binding(state, profile)
    receipt = read(state_dir / 'serial-admission.json')
    if (fingerprint(receipt) != state.get('serial_admission_digest')
            or receipt.get('contract') != CONTRACT or receipt.get('run_id') != state.get('run_id')
            or receipt.get('profile_digest') != fingerprint(profile)
            or receipt.get('state_dir') != str(state_dir.resolve())
            or receipt.get('pool_scope') != pool_scope(profile)
            or receipt.get('phase') != 'RESERVED_AFTER_PREFLIGHT'
            or receipt.get('remote_termination_confirmed') is not False
            or receipt.get('cleanup_authorized') is not False):
        raise ConfigError('serial admission receipt differs from its frozen Run')
    transition = {}
    if 'profile_transition_digest' in receipt:
        transition = frozen_transition(profile, state_dir, receipt)
    elif (state_dir / TRANSITION_FILE).exists() or (state_dir / TRANSITION_FILE).is_symlink():
        raise ConfigError('profile transition artifact is not bound to serial admission')
    discovery = read(state_dir / 'preflight.json')
    if (fingerprint(discovery) != receipt.get('preflight_digest')
            or discovery.get('status') != 'DISCOVERY_PASSED'
            or discovery.get('profile_digest') != fingerprint(profile)):
        raise ConfigError('serial admission lacks its successful bound preflight')
    if require_finalized:
        pending = state_dir / 'serial-finalization-pending.json'
        cases = state.get('attempted_cases')
        if (pending.exists() or pending.is_symlink() or state.get('status') != 'RETAINED'
                or not isinstance(cases, list) or len(cases) != 1
                or cases[0] not in retained_cases(frozen_manifest(state_dir, state)['suite'])
                or state.get('last_report') != f'report-{cases[0]}.json'):
            raise ConfigError('serial retained finalization is incomplete')
        completion = read(state_dir / 'serial-completion.json')
        report = read(state_dir / state['last_report'])
        if (completion.get('contract') != CONTRACT or completion.get('run_id') != state['run_id']
                or completion.get('state_digest') != fingerprint(state)
                or completion.get('report_digest') != fingerprint(report)):
            raise ConfigError('serial retained completion differs from its final Run/Case')
    # Receipt is self-contained for historical reporting; no read of the current
    # reservation, Cookie, remote dependency or mutable pool is performed here.
    return {'contract': CONTRACT, 'preflight': 'PASSED_AT_ADMISSION', **transition,
            'previous_run_id': receipt.get('previous_run_id'),
            'next_run_admission': 'FRESH_CHECK_REQUIRED', 'execution_closure': 'DIAGNOSTIC',
            'cleanup_authorized': False, 'current_trigger_authority': 'NOT_RECHECKED'}
