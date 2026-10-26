"""Explicit recovery never invents terminal evidence or changes failed Stories."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pilot import run_case
from bcs_e2e.pool import PoolLease
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import load, fingerprint
from bcs_e2e.reporting import build_report
from bcs_e2e.run_state import read, write


class LocalRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        self.profile_path = ROOT / 'profiles/local.mock.json'
        self.profile = load(self.profile_path)
        self.digest = fingerprint(self.profile)
        self.registry = self.root / 'pools'
        registry = patch('bcs_e2e.pool.pool_root', return_value=self.registry)
        registry.start()
        self.addCleanup(registry.stop)
        prepare_run(self.profile, self.state, 'recovery-run')
        self.lock = Path(read(self.state / 'run.json')['lock_path'])

    def args(self):
        return ['recover-local', '--profile', str(self.profile_path), '--state-dir', str(self.state),
                '--allow-write', '--confirm-run-id', 'recovery-run']

    def command(self, args=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(self.args() if args is None else args)
        return rc, json.loads(output.getvalue())

    def interrupted(self):
        # Terminal effects/control proof exist; resource deletion did not finish.
        with patch.object(PersistentLocalMockAdapter, 'session_delete', side_effect=RuntimeError('delete rejected')):
            report = run_case(self.profile, self.state, 'recovery-run', 'PRE-01')
        self.assertEqual(report['cleanup'], 'BLOCKED')
        state = read(self.state / 'run.json')
        state.update(status='QUARANTINED', quiescence='UNKNOWN')
        write(self.state / 'run.json', state)

    def test_explicit_recovery_cleans_exact_resources_without_rewriting_original_report(self):
        self.interrupted()
        before = (self.state / 'report-PRE-01.json').read_bytes()
        rc, result = self.command()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'RECOVERED')
        self.assertEqual(result['pool'], 'RELEASED')
        self.assertEqual(result['original_status'], 'QUARANTINED')
        self.assertEqual(result['functional_result'], 'UNCHANGED')
        self.assertEqual((self.state / 'report-PRE-01.json').read_bytes(), before)
        self.assertEqual(read(self.state / 'run.json')['status'], 'RECOVERED')
        self.assertFalse(self.lock.exists())
        self.assertEqual(build_report(self.profile, self.state)['status'], 'BLOCKED')
        with Ledger(self.state / 'run.sqlite', 'recovery-run', self.digest, read_only=True) as ledger:
            self.assertEqual({r['state'] for r in ledger.resource_snapshot()}, {'deleted'})
        with patch('bcs_e2e.recovery.PoolLease.from_file') as acquire:
            self.assertEqual(self.command()[0], 2)
            acquire.assert_not_called()

    def test_unknown_effect_never_reaches_adapter_or_mutates_state(self):
        self.interrupted()
        with Ledger(self.state / 'run.sqlite', 'recovery-run', self.digest) as ledger:
            with ledger.db:
                ledger.db.execute("UPDATE execution_effect SET state='UNKNOWN' WHERE rowid=1")
        before = (self.state / 'run.json').read_bytes()
        with patch('bcs_e2e.recovery.PersistentLocalMockAdapter') as construct:
            rc, _ = self.command()
        self.assertEqual(rc, 2)
        construct.assert_not_called()
        self.assertEqual((self.state / 'run.json').read_bytes(), before)
        self.assertTrue(self.lock.exists())
        self.assertEqual(list(self.state.glob('recovery-*.json')), [])

    def test_missing_lease_is_never_reacquired(self):
        self.interrupted()
        self.lock.unlink()
        with patch('bcs_e2e.pool.PoolLease.acquire') as acquire:
            self.assertEqual(self.command()[0], 2)
        acquire.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_active_command_and_foreign_reservation_both_block(self):
        self.interrupted()
        with PoolLease.from_file(self.lock, 'recovery-run', self.digest):
            self.assertEqual(self.command()[0], 2)
        payload = json.loads(self.lock.read_text())
        payload['run_id'] = 'foreign-run'
        self.lock.write_text(json.dumps(payload))
        self.assertEqual(self.command()[0], 2)
        self.assertEqual(json.loads(self.lock.read_text())['run_id'], 'foreign-run')

    def test_confirmation_and_remote_mode_rejected_before_lease(self):
        self.interrupted()
        invalid_args = self.args()
        invalid_args.remove('--allow-write')
        wrong_id = self.args()
        wrong_id[-1] = 'other-run'
        remote = self.root / 'remote.json'
        remote.write_text((ROOT / 'profiles/preprod.example.json').read_text())
        remote_args = self.args()
        remote_args[2] = str(remote)
        for args in (invalid_args, wrong_id, remote_args):
            with patch('bcs_e2e.recovery.PoolLease.from_file') as lease:
                self.assertEqual(self.command(args)[0], 2)
                lease.assert_not_called()

    def test_open_connection_or_inventory_drift_cannot_be_repaired_by_recovery(self):
        self.interrupted()
        path = self.state / 'local-mock.json'
        original = read(path)
        changed = dict(original)
        changed['connected_sessions'] = list(original['sessions'])
        write(path, changed)
        self.assertEqual(self.command()[0], 2)
        self.assertEqual(read(path)['connected_sessions'], changed['connected_sessions'])
        self.assertTrue(self.lock.exists())

    def test_partial_cleanup_failure_stays_quarantined_and_keeps_receipt(self):
        self.interrupted()
        with patch.object(PersistentLocalMockAdapter, 'group_delete', side_effect=RuntimeError('refused')):
            rc, report = self.command()
        self.assertEqual(rc, 2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertTrue(self.lock.exists())
        first_receipt = next(self.state.glob('recovery-*.json'))
        before = first_receipt.read_bytes()
        rc, result = self.command()
        self.assertEqual(rc, 0, result)
        self.assertEqual(first_receipt.read_bytes(), before)
        self.assertEqual(len(list(self.state.glob('recovery-*.json'))), 2)

    def test_initial_receipt_failure_does_not_release_pool_or_start_deletion(self):
        self.interrupted()
        def failing(path, value):
            if path.name.startswith('recovery-'):
                raise OSError('synthetic receipt failure')
            write(path, value)
        with patch('bcs_e2e.recovery.write', side_effect=failing), \
                patch.object(PersistentLocalMockAdapter, 'session_delete') as delete:
            self.assertEqual(self.command()[0], 2)
        delete.assert_not_called()
        self.assertTrue(self.lock.exists())

    def test_real_child_crash_then_fresh_interpreter_recovery(self):
        code = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
from bcs_e2e.cli import main
from bcs_e2e.run_state import write
import bcs_e2e.pool as pool
pool.pool_root = lambda: Path(sys.argv[2])
def checkpoint(path, value):
    if path.name == 'run.json' and value.get('status') == 'CASE_COMPLETED':
        os._exit(73)
    write(path, value)
args = [sys.argv[1], '--profile', sys.argv[4], '--state-dir', sys.argv[3],
        '--allow-write', '--confirm-run-id', 'recovery-run']
if sys.argv[1] == 'run': args += ['--run-id', 'recovery-run', '--case', 'PRE-01']
with patch('socket.socket', side_effect=AssertionError('offline only')), \\
     patch('bcs_e2e.pilot.write_state', side_effect=checkpoint):
    raise SystemExit(main(args))
'''
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        def child(command):
            return subprocess.run([sys.executable, '-c', code, command, str(self.registry),
                                   str(self.state), str(self.profile_path)], env=env,
                                  capture_output=True, text=True, timeout=20)
        crashed = child('run')
        self.assertEqual(crashed.returncode, 73, crashed.stderr)
        self.assertEqual(read(self.state / 'run.json')['status'], 'RUNNING')
        before = (self.state / 'report-PRE-01.json').read_bytes()
        recovered = child('recover-local')
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        result = json.loads(recovered.stdout)
        self.assertEqual(result['original_status'], 'RUNNING')
        self.assertEqual(result['status'], 'RECOVERED')
        self.assertEqual((self.state / 'report-PRE-01.json').read_bytes(), before)
        self.assertFalse(self.lock.exists())

    def test_final_lease_loss_does_not_reacquire_or_claim_recovered(self):
        self.interrupted()
        changed = False
        def checkpoint(path, value):
            nonlocal changed
            write(path, value)
            if path.name == 'run.json' and value.get('recovery_phase') == 'RELEASE_PENDING' and not changed:
                changed = True
                payload = json.loads(self.lock.read_text())
                payload['run_id'] = 'foreign-run'
                self.lock.write_text(json.dumps(payload))
        with patch('bcs_e2e.recovery.write', side_effect=checkpoint):
            self.assertEqual(self.command()[0], 2)
        self.assertEqual(read(self.state / 'run.json')['status'], 'QUARANTINED')
        self.assertEqual(json.loads(self.lock.read_text())['run_id'], 'foreign-run')
        receipt = read(next(self.state.glob('recovery-*.json')))
        self.assertEqual(receipt['status'], 'CLEANUP_VERIFIED')
        self.assertEqual(receipt['pool'], 'RELEASE_PENDING')

    def test_recovered_run_cannot_qualify_even_if_all_original_cases_were_green(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            self.assertEqual(run_case(self.profile, self.state, 'recovery-run', case)['status'], 'PASS')
        before = {p.name: p.read_bytes() for p in self.state.glob('report-*.json')}
        lease = PoolLease.acquire(self.lock, 'recovery-run', self.digest)
        lease.close()
        state = read(self.state / 'run.json')
        state.update(status='QUARANTINED', quiescence='UNKNOWN')
        write(self.state / 'run.json', state)
        self.assertEqual(self.command()[0], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.glob('report-*.json')})
        report = build_report(self.profile, self.state)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual({r['status'] for r in report['cases']}, {'BLOCKED'})

    def test_partial_recovery_artifact_blocks_new_prepare(self):
        path = self.root / 'partial'
        path.mkdir()
        write(path / ('recovery-' + 'a' * 32 + '.json'), {'schema_version': 1, 'status': 'RECOVERING'})
        with self.assertRaises(ValueError):
            prepare_run(self.profile, path, 'other-run')

    def crash_recovery_before_release(self):
        code = """
