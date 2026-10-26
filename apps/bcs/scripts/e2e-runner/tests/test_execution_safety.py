"""Fresh readers must not trust PASS strings or resource tombstones over effects."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cleanup import execute_cleanup
from bcs_e2e.cli import main
from bcs_e2e.execution_safety import inspect_execution
from bcs_e2e.ledger import Ledger
from bcs_e2e.pilot import run_case
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pool import PoolLease, pool_path, pool_scope
from bcs_e2e.prepare import prepare_run
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.reporting import build_report, render_junit, render_markdown


class ExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.digest = fingerprint(self.profile)
        self.state = self.root / 'run'
        self.run_id = 'safety-run'
        registry = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        registry.start()
        self.addCleanup(registry.stop)
        prepare_run(self.profile, self.state, self.run_id)
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            result = run_case(self.profile, self.state, self.run_id, case)
            self.assertEqual(result['status'], 'PASS', result)
        self.ledger = Ledger(self.state / 'run.sqlite', self.run_id, self.digest)
        self.addCleanup(self.ledger.close)

    def mutate(self, sql):
        with self.ledger.db:
            self.ledger.db.execute(sql)

    def test_normal_read_only_snapshot_and_all_formats(self):
        before = (self.state / 'run.sqlite').read_bytes()
        with Ledger(self.state / 'run.sqlite', self.run_id, self.digest, read_only=True) as ledger:
            queries = []
            ledger.db.set_trace_callback(queries.append)
            result = inspect_execution(ledger)
        self.assertEqual(result['status'], 'CONFIRMED')
        self.assertEqual(result['session_count'], 5)
        self.assertEqual(len(queries), 5)  # constant reads; no per-effect N+1
        self.assertEqual(before, (self.state / 'run.sqlite').read_bytes())
        summary = build_report(self.profile, self.state)
        self.assertEqual(summary['status'], 'PILOT_PASS')
        self.assertIn('Execution safety: CONFIRMED', render_markdown(summary))
        properties = ET.fromstring(render_junit(summary)).findall('.//property[@name="execution_safety"]')
        self.assertEqual([item.get('value') for item in properties], ['CONFIRMED'] * 3)

    def test_deleted_sessions_do_not_hide_active_unknown_or_cancel_pending_effects(self):
        for state in ('QUEUED', 'RUNNING', 'CANCELLING', 'UNKNOWN', 'RETAINED'):
            with self.subTest(state=state), self.ledger.db:
                self.ledger.db.execute('UPDATE execution_effect SET state=? WHERE rowid=1', (state,))
                self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')
                self.assertEqual(self.ledger.cleanup_plan()['resources'], [])
                adapter = Mock()
                result = execute_cleanup(self.ledger, adapter, dry_run=False)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(adapter.mock_calls, [])

    def test_green_report_cannot_override_ledger_and_junit_is_not_green(self):
        self.mutate("UPDATE execution_effect SET state='UNKNOWN' WHERE rowid=1")
        summary = build_report(self.profile, self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertEqual(summary['execution_safety']['status'], 'BLOCKED')
        self.assertEqual({case['status'] for case in summary['cases']}, {'BLOCKED'})
        self.assertEqual(len(ET.fromstring(render_junit(summary)).findall('.//skipped')), 3)
        self.assertIn('Execution safety: BLOCKED', render_markdown(summary))

    def test_release_keeps_reservation_despite_pass_run_json(self):
        self.mutate("UPDATE execution_effect SET state='UNKNOWN' WHERE rowid=1")
        lock = pool_path(pool_scope(self.profile))
        lease = PoolLease.acquire(lock, self.run_id, self.digest)
        lease.close()  # retain reservation, as after a process restart
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['release', '--state-dir', str(self.state)])
        self.assertEqual(rc, 2)
        self.assertTrue(lock.exists())
        self.assertEqual(json.loads(lock.read_text())['run_id'], self.run_id)

    def test_missing_or_foreign_session_binding_fails_closed(self):
        for binding in (None, 'foreign-session'):
            with self.subTest(binding=binding), self.ledger.db:
                self.ledger.db.execute('UPDATE execution_effect SET session_id=? WHERE rowid=1', (binding,))
                self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')

    def test_terminal_state_without_id_or_evidence_does_not_qualify(self):
        for field in ('effect_id', 'terminal_reason', 'evidence_request_id'):
            with self.subTest(field=field):
                original = self.ledger.db.execute(f'SELECT {field} FROM execution_effect WHERE rowid=1').fetchone()[0]
                self.mutate(f'UPDATE execution_effect SET {field}=NULL WHERE rowid=1')
                self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')
                with self.ledger.db:
                    self.ledger.db.execute(f'UPDATE execution_effect SET {field}=? WHERE rowid=1', (original,))

    def test_wrong_termination_binding_and_missing_proof_are_blocked(self):
        self.mutate("UPDATE termination_state SET attempt=99 WHERE rowid=1")
        self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')
        self.mutate('DELETE FROM termination_state')
        self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')

    def test_missing_marker_is_not_a_legacy_downgrade(self):
        self.mutate('DROP TABLE execution_contract')
        self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')

    def test_unsupported_contract_version_is_blocked(self):
        self.mutate('UPDATE execution_contract SET version=999')
        self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')

    def test_old_effect_schema_without_marker_is_blocked(self):
        self.mutate('DROP TABLE execution_contract')
        self.assertEqual(execute_cleanup(self.ledger, Mock(), dry_run=True)['status'], 'BLOCKED')

    def test_legacy_resource_only_ledger_is_not_claimed_as_execution_proof(self):
        with Ledger(self.root / 'old.sqlite', 'old-run', self.digest, create=True) as legacy:
            self.assertEqual(inspect_execution(legacy), {'status': 'NOT_TRACKED', 'issues': []})

    def test_inspection_budget_exhaustion_is_blocked(self):
        with patch('bcs_e2e.execution_safety.MAX_ROWS', 2):
            self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')

    def test_late_effect_after_resource_cleanup_blocks_automatic_pool_release(self):
        state = self.root / 'late-run'
        prepare_run(self.profile, state, 'late-run')
        original = PersistentLocalMockAdapter.group_verify_deleted

        def verified_with_late_effect(adapter, group_id):
            result = original(adapter, group_id)
            session_id = adapter.groups[group_id]['initial_session_id']
            handle = adapter._execution_ledger.intent('PRE-01', 1, 'context', 'late-request',
                                                       session_id=session_id)
            adapter._execution_ledger.unknown(handle, reason='late response lost')
            return result

        with patch.object(PersistentLocalMockAdapter, 'group_verify_deleted', verified_with_late_effect):
            result = run_case(self.profile, state, 'late-run', 'PRE-01')
        self.assertEqual(result['functional'], 'PASS')
        self.assertEqual(result['cleanup'], 'PASS')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['quiescence'], 'UNKNOWN')
        run = json.loads((state / 'run.json').read_text())
        self.assertEqual(run['status'], 'FAILED')
        self.assertTrue(Path(run['lock_path']).exists())

    def test_schema_error_is_blocked_not_an_empty_inventory(self):
        self.mutate('ALTER TABLE execution_effect RENAME COLUMN effect_id TO unexpected_column')
        self.assertEqual(inspect_execution(self.ledger)['status'], 'BLOCKED')

    def test_missing_required_tables_block_even_if_they_resemble_a_legacy_ledger(self):
        self.mutate('DROP TABLE execution_effect')
        self.mutate('DROP TABLE execution_contract')
        self.assertEqual(inspect_execution(self.ledger, required=True)['status'], 'BLOCKED')
        self.assertEqual(build_report(self.profile, self.state)['status'], 'BLOCKED')
        lock = pool_path(pool_scope(self.profile))
        lease = PoolLease.acquire(lock, self.run_id, self.digest)
        lease.close()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['release', '--state-dir', str(self.state)])
        self.assertEqual(rc, 2)
        self.assertTrue(lock.exists())

    def test_missing_ledger_path_does_not_downgrade_a_new_run_report(self):
        path = self.state / 'run.json'
        state = json.loads(path.read_text())
        del state['ledger_path']
        path.write_text(json.dumps(state))
        self.assertEqual(build_report(self.profile, self.state)['status'], 'BLOCKED')

    def test_cleanup_cannot_downgrade_when_contract_disappears_during_final_verification(self):
        lock = pool_path(pool_scope(self.profile))
        lease = PoolLease.acquire(lock, self.run_id, self.digest)
        lease.close()
        calls = []

        def scope(adapter, run_id, resources):
            calls.append(run_id)
            if len(calls) == 2:
                self.mutate('DROP TABLE execution_effect')
                self.mutate('DROP TABLE execution_contract')
            return {'status': 'VERIFIED'}

        with patch.object(PersistentLocalMockAdapter, 'verify_cleanup_scope', scope), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = main(['cleanup', '--profile', str(ROOT / 'profiles/local.mock.json'),
                       '--state-dir', str(self.state), '--allow-write', '--confirm-run-id', self.run_id])
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(lock.exists())
        receipt = json.loads((self.state / 'cleanup.json').read_text())
        self.assertEqual(receipt['status'], 'BLOCKED')
        self.assertEqual(receipt['phase'], 'FINAL_VERIFICATION')
