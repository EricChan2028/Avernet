"""Bounded SQLite evidence for the owned local Session sharing slice."""
from __future__ import annotations

import json

from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint
from .run_state import read


def _error(message: str):
    from .local_session_sharing import LocalSessionSharingError
    raise LocalSessionSharingError(message)


def _rows(db, sql: str, params: tuple, maximum: int) -> list[dict]:
    values = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
    if len(values) > maximum:
        _error('Session sharing SQLite snapshot exceeded its finite bound')
    return values


def fixture_snapshot(scope) -> dict:
    scope._active()
    receipt = read(scope.identities.fixtures.path)
    roles = receipt['roles']
    bot_ids = [roles[role]['bot_id'] for role in scope.ROLES]
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        bots = _rows(db,
            'SELECT bot_uuid,name,env,visibility,created_by,actor_kind,status,is_deleted '
            'FROM bcs_bots WHERE bot_uuid IN (?,?,?,?) ORDER BY bot_uuid',
            tuple(bot_ids), 4)
        human = _rows(db,
            'SELECT bot_uuid,name,env,created_by,actor_kind,status,is_deleted FROM bcs_bots '
            'WHERE env=? AND bot_uuid=?', ('local', 'human_' + scope.identities.human), 1)
    if (len(bots) != 4 or {row['bot_uuid'] for row in bots} != set(bot_ids)
            or any(row['env'] != 'local' or row['visibility'] != 'protected'
                   or row['created_by'] != scope.identities.human or row['actor_kind'] != 'bot'
                   or row['status'] != 'online' or row['is_deleted'] != 0 for row in bots)
            or len(human) != 1 or human[0]['actor_kind'] != 'human'):
        _error('Session sharing fixture snapshot differs from the exact workspace fixtures')
    return {'fixture_digest': fingerprint(receipt),
            'role_ids': {role: roles[role]['bot_id'] for role in scope.ROLES},
            'bot_rows': len(bots), 'bot_rows_digest': fingerprint({'rows': bots}),
            'human_rows': 1, 'human_rows_digest': fingerprint({'rows': human})}


def _excluded_clause(group_ids: tuple[str, ...]) -> tuple[str, tuple]:
    if not group_ids:
        return '', ()
    if len(group_ids) > 5:
        _error('Session sharing exclusion set exceeds five owned Groups')
    return ' AND group_id NOT IN (' + ','.join('?' for _ in group_ids) + ')', group_ids


def unrelated_snapshot(scope, group_ids: tuple[str, ...] = ()) -> dict:
    """Snapshot finite local rows while excluding only exact owned sharing Groups."""
    clause, excluded = _excluded_clause(group_ids)
    queries = {
        'groups': ('SELECT group_id,status,driver_bot,originator,group_kind,group_strategy '
                   'FROM bcs_groups WHERE env=?' + clause + ' ORDER BY group_id', ('local', *excluded), 8),
        'group_participants': ('SELECT group_id,bot_uuid,role,actor_kind,mode FROM bcs_group_participants '
                               'WHERE env=?' + clause + ' ORDER BY group_id,bot_uuid', ('local', *excluded), 32),
        'sessions': ('SELECT session_id,group_id,status,session_kind,session_title FROM bcs_group_sessions '
                     'WHERE env=?' + clause + ' ORDER BY session_id', ('local', *excluded), 16),
        'session_participants': ('SELECT session_id,group_id,bot_uuid,role FROM bcs_session_participants '
                                 'WHERE env=?' + clause + ' ORDER BY session_id,bot_uuid', ('local', *excluded), 64),
        'messages': ('SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,'
                     'owner_bot_id,status,content FROM bcs_messages WHERE env=?' + clause +
                     ' ORDER BY message_id', ('local', *excluded), 64),
        'deliveries': ('SELECT delivery_id,group_id,session_id,status FROM bcs_message_deliveries '
                       'WHERE env=?' + clause + ' ORDER BY delivery_id', ('local', *excluded), 32),
        'state_machine_runs': ('SELECT run_id,group_id,session_id,status FROM bcs_state_machine_runs '
                               'WHERE env=?' + clause + ' ORDER BY run_id', ('local', *excluded), 16),
        'provider_bindings': ('SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? ORDER BY bot_uuid',
                              ('local',), 16),
    }
    result = {}
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        for name, (sql, params, maximum) in queries.items():
            rows = _rows(db, sql, params, maximum)
            result[name] = {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}
    return result


def public_history_snapshot(scope) -> dict:
    public = scope.public_journal
    group = public['resources']['group']
    explicit = public['resources']['session']['id']
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows = _rows(db,
            'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,owner_bot_id,status,content '
            'FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id',
            ('local', group['id']), 4)
    expected = {(sid, scope._id(role)) for sid in (group['initial_session_id'], explicit)
                for role in ('PM', 'ENG')}
    snapshot = {'status': 'PASS', 'row_count': len(rows),
                'digest': fingerprint({'rows': rows}),
                'disposition': 'RETAINED_PER_RECIPIENT_SYSTEM_CONTEXT'}
    if (len(rows) != 4 or {(row['session_id'], row['owner_bot_id']) for row in rows} != expected
            or snapshot != public['storage_before_delete']['history']):
        _error('Public-stage four-row retained history changed')
    return snapshot


