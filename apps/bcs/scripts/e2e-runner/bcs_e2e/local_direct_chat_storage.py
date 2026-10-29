"""Bounded read-only fixture evidence for the owned-local Direct chat Story.

Direct-chat Runs use the BCS in-process memory store in this executor.  This
module deliberately does not inspect ``bcs_chat_runs`` or claim physical row
retention/deletion; it proves only the exact four fixture Bots, absence of
incident legacy friendship rows, and the no-provider/no-engine local boundary.
"""
from __future__ import annotations

import json

from .local_provider_storage import OwnedLocalProviderStorage
from .local_sqlite import OwnedSqliteDatabase
from .profile import unique_keys, fingerprint
from .run_state import RunStateError, read


class LocalDirectChatStorageError(RunStateError):
    pass


def _require(value: object, message: str) -> None:
    if not value:
        raise LocalDirectChatStorageError(message)


def _rows(db, sql: str, params: tuple, maximum: int) -> list[dict]:
    rows = [dict(row) for row in db.execute(sql, params).fetchmany(maximum + 1)]
    _require(len(rows) <= maximum, 'Direct chat SQLite snapshot exceeded its finite bound')
    return rows


def bot_info_semantic_digest(raw: str) -> str:
    """Bot-store JSON_SET materializes these three default-None fields on reconnect."""
    _require(isinstance(raw, str) and len(raw.encode()) <= 65_536, 'Bot info exceeds observation budget')
    value = json.loads(raw, object_pairs_hook=unique_keys)
    _require(isinstance(value, dict), 'Bot info must be an object')
    for key in ('binding_channels', 'agent_code', 'agent_token'):
        if key in value and value[key] is None:
            del value[key]
    return fingerprint(value)


def fixture_snapshot(scope) -> dict:
    """Return one finite snapshot for the exact CEO/PM/ENG/QA fixtures."""
    scope._active()
    receipt = read(scope.identities.fixtures.path)
    ids = tuple(receipt['roles'][role]['bot_id'] for role in scope.ROLES)
    _require(len(ids) == 4 and len(set(ids)) == 4, 'Direct chat fixture identities differ')
    fixtures = OwnedLocalProviderStorage(scope.runtime).fixture_snapshot(
        scope.identities._entries, scope.identities.human)
    placeholders = ','.join('?' for _ in ids)
    with OwnedSqliteDatabase(scope.runtime)._connection() as db:
        db.execute('BEGIN')
        bot_infos = _rows(db, 'SELECT bot_uuid,bot_info FROM bcs_bots WHERE env=? AND '
                         f'bot_uuid IN ({placeholders})', ('local', *ids), 4)
        _require(len(bot_infos) == 4, 'Direct chat Bot info snapshot is incomplete')
        normalized = {row['bot_uuid']: bot_info_semantic_digest(row['bot_info']) for row in bot_infos}
        for row in fixtures['roles'].values():
            row.pop('bot_info_sha256')
            row['bot_info_semantic_sha256'] = normalized[row['bot_uuid']]
        friendships = _rows(
            db,
            'SELECT left_bot,right_bot,env FROM bcs_friendships WHERE env=? AND '
            f'(left_bot IN ({placeholders}) OR right_bot IN ({placeholders})) '
            'ORDER BY left_bot,right_bot',
            ('local', *ids, *ids),
            12,
        )
        requests = _rows(
            db,
            'SELECT request_id,from_bot,to_bot,status,env FROM bcs_friend_requests '
            'WHERE env=? AND '
            f'(from_bot IN ({placeholders}) OR to_bot IN ({placeholders})) '
            'ORDER BY request_id',
            ('local', *ids, *ids),
            24,
        )
        providers = _rows(db, 'SELECT provider_id FROM bcs_providers WHERE env=?', ('local',), 1)
        provider_bindings = _rows(
            db,
            'SELECT bot_uuid,provider_id FROM bcs_provider_bot_bindings WHERE env=? AND '
            f'bot_uuid IN ({placeholders}) ORDER BY bot_uuid',
            ('local', *ids),
            4,
        )
    role_rows = fixtures.get('roles')
    _require(isinstance(role_rows, dict) and set(role_rows) == set(scope.ROLES),
             'Direct chat fixture role projection differs')
    _require(all(role_rows[role].get('visibility') == 'protected' for role in scope.ROLES),
             'Direct chat fixtures must start and finish protected')
    _require(not friendships and not requests,
             'Direct chat requires CEO and PM to remain non-friends without request history')
    _require(not providers and not provider_bindings,
             'Direct chat owned runtime must not contain Provider delivery bindings')
    config = json.loads(scope.runtime.inputs.config_path.read_text())
    explicit_store = config.get('async_chat_run_store')
    _require(explicit_store in (None, 'memory'),
             'Direct chat refuses a persistent async chat Run store override')
    _require(config.get('store_messages') is False
             and config.get('allowed_switch_provider_ids') == []
             and config.get('llm') == {'type': 'none'}
             and config.get('human_notify') == {'providers': []}
             and config.get('eventing') == {'enabled': False, 'dispatcher_enabled': False},
             'Direct chat local no-engine configuration differs')
    safe = {
        'fixture_digest': fingerprint(receipt),
        'roles': role_rows,
        'friendships': friendships,
        'friend_requests': requests,
        'provider_count': len(providers),
        'provider_binding_count': len(provider_bindings),
        'async_chat_run_store': 'memory',
        'async_chat_run_store_source': 'DEFAULT_ABSENT' if explicit_store is None else 'EXPLICIT_MEMORY',
        'chat_run_sqlite_evidence': 'NOT_ASSERTED_MEMORY_STORE',
        'engine': 'NOT_STARTED',
    }
    return {**safe, 'digest': fingerprint(safe)}