import os, sys
from pathlib import Path
from unittest.mock import patch
from bcs_e2e.cli import main
from bcs_e2e.run_state import write
import bcs_e2e.pool as pool
pool.pool_root = lambda: Path(sys.argv[1])
def checkpoint(path, value):
    write(path, value)
    if path.name == 'run.json' and value.get('recovery_phase') == 'RELEASE_PENDING':
        os._exit(74)
with patch('bcs_e2e.recovery.write', side_effect=checkpoint):
    raise SystemExit(main(['recover-local', '--profile', sys.argv[3], '--state-dir', sys.argv[2],
                          '--allow-write', '--confirm-run-id', 'recovery-run']))
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        child = subprocess.run([sys.executable, '-c', code, str(self.registry), str(self.state),
                                str(self.profile_path)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 74, child.stdout + child.stderr)
        self.assertEqual(read(self.state / 'run.json')['status'], 'RECOVERING')
        self.assertTrue(self.lock.exists())

    def test_recovery_crash_before_release_can_be_explicitly_finished_without_stealing_lock(self):
        self.interrupted()
        self.crash_recovery_before_release()
        old_receipt = next(self.state.glob('recovery-*.json'))
        before = old_receipt.read_bytes()
        with patch('bcs_e2e.pool.PoolLease.acquire') as acquire:
            rc, result = self.command()
        self.assertEqual(rc, 0, result)
        acquire.assert_not_called()
        self.assertEqual(old_receipt.read_bytes(), before)
        self.assertEqual(result['original_status'], 'QUARANTINED')
        self.assertFalse(self.lock.exists())

    def test_manual_release_after_recovery_crash_cannot_requalify_original_passes(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            self.assertEqual(run_case(self.profile, self.state, 'recovery-run', case)['status'], 'PASS')
        lease = PoolLease.acquire(self.lock, 'recovery-run', self.digest)
        lease.close()
        state = read(self.state / 'run.json')
        state.update(status='QUARANTINED', quiescence='UNKNOWN')
        write(self.state / 'run.json', state)
        self.crash_recovery_before_release()
        rc, result = self.command(['release', '--state-dir', str(self.state)])
        self.assertEqual(rc, 0, result)
        self.assertEqual(read(self.state / 'run.json')['status'], 'RELEASED')
        report = build_report(self.profile, self.state)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual({r['status'] for r in report['cases']}, {'BLOCKED'})