def verify_join_relation(scope, *, target: str, group_id: str, session_id: str | None = None) -> dict:
    actor = 'human_' + scope.identities.human
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        group_rows = _rows(db,
            'SELECT group_id,bot_uuid,role,actor_kind,mode FROM bcs_group_participants '
            'WHERE env=? AND group_id=? AND bot_uuid=?', ('local', group_id, actor), 1)
        session_rows = [] if session_id is None else _rows(db,
            'SELECT session_id,group_id,bot_uuid,role FROM bcs_session_participants '
            'WHERE env=? AND session_id=? AND bot_uuid=?', ('local', session_id, actor), 1)
    if target == 'group':
        if (group_rows != [{'group_id': group_id, 'bot_uuid': actor, 'role': 'consultant',
                            'actor_kind': 'human', 'mode': 'present'}]
                or session_rows):
            _error('Group invite join did not persist the exact Human member relation')
    elif target == 'session':
        if (group_rows or session_rows != [{'session_id': session_id, 'group_id': group_id,
                                            'bot_uuid': actor, 'role': 'consultant'}]):
            _error('Session invite join did not persist the exact Human member relation')
    else:
        _error('Unknown invite relation target')
    return {'target': target, 'actor_id': actor, 'group_member_rows': len(group_rows),
            'session_member_rows': len(session_rows), 'status': 'PASS'}


def owned_history_snapshot(scope) -> dict:
    group_ids = tuple(scope._group_ids())
    if not 1 <= len(group_ids) <= 5:
        _error('Owned history requires the bounded already-created Group inventory')
    placeholders = ','.join('?' for _ in group_ids)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows = _rows(db,
            'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,owner_bot_id,status,content '
            f'FROM bcs_messages WHERE env=? AND group_id IN ({placeholders}) ORDER BY message_id',
            ('local', *group_ids), 64)
    bootstrap = []
    for row in rows:
        try:
            content = json.loads(row['content'])
        except (TypeError, ValueError, json.JSONDecodeError):
            content = None
        if (row['sender_id'] == 'system' and row['sender_type'] == 'system'
                and row['message_type'] == 'system' and row['status'] == 'normal'
                and isinstance(content, str) and content.startswith('<GroupContext>')
                and content.endswith('</GroupContext>')):
            bootstrap.append(row)
    expected = {(sid, bot) for sid in scope._session_ids() for bot in (scope._id('PM'), scope._id('ENG'))}
    if len(bootstrap) != len(expected) or {(row['session_id'], row['owner_bot_id']) for row in bootstrap} != expected:
        _error('Owned retained bootstrap history is not exactly sixteen per-recipient rows')
    return {'row_count': len(rows), 'digest': fingerprint({'rows': rows}),
            'bootstrap_row_count': len(expected), 'status': 'PASS'}


def owned_snapshot(scope, helper: str) -> dict:
    group = scope._group(helper)
    group_ids = (group['id'],)
    session_ids = (group['initial_session_id'],)
    if helper in scope.state['resources']['sessions']:
        session_ids += (scope._session(helper)['id'],)
    group_count = 1
    member_count = 3 if helper == 'h2' else 2
    session_count = len(session_ids)
    session_member_count = 2*session_count + (1 if helper == 'h5' else 0)
    group_placeholders = ','.join('?' for _ in group_ids)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        groups = _rows(db,
            'SELECT group_id,status,driver_bot,originator,group_kind,group_strategy FROM bcs_groups '
            f'WHERE env=? AND group_id IN ({group_placeholders}) ORDER BY group_id',
            ('local', *group_ids), 5)
        group_members = _rows(db,
            'SELECT group_id,bot_uuid,role,actor_kind,mode FROM bcs_group_participants '
            f'WHERE env=? AND group_id IN ({group_placeholders}) ORDER BY group_id,bot_uuid',
            ('local', *group_ids), 11)
        sessions = _rows(db,
            'SELECT session_id,group_id,status,session_kind,session_title FROM bcs_group_sessions '
            f'WHERE env=? AND group_id IN ({group_placeholders}) ORDER BY session_id',
            ('local', *group_ids), 8)
        session_members = _rows(db,
            'SELECT session_id,group_id,bot_uuid,role FROM bcs_session_participants '
            f'WHERE env=? AND group_id IN ({group_placeholders}) ORDER BY session_id,bot_uuid',
            ('local', *group_ids), 17)
        deliveries = _rows(db,
            'SELECT delivery_id,group_id,session_id,status FROM bcs_message_deliveries '
            f'WHERE env=? AND group_id IN ({group_placeholders}) ORDER BY delivery_id',
            ('local', *group_ids), 16)
        runs = _rows(db,
            'SELECT run_id,group_id,session_id,status FROM bcs_state_machine_runs '
            f'WHERE env=? AND group_id IN ({group_placeholders}) ORDER BY run_id',
            ('local', *group_ids), 8)
    pm, eng = scope._id('PM'), scope._id('ENG')
    if (len(groups) != group_count or any(row['status'] != 'active' or row['driver_bot'] != pm
                                or row['originator'] != pm or row['group_kind'] != 'normal'
                                or row['group_strategy'] != 'chat' for row in groups)
            or len(group_members) != member_count or len(sessions) != session_count or len(session_members) != session_member_count
            or {row['session_id'] for row in sessions} != set(session_ids)
            or deliveries or runs):
        _error('Owned Session sharing SQLite scope is not exact or transport-only')
    history = owned_history_snapshot(scope)
    return {'status': 'PASS', 'group_rows': group_count, 'group_participant_rows': member_count,
            'session_rows': session_count, 'session_participant_rows': session_member_count,
            'delivery_rows': 0, 'state_machine_run_rows': 0, 'history': history}


