"""Bounded owned-SQLite evidence for the complete Channel CLI parent."""
from __future__ import annotations

import json

from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint
from .run_state import read


def _error(message: str):
    from .local_channel_cli import LocalChannelCliError
    raise LocalChannelCliError(message)


def _rows(db, sql: str, params: tuple, maximum: int) -> list[dict]:
    rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum+1)]
    if len(rows) > maximum: _error('Channel CLI SQLite snapshot exceeded its finite bound')
    return rows


def fixture_snapshot(scope) -> dict:
    scope._active(); receipt = read(scope.identities.fixtures.path)
    ids = tuple(receipt['roles'][role]['bot_id'] for role in scope.ROLES)
    placeholders=','.join('?' for _ in ids)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        rows = _rows(db, 'SELECT bot_uuid,visibility,created_by,actor_kind,status,is_deleted '
            'FROM bcs_bots WHERE bot_uuid IN (?,?,?,?) ORDER BY bot_uuid', ids, 4)
        friendships=_rows(db,'SELECT left_bot,right_bot,env FROM bcs_friendships WHERE env=? AND '
            f'(left_bot IN ({placeholders}) OR right_bot IN ({placeholders})) ORDER BY left_bot,right_bot',
            ('local',*ids,*ids),12)
        requests=_rows(db,'SELECT request_id,from_bot,to_bot,status,env FROM bcs_friend_requests WHERE env=? AND '
            f'(from_bot IN ({placeholders}) OR to_bot IN ({placeholders})) ORDER BY request_id',
            ('local',*ids,*ids),24)
    if (len(rows) != 4 or {r['bot_uuid'] for r in rows} != set(ids)
            or any(r['visibility'] != 'protected' or r['created_by'] != scope.identities.human
                   or r['actor_kind'] != 'bot' or r['status'] != 'online' or r['is_deleted'] != 0
                   for r in rows) or friendships or requests):
        _error('Channel CLI fixture or friendship snapshot differs')
    return {'fixture_digest':fingerprint(receipt),'row_count':4,'digest':fingerprint({'rows':rows}),
            'friendship_rows':0,'friend_request_rows':0,
            'relationship_digest':fingerprint({'friendships':friendships,'requests':requests})}


def channel_empty_snapshot(scope) -> dict:
    scope._active(); result = {}
    tables = ('bcs_channel_bindings','bcs_channel_conversations','bcs_channel_im_participants')
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        for table in tables:
            rows = _rows(db, 'SELECT 1 AS present FROM '+table+' LIMIT 1', (), 1)
            result[table] = len(rows)
    if any(result.values()): _error('Channel CLI local Channel tables are not empty')
    return {'status':'PASS','source':'OWNED_LOCAL_SQLITE','row_counts':result,
            'digest':fingerprint({'row_counts':result})}


def unrelated_snapshot(scope, excluded_group: str | None = None) -> dict:
    clause = '' if excluded_group is None else ' AND group_id!=?'
    params = ('local',) if excluded_group is None else ('local',excluded_group)
    queries = {
        'groups':('SELECT group_id,status,driver_bot FROM bcs_groups WHERE env=?'+clause+' ORDER BY group_id',8),
        'participants':('SELECT group_id,bot_uuid,role,actor_kind,mode FROM bcs_group_participants WHERE env=?'+clause+' ORDER BY group_id,bot_uuid',32),
        'sessions':('SELECT session_id,group_id,status,session_kind,session_title FROM bcs_group_sessions WHERE env=?'+clause+' ORDER BY session_id',16),
        'session_participants':('SELECT session_id,group_id,bot_uuid,role FROM bcs_session_participants WHERE env=?'+clause+' ORDER BY session_id,bot_uuid',64),
        'messages':('SELECT message_id,group_id,session_id,owner_bot_id,status,content FROM bcs_messages WHERE env=?'+clause+' ORDER BY message_id',64),
        'deliveries':('SELECT delivery_id,group_id,session_id,status FROM bcs_message_deliveries WHERE env=?'+clause+' ORDER BY delivery_id',16),
        'runs':('SELECT run_id,group_id,session_id,status FROM bcs_state_machine_runs WHERE env=?'+clause+' ORDER BY run_id',16),
    }
    result = {}
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        for name,(sql,maximum) in queries.items():
            rows=_rows(db,sql,params,maximum);result[name]={'row_count':len(rows),'digest':fingerprint({'rows':rows})}
    return result


