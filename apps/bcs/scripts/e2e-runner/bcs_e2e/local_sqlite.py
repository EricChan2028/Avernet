"""Read-only persistence evidence for a live, exclusively owned local BCS.

Only fixed semantic projections of current-Run ledger resources are available.
This is real SQLite evidence, not the mock semantic provider and not OB evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import stat
import time

from .evidence import (ASSERTIONS, EvidenceError, _admission, _make_plan,
                       _stored_rows_to_semantic, _validate_observation)
from .ledger import Ledger
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import identifier


class OwnedSqliteDatabase:
    """Shared owned-process/path/inode and bounded read-only connection boundary."""
    def __init__(self, runtime: OwnedBcsProcess):
        if not isinstance(runtime, OwnedBcsProcess) or runtime.inputs is None:
            raise EvidenceError('SQLite observation requires a live owned runtime')
        self.runtime = runtime
        self.root = runtime.root
        self.path = self.root / 'bcs.sqlite'
        self.config_digest = file_sha256(runtime.inputs.config_path)
        self.run_id = runtime.run_id
        self._file_identity = self._require_owned()

    def _require_owned(self) -> tuple[int, int]:
        runtime = self.runtime
        if (runtime.state != 'RUNNING' or not runtime.ready or runtime.run_id != self.run_id
                or runtime.root != self.root or self.root.is_symlink()
                or runtime.inputs.root != self.root or runtime.inputs.config_path != self.root / 'bcs-config.json'
                or runtime.inputs.config_path.is_symlink()
                or runtime.inputs.environment.get('SERVER_ENV') != 'local'
                or runtime.child is None or runtime.child.poll() is not None
                or not runtime._owns_listener(1)
                or file_sha256(runtime.inputs.config_path) != self.config_digest):
            raise EvidenceError('SQLite runtime ownership or configuration has changed')
        config = json.loads(runtime.inputs.config_path.read_text())
        if config.get('database') != {'type': 'sqlite', 'sqlite': {'path': str(self.path)}}:
            raise EvidenceError('SQLite path is not the owned runtime database')
        try:
            metadata = self.path.lstat()
        except OSError:
            raise EvidenceError('owned SQLite database is missing') from None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise EvidenceError('owned SQLite database must be a regular unshared file')
        identity = (metadata.st_dev, metadata.st_ino)
        if (identity != runtime.database_identity
                or hasattr(self, '_file_identity') and identity != self._file_identity):
            raise EvidenceError('owned SQLite database was replaced')
        return identity

    @contextmanager
    def _connection(self):
        self._require_owned()
        try:
            # Never create/migrate the database or detach it from its live WAL.
            db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=1)
        except sqlite3.Error:
            raise EvidenceError('owned SQLite database cannot be opened read-only') from None
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            deadline = time.monotonic() + 2
            ticks = 0
            def bounded_work():
                nonlocal ticks
                ticks += 1
                return int(ticks > 100 or time.monotonic() >= deadline)
            db.set_progress_handler(bounded_work, 1000)
            self._require_owned()
            yield db
            self._require_owned()
        except sqlite3.Error:
            raise EvidenceError('owned SQLite observation failed or exceeded its budget') from None
        finally:
            db.close()


class OwnedSqliteEvidenceProvider(OwnedSqliteDatabase):
    def __init__(self, runtime: OwnedBcsProcess, *, ledger_path: Path,
                 profile_digest: str, case_id: str, attempt: int = 1):
        identifier(case_id, 'observation case ID')
        if type(attempt) is not int or attempt < 1:
            raise EvidenceError('invalid observation attempt')
        super().__init__(runtime)
        self.ledger_path, self.profile_digest = ledger_path, profile_digest
        self.case_id, self.attempt = case_id, attempt
        self.assertion_ids: tuple[str, ...] = ()
        # Validate the journal binding before any product database access.
        with Ledger(ledger_path, self.run_id, profile_digest, read_only=True):
            pass

    @staticmethod
    def _schema(db, assertion_id: str) -> None:
        table, fields, _key = ASSERTIONS[assertion_id]
        kind = db.execute('SELECT type FROM sqlite_master WHERE name=? LIMIT 2', (table,)).fetchall()
        if len(kind) != 1 or kind[0]['type'] != 'table':
            raise EvidenceError('required SQLite table is missing or is a view')
        columns = db.execute(f'PRAGMA table_info("{table}")').fetchmany(101)
        if len(columns) > 100 or not set(fields) <= {row['name'] for row in columns}:
            raise EvidenceError('SQLite schema does not match the semantic projection')

    def admit(self, assertion_ids: tuple[str, ...]) -> dict:
        self.assertion_ids = ()
        result = _admission(assertion_ids, 'local-sqlite', 'owned-local', 'local')
        with self._connection() as db:
            for assertion_id in dict.fromkeys(assertion_ids):
                self._schema(db, assertion_id)
        self.assertion_ids = tuple(assertion_ids)
        return result

    def _owned_resource(self, assertion_id: str, resource_id: str) -> dict:
        kind = assertion_id.split('.')[0]
        with Ledger(self.ledger_path, self.run_id, self.profile_digest, read_only=True) as ledger:
            rows = ledger.db.execute('SELECT * FROM resource WHERE kind=? AND resource_id=? LIMIT 2',
                                     (kind, resource_id)).fetchall()
            if len(rows) != 1:
                raise EvidenceError('observation requires one exact current-Run resource')
            result = dict(rows[0])
            row = result
            for expected_kind in {'group': ('group',), 'session': ('session', 'group'),
                                  'file': ('file', 'session', 'group')}[kind]:
                if (row['kind'] != expected_kind or row['origin'] != 'created' or row['state'] != 'live'
                        or row['case_id'] != self.case_id or row['attempt'] != self.attempt
                        or not row['resource_id']):
                    raise EvidenceError('resource or parent is not live and owned by this case attempt')
                if expected_kind == 'group':
                    if row['parent'] is not None:
                        raise EvidenceError('group ledger parent is invalid')
                else:
                    parents = ledger.db.execute('SELECT * FROM resource WHERE handle=? LIMIT 2',
                                                 (row['parent'],)).fetchall()
                    if len(parents) != 1:
                        raise EvidenceError('resource ledger parent is missing')
                    row = dict(parents[0])
                    if expected_kind == kind:
                        result['parent_resource_id'] = row['resource_id']
            return result

    def observe(self, assertion_id: str, resource_id: str) -> dict:
        if assertion_id not in self.assertion_ids:
            raise EvidenceError('SQLite observation was not admitted')
        plan = _make_plan(assertion_id, resource_id, 'owned-local', 'local')
        resource = self._owned_resource(assertion_id, resource_id)
        table, fields, key = ASSERTIONS[assertion_id]
        # All SQL identifiers come from the fixed code-owned projection registry.
        sql = f'SELECT {", ".join(fields)} FROM {table} WHERE env=? AND {key}=? LIMIT 2'
        with self._connection() as db:
            self._schema(db, assertion_id)
            rows = [dict(row) for row in db.execute(sql, ('local', resource_id)).fetchmany(2)]
        rows = _validate_observation(plan, {'success': True, 'data': rows})
        if assertion_id != 'group.persisted':
            parent_field = 'group_id' if assertion_id == 'session.persisted' else 'session_id'
            if rows[0][parent_field] != resource['parent_resource_id']:
                raise EvidenceError('persisted resource parent differs from owned ledger parent')
        rows = _stored_rows_to_semantic(assertion_id, rows)
        if self._owned_resource(assertion_id, resource_id) != resource:
            raise EvidenceError('resource ownership changed during observation')
        return {'status': 'OBSERVED', 'source': 'local-sqlite', 'plan': plan.public(), 'rows': rows}
