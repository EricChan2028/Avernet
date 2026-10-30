"""Owned-local Session storage snapshots and deletion-admission helpers.

Split out of :mod:`bcs_e2e.local_session_public` to keep every executor source
file within the reviewed 1,000-line limit.  The methods below are unchanged; the
parent module inherits them through :class:`LocalSessionPublicStorageMixin` and
re-exports :class:`LocalSessionPublicError` and ``ROLES``.
"""
from __future__ import annotations

import json

from .local_sqlite import OwnedSqliteDatabase
from .owned_bcs import file_sha256
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError

ROLES = ('CEO', 'PM', 'ENG', 'QA')


class LocalSessionPublicError(RunStateError):
    pass


class LocalSessionPublicStorageMixin:
    """Finite SQLite snapshot, deletion admission, and cascade verification."""

    @staticmethod
    def _bounded_rows(db, sql: str, params: tuple, maximum: int) -> list[dict]:
        rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
        if len(rows) > maximum:
            raise LocalSessionPublicError('Session SQLite snapshot exceeded its finite bound')
        return rows

    def _unrelated_snapshot(self, *, exclude_group_id: str | None = None) -> dict:
        """Finite control snapshot, excluding only this run's exact owned Group."""
        if exclude_group_id is not None:
            identifier(exclude_group_id, 'excluded owned Group ID')
            group_clause, params = ' AND group_id<>?', ('local', exclude_group_id)
        else:
            group_clause, params = '', ('local',)
        queries = {
            'groups': ('SELECT group_id,status,driver_bot,group_kind,group_strategy '
                       'FROM bcs_groups WHERE env=?' + group_clause + ' ORDER BY group_id', params, 8),
            'group_participants': ('SELECT group_id,bot_uuid,role,actor_kind,mode '
                                   'FROM bcs_group_participants WHERE env=?' + group_clause +
                                   ' ORDER BY group_id,bot_uuid', params, 32),
            'sessions': ('SELECT session_id,group_id,status,session_kind,session_title '
                         'FROM bcs_group_sessions WHERE env=?' + group_clause +
                         ' ORDER BY session_id', params, 16),
            'session_participants': ('SELECT session_id,group_id,bot_uuid,role '
                                     'FROM bcs_session_participants WHERE env=?' + group_clause +
                                     ' ORDER BY session_id,bot_uuid', params, 64),
            'messages': ('SELECT message_id,group_id,session_id,status FROM bcs_messages '
                         'WHERE env=?' + group_clause + ' ORDER BY message_id', params, 64),
            'deliveries': ('SELECT delivery_id,group_id,session_id,status FROM bcs_message_deliveries '
                           'WHERE env=?' + group_clause + ' ORDER BY delivery_id', params, 64),
            'state_machine_runs': ('SELECT run_id,group_id,session_id,status FROM bcs_state_machine_runs '
                                   'WHERE env=?' + group_clause + ' ORDER BY run_id', params, 16),
            'provider_bindings': ('SELECT bot_uuid FROM bcs_provider_bot_bindings '
                                  'WHERE env=? ORDER BY bot_uuid', ('local',), 16),
        }
        result = {}
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            for name, (sql, values, maximum) in queries.items():
                rows = self._bounded_rows(db, sql, values, maximum)
                result[name] = {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}
        return result

    def _owned_storage(self):
        from .local_session_storage import owned_storage
        return owned_storage(self)

    def _deletion_admission(self, target: str) -> dict:
        self._active()
        if target not in ('session', 'group'):
            raise LocalSessionPublicError('Session deletion target is not reviewed')
        if (self.runtime.state != 'RUNNING' or not self.runtime.ready
                or self.runtime.child is None or self.runtime.child.poll() is not None
                or not self.runtime._owns_listener(1)):
            raise LocalSessionPublicError('Session deletion requires live foreground-owned execution')
        if self._ws_state != 'CLOSED' or self._ws_connection is not None:
            raise LocalSessionPublicError('Session deletion requires the owned WebSocket to be closed first')
        config = json.loads(self.runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
        if (file_sha256(self.runtime.inputs.config_path) != self.identities.fixtures.config_digest
                or config.get('store_messages') is not True
                or config.get('eventing') != {'enabled': False, 'dispatcher_enabled': False}
                or config.get('collaboration', {}).get('loop_execution_enabled') is not False
                or config.get('llm') != {'type': 'none'}
                or config.get('human_notify') != {'providers': []}
                or config.get('security', {}).get('outbound_url') != {
                    'block_private_networks': True, 'allow_loopback': False}):
            raise LocalSessionPublicError('Session deletion requires isolated local transport-only configuration')
        group_id = self._group_id()
        bot_ids = [self._id(role) for role in ROLES]
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            providers = db.execute(
                'SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? '
                'AND bot_uuid IN (?,?,?,?) LIMIT 1', ('local', *bot_ids)).fetchall()
            deliveries = db.execute(
                'SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id=? LIMIT 1',
                ('local', group_id)).fetchall()
            runs = db.execute(
                'SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id=? LIMIT 1',
                ('local', group_id)).fetchall()
        if providers or deliveries or runs:
            raise LocalSessionPublicError('Session deletion cannot admit Provider, queued delivery, or state-machine work')
        return {'target': target, 'status': 'LOCAL_DELETION_ADMITTED',
                'contract': 'owned-local-foreground-transport-only-v1',
                'foreground_runtime': 'LIVE_OWNED_CHILD', 'websocket': 'CLOSED',
                'isolated_config': True, 'store_messages': True, 'provider_bindings': 0,
                'queued_deliveries': 0, 'state_machine_runs': 0,
                'receiver_mode': 'NOT_OPENED', 'engine_started_by_executor': False,
                'business_session_terminal': 'NOT_CLAIMED',
                'remote_quiescence': 'NOT_APPLICABLE'}

    def _verify_session_deleted_storage(self) -> dict:
        group_id, initial_id = self._group_id(), self._initial_session_id()
        session_id = self.state['resources']['session']['id']
        self._resource(self._explicit_session_handle, kind='session', resource_id=session_id)
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            counts = {
                'session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND session_id=?',
                                           ('local', session_id)).fetchone()[0],
                'session_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND session_id=?',
                                                       ('local', session_id)).fetchone()[0],
                'message_rows': db.execute('SELECT COUNT(*) FROM bcs_messages WHERE env=? AND session_id=?',
                                           ('local', session_id)).fetchone()[0],
                'delivery_rows': db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND session_id=?',
                                            ('local', session_id)).fetchone()[0],
                'state_machine_run_rows': db.execute('SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND session_id=?',
                                                     ('local', session_id)).fetchone()[0],
                'group_rows': db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?',
                                         ('local', group_id)).fetchone()[0],
                'initial_session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND session_id=?',
                                                   ('local', initial_id)).fetchone()[0],
            }
        expected = {'session_rows': 0, 'session_participant_rows': 0,
                    'message_rows': 2, 'delivery_rows': 0,
                    'state_machine_run_rows': 0, 'group_rows': 1,
                    'initial_session_rows': 1}
        if counts != expected:
            raise LocalSessionPublicError('Explicit Session deletion cascade is not exact')
        from .local_session_storage import history_snapshot
        if history_snapshot(self) != self.state['storage_before_delete']['history']:
            raise LocalSessionPublicError('Explicit Session deletion changed retained bootstrap history')
        return {'status': 'PASS', **counts, 'history_preserved': True}

    def _verify_group_deleted_storage(self) -> dict:
        group_id = self.state['resources']['group']['id']
        session_ids = (self.state['resources']['group']['initial_session_id'],
                       self.state['resources']['session']['id'])
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            counts = {
                'group_rows': db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?',
                                         ('local', group_id)).fetchone()[0],
                'group_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_group_participants WHERE env=? AND group_id=?',
                                                     ('local', group_id)).fetchone()[0],
                'session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND group_id=?',
                                           ('local', group_id)).fetchone()[0],
                'session_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND group_id=?',
                                                       ('local', group_id)).fetchone()[0],
                'message_rows': db.execute('SELECT COUNT(*) FROM bcs_messages WHERE env=? AND group_id=?',
                                           ('local', group_id)).fetchone()[0],
                'delivery_rows': db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND group_id=?',
                                            ('local', group_id)).fetchone()[0],
                'state_machine_run_rows': db.execute('SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND group_id=?',
                                                     ('local', group_id)).fetchone()[0],
            }
        if any(value != (4 if key == 'message_rows' else 0) for key, value in counts.items()):
            raise LocalSessionPublicError('Group deletion cascade or retained history is not exact')
        from .local_session_storage import history_snapshot
        if history_snapshot(self) != self.state['storage_before_delete']['history']:
            raise LocalSessionPublicError('Group deletion changed retained bootstrap history')
        if len(set(session_ids)) != 2:
            raise LocalSessionPublicError('Session deletion inventory lost distinct Session IDs')
        return {'status': 'PASS', **counts, 'history_preserved': True}
