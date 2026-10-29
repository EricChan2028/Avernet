"""Read/report/dry-run must preserve historical recovery journals byte-for-byte."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.ledger import Ledger, LedgerError
from bcs_e2e.profile import load


class ReadOnlyLedgerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / 'run ?# journal.sqlite'
        self.digest = 'a' * 64
        with Ledger(self.path, 'read-run', self.digest, create=True):
            pass

    def historical_schema(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE correlation')
            db.execute('DROP TABLE diagnostic')

    def test_read_only_connection_rejects_dml_and_ddl(self):
        original = self.path.read_bytes()
        with Ledger(self.path, 'read-run', self.digest, read_only=True) as ledger:
            self.assertEqual(ledger.correlations(), [])
            self.assertEqual(ledger.diagnostics(), [])
            with self.assertRaises(sqlite3.OperationalError):
                ledger.intent('PRE-01', 1, 'group', 'req-1')
            with self.assertRaises(sqlite3.OperationalError):
                ledger.db.execute('CREATE TABLE unexpected (value TEXT)')
        self.assertEqual(self.path.read_bytes(), original)

    def test_historical_schema_is_not_migrated_by_read_only_open(self):
        self.historical_schema()
        original, modified = self.path.read_bytes(), self.path.stat().st_mtime_ns
        with Ledger(self.path, 'read-run', self.digest, read_only=True) as ledger:
            self.assertEqual(ledger.cleanup_plan()['resources'], [])
            with self.assertRaises(sqlite3.OperationalError):
                ledger.diagnostics()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.stat().st_mtime_ns, modified)

    def test_foreign_binding_is_rejected_before_writable_schema_upgrade(self):
        self.historical_schema()
        original = self.path.read_bytes()
        for read_only in (False, True):
            with self.subTest(read_only=read_only), self.assertRaises(LedgerError):
                Ledger(self.path, 'other-run', self.digest, read_only=read_only)
        self.assertEqual(self.path.read_bytes(), original)

    def test_no_creation_or_symlink_following_for_read_only_ledger(self):
        missing = self.root / 'missing.sqlite'
        with self.assertRaises(LedgerError):
            Ledger(missing, 'read-run', self.digest, read_only=True)
        with self.assertRaises(LedgerError):
            Ledger(missing, 'read-run', self.digest, read_only=True, create=True)
        self.assertFalse(missing.exists())
        link = self.root / 'link.sqlite'
        link.symlink_to(self.path)
        with self.assertRaises(LedgerError):
            Ledger(link, 'read-run', self.digest, read_only=True)

    def test_report_gate_and_cleanup_dry_run_preserve_old_schema(self):
        profile = load(ROOT / 'profiles/local.mock.json')
        profile_path = self.root / 'profile.json'
        profile_path.write_text(json.dumps(profile))
        state = self.root / 'state'
        with patch('bcs_e2e.pool.pool_root', return_value=self.root / 'pools'):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['prepare', '--profile', str(profile_path),
                                       '--run-id', 'inspect-run', '--state-dir', str(state)]), 0)
            path = state / 'run.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE correlation')
                db.execute('DROP TABLE diagnostic')
            original, modified = path.read_bytes(), path.stat().st_mtime_ns
            for command, flags, expected in (
                ('report', ['--format', 'json'], 2), ('gate', [], 2),
                ('cleanup', ['--dry-run'], 0),
            ):
                with self.subTest(command=command):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        rc = main([command, '--profile', str(profile_path),
                                   '--state-dir', str(state), *flags])
                    self.assertEqual(rc, expected, output.getvalue())
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(path.stat().st_mtime_ns, modified)
                    if command == 'report':
                        report = json.loads(output.getvalue())['summary']
                        self.assertEqual(report['integrity']['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
