"""Cancellation anywhere in a Story forbids ordinary follow-up target calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.correlation import CorrelatedEvidence
from bcs_e2e.errors import primary_error
from bcs_e2e.ledger import Ledger
from bcs_e2e.operation_authority import AuthorizedAdapter
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pilot import run_case
from bcs_e2e.pool import PoolLease, PoolLeaseError
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import read


class CancellationFenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / 'state'
        self.profile_path = ROOT / 'profiles/local.mock.json'
        self.profile = load(self.profile_path)
        self.registry = self.root / 'pools'
        patcher = patch('bcs_e2e.pool.pool_root', return_value=self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        prepare_run(self.profile, self.state, 'cancel-run')
        self.lock = Path(read(self.state / 'run.json')['lock_path'])

    def test_cancellation_in_adapter_latches_after_exception_is_caught(self):
        with PoolLease.from_file(self.lock, 'cancel-run', fingerprint(self.profile)) as lease:
            raw = Mock()
            adapter = AuthorizedAdapter(raw, lease)
            raw.group_update.side_effect = KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                adapter.group_update('group-1', {})
            with self.assertRaises(PoolLeaseError):
                adapter.group_delete('group-1')
            raw.group_delete.assert_not_called()
            adapter.session_ws_close('owned-session')
            raw.session_ws_close.assert_called_once()

    def test_finally_after_external_cancellation_is_fenced(self):
        with PoolLease.from_file(self.lock, 'cancel-run', fingerprint(self.profile)) as lease:
            raw = Mock()
            adapter = AuthorizedAdapter(raw, lease)
            try:
                try:
                    raise KeyboardInterrupt()
                finally:
                    with self.assertRaises(PoolLeaseError):
                        adapter.group_delete('group-1')
            except KeyboardInterrupt:
                pass
            raw.group_delete.assert_not_called()
            with self.assertRaises(PoolLeaseError):
                adapter.group_create({})

    def test_chained_secondary_error_cannot_reenable_cleanup(self):
        with PoolLease.from_file(self.lock, 'cancel-run', fingerprint(self.profile)) as lease:
            raw = Mock()
            adapter = AuthorizedAdapter(raw, lease)
            def interrupted():
                try:
                    raise KeyboardInterrupt()
                finally:
                    raise OSError('secondary')
            raw.group_update.side_effect = interrupted
            with self.assertRaises(OSError) as error:
                adapter.group_update()
            self.assertIsInstance(primary_error(error.exception), KeyboardInterrupt)
            with self.assertRaises(PoolLeaseError):
                adapter.group_delete('group-1')
            raw.group_delete.assert_not_called()

    def test_cancellation_in_ws_evidence_closes_socket_without_deleting_resources(self):
        raw = PersistentLocalMockAdapter(self.profile, self.state, case_id='PRE-02')
        original = CorrelatedEvidence.append
        def evidence(records, value):
            if value.get('operation') == 'session.ws.connect':
                raise KeyboardInterrupt()
            return original(records, value)
        with patch.object(CorrelatedEvidence, 'append', evidence), \
                patch.object(raw, 'session_ws_close', wraps=raw.session_ws_close) as close, \
                patch.object(raw, 'session_delete') as delete, \
                patch.object(raw, 'group_delete') as group:
            with self.assertRaises(KeyboardInterrupt):
                run_case(self.profile, self.state, 'cancel-run', 'PRE-02',
                         adapter_factory=lambda *a, **k: raw)
        close.assert_called_once()
        delete.assert_not_called()
        group.assert_not_called()
        self.assertFalse(raw.connected_sessions)
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertTrue(self.lock.exists())
        with Ledger(self.state / 'run.sqlite', 'cancel-run', fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual({r['state'] for r in ledger.resource_snapshot()}, {'live'})

    def test_non_cancel_failure_still_allows_proven_exact_cleanup(self):
        raw = PersistentLocalMockAdapter(self.profile, self.state, case_id='PRE-01')
        with patch.object(raw, 'group_update', side_effect=RuntimeError('assertion failed')):
            report = run_case(self.profile, self.state, 'cancel-run', 'PRE-01',
                              adapter_factory=lambda *a, **k: raw)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['cleanup'], 'PASS')
        self.assertEqual(report['quiescence'], 'CONFIRMED')
        self.assertEqual(read(self.state / 'run.json')['status'], 'FAILED')

    def test_real_sigterm_child_keeps_unsettled_effects_and_resources(self):
        code = '''
import os, signal, sys
from pathlib import Path
from unittest.mock import patch
from bcs_e2e.pilot import run_case
from bcs_e2e.profile import load
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
import bcs_e2e.pool as pool
pool.pool_root = lambda: Path(sys.argv[1])
def stop(signum, frame): raise KeyboardInterrupt()
signal.signal(signal.SIGTERM, stop)
profile = load(Path(sys.argv[3]))
state = Path(sys.argv[2])
raw = PersistentLocalMockAdapter(profile, state, case_id='PRE-02')
def interrupted(*args): os.kill(os.getpid(), signal.SIGTERM)
try:
    with patch.object(raw, 'session_update', interrupted), \\
         patch('socket.socket', side_effect=AssertionError('offline only')):
        run_case(profile, state, 'cancel-run', 'PRE-02', adapter_factory=lambda *a, **k: raw)
except KeyboardInterrupt:
    raise SystemExit(130)
raise SystemExit(1)
'''
        child = subprocess.run([sys.executable, '-c', code, str(self.registry), str(self.state),
                                str(self.profile_path)], capture_output=True, text=True, timeout=20,
                               env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(child.returncode, 130, child.stdout + child.stderr)
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertTrue(self.lock.exists())
        with Ledger(self.state / 'run.sqlite', 'cancel-run', fingerprint(self.profile), read_only=True) as ledger:
            self.assertEqual(len(ledger.resource_snapshot()), 3)
            self.assertEqual({r['state'] for r in ledger.resource_snapshot()}, {'live'})
            self.assertEqual({r['state'] for r in ledger.execution_effects()}, {'QUEUED'})

    def test_cancellation_after_an_ordinary_error_still_invalidates_authority(self):
        with PoolLease.from_file(self.lock, 'cancel-run', fingerprint(self.profile)) as lease:
            for secondary in (False, True):
                raw = Mock()
                adapter = AuthorizedAdapter(raw, lease)
                def callback():
                    try:
                        try:
                            raise RuntimeError('earlier ordinary error')
                        except RuntimeError:
                            raise KeyboardInterrupt()
                    finally:
                        if secondary:
                            raise OSError('later secondary')
                raw.group_update.side_effect = callback
                with self.assertRaises(OSError if secondary else KeyboardInterrupt):
                    adapter.group_update()
                with self.assertRaises(PoolLeaseError):
                    adapter.group_delete('group-1')
                raw.group_delete.assert_not_called()

    def test_visible_chain_cancel_is_found_but_suppression_and_cycles_are_respected(self):
        from bcs_e2e.errors import has_cancellation
        ordinary = RuntimeError('ordinary')
        cancel = KeyboardInterrupt()
        cancel.__context__ = ordinary
        later = OSError('later')
        later.__context__ = cancel
        self.assertTrue(has_cancellation(later))
        later.__suppress_context__ = True
        self.assertFalse(has_cancellation(later))
        later.__cause__ = cancel
        self.assertTrue(has_cancellation(later))
        ordinary.__context__ = ordinary
        self.assertFalse(has_cancellation(ordinary))
