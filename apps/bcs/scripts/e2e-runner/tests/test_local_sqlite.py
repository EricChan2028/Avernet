"""Synthetic SQLite contract tests; live schema is tested separately."""
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.evidence import EvidenceError
from bcs_e2e.ledger import Ledger, LedgerError
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_sqlite import OwnedSqliteEvidenceProvider
from bcs_e2e.owned_bcs import OwnedBcsProcess


class OwnedSqliteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        inputs = prepare_local_bcs_inputs(self.root / 'runtime', run_id='sqlite-run', port=45000)
        self.runtime = Mock(spec=OwnedBcsProcess)
        self.runtime.inputs, self.runtime.root = inputs, inputs.root
        self.runtime.run_id, self.runtime.state, self.runtime.ready = 'sqlite-run', 'RUNNING', True
        self.runtime.child = Mock()
        self.runtime.child.poll.return_value = None
        self.runtime._owns_listener.return_value = True
        self.path = inputs.root / 'bcs.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE bcs_groups(group_id TEXT, env TEXT, driver_bot TEXT, originator TEXT, status TEXT, secret TEXT);
            CREATE TABLE bcs_group_sessions(session_id TEXT, env TEXT, group_id TEXT, status TEXT, session_title TEXT);
            CREATE TABLE bcs_session_files(file_id TEXT, env TEXT, session_id TEXT, status TEXT, size INTEGER, sha256 TEXT, storage_backend TEXT);
            INSERT INTO bcs_groups VALUES ('g1','local','bot-driver','human_actual','active','secret-value');
            INSERT INTO bcs_group_sessions VALUES ('s1','local','g1','running','synthetic title');
            INSERT INTO bcs_session_files VALUES ('f1','local','s1','Ready',12,NULL,'local');
        ''')
        metadata = self.path.stat()
        self.runtime.database_identity = (metadata.st_dev, metadata.st_ino)
        self.ledger_path = self.root / 'ledger.sqlite'
        self.ledger = Ledger(self.ledger_path, 'sqlite-run', 'a' * 64, create=True)
        self.addCleanup(self.ledger.close)
        self.handles = []
        for kind, resource in (('group', 'g1'), ('session', 's1'), ('file', 'f1')):
            parent = self.handles[-1] if self.handles else None
            handle = self.ledger.intent('PRE-03', 1, kind, 'request-' + resource, parent=parent)
            self.ledger.observed(handle, resource, origin='created')
            self.handles.append(handle)
        self.provider = self.provider_for()
        self.provider.admit(('group.persisted', 'session.persisted', 'file.persisted'))

    def provider_for(self, **kwargs):
        return OwnedSqliteEvidenceProvider(self.runtime, ledger_path=self.ledger_path,
            profile_digest='a' * 64, case_id=kwargs.get('case_id', 'PRE-03'), attempt=kwargs.get('attempt', 1))

    def test_real_rows_are_projected_without_synthesizing_originator_or_hash(self):
        group = self.provider.observe('group.persisted', 'g1')
        self.assertEqual(group['source'], 'local-sqlite')
        self.assertEqual(group['rows'][0]['originator'], 'human_actual')
        self.assertNotIn('secret', group['rows'][0])
        self.assertEqual(group['plan']['filters'], {'env': 'local', 'group_id': 'g1'})
        self.assertEqual(group['plan']['limit'], 2)
        session = self.provider.observe('session.persisted', 's1')
        self.assertEqual(session['rows'][0]['session_title'], 'synthetic title')
        file = self.provider.observe('file.persisted', 'f1')['rows'][0]
        self.assertEqual(file['status'], 'ready')
        self.assertIsNone(file['sha256'])

    def test_stored_status_mapping_preserves_sqlite_rows(self):
        for raw, semantic in (('Pending', 'pending'), ('Ready', 'ready'),
                              ('Deleting', 'deleting'), ('Failed', 'failed')):
            self.db.execute('UPDATE bcs_session_files SET status=?', (raw,))
            self.db.commit()
            row = self.provider.observe('file.persisted', 'f1')['rows'][0]
            self.assertEqual(row['status'], semantic)
            self.assertIsNone(row['sha256'])
            self.assertEqual(row['size'], 12)
            self.assertEqual(self.db.execute('SELECT status FROM bcs_session_files').fetchone()[0], raw)
        self.db.execute('UPDATE bcs_session_files SET status=NULL')
        self.db.commit()
        with self.assertRaisesRegex(EvidenceError, 'persisted file status'):
            self.provider.observe('file.persisted', 'f1')

    def test_read_only_connection_and_no_database_or_ledger_mutation(self):
        snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (self.path, self.ledger_path)}
        with self.provider._connection() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM bcs_groups')
        self.provider.observe('group.persisted', 'g1')
        for path, snapshot in snapshots.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)

    def test_unknown_resource_and_other_case_attempt_never_query_product_rows(self):
        for provider, resource in ((self.provider, 'foreign'), (self.provider_for(case_id='PRE-01'), 'g1'),
                                    (self.provider_for(attempt=2), 'g1')):
            provider.admit(('group.persisted',))
            with patch.object(provider, '_connection') as connect, self.assertRaises(EvidenceError):
                provider.observe('group.persisted', resource)
            connect.assert_not_called()

    def test_borrowed_deleted_and_unknown_resources_are_refused(self):
        for origin, state in (('borrowed', 'live'), ('reused', 'live'), ('unknown', 'unknown'), ('created', 'deleted')):
            with self.subTest(origin=origin, state=state):
                with self.ledger.db:
                    self.ledger.db.execute('UPDATE resource SET origin=?, state=? WHERE handle=?',
                                           (origin, state, self.handles[0]))
                with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
                with self.assertRaises(EvidenceError): self.provider.observe('file.persisted', 'f1')

    def test_parent_mismatch_is_not_valid_persistence(self):
        for table, column, assertion, resource in (
                ('bcs_group_sessions', 'group_id', 'session.persisted', 's1'),
                ('bcs_session_files', 'session_id', 'file.persisted', 'f1')):
            self.db.execute(f'UPDATE {table} SET {column}=?', ('foreign',))
            self.db.commit()
            with self.assertRaises(EvidenceError): self.provider.observe(assertion, resource)

    def test_missing_cross_environment_duplicate_rows_and_unknown_status_block(self):
        self.db.execute("UPDATE bcs_groups SET env='pre'")
        self.db.commit()
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        self.db.execute("UPDATE bcs_groups SET env='local'")
        self.db.execute('INSERT INTO bcs_groups SELECT * FROM bcs_groups')
        self.db.execute("UPDATE bcs_session_files SET status='ready'")
        self.db.commit()
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        with self.assertRaises(EvidenceError): self.provider.observe('file.persisted', 'f1')

    def test_schema_drift_and_view_are_rejected_before_business_query(self):
        self.db.execute('DROP TABLE bcs_groups')
        self.db.execute('CREATE TABLE bcs_groups(group_id TEXT)')
        self.db.commit()
        with self.assertRaises(EvidenceError): self.provider.admit(('group.persisted',))
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        self.db.execute('DROP TABLE bcs_groups')
        self.db.execute('CREATE VIEW bcs_groups AS SELECT * FROM bcs_group_sessions')
        self.db.commit()
        with self.assertRaises(EvidenceError): self.provider.admit(('group.persisted',))

    def test_runtime_stopped_env_drift_and_config_drift_are_rejected(self):
        self.runtime.ready = False
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        self.runtime.ready = True
        self.runtime.inputs.environment['SERVER_ENV'] = 'pre'
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        self.runtime.inputs.environment['SERVER_ENV'] = 'local'
        config = json.loads(self.runtime.inputs.config_path.read_text())
        config['database']['sqlite']['path'] = str(self.root / 'other.sqlite')
        self.runtime.inputs.config_path.write_text(json.dumps(config))
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        with self.assertRaises(EvidenceError): self.provider_for()

    def test_missing_replaced_or_symlinked_database_is_never_created_or_adopted(self):
        retained = self.path.with_name('retained.sqlite')
        self.path.rename(retained)
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        self.assertFalse(self.path.exists())
        self.path.symlink_to(retained)
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')
        self.path.unlink()
        with sqlite3.connect(self.path): pass
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', 'g1')

    def test_ledger_run_binding_and_unadmitted_assertions_are_required(self):
        with self.assertRaises(LedgerError):
            OwnedSqliteEvidenceProvider(self.runtime, ledger_path=self.ledger_path,
                                        profile_digest='b' * 64, case_id='PRE-03')
        provider = self.provider_for()
        with self.assertRaises(EvidenceError): provider.observe('group.persisted', 'g1')
        with self.assertRaises(EvidenceError): provider.admit(('arbitrary.sql',))
        with self.assertRaises(EvidenceError): self.provider.observe('group.persisted', "g1' OR 1=1")

    def test_query_execution_budget_interrupts_work(self):
        with patch('bcs_e2e.local_sqlite.time.monotonic', side_effect=[0, 3]):
            with self.assertRaisesRegex(EvidenceError, 'budget'):
                with self.provider._connection() as db:
                    db.execute('WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n').fetchall()

    def test_replacement_before_observer_construction_cannot_be_adopted(self):
        self.path.rename(self.path.with_name('original.sqlite'))
        with sqlite3.connect(self.path): pass
        with self.assertRaisesRegex(EvidenceError, 'replaced'):
            self.provider_for()