def deletion_admission(scope) -> dict:
    scope._active()
    config = json.loads(scope.runtime.inputs.config_path.read_text())
    bot_ids = tuple(scope._id(role) for role in scope.ROLES)
    group_ids = tuple(scope._group_ids())
    placeholders = ','.join('?' for _ in group_ids)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        providers = _rows(db,
            'SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? AND bot_uuid IN (?,?,?,?)',
            ('local', *bot_ids), 4)
        deliveries = _rows(db,
            f'SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id IN ({placeholders})',
            ('local', *group_ids), 1)
        runs = _rows(db,
            f'SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id IN ({placeholders})',
            ('local', *group_ids), 1)
    if (scope.runtime.state != 'RUNNING' or not scope.runtime.ready
            or scope.runtime.child is None or scope.runtime.child.poll() is not None
            or not scope.runtime._owns_listener(1) or providers or deliveries or runs
            or config.get('store_messages') is not True
            or config.get('eventing') != {'enabled': False, 'dispatcher_enabled': False}
            or config.get('collaboration', {}).get('loop_execution_enabled') is not False
            or config.get('llm') != {'type': 'none'}
            or config.get('human_notify') != {'providers': []}):
        _error('Session sharing deletion is not admitted in isolated foreground scope')
    return {'status': 'LOCAL_DELETION_ADMITTED',
            'contract': 'owned-local-foreground-transport-only-v1',
            'foreground_runtime': 'LIVE_OWNED_CHILD', 'scope': 'LOCAL_FOREGROUND_ONLY',
            'provider_bindings': 0, 'queued_deliveries': 0, 'state_machine_runs': 0,
            'eventing': 'DISABLED', 'dispatcher': 'DISABLED', 'engine': 'NOT_STARTED',
            'receiver': 'NOT_OPENED', 'state_machine': 'NOT_STARTED',
            'business_session_terminal': 'NOT_CLAIMED', 'remote_quiescence': 'NOT_APPLICABLE'}


def deletion_proof(scope, group_id: str, session_ids: tuple[str, ...], history: dict) -> dict:
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        counts = {
            'group_rows': db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?',
                                     ('local', group_id)).fetchone()[0],
            'group_participant_rows': db.execute(
                'SELECT COUNT(*) FROM bcs_group_participants WHERE env=? AND group_id=?',
                ('local', group_id)).fetchone()[0],
            'session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND group_id=?',
                                       ('local', group_id)).fetchone()[0],
            'session_participant_rows': db.execute(
                'SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND group_id=?',
                ('local', group_id)).fetchone()[0],
            'delivery_rows': db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND group_id=?',
                                        ('local', group_id)).fetchone()[0],
            'state_machine_run_rows': db.execute(
                'SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND group_id=?',
                ('local', group_id)).fetchone()[0],
        }
        rows = _rows(db,
            'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,owner_bot_id,status,content '
            'FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id', ('local', group_id), 64)
    if any(counts.values()) or {row['session_id'] for row in rows} - set(session_ids):
        _error('Group deletion did not remove exact Group/Session/member rows')
    group_history = {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}
    expected = history['groups'][group_id]
    if group_history != expected:
        _error('Group deletion changed retained local history')
    return {'status': 'PASS', **counts, 'retained_history': group_history,
            'child_session_count': len(session_ids)}


def group_history_map(scope) -> dict:
    result = {}
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        for group_id in scope._group_ids():
            rows = _rows(db,
                'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,owner_bot_id,status,content '
                'FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id',
                ('local', group_id), 64)
            result[group_id] = {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}
    return result