def owned_snapshot(scope) -> dict:
    group=scope._group(); explicit=scope._session(); ids=(group['initial_session_id'],explicit['id'])
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        groups=_rows(db,'SELECT group_id,status,driver_bot FROM bcs_groups WHERE env=? AND group_id=?',('local',group['id']),1)
        members=_rows(db,'SELECT group_id,bot_uuid,role,actor_kind,mode FROM bcs_group_participants WHERE env=? AND group_id=? ORDER BY bot_uuid',('local',group['id']),2)
        sessions=_rows(db,'SELECT session_id,group_id,status,session_kind,session_title FROM bcs_group_sessions WHERE env=? AND group_id=? ORDER BY session_id',('local',group['id']),2)
        participants=_rows(db,'SELECT session_id,group_id,bot_uuid,role FROM bcs_session_participants WHERE env=? AND group_id=? ORDER BY session_id,bot_uuid',('local',group['id']),4)
        deliveries=_rows(db,'SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id=?',('local',group['id']),1)
        runs=_rows(db,'SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id=?',('local',group['id']),1)
    expected_bots={scope._id('PM'),scope._id('ENG')}
    if (len(groups)!=1 or groups[0]['group_id']!=group['id'] or groups[0]['driver_bot']!=scope._id('PM')
            or len(members)!=2 or {r['bot_uuid'] for r in members}!=expected_bots
            or len(sessions)!=2 or {r['session_id'] for r in sessions}!=set(ids)
            or len(participants)!=4 or {(r['session_id'],r['bot_uuid']) for r in participants}!={(s,b) for s in ids for b in expected_bots}
            or deliveries or runs):
        _error('Channel CLI owned resource snapshot is not exact')
    return {'status':'PASS','group_rows':1,'group_participant_rows':2,'session_rows':2,
            'session_participant_rows':4,'delivery_rows':0,'state_machine_run_rows':0}


def history_snapshot(scope) -> dict:
    group=scope._group()
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        rows=_rows(db,'SELECT message_id,group_id,session_id,sender_id,sender_type,message_type,'
            'owner_bot_id,status,content FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id',
            ('local',group['id']),16)
    return {'row_count':len(rows),'digest':fingerprint({'rows':rows}),
            'disposition':'RETAINED_HISTORY_EXPLICIT'}


def deletion_admission(scope) -> dict:
    scope._active(); group=scope._group(); ids=tuple(scope._id(r) for r in scope.ROLES)
    config=json.loads(scope.runtime.inputs.config_path.read_text())
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        providers=_rows(db,'SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? AND bot_uuid IN (?,?,?,?)',('local',*ids),4)
        deliveries=_rows(db,'SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id=?',('local',group['id']),1)
        runs=_rows(db,'SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id=?',('local',group['id']),1)
    if (scope.runtime.state!='RUNNING' or not scope.runtime.ready or scope.runtime.child is None
            or scope.runtime.child.poll() is not None or not scope.runtime._owns_listener(1)
            or providers or deliveries or runs or config.get('store_messages') is not False
            or config.get('eventing')!={'enabled':False,'dispatcher_enabled':False}
            or config.get('collaboration',{}).get('loop_execution_enabled') is not False
            or config.get('llm')!={'type':'none'} or config.get('human_notify')!={'providers':[]}):
        _error('Channel CLI deletion is not admitted')
    return {'status':'LOCAL_DELETION_ADMITTED','contract':'owned-local-foreground-transport-only-v1',
        'foreground_runtime':'LIVE_OWNED_CHILD','provider_bindings':0,'queued_deliveries':0,
        'state_machine_runs':0,'engine':'NOT_STARTED','receiver':'NOT_OPENED','queue':'NOT_STARTED',
        'state_machine':'NOT_STARTED','business_execution':'NOT_CLAIMED','remote_quiescence':'NOT_APPLICABLE'}


def explicit_session_deletion_proof(scope) -> dict:
    session=scope._session()
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        sessions=db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND session_id=?',('local',session['id'])).fetchone()[0]
        participants=db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND session_id=?',('local',session['id'])).fetchone()[0]
    if sessions or participants: _error('Channel CLI explicit Session deletion proof differs')
    return {'status':'PASS','session_rows':sessions,'session_participant_rows':participants}


def group_deletion_proof(scope, history: dict) -> dict:
    group=scope._group()
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        counts={
          'group_rows':db.execute('SELECT COUNT(*) FROM bcs_groups WHERE env=? AND group_id=?',('local',group['id'])).fetchone()[0],
          'group_participant_rows':db.execute('SELECT COUNT(*) FROM bcs_group_participants WHERE env=? AND group_id=?',('local',group['id'])).fetchone()[0],
          'session_rows':db.execute('SELECT COUNT(*) FROM bcs_group_sessions WHERE env=? AND group_id=?',('local',group['id'])).fetchone()[0],
          'session_participant_rows':db.execute('SELECT COUNT(*) FROM bcs_session_participants WHERE env=? AND group_id=?',('local',group['id'])).fetchone()[0],
          'delivery_rows':db.execute('SELECT COUNT(*) FROM bcs_message_deliveries WHERE env=? AND group_id=?',('local',group['id'])).fetchone()[0],
          'state_machine_run_rows':db.execute('SELECT COUNT(*) FROM bcs_state_machine_runs WHERE env=? AND group_id=?',('local',group['id'])).fetchone()[0]}
    after=history_snapshot(scope)
    if any(counts.values()) or after!=history: _error('Channel CLI Group deletion or retained history proof differs')
    return {'status':'PASS',**counts,'retained_history':after}
