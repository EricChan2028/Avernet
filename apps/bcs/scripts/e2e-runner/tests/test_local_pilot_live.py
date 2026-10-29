"""Opt-in shared Stories against a real BCS, not a fake HTTP response server."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.local_adapter import OwnedLocalPilotAdapter, resolve_owned_profile
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import fingerprint
from bcs_e2e.reporting import build_report, write_reports
from bcs_e2e.pilot import evaluate
from bcs_e2e.run_state import read, write

BINARY = os.environ.get('BCS_E2E_TEST_BINARY', '')
DIGEST = os.environ.get('BCS_E2E_TEST_BINARY_SHA256', '')
CLI_BINARY = os.environ.get('BCS_E2E_TEST_CLI_BINARY', '')
CLI_DIGEST = os.environ.get('BCS_E2E_TEST_CLI_BINARY_SHA256', '')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'), 'live BCS binary/SHA256/lsof required')
class LocalPilotLiveTests(unittest.TestCase):
    def test_real_cli_local_run_generates_profile_reports_and_stops_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'state'
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                rc = main(['local-run', '--binary', BINARY, '--binary-sha256', DIGEST,
                           '--state-dir', str(state), '--run-id', 'live-cli-pilot',
                           '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                           '--confirm-run-id', 'live-cli-pilot'])
            self.assertEqual(rc, 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], 'LOCAL_PILOT_PASS')
            self.assertEqual(result['pilot_status'], 'PILOT_PASS')
            self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
            self.assertEqual(result['product_cli'], {'status': 'NOT_SELECTED'})
            self.assertEqual(result['local_logs'], {'status': 'NOT_SELECTED'})
            self.assertFalse((state / 'product-cli-check.json').exists())
            self.assertEqual(result['shutdown']['status'], 'STOPPED')
            self.assertEqual(result['shutdown']['returncode'], 0)
            self.assertEqual(read(state / 'local-run.json'), result)
            self.assertEqual(len(result['completed_cases']), 3)
            self.assertEqual(set(result['reports']), {'json', 'junit', 'markdown'})
            self.assertTrue((state / 'runtime/bcs.sqlite').is_file())
            self.assertEqual(read(state / 'profile.json')['auth'], {'kind': 'owned-local'})
            self.assertEqual(len(read(state / 'manifest.json')['cases']), 3)
            self.assertFalse(Path(read(state / 'run.json')['lock_path']).exists())
            before = (state / 'local-run.json').read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(['local-run', '--binary', BINARY, '--binary-sha256', DIGEST,
                           '--state-dir', str(state), '--run-id', 'live-cli-pilot',
                           '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                           '--confirm-run-id', 'live-cli-pilot'])
            self.assertEqual(rc, 2)
            self.assertEqual((state / 'local-run.json').read_bytes(), before)

    def test_live_cli_case_cancel_or_cleanup_failure_retains_lease_and_stops_child(self):
        for failure in ('cancel', 'cleanup'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root / 'state'
                operation = 'group_update' if failure == 'cancel' else 'group_delete'
                error = KeyboardInterrupt() if failure == 'cancel' else RuntimeError('injected cleanup refusal')
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'), \
                     patch.object(OwnedLocalPilotAdapter, operation, side_effect=error), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(['local-run', '--binary', BINARY, '--binary-sha256', DIGEST,
                               '--state-dir', str(state), '--run-id', 'live-cli-fault',
                               '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                               '--confirm-run-id', 'live-cli-fault'])
                self.assertEqual(rc, 130 if failure == 'cancel' else 2, output.getvalue())
                result = json.loads(output.getvalue())
                self.assertEqual(result['shutdown']['status'], 'STOPPED')
                self.assertEqual(result['shutdown']['returncode'], 0)
                run_state = read(state / 'run.json')
                self.assertTrue(Path(run_state['lock_path']).exists())
                self.assertEqual(run_state['attempted_cases'], ['PRE-01'])
                self.assertEqual(run_state['status'], 'QUARANTINED' if failure == 'cancel' else 'FAILED')
                self.assertNotEqual(result['pilot_status'], 'PILOT_PASS')
                self.assertEqual(read(state / 'local-run.json'), result)
                self.assertTrue((state / 'reports/summary.json').is_file())
                if failure == 'cleanup':
                    report = read(state / 'report-PRE-01.json')
                    self.assertEqual(report['functional'], 'PASS')
                    self.assertEqual(report['cleanup'], 'BLOCKED')

    def test_required_observer_chained_cancellation_quarantines_run_and_stops_child(self):
        from bcs_e2e.local_sqlite import OwnedSqliteEvidenceProvider
        def interrupted(*args, **kwargs):
            try:
                raise KeyboardInterrupt()
            finally:
                raise OSError('private-observer-secondary')
        for operation in ('admit', 'observe'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); state = root / 'state'
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'), \
                     patch.object(OwnedSqliteEvidenceProvider, operation, side_effect=interrupted), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(['local-run', '--binary', BINARY, '--binary-sha256', DIGEST,
                               '--state-dir', str(state), '--run-id', 'live-observer-cancel',
                               '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                               '--confirm-run-id', 'live-observer-cancel'])
                result = json.loads(output.getvalue())
                self.assertEqual(rc, 130, result)
                self.assertEqual(result['status'], 'CANCELLED')
                self.assertEqual(result['shutdown']['status'], 'STOPPED')
                self.assertEqual(result['shutdown']['returncode'], 0)
                self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
                self.assertNotIn('private-observer-secondary', json.dumps(result))
                run_state = read(state / 'run.json')
                self.assertEqual(run_state['status'], 'QUARANTINED')
                self.assertEqual(run_state['attempted_cases'], ['PRE-01'])
                self.assertTrue(Path(run_state['lock_path']).exists())
                with Ledger(state / 'run.sqlite', 'live-observer-cancel', run_state['profile_digest'],
                            read_only=True) as ledger:
                    self.assertEqual(len(ledger.resource_snapshot()), 0 if operation == 'admit' else 2)
                    self.assertEqual(len(ledger.cleanup_plan()['resources']), 0 if operation == 'admit' else 2)
                    self.assertFalse(any(r['method'] == 'DELETE' for r in ledger.correlations()))

    def test_live_denied_business_request_is_journaled_without_identity_fallback(self):
        from bcs_e2e.local_logs import OwnedLocalLogObserver
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=root / 'runtime', run_id='live-denied') as runtime:
                    OwnedLocalFixtures(runtime).apply(allow_write=True)
                    profile = resolve_owned_profile(runtime)
                    write(root / 'profile.json', profile)
                    state = root / 'state'
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['prepare', '--profile', str(root / 'profile.json'),
                            '--state-dir', str(state), '--run-id', runtime.run_id]), 0)
                    def factory(current, *, state_dir, case_id, **_kwargs):
                        adapter = OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                        update = adapter.group_update
                        def denied(group_id, body):
                            with patch.object(adapter.local_auth, 'headers', return_value={'X-Avernet-Principal': 'invalid-test-principal'}):
                                return update(group_id, body)
                        adapter.group_update = denied
                        return adapter
                    observer = OwnedLocalLogObserver(runtime, ledger_path=state / 'run.sqlite',
                        profile_digest=fingerprint(profile), case_id='PRE-01')
                    report = run_case(profile, state, runtime.run_id, 'PRE-01', adapter_factory=factory,
                                      log_observer=observer)
                    self.assertEqual(report['status'], 'FAIL')
                    self.assertEqual(report['cleanup'], 'BLOCKED')
                    self.assertEqual(read(state / 'run.json')['status'], 'QUARANTINED')
                    self.assertEqual(report['operation_authority'], 'INVALIDATED')
                    self.assertFalse(any(e.get('method') == 'DELETE' for e in report['evidence']))
                    self.assertEqual(report['diagnostic_status'], 'PASS')
                    failed = [e for e in report['evidence'] if e.get('outcome') == 'ERROR']
                    self.assertEqual(len(failed), 1)
                    self.assertEqual(failed[0]['operation'], 'group.update')
                    self.assertEqual(failed[0]['http_status'], 401)
                    with Ledger(state / 'run.sqlite', runtime.run_id, fingerprint(profile), read_only=True) as ledger:
                        correlations = [r for r in ledger.correlations() if r['operation'] == 'group.update']
                        self.assertEqual(len(correlations), 1)
                        self.assertEqual(correlations[0]['request_id'], failed[0]['submitted_request_id'])
                        log_rows = [row for row in ledger.diagnostics()
                                    if row['request_id'] == failed[0]['submitted_request_id']]
                        self.assertEqual(len(log_rows), 1)
                        self.assertEqual(log_rows[0]['status'], 'OBSERVED')
                        self.assertGreater(log_rows[0]['record_count'], 0)
                    self.assertNotIn('invalid-test-principal', json.dumps(report))
                self.assertEqual(runtime.child.poll(), 0)

    def test_live_denied_websocket_upgrade_is_journaled_without_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=root / 'runtime', run_id='live-ws-denied') as runtime:
                    OwnedLocalFixtures(runtime).apply(allow_write=True)
                    profile = resolve_owned_profile(runtime)
                    write(root / 'profile.json', profile)
                    state = root / 'state'
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['prepare', '--profile', str(root / 'profile.json'),
                            '--state-dir', str(state), '--run-id', runtime.run_id]), 0)
                    def factory(current, *, state_dir, case_id, **_kwargs):
                        adapter = OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                        connect = adapter.websocket.connect
                        def denied(_token, group_id, session_id):
                            return connect('invalid-test-ws-token', group_id, session_id)
                        adapter.websocket.connect = denied
                        return adapter
                    report = run_case(profile, state, runtime.run_id, 'PRE-02', adapter_factory=factory)
                    self.assertEqual(report['status'], 'FAIL')
                    self.assertEqual(report['cleanup'], 'BLOCKED')
                    self.assertEqual(read(state / 'run.json')['status'], 'QUARANTINED')
                    self.assertEqual(report['operation_authority'], 'INVALIDATED')
                    self.assertFalse(any(e.get('method') == 'DELETE' for e in report['evidence']))
                    failed = [e for e in report['evidence'] if e.get('outcome') == 'ERROR']
                    self.assertEqual(len(failed), 1)
                    self.assertEqual(failed[0]['operation'], 'session.ws.connect')
                    self.assertEqual(failed[0]['http_status'], 401)
                    self.assertEqual(failed[0]['phase'], 'upgrade')
                    self.assertIsNone(failed[0]['connect_request_id'])
                    self.assertEqual(report['quiescence'], 'UNKNOWN')
                    with Ledger(state / 'run.sqlite', runtime.run_id, fingerprint(profile), read_only=True) as ledger:
                        correlations = [r for r in ledger.correlations() if r['operation'] == 'session.ws.connect']
                        self.assertEqual(len(correlations), 1)
                        self.assertEqual(correlations[0]['request_id'], failed[0]['submitted_request_id'])
                    self.assertNotIn('invalid-test-ws-token', json.dumps(report))
                self.assertEqual(runtime.child.poll(), 0)

    def test_run_owned_ws_probe_uses_real_session_token_and_preserves_run_state(self):
        from bcs_e2e.probe import ws_probe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root/'pools'):
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=root/'runtime', run_id='live-ws-probe') as runtime:
                    OwnedLocalFixtures(runtime).apply(allow_write=True)
                    profile = resolve_owned_profile(runtime)
                    write(root/'profile.json', profile)
                    state = root/'state'
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['prepare', '--profile', str(root/'profile.json'),
                            '--state-dir', str(state), '--run-id', runtime.run_id]), 0)
                    results = []
                    def factory(current, *, state_dir, case_id, **_kwargs):
                        adapter = OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                        original_connect = adapter.session_ws_connect
                        def connect(group_id, session_id, token):
                            before = {path: path.read_bytes() for path in state_dir.iterdir() if path.is_file()}
                            # The invalid token must fail against the actual BCS,
                            # then the independently issued valid token must bind.
                            for supplied in ('test-token', token):
                                with patch.dict(os.environ, {'BCS_E2E_SESSION_TOKEN': supplied}):
                                    result = ws_probe(current, state_dir, runtime.run_id,
                                        token_env='BCS_E2E_SESSION_TOKEN', group_id=group_id, session_id=session_id)
                                self.assertNotIn(supplied, json.dumps(result))
                                self.assertFalse(result['pool_release_authorized'])
                                self.assertEqual(result['execution_status'], 'UNCONFIRMED')
                                results.append(result)
                            self.assertEqual(before, {path: path.read_bytes() for path in state_dir.iterdir() if path.is_file()})
                            return original_connect(group_id, session_id, token)
                        adapter.session_ws_connect = connect
                        return adapter
                    report = run_case(profile, state, runtime.run_id, 'PRE-02', adapter_factory=factory)
                    self.assertEqual(report['functional'], 'PASS', report)
                    self.assertEqual(report['cleanup'], 'PASS', report)
                    self.assertEqual([r['status'] for r in results], ['BLOCKED', 'PROBE_PASSED'])
                    self.assertEqual(results[0]['socket_close_status'], 'NOT_ACQUIRED')
                    self.assertEqual(results[1]['socket_close_status'], 'CLOSED')
                    self.assertEqual(results[1]['evidence']['http_status'], 101)
                    self.assertTrue(results[1]['evidence']['connected'])
                self.assertEqual(runtime.child.poll(), 0)

    @unittest.skipUnless(CLI_BINARY and CLI_DIGEST, 'real product CLI binary/SHA256 required')
    def test_real_product_cli_reads_owned_session_with_isolated_context(self):
        from bcs_e2e.local_cli import LocalCliError, OwnedLocalSessionCli, _execute
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=root / 'runtime', run_id='live-product-cli') as runtime:
                    credentials = []
                    OwnedLocalFixtures(runtime).apply(allow_write=True,
                        driver_token_sink=lambda bot, issued: credentials.append((bot, issued)))
                    self.assertEqual(len(credentials), 1)
                    profile = resolve_owned_profile(runtime)
                    write(root / 'profile.json', profile)
                    state = root / 'state'
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['prepare', '--profile', str(root / 'profile.json'),
                            '--state-dir', str(state), '--run-id', runtime.run_id]), 0)
                    cli_reports = []
                    def factory(current, *, state_dir, case_id, **_kwargs):
                        adapter = OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                        cli = OwnedLocalSessionCli(runtime, current, state_dir=state_dir, case_id=case_id,
                            binary=Path(CLI_BINARY), binary_sha256=CLI_DIGEST)
                        update = adapter.session_update
                        def update_and_check(session_id, body):
                            value, record = update(session_id, body)
                            cli_reports.append(cli.read_session(session_id, value['group_id'], body['title']))
                            driver, issued = credentials[0]
                            with self.assertRaisesRegex(LocalCliError, 'nonzero'):
                                cli.read_permission(session_id, value['group_id'], driver, 'test-token')
                            # An empty isolated context also reaches the real CLI;
                            # it must not silently inherit the parent Bot token.
                            context_root, _, env = cli._context()
                            with self.assertRaisesRegex(LocalCliError, 'nonzero'):
                                _execute(Path(CLI_BINARY), env, context_root, session_id, 10, permission=True)
                            cli_reports.append(cli.read_permission(session_id, value['group_id'], driver, issued))
                            return value, record
                        adapter.session_update = update_and_check
                        return adapter
                    with patch.dict(os.environ, {'BCS_URL': 'https://must-not-use.invalid',
                                                 'BCN_BOT_TOKEN': 'must-not-use-token'}):
                        report = run_case(profile, state, runtime.run_id, 'PRE-02', adapter_factory=factory)
                    self.assertEqual(report['status'], 'PASS', json.dumps(report, indent=2))
                    self.assertEqual(report['cleanup'], 'PASS')
                    self.assertEqual(len(cli_reports), 2)
                    cli_report = cli_reports[0]
                    self.assertEqual(cli_report['status'], 'LOCAL_CLI_SLICE_PASS')
                    self.assertEqual(cli_report['binary_sha256'], CLI_DIGEST)
                    self.assertEqual(cli_report['authentication'], 'NONE')
                    self.assertEqual(cli_report['full_cli_coverage'], 'NOT_QUALIFIED')
                    context = Path(cli_report['context_dir'])
                    self.assertTrue((context / 'home/.bcs/logs/bcs-cli.log').is_file())
                    self.assertEqual(json.loads((context / 'bot/.bcs/session.json').read_text())['token'], '')
                    permission = cli_reports[1]
                    self.assertEqual(permission['status'], 'LOCAL_CLI_AUTH_PASS')
                    self.assertEqual(permission['caller_bot_id'], credentials[0][0])
                    self.assertEqual(permission['credential_cleanup'], 'CLEARED')
                    for private_context in state.glob('product-cli-*'):
                        for artifact in private_context.rglob('*'):
                            if artifact.is_file():
                                self.assertFalse(credentials[0][1].encode() in artifact.read_bytes(),
                                                 'generated Bot credential remained in CLI context artifacts')
                    self.assertFalse(credentials[0][1] in json.dumps(cli_reports), 'credential leaked in report')
                self.assertEqual(runtime.child.poll(), 0)

    @unittest.skipUnless(CLI_BINARY and CLI_DIGEST, 'real product CLI binary/SHA256 required')
    def test_local_run_optional_product_cli_check_and_independent_failure_gate(self):
        from contextlib import nullcontext
        def chained_cancellation(*args, **kwargs):
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                raise OSError('private-secondary-error')
        for mode in ('pass', 'cli-failure', 'permission-failure', 'cancel', 'chained-cancel', 'checkpoint-cancel'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); state = root / 'state'
                def checkpoint(path, value):
                    if value.get('status') == 'LOCAL_CLI_SLICE_PASS':
                        chained_cancellation()
                    write(path, value)
                fault = (patch('bcs_e2e.local_cli_check.write', side_effect=checkpoint) if mode == 'checkpoint-cancel' else
                         nullcontext() if mode == 'pass' else
                         patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli.read_permission',
                               side_effect=RuntimeError('private-cli-error')) if mode == 'permission-failure' else
                         patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli.read_session',
                               side_effect=chained_cancellation if mode == 'chained-cancel' else
                                           KeyboardInterrupt() if mode == 'cancel' else RuntimeError('private-cli-error')))
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'), fault, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(['local-run', '--binary', BINARY, '--binary-sha256', DIGEST,
                               '--cli-binary', CLI_BINARY, '--cli-binary-sha256', CLI_DIGEST, '--local-logs',
                               '--state-dir', str(state), '--run-id', 'live-cli-option',
                               '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                               '--confirm-run-id', 'live-cli-option'])
                result = json.loads(output.getvalue())
                self.assertEqual(rc, {'pass': 0, 'cli-failure': 2, 'permission-failure': 2, 'cancel': 130, 'chained-cancel': 130, 'checkpoint-cancel': 130}[mode], result)
                self.assertEqual(result['shutdown']['status'], 'STOPPED')
                self.assertEqual(read(state / 'local-run.json'), result)
                check = result['product_cli']
                if mode == 'checkpoint-cancel':
                    self.assertEqual(read(state / 'product-cli-check.json')['status'], 'RUNNING')
                    self.assertEqual(check['persistence_error_type'], 'OSError')
                else:
                    self.assertEqual(read(state / 'product-cli-check.json'), check)
                self.assertEqual(check['attempts'], 1)
                self.assertEqual(check['run_id'], 'live-cli-option')
                for field in ('profile_digest', 'executor_digest', 'manifest_digest'):
                    self.assertEqual(check[field], read(state / 'run.json')[field])
                summary = read(state / 'reports/summary.json')
                self.assertEqual([case['id'] for case in summary['cases']], ['PRE-01', 'PRE-02', 'PRE-03'])
                self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
                self.assertNotIn('private-cli-error', json.dumps(result))
                if mode == 'pass':
                    self.assertEqual(check['status'], 'LOCAL_CLI_SLICE_PASS')
                    self.assertEqual(result['local_logs']['status'], 'PASS')
                    self.assertEqual(result['pilot_status'], 'PILOT_PASS')
                    self.assertEqual(check['authentication'], 'NONE')
                    self.assertEqual(check['bot_permission']['status'], 'LOCAL_CLI_AUTH_PASS')
                    self.assertEqual(check['bot_permission']['credential_cleanup'], 'CLEARED')
                    self.assertEqual(len(result['completed_cases']), 3)
                elif mode in ('cli-failure', 'permission-failure'):
                    self.assertEqual(check['status'], 'BLOCKED')
                    self.assertEqual(result['status'], 'BLOCKED')
                    self.assertEqual([case['id'] for case in result['completed_cases']], ['PRE-01', 'PRE-02'])
                    self.assertTrue(all(case['status'] == 'PASS' and case['cleanup'] == 'PASS'
                                        for case in result['completed_cases']))
                    self.assertEqual(summary['cases'][2]['functional'], 'NOT_RUN')
                    self.assertEqual(result['pilot_status'], 'BLOCKED')
                    self.assertFalse(Path(read(state / 'run.json')['lock_path']).exists())
                else:
                    self.assertEqual(check['status'], 'CANCELLED')
                    self.assertEqual(result['status'], 'CANCELLED')
                    self.assertNotIn('PRE-03', read(state / 'run.json')['attempted_cases'])
                with Ledger(state / 'run.sqlite', 'live-cli-option',
                            read(state / 'run.json')['profile_digest'], read_only=True) as ledger:
                    pending = ledger.cleanup_plan()['resources']
                    self.assertEqual(len(pending), 3 if 'cancel' in mode else 0)
                    if 'cancel' in mode:
                        self.assertEqual({r['case_id'] for r in pending}, {'PRE-02'})
                        self.assertFalse(any(r['method'] == 'DELETE' and r['case_id'] == 'PRE-02'
                                             for r in ledger.correlations()))
                    self.assertFalse(ledger.cleanup_plan()['unresolved_handles'])

    def test_real_three_story_pilot_two_isolated_rounds(self):
        for round_number in range(2):
            with self.subTest(round=round_number), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                    with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                         root=root / 'runtime', run_id=f'real-pilot-{round_number}') as runtime:
                        OwnedLocalFixtures(runtime).apply(allow_write=True)
                        profile = resolve_owned_profile(runtime)
                        write(root / 'profile.json', profile)
                        state = root / 'state'
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(main(['prepare', '--profile', str(root / 'profile.json'),
                                '--run-id', runtime.run_id, '--state-dir', str(state)]), 0)
                        def factory(current, *, state_dir, case_id, **_kwargs):
                            return OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                        reports = []
                        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
                            report = run_case(profile, state, runtime.run_id, case, adapter_factory=factory)
                            reports.append(report)
                            self.assertEqual(report['status'], 'PASS', json.dumps(report, indent=2))
                            self.assertEqual(report['cleanup'], 'PASS')
                            self.assertEqual(report['evidence_status'], 'PASS')
                        self.assertTrue(all(e['source'] == 'local-sqlite'
                            for report in reports for e in report['semantic_evidence']))
                        with Ledger(state / 'run.sqlite', runtime.run_id, fingerprint(profile), read_only=True) as ledger:
                            rows = ledger.resource_snapshot()
                        self.assertEqual(len(rows), 9)
                        self.assertEqual({r['state'] for r in rows}, {'deleted'})
                        self.assertFalse(Path(read(state / 'run.json')['lock_path']).exists())
                        summary = build_report(profile, state, json.loads((ROOT / 'manifest.json').read_text()))
                        self.assertEqual(summary['status'], 'PILOT_PASS', json.dumps(summary, indent=2))
                        self.assertEqual(summary['integrity']['status'], 'PASS')
                        self.assertEqual(evaluate(summary)['status'], 'NOT_QUALIFIED')
                        write_reports(summary, state / 'reports', {'json', 'junit', 'markdown'})
                    self.assertEqual(runtime.child.poll(), 0)

    def test_real_three_story_request_logs_are_observed_from_owned_json_sink(self):
        from bcs_e2e.local_logs import OwnedLocalLogObserver
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=root / 'runtime', run_id='live-request-logs') as runtime:
                    OwnedLocalFixtures(runtime).apply(allow_write=True)
                    profile = resolve_owned_profile(runtime)
                    write(root / 'profile.json', profile)
                    state = root / 'state'
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['prepare', '--profile', str(root / 'profile.json'),
                            '--state-dir', str(state), '--run-id', runtime.run_id]), 0)
                    self.assertIsNotNone(runtime.access_log_identity)
                    self.assertEqual(runtime.inputs.environment['TZ'], 'UTC')
                    def factory(current, *, state_dir, case_id, **_kwargs):
                        return OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                    for case in ('PRE-01', 'PRE-02', 'PRE-03'):
                        observer = OwnedLocalLogObserver(runtime, ledger_path=state / 'run.sqlite',
                            profile_digest=fingerprint(profile), case_id=case)
                        report = run_case(profile, state, runtime.run_id, case,
                                          adapter_factory=factory, log_observer=observer)
                        self.assertEqual(report['status'], 'PASS', json.dumps(report, indent=2))
                        self.assertEqual(report['diagnostic_status'], 'PASS', json.dumps(report['diagnostic_evidence']))
                        self.assertEqual(observer.client.scan_count, 1)
                        with Ledger(state / 'run.sqlite', runtime.run_id, fingerprint(profile), read_only=True) as ledger:
                            rows = [row for row in ledger.diagnostics() if row['case_id'] == case]
                            self.assertTrue(rows)
                            self.assertTrue(all(row['status'] == 'OBSERVED' and row['record_count'] > 0 for row in rows))
                            self.assertTrue(all(row['environment'] == 'local' and row['traceparent'] is None for row in rows))
                            self.assertFalse(ledger.cleanup_plan()['resources'])
                self.assertEqual(runtime.child.poll(), 0)
                self.assertTrue((runtime.root / 'logs/e2e-access.jsonl').is_file())

    def test_local_run_log_flag_is_optional_and_preserves_cancellation(self):
        from contextlib import nullcontext
        def interrupted_log(*args, **kwargs):
            try: raise KeyboardInterrupt()
            except KeyboardInterrupt: raise OSError('private-log-secondary')
        for mode in ('pass', 'unavailable', 'init-failure', 'init-cancel', 'cancel'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); state = root / 'state'
                fault = (patch('bcs_e2e.local_run.OwnedLocalLogObserver',
                               side_effect=interrupted_log if mode == 'init-cancel' else OSError('private-log-init'))
                         if mode in ('init-failure', 'init-cancel') else
                         nullcontext() if mode == 'pass' else
                         patch('bcs_e2e.local_cli_check.OwnedLocalSessionCli.read_permission',
                               side_effect=RuntimeError('private-cli-error')) if mode == 'permission-failure' else
                         patch('bcs_e2e.local_logs.OwnedLocalLogClient.query',
                               side_effect=interrupted_log if mode in ('cancel', 'init-cancel') else OSError('private-log-path')))
                with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'), fault, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = main(['local-run', '--binary', BINARY, '--binary-sha256', DIGEST,
                               '--state-dir', str(state), '--run-id', 'live-log-option', '--local-logs',
                               '--allow-local-processes', '--allow-fixture-write', '--allow-write',
                               '--confirm-run-id', 'live-log-option'])
                result = json.loads(output.getvalue())
                self.assertEqual(rc, 130 if mode in ('cancel', 'init-cancel') else 0, result)
                self.assertEqual(read(state / 'local-run.json'), result)
                self.assertEqual(result['shutdown']['status'], 'STOPPED')
                self.assertEqual(result['local_logs']['source'], 'owned-local-access-json')
                self.assertFalse(result['local_logs']['required'])
                self.assertEqual(len(result['local_logs']['cases']), 3)
                self.assertNotIn('private-log-', json.dumps(result))
                self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
                if mode in ('cancel', 'init-cancel'):
                    self.assertEqual(result['status'], 'CANCELLED')
                    self.assertEqual(read(state / 'run.json')['attempted_cases'], ['PRE-01'])
                else:
                    self.assertEqual(result['status'], 'LOCAL_PILOT_PASS')
                    self.assertEqual(result['local_logs']['status'], 'PASS' if mode == 'pass' else 'INCOMPLETE')
                    self.assertEqual(len(result['completed_cases']), 3)
                    for case in ('PRE-01', 'PRE-02', 'PRE-03'):
                        report = read(state / ('report-' + case + '.json'))
                        self.assertEqual(report['functional'], 'PASS')
                        self.assertEqual(report['cleanup'], 'PASS')
                        self.assertEqual(report['diagnostic_status'], 'PASS' if mode == 'pass' else 'BLOCKED')
                        if mode in ('unavailable', 'init-failure'): self.assertTrue(report['diagnostic_warnings'])
                with Ledger(state / 'run.sqlite', 'live-log-option', read(state / 'run.json')['profile_digest'], read_only=True) as ledger:
                    self.assertFalse(ledger.cleanup_plan()['resources'])

    def test_real_pre01_shared_story_with_owned_auth_and_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('bcs_e2e.pool.pool_root', return_value=root / 'pools'):
                with OwnedBcsProcess(binary=Path(BINARY), binary_sha256=DIGEST,
                                     root=root / 'runtime', run_id='real-pre01') as runtime:
                    OwnedLocalFixtures(runtime).apply(allow_write=True)
                    profile = resolve_owned_profile(runtime)
                    write(root / 'profile.json', profile)
                    state = root / 'state'
                    with contextlib.redirect_stdout(io.StringIO()):
                        rc = main(['prepare', '--profile', str(root / 'profile.json'),
                                   '--run-id', runtime.run_id, '--state-dir', str(state)])
                    self.assertEqual(rc, 0)
                    def factory(current, *, state_dir, case_id, **_kwargs):
                        return OwnedLocalPilotAdapter(runtime, current, state_dir=state_dir, case_id=case_id)
                    report = run_case(profile, state, runtime.run_id, 'PRE-01', adapter_factory=factory)
                    self.assertEqual(report['status'], 'PASS', json.dumps(report, indent=2))
                    self.assertEqual(report['cleanup'], 'PASS')
                    self.assertEqual(report['evidence_status'], 'PASS')
                    self.assertEqual(report['evidence_required'], ['api', 'sqlite'])
                    self.assertEqual(report['semantic_evidence'][0]['source'], 'local-sqlite')
                    self.assertFalse(Path(read(state / 'run.json')['lock_path']).exists())
                    with Ledger(state / 'run.sqlite', runtime.run_id, fingerprint(profile), read_only=True) as ledger:
                        resources = ledger.resource_snapshot()
                    self.assertEqual([row['kind'] for row in resources], ['group', 'session'])
                    self.assertEqual({row['state'] for row in resources}, {'deleted'})
                    requests = report['evidence']
                    self.assertTrue(any(r.get('operation') == 'group.create' and r.get('http_status') == 201 for r in requests))
                    self.assertTrue(any(r.get('path') == '/auth/user' for r in requests))
                    for secret in runtime.inputs.environment.values():
                        if len(secret) == 64:
                            self.assertNotIn(secret, json.dumps(report))
                self.assertEqual(runtime.child.poll(), 0)
