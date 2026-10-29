"""Bounded SQLite evidence for the final three original Session helpers."""
from __future__ import annotations
import json

from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint
from .run_state import read


def _error(message):
    from .local_session_completion import LocalSessionCompletionError
    raise LocalSessionCompletionError(message)


def _rows(db, sql, params, maximum):
    rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
    if len(rows) > maximum: _error('Session completion SQLite snapshot exceeded its finite bound')
    return rows


def fixture_snapshot(scope):
    receipt = read(scope.identities.fixtures.path); roles = receipt['roles']
    ids = tuple(roles[r]['bot_id'] for r in scope.ROLES)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        bots = _rows(db, 'SELECT bot_uuid,visibility,created_by,actor_kind,status,is_deleted FROM bcs_bots '
                         'WHERE bot_uuid IN (?,?,?,?) ORDER BY bot_uuid', ids, 4)
    if (len(bots) != 4 or {r['bot_uuid'] for r in bots} != set(ids)
            or any(r['visibility'] != 'protected' or r['created_by'] != scope.identities.human
                   or r['actor_kind'] != 'bot' or r['status'] != 'online' or r['is_deleted'] != 0 for r in bots)):
        _error('Session completion fixture snapshot differs')
    return {'fixture_digest': fingerprint(receipt), 'rows': 4,
            'digest': fingerprint({'rows': bots})}


def _clause(excluded):
    if len(excluded) > 3: _error('Session completion exclusion set exceeds three Groups')
    return ('' if not excluded else ' AND group_id NOT IN (' + ','.join('?' for _ in excluded) + ')')


def unrelated_snapshot(scope, excluded=()):
    clause = _clause(excluded); params = ('local', *excluded)
    queries = {
        'groups': ('SELECT group_id,status,driver_bot,group_kind,group_strategy FROM bcs_groups WHERE env=?'+clause+' ORDER BY group_id', 12),
        'participants': ('SELECT group_id,bot_uuid,role,actor_kind,mode FROM bcs_group_participants WHERE env=?'+clause+' ORDER BY group_id,bot_uuid', 48),
        'sessions': ('SELECT session_id,group_id,status,session_kind,session_title,caller_id,output FROM bcs_group_sessions WHERE env=?'+clause+' ORDER BY session_id', 32),
        'session_participants': ('SELECT session_id,group_id,bot_uuid,role,collected,collected_at FROM bcs_session_participants WHERE env=?'+clause+' ORDER BY session_id,bot_uuid', 96),
        'messages': ('SELECT message_id,group_id,session_id,owner_bot_id,status,content FROM bcs_messages WHERE env=?'+clause+' ORDER BY message_id', 128),
        'deliveries': ('SELECT delivery_id,group_id,session_id,status FROM bcs_message_deliveries WHERE env=?'+clause+' ORDER BY delivery_id', 64),
        'runs': ('SELECT run_id,group_id,session_id,status FROM bcs_state_machine_runs WHERE env=?'+clause+' ORDER BY run_id', 32),
    }
    result = {}
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        for name, (sql, maximum) in queries.items():
            rows = _rows(db, sql, params, maximum)
            result[name] = {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}
    return result


def predecessor_snapshot(scope):
    groups = [scope.public_journal['resources']['group']['id']]
    groups.extend(row['id'] for row in scope.sharing_journal['resources']['groups'].values())
    placeholders = ','.join('?' for _ in groups)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows = _rows(db, 'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,'
                         'owner_bot_id,status,content FROM bcs_messages WHERE env=? AND group_id IN ('+
                         placeholders+') ORDER BY message_id', ('local', *groups), 21)
    public_id = groups[0]
    public = [r for r in rows if r['group_id'] == public_id]
    sharing = [r for r in rows if r['group_id'] != public_id]
    public_digest = fingerprint({'rows': public})
    sharing_digest = fingerprint({'rows': sharing})
    expected_public = scope.public_journal.get('storage_before_delete', {}).get('history', {}).get('digest')
    expected_sharing = scope.sharing_journal.get('retained_histories', {}).get('after_delete', {}).get('digest')
    if (len(public) != 4 or len(sharing) != 17 or len(rows) != 21
            or public_digest != expected_public or sharing_digest != expected_sharing):
        _error('Session predecessor retained history count or accepted journal digest differs')
    return {'status': 'PASS', 'row_count': 21, 'public_rows': 4, 'sharing_rows': 17,
            'public_digest': public_digest, 'sharing_digest': sharing_digest,
            'digest': fingerprint({'rows': rows})}


