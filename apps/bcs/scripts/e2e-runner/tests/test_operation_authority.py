"""Lease and credential loss stop subsequent actions, never broaden cleanup."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.operation_authority import AuthorizedAdapter
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pilot import run_case
from bcs_e2e.pool import PoolLease, PoolLeaseError
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load
from bcs_e2e.run_state import read
from bcs_e2e.reporting import build_report
from bcs_e2e.write_transport import WriteTransportError


class AuthorityUnitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'pool.lock'
        self.lease = PoolLease.acquire(self.path, 'authority-run', 'a' * 64)
        self.addCleanup(self.lease.close)
        self.raw = Mock()
        self.adapter = AuthorizedAdapter(self.raw, self.lease)

    def test_valid_lease_allows_call_without_replacing_reservation(self):
        before = self.path.read_bytes()
        self.adapter.group_create({'name': 'test'})
        self.raw.group_create.assert_called_once_with({'name': 'test'})
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_lock_latches_even_when_original_file_is_restored(self):
        original = self.path.read_bytes()
        self.path.unlink()
        with self.assertRaises(PoolLeaseError):
            self.adapter.group_create({})
        self.path.write_bytes(original)
        with self.assertRaises(PoolLeaseError):
            self.adapter.group_create({})
        self.assertEqual(self.raw.mock_calls, [])

    def test_replaced_guard_inode_cannot_authorize_call_or_release(self):
        self.path.with_suffix('.guard').unlink()
        self.path.with_suffix('.guard').write_text('new guard')
        with self.assertRaises(PoolLeaseError):
            self.adapter.group_delete('group-1')
        with self.assertRaises(PoolLeaseError):
            self.lease.release()
        self.assertTrue(self.path.exists())
        self.raw.group_delete.assert_not_called()

    def test_guard_closed_cannot_authorize_further_operations(self):
        self.lease.close()
        with self.assertRaises(PoolLeaseError):
            self.adapter.group_update('group-1', {})
        self.assertTrue(self.path.exists())

    def test_loss_during_request_fails_after_call_without_retry(self):
        self.raw.group_create.side_effect = lambda *_: self.path.unlink()
        with self.assertRaises(PoolLeaseError):
            self.adapter.group_create({})
        with self.assertRaises(PoolLeaseError):
            self.adapter.group_delete('group-1')
        self.raw.group_create.assert_called_once()
        self.raw.group_delete.assert_not_called()

    def test_auth_failure_preserves_original_error_and_blocks_cleanup(self):
        for status in (401, 403):
            raw = Mock()
            adapter = AuthorizedAdapter(raw, self.lease)
            error = WriteTransportError('synthetic auth refusal').with_record({'http_status': status})
            raw.session_update.side_effect = error
            with self.assertRaises(WriteTransportError) as raised:
                adapter.session_update('session-1', {})
            self.assertIs(raised.exception, error)
            with self.assertRaises(PoolLeaseError):
                adapter.session_delete('session-1')
            raw.session_delete.assert_not_called()

    def test_existing_socket_close_remains_available_after_lease_loss(self):
        self.path.unlink()
        with self.assertRaises(PoolLeaseError):
            self.adapter.session_update('session-1', {})
        self.adapter.session_ws_close('session-1')
        self.raw.session_ws_close.assert_called_once_with('session-1')
        with self.assertRaises(PoolLeaseError):
            self.adapter.session_ws_connect('group-1', 'session-1', 'test-value')


class AuthorityCaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        registry.start()
        self.addCleanup(registry.stop)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.state = self.root / 'state'
        prepare_run(self.profile, self.state, 'authority-run')
        self.path = Path(read(self.state / 'run.json')['lock_path'])
        self.raw = PersistentLocalMockAdapter(self.profile, self.state, case_id='PRE-01')

    def execute(self):
        return run_case(self.profile, self.state, 'authority-run', 'PRE-01',
                        adapter_factory=lambda *a, **k: self.raw)

    def test_replaced_lease_during_case_prevents_deletes_and_preserves_new_owner(self):
        original = self.raw.group_update

        def changed(group_id, body):
            result = original(group_id, body)
            payload = json.loads(self.path.read_text())
            payload['run_id'] = 'replacement-run'
            self.path.write_text(json.dumps(payload))
            return result

        with patch.object(self.raw, 'group_update', changed), \
                patch.object(self.raw, 'session_delete') as session_delete, \
                patch.object(self.raw, 'group_delete') as group_delete:
            report = self.execute()
        session_delete.assert_not_called()
        group_delete.assert_not_called()
        self.assertNotEqual(report['status'], 'PASS')
        self.assertEqual(report['operation_authority'], 'INVALIDATED')
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertEqual(json.loads(self.path.read_text())['run_id'], 'replacement-run')

    def test_expired_credential_retains_run_and_independent_cleanup_is_blocked(self):
        error = WriteTransportError('credential expired').with_record({'http_status': 401})
        with patch.object(self.raw, 'group_update', side_effect=error), \
                patch.object(self.raw, 'session_delete') as session_delete:
            report = self.execute()
        session_delete.assert_not_called()
        self.assertEqual(report['quiescence'], 'UNKNOWN')
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertTrue(self.path.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['cleanup', '--profile', str(ROOT / 'profiles/local.mock.json'),
                       '--state-dir', str(self.state), '--allow-write', '--confirm-run-id', 'authority-run'])
        self.assertEqual(rc, 2)
        self.assertTrue(self.path.exists())

    def test_independent_cleanup_loss_stops_before_parent_and_quarantines(self):
        with patch.object(self.raw, 'session_delete', side_effect=RuntimeError('temporary failure')):
            result = self.execute()
        self.assertEqual(result['quiescence'], 'CONFIRMED')
        original = PersistentLocalMockAdapter.session_delete

        def revoked(adapter, session_id):
            result = original(adapter, session_id)
            payload = json.loads(self.path.read_text())
            payload['run_id'] = 'replacement-run'
            self.path.write_text(json.dumps(payload))
            return result

        with patch.object(PersistentLocalMockAdapter, 'session_delete', revoked), \
                patch.object(PersistentLocalMockAdapter, 'group_delete') as delete_group, \
                contextlib.redirect_stdout(io.StringIO()):
            rc = main(['cleanup', '--profile', str(ROOT / 'profiles/local.mock.json'),
                       '--state-dir', str(self.state), '--allow-write', '--confirm-run-id', 'authority-run'])
        self.assertEqual(rc, 2)
        delete_group.assert_not_called()
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertEqual(json.loads(self.path.read_text())['run_id'], 'replacement-run')

    def test_lease_loss_during_final_checkpoint_never_yields_green_aggregate(self):
        from bcs_e2e.run_state import write

        def checkpoint(path, value):
            write(path, value)
            if path.name == 'run.json' and value['status'] == 'CASE_COMPLETED':
                self.path.unlink()

        with patch('bcs_e2e.pilot.write_state', side_effect=checkpoint):
            with self.assertRaises(PoolLeaseError):
                self.execute()
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertEqual(read(self.state / 'report-PRE-01.json')['status'], 'PASS')
        report = build_report(self.profile, self.state)
        self.assertEqual(report['cases'][0]['status'], 'BLOCKED')

    def test_connect_handoff_loss_closes_exact_socket_once_without_deletes(self):
        for close_fails in (False, True):
            with self.subTest(close_fails=close_fails):
                if close_fails:
                    self.setUp()
                raw = PersistentLocalMockAdapter(self.profile, self.state, case_id='PRE-02')
                original = raw.session_ws_connect

                def connect(*args):
                    result = original(*args)
                    self.path.unlink()
                    return result

                with patch.object(raw, 'session_ws_connect', side_effect=connect), \
                        patch.object(raw, 'session_ws_close', wraps=raw.session_ws_close,
                                     side_effect=RuntimeError('close failed') if close_fails else None) as close, \
                        patch.object(raw, 'session_delete') as delete:
                    result = run_case(self.profile, self.state, 'authority-run', 'PRE-02',
                                      adapter_factory=lambda *a, **k: raw)
                self.assertEqual(close.call_count, 1)
                delete.assert_not_called()
                self.assertEqual(result['authority_socket_close'], 'UNCONFIRMED' if close_fails else 'CLOSED')
                self.assertEqual(bool(raw.connected_sessions), close_fails)
                self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')

    def test_cleanup_final_checkpoint_loss_blocks_reacquisition(self):
        from bcs_e2e.run_state import write
        from bcs_e2e.profile import fingerprint
        from bcs_e2e.pool import pool_path, pool_scope
        result = self.execute()
        self.assertEqual(result['status'], 'PASS')
        lock = PoolLease.acquire(pool_path(pool_scope(self.profile)), 'authority-run', fingerprint(self.profile))
        lock.close()
        changed = False

        def checkpoint(path, value):
            nonlocal changed
            write(path, value)
            if path.name == 'run.json' and not changed:
                changed = True
                self.path.unlink()

        args = ['cleanup', '--profile', str(ROOT / 'profiles/local.mock.json'),
                '--state-dir', str(self.state), '--allow-write', '--confirm-run-id', 'authority-run']
        with patch('bcs_e2e.cli.write_state', side_effect=checkpoint), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 2)
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertEqual(build_report(self.profile, self.state)['cases'][0]['status'], 'BLOCKED')
        with patch('bcs_e2e.cli.PoolLease.acquire') as acquire, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 2)
        acquire.assert_not_called()
