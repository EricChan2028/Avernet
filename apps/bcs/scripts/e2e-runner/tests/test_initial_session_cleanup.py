"""Group creation's implicit Session must never escape the shared lifecycle."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.adapters import LocalMockAdapter
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger
from bcs_e2e.mock_state import MockStateError
from bcs_e2e.persistent_mock import PersistentLocalMockAdapter
from bcs_e2e.pilot import run_case, PilotError
from bcs_e2e.profile import fingerprint, load
from bcs_e2e.run_state import read


class InitialSessionCleanupTests(unittest.TestCase):
    def prepare(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools')
        registry.start()
        self.addCleanup(registry.stop)
        self.profile = load(ROOT / 'profiles/local.mock.json')
        self.state = self.root / 'run'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['prepare', '--profile', str(ROOT / 'profiles/local.mock.json'),
                                   '--state-dir', str(self.state), '--run-id', 'initial-test']), 0)
        self.adapter = LocalMockAdapter(self.profile)

    def execute(self, case_id):
        return run_case(self.profile, self.state, 'initial-test', case_id,
                        adapter_factory=lambda *args, **kwargs: self.adapter)

    def resources(self):
        with Ledger(self.state / 'run.sqlite', 'initial-test', fingerprint(self.profile), read_only=True) as ledger:
            return ledger.resource_snapshot()

    def test_every_pilot_registers_initial_session_before_mutation_and_deletes_it_before_group(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            with self.subTest(case=case):
                self.prepare()
                original = self.adapter.group_create
                def create(body):
                    rows = self.resources()
                    self.assertEqual([row['kind'] for row in rows], ['group', 'session'])
                    self.assertEqual({row['state'] for row in rows}, {'intended'})
                    return original(body)
                with patch.object(self.adapter, 'group_create', side_effect=create):
                    result = self.execute(case)
                self.assertEqual(result['status'], 'PASS')
                rows = self.resources()
                self.assertEqual(len(rows), {'PRE-01': 2, 'PRE-02': 3, 'PRE-03': 4}[case])
                self.assertEqual({row['state'] for row in rows}, {'deleted'})
                initial = next(iter(self.adapter.groups.values()))['initial_session_id']
                self.assertIn(initial, self.adapter.deleted_sessions)
                operations = result['evidence']
                initial_deleted = next(i for i, record in enumerate(operations)
                    if record['operation'] == 'session.delete' and initial in record['path'])
                group_deleted = next(i for i, record in enumerate(operations) if record['operation'] == 'group.delete')
                self.assertLess(initial_deleted, group_deleted)

    def test_missing_or_invalid_initial_id_retains_group_and_unresolved_child(self):
        for case in ('PRE-01', 'PRE-02', 'PRE-03'):
            for initial in (None, '', 'bad/id'):
                with self.subTest(case=case, initial=initial):
                    self.prepare()
                    original = self.adapter.group_create
                    def create(body):
                        data, evidence = original(body)
                        return {**data, 'initial_session_id': initial}, evidence
                    with patch.object(self.adapter, 'group_create', side_effect=create), \
                         patch.object(self.adapter, 'group_delete') as delete:
                        result = self.execute(case)
                    self.assertNotEqual(result['status'], 'PASS')
                    self.assertEqual(result['cleanup'], 'BLOCKED')
                    delete.assert_not_called()
                    rows = self.resources()
                    self.assertEqual([row['state'] for row in rows], ['live', 'intended'])
                    self.assertTrue(Path(read(self.state / 'run.json')['lock_path']).exists())

    def test_wrong_initial_parent_or_unconfirmed_delete_never_deletes_group(self):
        for failure in ('parent', 'delete', 'verify'):
            self.prepare()
            original_read = self.adapter.session_read
            def read_session(session):
                data, evidence = original_read(session)
                return {**data, 'group_id': 'foreign'}, evidence
            target = {'parent': 'session_read', 'delete': 'session_delete', 'verify': 'session_verify_deleted'}[failure]
            effect = read_session if failure == 'parent' else (
                (lambda session: ({'deleted': False}, {'operation': 'session.delete'})) if failure == 'delete'
                else PilotError('synthetic verification failure'))
            with patch.object(self.adapter, target, side_effect=effect), \
                 patch.object(self.adapter, 'group_delete') as delete:
                result = self.execute('PRE-01')
            self.assertEqual(result['functional'], 'PASS')
            self.assertEqual(result['cleanup'], 'BLOCKED')
            delete.assert_not_called()
            self.assertEqual({row['state'] for row in self.resources()}, {'live'})

    def test_lost_create_response_leaves_both_intents_and_no_blind_cleanup(self):
        self.prepare()
        original = self.adapter.group_create
        def lost(body):
            original(body)
            raise PilotError('synthetic response lost')
        with patch.object(self.adapter, 'group_create', side_effect=lost), \
             patch.object(self.adapter, 'group_delete') as group, \
             patch.object(self.adapter, 'session_delete') as session:
            result = self.execute('PRE-01')
        self.assertEqual(result['cleanup'], 'BLOCKED')
        self.assertEqual({row['state'] for row in self.resources()}, {'intended'})
        self.assertEqual(len(self.resources()), 2)
        group.assert_not_called()
        session.assert_not_called()

    def test_adapter_runtime_error_persists_failure_and_allows_exact_cleanup_recovery(self):
        self.prepare()
        self.adapter = PersistentLocalMockAdapter(self.profile, self.state, case_id='PRE-01')
        with patch.object(self.adapter, 'session_delete', side_effect=MockStateError('synthetic rejection')):
            report = self.execute('PRE-01')
        self.assertEqual(report['functional'], 'PASS')
        self.assertEqual(report['cleanup'], 'BLOCKED')
        state = read(self.state / 'run.json')
        self.assertEqual(state['status'], 'FAILED')
        self.assertEqual(state['quiescence'], 'CONFIRMED')
        saved = read(self.state / 'report-PRE-01.json')
        self.assertEqual(saved['cleanup'], 'BLOCKED')
        assertion = next(a for a in saved['assertions'] if a['id'] == 'PRE-01.owned_initial_session_deleted')
        self.assertEqual(assertion['status'], 'BLOCKED')
        self.assertTrue(Path(state['lock_path']).exists())
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(['cleanup', '--profile', str(ROOT / 'profiles/local.mock.json'),
                       '--state-dir', str(self.state), '--allow-write', '--confirm-run-id', 'initial-test'])
        self.assertEqual(rc, 0)
        self.assertEqual({row['state'] for row in self.resources()}, {'deleted'})
        self.assertFalse(Path(state['lock_path']).exists())
