"""Foreground, fresh-runtime pilot lifecycle; never attaches to an existing BCS."""
from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess

from .errors import primary_error as _primary_error
from .inventory import executor_digest, freeze_manifest
from .local_adapter import OwnedLocalPilotAdapter, resolve_owned_profile
from .local_fixtures import OwnedLocalFixtures
from .local_logs import OwnedLocalLogObserver
from .log_observer import DeferredLogObserver
from .local_cli_check import CliCheckedLocalAdapter, LocalCliCheck
from .owned_bcs import OwnedBcsProcess
from .pilot import run_case
from .prepare import prepare_run
from .profile import ConfigError, identifier, fingerprint
from .pilot import evaluate
from .reporting import build_report, write_reports
from .run_state import write

ROOT = Path(__file__).resolve().parents[1]
CASES = ('PRE-01', 'PRE-02', 'PRE-03')


def _runner_revision() -> dict:
    root = ROOT.parents[3]
    try:
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                                      stderr=subprocess.DEVNULL, timeout=5, text=True).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root,
                                            stderr=subprocess.DEVNULL, timeout=5))
        return {'commit': sha, 'dirty': dirty}
    except (OSError, subprocess.SubprocessError):
        return {'commit': 'unknown', 'dirty': 'unknown'}


def _local_logs_result(selected: bool, reports: list[dict]) -> dict:
    if not selected:
        return {'status': 'NOT_SELECTED'}
    statuses = {report['case_id']: report.get('diagnostic_status', 'NOT_RUN') for report in reports}
    cases = [{'case_id': case, 'status': statuses.get(case, 'NOT_RUN')} for case in CASES]
    status = ('PASS' if all(case['status'] == 'PASS' for case in cases) else
              'NOT_RUN' if not reports else 'INCOMPLETE')
    return {'status': status, 'required': False, 'source': 'owned-local-access-json',
            'scope': 'exact request ID and Case time window; not business or trace qualification',
            'cases': cases}


