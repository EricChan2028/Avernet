"""Bounded actual bootstrap-history evidence; deletion does not imply history purge."""
import hashlib
import json

from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint
from .run_state import RunStateError


def history_rows(scope):
    scope._active()
    group = scope.state['resources']['group']
    explicit = scope.state['resources']['session']['id']
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows = scope._bounded_rows(db,
            'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,owner_bot_id,status,content '
            'FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id',
            ('local', group['id']), 4)
    expected = {(sid, scope._id(role)) for sid in (group['initial_session_id'], explicit) for role in ('PM', 'ENG')}
    if len(rows) != 4 or {(r['session_id'], r['owner_bot_id']) for r in rows} != expected:
        raise RunStateError('Session history is not the exact four per-recipient bootstrap messages')
    for row in rows:
        content = json.loads(row['content'])
        if (row['sender_id'] != 'system' or row['sender_type'] != 'system' or row['message_type'] != 'system'
                or row['status'] != 'normal' or not isinstance(content, str)
                or not content.startswith('<GroupContext>') or not content.endswith('</GroupContext>')
                or any(value not in content for value in (group['id'], row['session_id'], row['owner_bot_id']))):
            raise RunStateError('Session bootstrap history content or ownership differs')
    return rows


def history_snapshot(scope):
    rows = history_rows(scope)
    return {'status': 'PASS', 'row_count': len(rows), 'digest': fingerprint({'rows': rows}),
            'disposition': 'RETAINED_PER_RECIPIENT_SYSTEM_CONTEXT'}


def verify_history(scope, messages):
    rows = history_rows(scope)
    expected = next(r for r in rows if r['session_id'] == scope._session_id() and r['owner_bot_id'] == scope._id('PM'))
    if (not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict)
            or messages[0].get('id') != expected['message_id'] or messages[0].get('sender') != 'system'
            or messages[0].get('role') != 'system' or messages[0].get('content') != json.loads(expected['content'])):
        raise RunStateError('PM message history differs from exact per-recipient persisted bootstrap')
    return {'status': 'PASS', 'message_count': 1, 'message_id': expected['message_id'],
            'content_sha256': hashlib.sha256(messages[0]['content'].encode()).hexdigest(),
            'kind': 'PERSISTED_PER_RECIPIENT_BOOTSTRAP'}


def owned_storage(scope) -> dict:
    group_id, initial_id, session_id = scope._group_id(), scope._initial_session_id(), scope._session_id()
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        groups = scope._bounded_rows(db,
            'SELECT group_id,status,driver_bot,group_kind,group_strategy '
            'FROM bcs_groups WHERE env=? AND group_id=?', ('local', group_id), 1)
        participants = scope._bounded_rows(db,
            'SELECT bot_uuid,role,actor_kind,mode FROM bcs_group_participants '
            'WHERE env=? AND group_id=? ORDER BY bot_uuid', ('local', group_id), 4)
        sessions = scope._bounded_rows(db,
            'SELECT session_id,group_id,status,session_kind,session_title '
            'FROM bcs_group_sessions WHERE env=? AND group_id=? ORDER BY session_id',
            ('local', group_id), 3)
        session_participants = scope._bounded_rows(db,
            'SELECT session_id,bot_uuid,role FROM bcs_session_participants '
            'WHERE env=? AND group_id=? ORDER BY session_id,bot_uuid', ('local', group_id), 12)
        messages = scope._bounded_rows(db,
            'SELECT message_id,session_id,status FROM bcs_messages '
            'WHERE env=? AND group_id=? ORDER BY message_id', ('local', group_id), 16)
        deliveries = scope._bounded_rows(db,
            'SELECT delivery_id,session_id,status FROM bcs_message_deliveries '
            'WHERE env=? AND group_id=? ORDER BY delivery_id', ('local', group_id), 16)
        runs = scope._bounded_rows(db,
            'SELECT run_id,session_id,status FROM bcs_state_machine_runs '
            'WHERE env=? AND group_id=? ORDER BY run_id', ('local', group_id), 8)
    if (groups != [{'group_id': group_id, 'status': 'active', 'driver_bot': scope._id('PM'),
                    'group_kind': 'normal', 'group_strategy': 'chat'}]
            or {row['bot_uuid'] for row in participants} != {scope._id('PM'), scope._id('ENG')}
            or {row['session_id'] for row in sessions} != {initial_id, session_id}
            or any(row['group_id'] != group_id for row in sessions)
            or deliveries or runs):
        raise RunStateError('Session owned SQLite state is not the exact transport-only scope')
    history = history_snapshot(scope)
    return {'history': history, 'status': 'PASS', 'group_rows': 1, 'group_participant_rows': len(participants),
            'session_rows': 2, 'session_participant_rows': len(session_participants),
            'message_rows': 0, 'message_digest': fingerprint({'rows': []}),
            'explicit_session_message_rows': 0,
            'delivery_rows': 0, 'state_machine_run_rows': 0,
            'session_ids': [initial_id, session_id]}

