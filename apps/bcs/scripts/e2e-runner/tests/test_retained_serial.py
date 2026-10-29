"""Real file locks and retained mock lifecycle; no remote calls or cleanup."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.pool import PoolBusyError, PoolLease, pool_path, pool_scope
from bcs_e2e.profile import ConfigError, fingerprint, load
from bcs_e2e.retained import retained_run
from bcs_e2e.retained_serial import CONTRACT
from bcs_e2e.run_state import read, write


class SerialRetainedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        registry = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'registry')
        registry.start(); self.addCleanup(registry.stop)
        self.lock = pool_path(pool_scope(self.profile))

    def run_case(self, name, previous=None, case='RETAIN-GROUP-01', serial=True):
        return retained_run(self.profile, state_dir=self.root/name, run_id=name, case_id=case,
                            allow_write=True, confirm_run_id=name, serial_preflight=serial,
                            previous_state_dir=self.root/previous if previous else None)

    def snapshot(self, name):
        return {str(p.relative_to(self.root/name)): p.read_bytes()
                for p in (self.root/name).rglob('*') if p.is_file()}

    def test_two_serial_runs_preserve_old_unknown_and_all_old_bytes(self):
        a = self.run_case('first')
        self.assertEqual(a['functional'], 'PASS')
        self.assertEqual(a['execution_status'], 'UNCONFIRMED')
        before = self.snapshot('first')
        b = self.run_case('second', 'first', case='RETAIN-SESSION-01')
        self.assertEqual(b['functional'], 'PASS', b)
        self.assertEqual(b['socket_status'], 'CLOSED')
        self.assertEqual(self.snapshot('first'), before)
        receipt = b['serial_admission']
        self.assertEqual(receipt['contract'], CONTRACT)
        self.assertEqual(receipt['previous_run_id'], 'first')
        self.assertFalse(receipt['remote_termination_confirmed'])
        self.assertFalse(receipt['cleanup_authorized'])
        self.assertEqual(read(self.lock)['run_id'], 'second')
        for name in ('first', 'second'):
            mock = read(self.root/name/'local-mock.json')
            self.assertEqual(mock['deleted'], [])
            self.assertEqual(mock['deleted_sessions'], [])
            self.assertEqual(mock['connected_sessions'], [])
        self.assertEqual(len(list(self.lock.with_suffix('.serial-history').glob('*.json'))), 2)

    def test_old_policy_completed_command_can_transfer_without_closure(self):
        self.run_case('old', serial=False)
        before = self.snapshot('old')
        result = self.run_case('new', 'old')
        self.assertEqual(result['functional'], 'PASS')
        self.assertEqual(self.snapshot('old'), before)

    def test_guard_is_held_through_preflight_prepare_and_business(self):
        original = LocalMockAdapter.preflight
        calls = []
        def checked(adapter):
            with self.assertRaises(PoolBusyError):
                PoolLease.acquire(self.lock, 'competitor', fingerprint(self.profile))
            calls.append(True)
            return original(adapter)
        with patch.object(LocalMockAdapter, 'preflight', checked):
            self.assertEqual(self.run_case('first')['functional'], 'PASS')
        self.assertGreaterEqual(len(calls), 2)

    def test_failed_fresh_dependencies_leave_old_reservation_and_history_unchanged(self):
        self.run_case('first')
        before, reservation = self.snapshot('first'), self.lock.read_bytes()
        with patch.object(LocalMockAdapter, 'preflight', return_value={'status':'BLOCKED'}):
            result = self.run_case('second', 'first')
        self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertEqual(self.lock.read_bytes(), reservation)
        self.assertEqual(self.snapshot('first'), before)
        self.assertFalse((self.root/'second').exists())
        self.assertEqual(len(list(self.lock.with_suffix('.serial-history').glob('*.json'))), 1)
        self.assertEqual(self.run_case('second', 'first')['functional'], 'PASS')

    def test_active_command_blocks_before_preflight(self):
        self.run_case('first')
        with PoolLease.from_file(self.lock, 'first', fingerprint(self.profile)), \
             patch.object(LocalMockAdapter, 'preflight') as preflight:
            with self.assertRaises(PoolBusyError): self.run_case('second', 'first')
            preflight.assert_not_called()
        self.assertFalse((self.root/'second').exists())

    def test_previous_run_or_socket_uncertainty_cannot_hide_active_local_trigger(self):
        self.run_case('first')
        path = self.root/'first'/'report-RETAIN-GROUP-01.json'
        result = read(path); result['socket_status'] = 'UNCONFIRMED'; write(path, result)
        with patch.object(LocalMockAdapter, 'preflight') as preflight:
            with self.assertRaises(ConfigError): self.run_case('second', 'first')
            preflight.assert_not_called()
        self.assertEqual(read(self.lock)['run_id'], 'first')

    def test_same_run_id_and_foreign_profile_are_refused(self):
        self.run_case('first')
        before = self.lock.read_bytes()
        with self.assertRaises(ConfigError): self.run_case('first', 'first')
        self.profile = copy.deepcopy(self.profile)
        self.profile['human']['user_id'] = 'another-human'
        with self.assertRaises(Exception): self.run_case('second', 'first')
        self.assertEqual(self.lock.read_bytes(), before)

    def test_existing_reservation_requires_explicit_previous_binding(self):
        self.run_case('first')
        with self.assertRaises(PoolBusyError): self.run_case('second')
        with self.assertRaises(ConfigError): self.run_case('second', 'first', serial=False)
        self.assertEqual(read(self.lock)['run_id'], 'first')

    def test_interrupted_transfer_cannot_replay_or_silently_restore_old_owner(self):
        self.run_case('first')
        from bcs_e2e import retained_serial
        original = retained_serial.write
        def fail(path, value):
            if path == self.lock: raise OSError('injected before reservation replace')
            return original(path, value)
        with patch.object(retained_serial, 'write', fail):
            with self.assertRaises(OSError): self.run_case('second', 'first')
        self.assertEqual(read(self.lock)['run_id'], 'first')
        with self.assertRaises(FileExistsError): self.run_case('second', 'first')
        self.assertFalse((self.root/'second').exists())

    def test_post_replace_failure_keeps_new_reservation_and_no_business(self):
        self.run_case('first')
        from bcs_e2e import retained_serial
        original = retained_serial.write
        def fail(path, value):
            if path.name == 'serial-admission.json': raise OSError('injected after reservation replace')
            return original(path, value)
        with patch.object(retained_serial, 'write', fail):
            with self.assertRaises(OSError): self.run_case('second', 'first')
        self.assertEqual(read(self.lock)['run_id'], 'second')
        self.assertFalse((self.root/'second'/'run.sqlite').exists())
        with self.assertRaises(Exception): self.run_case('third', 'first')

    def test_failed_prepare_keeps_reservation_and_releases_only_command_guard(self):
        with patch('bcs_e2e.retained.prepare_run', side_effect=OSError('injected')):
            with self.assertRaises(OSError): self.run_case('first')
        self.assertEqual(read(self.lock)['run_id'], 'first')
        with PoolLease.from_file(self.lock, 'first', fingerprint(self.profile)):
            pass


# Exercise the actual CLI and remote composition; only HTTP/MCP replies are
# synthetic. Reuse fixtures without inheriting their entire test class.
import test_retained_remote as remote_fixtures
from bcs_e2e.inventory import current_manifest


class SerialRemoteCompositionTests(unittest.TestCase):
    setUp = remote_fixtures.RetainedRemoteTests.setUp
    command = remote_fixtures.RetainedRemoteTests.command
    process = remote_fixtures.RetainedRemoteTests.process
    boundaries = remote_fixtures.RetainedRemoteTests.boundaries
    args = remote_fixtures.RetainedRemoteTests.args

    def next_run(self):
        old = self.state
        self.state = self.root/'second'; self.run_id = 'second'
        self.review['run_id'] = self.run_id
        quota_path = pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')
        self.review['retention_quota'].update(mode='existing', expected_digest=fingerprint(read(quota_path)))
        write(self.review_path, self.review)
        return old

    def test_cli_two_gateway_shaped_runs_recheck_and_accumulate_quota(self):
        with self.boundaries():
            rc, first = self.command(self.args()+['--serial-preflight'])
        self.assertEqual(first['functional'], 'PASS', first)
        self.assertEqual(first['retention_quota']['accounted_after'], {'groups':1,'sessions':1})
        old = self.next_run()
        before = {p.name:p.read_bytes() for p in old.iterdir() if p.is_file()}
        reads = len(self.http)
        with self.boundaries():
            rc, second = self.command(self.args()+['--serial-preflight','--previous-state-dir',str(old)])
        self.assertEqual(second['functional'], 'PASS', second)
        self.assertEqual(second['retention_quota']['accounted_after'], {'groups':2,'sessions':2})
        self.assertEqual(second['execution_status'], 'UNCONFIRMED')
        self.assertEqual({p.name:p.read_bytes() for p in old.iterdir() if p.is_file()}, before)
        self.assertGreater(sum(kind=='read' for kind,_ in self.http[reads:]), 0)
        self.assertEqual(len(self.adapter.groups), 2)
        self.assertFalse(any('delete' in operation for _,operation in self.http))
        self.assertEqual(rc, 0)
        self.assertEqual(second['canonical_summary_status'], 'RETAINED_CASE_PASS')

    def test_fresh_remote_actor_failure_does_not_transfer_or_write(self):
        with self.boundaries(): self.command(self.args()+['--serial-preflight'])
        old = self.next_run()
        lock = pool_path(pool_scope(self.profile)); before = lock.read_bytes()
        self.visibility = 'public'
        with self.boundaries():
            rc, result = self.command(self.args()+['--serial-preflight','--previous-state-dir',str(old)])
        self.assertEqual(rc, 2)
        self.assertEqual(result['functional'], 'NOT_RUN')
        self.assertEqual(lock.read_bytes(), before)
        self.assertEqual(len(self.adapter.groups), 1)
        self.assertFalse(self.state.exists())

    def test_quota_exhaustion_after_handoff_blocks_new_creation_without_refund(self):
        self.review['retention_quota'].update(max_groups=1, max_sessions=1)
        write(self.review_path, self.review)
        with self.boundaries(): self.command(self.args()+['--serial-preflight'])
        old = self.next_run()
        quota_path = pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')
        before = quota_path.read_bytes()
        with self.boundaries():
            rc, result = self.command(self.args()+['--serial-preflight','--previous-state-dir',str(old)])
        self.assertEqual(rc, 2)
        self.assertEqual(result['functional'], 'NOT_RUN', result)
        self.assertEqual(result['persistence'], 'PERSISTED', result)
        self.assertEqual(len(self.adapter.groups), 1)
        self.assertEqual(quota_path.read_bytes(), before)
        self.assertFalse(read(self.state/'run.json').get('attempted_cases'))

    def test_failed_quota_snapshot_can_handoff_after_fresh_review_without_replaying_run(self):
        with self.boundaries(): self.command(self.args()+['--serial-preflight'])
        old = self.next_run()
        self.review['retention_quota']['expected_digest'] = '0'*64
        write(self.review_path, self.review)
        with self.boundaries():
            rc, failed = self.command(self.args()+['--serial-preflight','--previous-state-dir',str(old)])
        self.assertEqual(failed['local_trigger_status'], 'NOT_STARTED', failed)
        second = self.state
        before = {p.name:p.read_bytes() for p in second.iterdir() if p.is_file()}
        self.state = self.root/'third'; self.run_id = 'third'; self.review['run_id'] = 'third'
        quota = pool_path(pool_scope(self.profile)).with_suffix('.retention-quota.json')
        self.review['retention_quota']['expected_digest'] = fingerprint(read(quota))
        write(self.review_path, self.review)
        with self.boundaries():
            rc, result = self.command(self.args()+['--serial-preflight','--previous-state-dir',str(second)])
        self.assertEqual(result['functional'], 'PASS', result)
        self.assertEqual(result['retention_quota']['accounted_after'], {'groups':2,'sessions':2})
        self.assertEqual(len(self.adapter.groups), 2)
        self.assertEqual({p.name:p.read_bytes() for p in second.iterdir() if p.is_file()}, before)


    def test_serial_credential_authority_expiry_after_create_stops_new_actions(self):
        with self.boundaries(expire_after_create=True):
            rc, result = self.command(self.args()+['--serial-preflight'])
        self.assertEqual(rc, 2)
        self.assertNotEqual(result['status'], 'PASS')
        self.assertEqual(len(self.adapter.groups), 1)
        writes = [operation for kind,operation in self.http if kind=='write']
        self.assertEqual(writes, ['group.create'])
        self.assertFalse(self.adapter.deleted)

    def test_serial_lost_create_response_preserves_unknown_without_retry_or_refund(self):
        with self.boundaries(lose_create=True):
            rc, result = self.command(self.args()+['--serial-preflight'])
        self.assertEqual(rc, 2)
        self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual([operation for kind,operation in self.http if kind=='write'], ['group.create'])
        self.assertEqual(len(self.adapter.groups), 1)
        self.assertEqual(result['retention_quota']['accounted_after'], {'groups':1,'sessions':1})
        self.assertEqual(read(self.state/'retention.json')['inventory_status'], 'UNKNOWN_RESOURCES')
        self.assertFalse(self.adapter.deleted)


class SerialReportingTests(unittest.TestCase):
    setUp = SerialRetainedTests.setUp
    run_case = SerialRetainedTests.run_case
    snapshot = SerialRetainedTests.snapshot

    def summary(self, name='first'):
        from bcs_e2e.reporting import build_report
        return build_report(self.profile, self.root/name)

    def test_selected_case_pass_with_unknown_execution_in_all_formats(self):
        import xml.etree.ElementTree as ET
        from bcs_e2e.reporting import render_junit, render_markdown
        result = self.run_case('first')
        self.assertEqual(result['status'], 'PASS')
        summary = self.summary()
        self.assertEqual(summary['status'], 'RETAINED_CASE_PASS', summary['integrity'])
        self.assertEqual(summary['integrity']['status'], 'PASS')
        self.assertEqual(summary['scope']['selected_cases'], ['RETAIN-GROUP-01'])
        self.assertEqual(summary['scope']['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(summary['scope']['legacy_inventory_count'], len(current_manifest('preprod-retained-v1')['legacy_inventory']))
        self.assertEqual(summary['reports']['declared_case_count'], 2)
        case = next(c for c in summary['cases'] if c['id']=='RETAIN-GROUP-01')
        self.assertEqual(case['execution_status'], 'UNCONFIRMED')
        self.assertEqual(case['pool_disposition'], 'RETAINED_SERIAL')
        self.assertEqual(case['cleanup'], 'RETAINED')
        self.assertEqual(case['next_run_admission'], 'FRESH_CHECK_REQUIRED')
        self.assertEqual(summary['execution_safety']['qualification_role'], 'DIAGNOSTIC')
        self.assertEqual(summary['execution_safety']['status'], 'BLOCKED')
        junit = ET.fromstring(render_junit(summary))
        selected = junit.find("testcase[@name='RETAIN-GROUP-01']")
        self.assertIsNone(selected.find('skipped')); self.assertIsNone(selected.find('failure'))
        self.assertIsNotNone(junit.find("testcase[@name='RETAIN-SESSION-01']/skipped"))
        prop = selected.find("properties/property[@name='retention.serial.execution_closure']")
        self.assertEqual(prop.get('value'), 'DIAGNOSTIC')
        self.assertIn('not confirmed', render_markdown(summary))
        saved = read(self.root/'first'/'reports'/'summary.json')
        self.assertEqual({k:v for k,v in saved.items() if k != 'generated_at'},
                         {k:v for k,v in summary.items() if k != 'generated_at'})

    def test_old_policy_report_stays_blocked_and_cannot_be_upgraded_by_report_command(self):
        self.run_case('first', serial=False)
        before = self.snapshot('first')
        summary = self.summary()
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertNotIn('serial_admission', summary['retention'])
        self.assertEqual(self.snapshot('first'), before)

    def test_missing_or_mismatched_policy_receipt_blocks_green_reporting(self):
        self.run_case('first')
        path = self.root/'first'/'serial-admission.json'
        receipt = read(path)
        for change in ({'run_id':'foreign'}, {'profile_digest':'a'*64}, {'phase':'INTENDED'},
                       {'cleanup_authorized':True}, {'remote_termination_confirmed':True}):
            with self.subTest(change=change):
                write(path, {**receipt, **change})
                summary = self.summary()
                self.assertEqual(summary['status'], 'BLOCKED')
                self.assertEqual(summary['integrity']['status'], 'BLOCKED')
        path.unlink()
        self.assertEqual(self.summary()['status'], 'BLOCKED')

    def test_failed_or_missing_business_assertion_never_gets_serial_pass(self):
        self.run_case('first')
        path = self.root/'first'/'report-RETAIN-GROUP-01.json'
        original = read(path)
        changed = copy.deepcopy(original); changed['assertions'][0]['status'] = 'FAIL'
        write(path, changed)
        self.assertEqual(self.summary()['status'], 'BLOCKED')
        changed = copy.deepcopy(original); changed['assertions'] = []
        write(path, changed)
        self.assertEqual(self.summary()['status'], 'BLOCKED')
        changed = copy.deepcopy(original); changed['evidence_status'] = 'MISSING'
        write(path, changed)
        self.assertEqual(self.summary()['status'], 'BLOCKED')

    def test_failed_business_and_cancellation_keep_non_success_without_delete(self):
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        with patch.object(PersistentLocalMockAdapter, 'group_read', side_effect=RuntimeError('injected')):
            result = self.run_case('first')
        self.assertEqual(result['functional'], 'FAIL')
        self.assertEqual(result['canonical_summary_status'], 'BLOCKED')
        self.assertEqual(read(self.root/'first'/'local-mock.json')['deleted'], [])
        # A completed failed command may hand off; its failed assertions stay failed.
        result = self.run_case('second', 'first')
        self.assertEqual(result['canonical_summary_status'], 'RETAINED_CASE_PASS')
        self.assertEqual(self.summary('first')['status'], 'BLOCKED')

    def test_unknown_ws_close_blocks_pass_and_next_handoff(self):
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        with patch.object(PersistentLocalMockAdapter, 'session_ws_close', side_effect=RuntimeError('injected')):
            result = self.run_case('first', case='RETAIN-SESSION-01')
        self.assertEqual(result['socket_status'], 'UNCONFIRMED')
        self.assertEqual(result['canonical_summary_status'], 'BLOCKED')
        with self.assertRaises(ConfigError): self.run_case('second', 'first')

    def test_offline_report_never_uses_current_pool_as_historical_evidence(self):
        self.run_case('first'); before = self.snapshot('first')
        self.run_case('second', 'first')
        before_report = self.lock.read_bytes()
        self.assertEqual(self.summary('first')['status'], 'RETAINED_CASE_PASS')
        self.assertEqual(self.snapshot('first'), before)
        self.assertEqual(self.lock.read_bytes(), before_report)
        self.assertEqual(self.summary('first')['retention']['serial_admission']['current_trigger_authority'], 'NOT_RECHECKED')

    def test_cancellation_never_becomes_pass_under_diagnostic_closure_policy(self):
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        with patch.object(PersistentLocalMockAdapter, 'group_read', side_effect=KeyboardInterrupt()):
            result = self.run_case('first')
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['canonical_summary_status'], 'BLOCKED')
        self.assertEqual(read(self.root/'first'/'local-mock.json')['deleted'], [])

    def test_cli_report_returns_selected_case_success_without_runtime_or_credential_reads(self):
        import contextlib
        import io
        from bcs_e2e.cli import main
        self.run_case('first'); before = self.snapshot('first')
        profile = self.root/'profile.json'; write(profile, self.profile)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(['report','--profile',str(profile),'--state-dir',str(self.root/'first'),
                       '--output-dir',str(self.root/'export')])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['status'], 'RETAINED_CASE_PASS')
        self.assertEqual(self.snapshot('first'), before)

    def test_case_pass_before_final_run_checkpoint_failure_cannot_report_green(self):
        from bcs_e2e import retained
        original = retained.write
        def fail(path, value):
            if path.name == 'run.json' and value.get('status') == 'RETAINED':
                raise OSError('injected before final Run replace')
            return original(path, value)
        with patch.object(retained, 'write', fail):
            with self.assertRaises(OSError): self.run_case('first')
        self.assertEqual(read(self.root/'first'/'report-RETAIN-GROUP-01.json')['status'], 'PASS')
        self.assertEqual(read(self.root/'first'/'run.json')['status'], 'RUNNING')
        self.assertEqual(self.summary()['status'], 'BLOCKED')
        with self.assertRaises(ConfigError): self.run_case('second', 'first')

    def test_completion_write_failure_after_replace_keeps_pending_and_blocks_green(self):
        from bcs_e2e import retained
        original = retained.write
        def fail(path, value):
            original(path, value)
            if path.name == 'serial-completion.json':
                raise OSError('injected after completion replace')
        with patch.object(retained, 'write', fail):
            with self.assertRaises(OSError): self.run_case('first')
        self.assertTrue((self.root/'first'/'serial-completion.json').exists())
        self.assertTrue((self.root/'first'/'serial-finalization-pending.json').exists())
        self.assertEqual(self.summary()['status'], 'BLOCKED')
        with self.assertRaises(ConfigError): self.run_case('second', 'first')

    def test_real_sigterm_before_final_publication_is_cancelled_not_green(self):
        import os
        import signal
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        original = PersistentLocalMockAdapter.group_read
        def interrupted(adapter, group_id):
            os.kill(os.getpid(), signal.SIGTERM)
            return original(adapter, group_id)
        with patch.object(PersistentLocalMockAdapter, 'group_read', interrupted):
            result = self.run_case('first')
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(self.summary()['status'], 'BLOCKED')
        self.assertEqual(read(self.root/'first'/'local-mock.json')['deleted'], [])

    def test_lost_active_guard_never_turns_business_success_into_pass(self):
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        original = PersistentLocalMockAdapter.group_read
        def lose(adapter, group_id):
            value = original(adapter, group_id)
            self.lock.unlink()  # Isolated fault fixture only; never runtime recovery.
            return value
        with patch.object(PersistentLocalMockAdapter, 'group_read', lose):
            result = self.run_case('first')
        self.assertNotEqual(result['status'], 'PASS')
        self.assertEqual(self.summary()['status'], 'BLOCKED')
        self.assertEqual(read(self.root/'first'/'local-mock.json')['deleted'], [])

    def test_historical_redirect_without_connect_can_handoff_without_rewriting_failure(self):
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        from bcs_e2e.ws_transport import WebSocketTransportError
        error=WebSocketTransportError('rejected upgrade').with_record({
            'operation':'session.ws.connect','method':'WS','protocol':'websocket',
            'path':'/openapi/v1/collaboration/messages/ws','phase':'upgrade','http_status':302,
            'connected':False,'connect_request_id':None,'submitted_request_id':'rejected-upgrade',
            'request_id_echoed':False})
        with patch.object(PersistentLocalMockAdapter,'session_ws_connect',side_effect=error):
            result=self.run_case('first',case='RETAIN-SESSION-01')
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['socket_status'],'UNCONFIRMED')
        before=self.snapshot('first')
        second=self.run_case('second','first')
        self.assertEqual(second['canonical_summary_status'],'RETAINED_CASE_PASS')
        self.assertTrue(second['serial_admission']['previous_ws_rejected_before_connect'])
        self.assertEqual(self.snapshot('first'),before)
        self.assertEqual(self.summary('first')['status'],'BLOCKED')

    def test_transport_closed_socket_survives_failure_projection_and_report(self):
        from bcs_e2e.adapters import RemotePilotAdapter
        from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
        from bcs_e2e.ws_transport import SessionWebSocketClient
        from test_ws_evidence import ReplySocket
        remote=RemotePilotAdapter.__new__(RemotePilotAdapter)
        remote.websocket=SessionWebSocketClient('http://example.test')
        remote.ws_connections={}
        sock=ReplySocket(101)  # Upgrade succeeds; application connect is rejected.
        with patch.object(PersistentLocalMockAdapter,'session_ws_connect',side_effect=remote.session_ws_connect), \
             patch('bcs_e2e.ws_transport.socket.create_connection',return_value=sock):
            result=self.run_case('first',case='RETAIN-SESSION-01')
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['functional'],'FAIL')
        self.assertEqual(result['socket_status'],'CLOSED')
        self.assertTrue(sock.closed)
        record=next(r for r in result['evidence'] if r.get('operation')=='session.ws.connect')
        self.assertEqual(record['socket_close_status'],'CLOSED')
        self.assertEqual(record['http_status'],101)
        self.assertEqual(record['outcome'],'ERROR')
        self.assertEqual(self.summary('first')['status'],'BLOCKED')
        before=self.snapshot('first')
        self.assertEqual(self.run_case('second','first')['canonical_summary_status'],'RETAINED_CASE_PASS')
        self.assertEqual(self.snapshot('first'),before)