def local_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
              allow_local_processes: bool = False, allow_fixture_write: bool = False,
              allow_write: bool = False, confirm_run_id: str = '', port: int = 0,
              startup_timeout: int = 30, stop_timeout: int = 15,
              cli_binary: Path | None = None, cli_binary_sha256: str | None = None,
              collect_local_logs: bool = False) -> dict:
    """Always stop the live child; failed/unknown resources and leases are retained."""
    identifier(run_id, 'local run ID')
    if type(collect_local_logs) is not bool:
        raise ConfigError('local log selection must be boolean')
    if (allow_local_processes is not True or allow_fixture_write is not True
            or allow_write is not True or confirm_run_id != run_id):
        raise ConfigError('local-run requires process, fixture and Story write permission plus exact Run confirmation')
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('local-run requires a fresh state directory; existing data must be preserved')
    if (cli_binary is None) != (cli_binary_sha256 is None):
        raise ConfigError("local-run product CLI requires both binary and SHA256")
    cli_check = LocalCliCheck(cli_binary, cli_binary_sha256) if cli_binary is not None else None
    state_dir = state_dir.resolve()
    runtime = OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                              root=state_dir / 'runtime', run_id=run_id, port=port,
                              startup_timeout=startup_timeout, stop_timeout=stop_timeout)
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    if tuple(case['id'] for case in manifest['cases']) != CASES:
        raise ConfigError('local-run pilot inventory differs from the approved three Stories')
    executor_digest()  # Refuse stale already-imported code before runtime/fixture writes.
    revision = _runner_revision()
    primary = 'BLOCKED'
    failure = None
    finalization_errors = []
    phase = 'signal-handlers'
    reports = []
    profile = None
    prepared = False
    started = False
    summary = None
    outputs = {}
    claimed = False
    finalizing = False
    accepting_cancellation = True
    cancellation = {'requested': False, 'signal': None}
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    def cancelled(signum, _frame):
        if not accepting_cancellation:
            return  # The durable outcome is sealed; restore caller handlers next.
        first = not cancellation['requested']
        cancellation.update(requested=True, signal=cancellation['signal'] or signal.Signals(signum).name)
        # Startup may be inside its own cleanup before the outer finally runs.
        # Preserve that primary error and allow its one bounded reap to finish.
        if (first and not finalizing and not runtime.stop_attempted
                and not (reports and reports[-1]['status'] != 'PASS')):
            raise KeyboardInterrupt()
    try:
        for sig in previous:
            signal.signal(sig, cancelled)
        phase = 'state-directory'
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        claimed = True
        phase = 'inventory'
        manifest_digest = freeze_manifest(state_dir, manifest)
        if cli_check is not None:
            cli_check.result.update(run_id=run_id, manifest_digest=manifest_digest,
                                    executor_digest=executor_digest())
            cli_check.persist(state_dir)
        phase = 'startup'
        runtime.start()
        started = True
        phase = 'fixtures'
        fixtures = OwnedLocalFixtures(runtime)
        if cli_check is None:
            fixtures.apply(allow_write=True)
        else:
            fixtures.apply(allow_write=True, driver_token_sink=cli_check.capture_driver_token)
        phase = 'profile'
        profile = resolve_owned_profile(runtime)
        write(state_dir / 'profile.json', profile)
        phase = 'prepare'
        prepare_run(profile, state_dir, run_id, manifest=manifest)
        prepared = True
        if cli_check is not None:
            cli_check.result['profile_digest'] = fingerprint(profile)
            cli_check.persist(state_dir)
        def factory(current, *, state_dir, case_id, **_kwargs):
            if cli_check is not None and case_id == 'PRE-02':
                return CliCheckedLocalAdapter(runtime, current, state_dir=state_dir, case_id=case_id,
                                              cli_check=cli_check)
            return OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
        for case_id in CASES:
            phase = case_id
            log_observer = (DeferredLogObserver(lambda selected_case=case_id: OwnedLocalLogObserver(
                            runtime, ledger_path=state_dir / 'run.sqlite', profile_digest=fingerprint(profile),
                            case_id=selected_case)) if collect_local_logs else None)
            report = run_case(profile, state_dir, run_id, case_id, adapter_factory=factory, log_observer=log_observer)
            reports.append(report)
            if report['status'] != 'PASS':
                primary = report['status']
                break  # The failed pool must not be reused by the next Story.
            if cli_check is not None and case_id == 'PRE-02' and not cli_check.passed:
                primary = 'BLOCKED'
                break  # Finish PRE-02 cleanup, but do not begin PRE-03 after a required CLI failure.
        else:
            primary = 'PASS'
    except KeyboardInterrupt as error:
        finalizing = True
        cancellation['requested'] = True
        cause = _primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            cancellation['requested'] = True
            primary = 'CANCELLED'
        failure = {'phase': phase, 'error_type': type(cause).__name__}
    except Exception as error:
        finalizing = True
        # Never echo private URLs, tokens, response payloads or subprocess output.
        cause = _primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            cancellation['requested'] = True
            primary = 'CANCELLED'
        failure = {'phase': phase, 'error_type': type(cause).__name__}
        if cause is not error:
            failure['surfaced_error_type'] = type(error).__name__
    finally:
        finalizing = True
        if cli_check is not None:
            cli_check.forget_credentials()
        try:
            # Include partially completed handler installation in restoration.
            for sig in previous:
                signal.signal(sig, cancelled)
            # Startup already attempts its own reaping on failure. Do not repeat
            # an attempted stop, even if it failed; preserve unresolved shutdown.
            if not runtime.stop_attempted and (started or runtime.child is not None):
                try:
                    runtime.stop()
                except Exception as error:
                    finalization_errors.append({'phase': 'shutdown', 'error_type': type(error).__name__})
            if prepared:
                try:
                    summary = build_report(profile, state_dir, manifest)
                    outputs = write_reports(summary, state_dir / 'reports', {'all'})['outputs']
                except Exception as error:
                    finalization_errors.append({'phase': 'reports', 'error_type': type(error).__name__})
            shutdown = runtime._record()
            shutdown['stop_attempted'] = runtime.stop_attempted
            if runtime.child is not None and runtime.child.poll() is None:
                shutdown['status'] = 'UNCONFIRMED'
                shutdown['ready'] = False
            success = (primary == 'PASS' and not cancellation['requested'] and failure is None and not finalization_errors
                       and shutdown['status'] == 'STOPPED' and summary is not None
                       and summary['status'] == 'PILOT_PASS'
                       and (cli_check is None or cli_check.passed))
            result = {'schema_version': 1,
                      'status': 'LOCAL_PILOT_PASS' if success else 'CANCELLED'
                                if primary == 'CANCELLED' or (primary == 'PASS' and cancellation['requested']) else 'BLOCKED',
                      'run_id': run_id, 'ready_to_execute': False, 'primary_status': primary,
                      'pilot_status': summary['status'] if summary is not None else 'NOT_RUN',
                      'full_release_status': evaluate(summary)['status'] if summary is not None else 'NOT_QUALIFIED',
                      'runner': revision, 'binary_sha256': binary_sha256,
                      'state_dir': str(state_dir), 'runtime_dir': str(runtime.root),
                      'completed_cases': [{'id': report['case_id'], 'status': report['status'],
                                           'functional': report['functional'], 'cleanup': report['cleanup']}
                                          for report in reports],
                      'failure': failure, 'cancellation': dict(cancellation),
                      'finalization_errors': finalization_errors,
                      'shutdown': shutdown, 'reports': outputs,
                      'product_cli': dict(cli_check.result) if cli_check is not None else {'status': 'NOT_SELECTED'},
                      'local_logs': _local_logs_result(collect_local_logs, reports),
                      'retention': 'private runtime data retained; no recursive deletion or saved-PID adoption',
                      'not_verified': ['Gateway/SSO', 'Bot model/runtime', 'BaaS/OB', 'full release']}
            # If the initial directory claim failed, do not write into an unowned root.
            receipt_written = False
            if claimed:
                try:
                    write(state_dir / 'local-run.json', result)
                    receipt_written = True
                except Exception as error:
                    finalization_errors.append({'phase': 'lifecycle-receipt', 'error_type': type(error).__name__})
                    if result['status'] != 'CANCELLED':
                        result['status'] = 'BLOCKED'
                    # Return the intact primary outcome to stdout; no blind retry.
            accepting_cancellation = False
            sealed_status = ('CANCELLED' if primary == 'CANCELLED'
                             or (primary == 'PASS' and cancellation['requested'])
                             else 'LOCAL_PILOT_PASS' if primary == 'PASS' and failure is None
                             and not finalization_errors and shutdown['status'] == 'STOPPED'
                             and summary is not None and summary['status'] == 'PILOT_PASS'
                             and (cli_check is None or cli_check.passed)
                             else 'BLOCKED')
            if result['cancellation'] != cancellation or result['status'] != sealed_status:
                result['cancellation'] = dict(cancellation)
                result['status'] = sealed_status
                # A signal during a successful receipt write requires one
                # monotonic outcome update, not a retry of a failed write.
                if receipt_written:
                    try:
                        write(state_dir / 'local-run.json', result)
                    except Exception as error:
                        finalization_errors.append({'phase': 'cancellation-receipt', 'error_type': type(error).__name__})
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return result