def group_history(scope, helper):
    group = scope._group(helper)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows = _rows(db, 'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,'
                         'owner_bot_id,status,content FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id',
                     ('local', group['id']), 32)
    return {'row_count': len(rows), 'digest': fingerprint({'rows': rows})}


def deletion_admission(scope, helper):
    group = scope._group(helper); ids = tuple(scope._id(r) for r in scope.ROLES)
    config = json.loads(scope.runtime.inputs.config_path.read_text())
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        providers = _rows(db, 'SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? AND bot_uuid IN (?,?,?,?)', ('local', *ids), 4)
        deliveries = _rows(db, 'SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id=?', ('local', group['id']), 1)
        runs = _rows(db, 'SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id=?', ('local', group['id']), 1)
    if (scope.runtime.state != 'RUNNING' or scope.runtime.child is None or scope.runtime.child.poll() is not None
            or not scope.runtime._owns_listener(1) or providers or deliveries or runs
            or config.get('store_messages') is not True
            or config.get('eventing') != {'enabled': False, 'dispatcher_enabled': False}
            or config.get('collaboration', {}).get('loop_execution_enabled') is not False
            or config.get('llm') != {'type': 'none'} or config.get('human_notify') != {'providers': []}):
        _error('Session completion deletion is not admitted')
    return {'helper': helper, 'status': 'LOCAL_DELETION_ADMITTED',
            'contract': 'owned-local-foreground-transport-only-v1',
            'foreground_runtime': 'LIVE_OWNED_CHILD', 'provider_bindings': 0,
            'queued_deliveries': 0, 'state_machine_runs': 0, 'isolated_config': True,
            'engine': 'NOT_STARTED', 'receiver': 'NOT_OPENED', 'state_machine': 'NOT_STARTED',
            'business_session_terminal': 'MANUAL_CHAT_ONLY' if helper == 'complete' else 'NOT_CLAIMED',
            'service_business_execution': 'NOT_CLAIMED', 'remote_quiescence': 'NOT_APPLICABLE'}


def deletion_proof(scope, helper, history):
    group = scope._group(helper)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        counts = {
            'group_rows': db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?', ('local', group['id'])).fetchone()[0],
            'group_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_group_participants WHERE env=? AND group_id=?', ('local', group['id'])).fetchone()[0],
            'session_rows': db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND group_id=?', ('local', group['id'])).fetchone()[0],
            'session_participant_rows': db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND group_id=?', ('local', group['id'])).fetchone()[0],
            'delivery_rows': db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND group_id=?', ('local', group['id'])).fetchone()[0],
            'state_machine_run_rows': db.execute('SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND group_id=?', ('local', group['id'])).fetchone()[0],
        }
    after = group_history(scope, helper)
    if any(counts.values()) or after != history: _error('Session completion cleanup or retained history differs')
    return {'status': 'PASS', **counts, 'retained_history': after}

def verify_lifecycle_message(scope, messages):
    session = scope._session('lifecycle')['id']; pm = scope._id('PM')
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows = _rows(db, 'SELECT message_id,session_id,sender_id,owner_bot_id,status,content FROM bcs_messages '
                         'WHERE env=? AND session_id=? ORDER BY message_id', ('local', session), 16)
    matches = []
    for row in rows:
        try: content = json.loads(row['content'])
        except (TypeError, ValueError): content = None
        if content == 'hello-session' and row['sender_id'] == pm and row['status'] == 'normal':
            matches.append(row)
    message_ids = {m.get('id') for m in messages if isinstance(m, dict)} if isinstance(messages, list) else set()
    if len(matches) != 1 or matches[0]['message_id'] not in message_ids:
        _error('Lifecycle chat is not bound to one actual persisted history row')
    return {'status': 'PASS', 'message_id': matches[0]['message_id'],
            'sqlite_row_digest': fingerprint(matches[0]), 'http_contains_message': True}
