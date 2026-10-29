"""Foreground remote pilot; manages only its reports, never the target service."""
from __future__ import annotations

from pathlib import Path
import signal

from .errors import primary_error
from .inventory import current_manifest, executor_digest
from .pilot import run_case
from .prepare import prepare_run
from .identity_binding import require_pilot_execution_binding
from .profile import ConfigError, fingerprint, identifier
from .remote_check import check_remote
from .remote_observer import remote_check_dependencies, remote_dependencies
from .reporting import build_report, write_reports
from .run_state import read, write

CASES = ('PRE-01', 'PRE-02', 'PRE-03')


def remote_run(profile: dict, *, state_dir: Path, run_id: str, receipt: Path, executable: Path,
               allow_http_read: bool, allow_observer_read: bool, allow_write: bool,
               confirm_run_id: str) -> dict:
    require_pilot_execution_binding(profile)
    identifier(run_id, 'remote Run ID')
    if (allow_http_read is not True or allow_observer_read is not True
            or allow_write is not True or confirm_run_id != run_id):
        raise ConfigError('remote-run requires HTTP/MCP read and Story write permission plus exact Run confirmation')
    if state_dir.exists() or state_dir.is_symlink():
        raise ConfigError('remote-run requires a fresh state directory; existing artifacts must be preserved')
    # Validate target/review/executable before claiming a directory or contacting
    # either service. Constructing these dependencies does not spawn mcporter.
    remote_check_dependencies(profile, receipt, executable=executable, allow_read=allow_observer_read)
    manifest = current_manifest()
    if tuple(case['id'] for case in manifest['cases']) != CASES:
        raise ConfigError('remote-run inventory differs from the approved three pilot Stories')
    artifact = executor_digest()
    state_dir = state_dir.resolve()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    cancellation = {'requested': False, 'signal': None}
    finalizing = sealed = claimed = prepared = False
    reports, outputs, finalization_errors = [], {}, []
    preflight = None
    summary = None
    failure = None
    primary = 'BLOCKED'
    phase = 'state-directory'

    def cancel(signum, _frame):
        if sealed:
            return
        first = not cancellation['requested']
        cancellation.update(requested=True, signal=cancellation['signal'] or signal.Signals(signum).name)
        if first and not finalizing and not (reports and reports[-1]['status'] != 'PASS'):
            raise KeyboardInterrupt()

    def record_error(error, failed_phase):
        cause = primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            cancellation['requested'] = True
        result = {'phase': failed_phase, 'error_type': type(cause).__name__}
        if any(getattr(item, 'child_shutdown_status', None) == 'UNCONFIRMED' for item in (error, cause)):
            result['child_shutdown_status'] = 'UNCONFIRMED'
        return result

    def outcome():
        if primary == 'CANCELLED' or (primary == 'PASS' and cancellation['requested']):
            return 'CANCELLED'
        if (primary == 'PASS' and failure is None and not finalization_errors
                and summary is not None and summary['status'] == 'PILOT_PASS'):
            return 'REMOTE_PILOT_PASS'
        return 'BLOCKED'

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        claimed = True
        write(state_dir / 'profile.json', profile)
        phase = 'preflight'
        preflight = check_remote(profile, receipt=receipt, executable=executable,
                                 allow_http_read=allow_http_read, allow_observer_read=allow_observer_read)
        write(state_dir / 'preflight.json', preflight)
        if preflight['status'] == 'DISCOVERY_PASSED':
            if cancellation['requested']:
                raise KeyboardInterrupt()
            phase = 'prepare'
            prepare_run(profile, state_dir, run_id, manifest=manifest, observer_binding=receipt)
            prepared = True
            state = read(state_dir / 'run.json')
            if state['observer_binding'] != preflight['observer_binding']:
                raise ConfigError('mapping review changed between preflight and Run preparation')
            phase = 'observer-composition'
            client, binding = remote_dependencies(profile, state_dir, run_id, executable=executable,
                                                  allow_read=allow_observer_read)
            for case_id in CASES:
                if cancellation['requested']:
                    raise KeyboardInterrupt()
                phase = case_id
                report = run_case(profile, state_dir, run_id, case_id,
                                  observer_client=client, physical_binding_probe=binding)
                reports.append(report)
                if report['status'] != 'PASS':
                    primary = report['status']
                    break
                if cancellation['requested']:
                    primary = 'CANCELLED'
                    break
            else:
                primary = 'PASS'
    except KeyboardInterrupt as error:
        cancellation['requested'] = True
        primary = 'CANCELLED'
        failure = record_error(error, phase)
    except Exception as error:
        failure = record_error(error, phase)
        if cancellation['requested']:
            primary = 'CANCELLED'
    finally:
        finalizing = True
        try:
            # Initial installation may have been interrupted between signals.
            # Cover both before result/report construction, as in local-run.
            for sig in previous:
                signal.signal(sig, cancel)
            if prepared:
                try:
                    summary = build_report(profile, state_dir, manifest)
                    outputs = write_reports(summary, state_dir / 'reports', {'all'})['outputs']
                except (Exception, KeyboardInterrupt) as error:
                    finalization_errors.append(record_error(error, 'reports'))
            actual = {report['case_id']: report for report in reports}
            attempted = []
            if prepared:
                try:
                    attempted = read(state_dir / 'run.json').get('attempted_cases', [])
                except (Exception, KeyboardInterrupt) as error:
                    finalization_errors.append(record_error(error, 'run-state'))
                    attempted = [phase] if phase in CASES else []
            cases = []
            for case in CASES:
                unknown = case in attempted and case not in actual
                missing = {'status': ('CANCELLED' if primary == 'CANCELLED' else 'BLOCKED') if unknown else 'NOT_RUN',
                           'functional': 'UNKNOWN' if unknown else 'NOT_RUN',
                           'cleanup': 'UNKNOWN' if unknown else 'NOT_RUN',
                           'evidence_status': 'UNKNOWN' if unknown else 'NOT_RUN'}
                entry = actual.get(case, missing)
                cases.append({'id': case, **{key: entry[key] for key in ('status', 'functional', 'cleanup', 'evidence_status')}})
            result = {'schema_version': 1, 'status': outcome(), 'ready_to_execute': False,
                      'run_id': run_id, 'profile_digest': fingerprint(profile), 'executor_digest': artifact,
                      'state_dir': str(state_dir), 'primary_status': primary,
                      'pilot_status': summary['status'] if summary is not None else 'NOT_RUN',
                      'full_release_status': 'NOT_QUALIFIED',
                      'preflight_status': preflight['status'] if preflight is not None else 'NOT_RUN',
                      'prepared': prepared,
                      'preparation_status': 'CONFIRMED' if prepared else 'UNCONFIRMED' if phase == 'prepare' else 'NOT_STARTED',
                      'manifest_digest': fingerprint(manifest),
                      'cancellation': dict(cancellation),
                      'failure': failure, 'finalization_errors': finalization_errors,
                      'cases': cases,
                      'reports': outputs,
                      'target_lifecycle': 'NOT_MANAGED',
                      'recovery': 'No automatic remote recovery or force-release; preserve failed Run and involve its cleanup owner.'}
            written = False
            if claimed:
                try:
                    write(state_dir / 'remote-run.json', result)
                    written = True
                except (Exception, KeyboardInterrupt) as error:
                    finalization_errors.append(record_error(error, 'lifecycle-receipt'))
            # Signals during report/receipt finalization become a monotonic
            # non-success outcome, not repeated interrupts of the same cleanup.
            sealed = True
            if result['status'] != outcome() or result['cancellation'] != cancellation:
                result.update(status=outcome(), cancellation=dict(cancellation))
                if written:
                    try:
                        write(state_dir / 'remote-run.json', result)
                    except (Exception, KeyboardInterrupt) as error:
                        finalization_errors.append(record_error(error, 'cancellation-receipt'))
                        result.update(status=outcome(), cancellation=dict(cancellation))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return result
