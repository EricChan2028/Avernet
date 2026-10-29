"""Fixed, bounded, read-only Provider evidence in the live owned SQLite DB.

No credential values, Provider config, Bot payloads, or webhook capabilities
leave this module. Deletion is logical: membership/binding tombstones and the
soft-deleted Bot are retained, never removed by the runner.
"""
from __future__ import annotations

import hashlib
import json

from .local_sqlite import OwnedSqliteDatabase
from .profile import identifier, unique_keys
from .run_state import RunStateError


def _require(condition):
    if not condition:
        raise RunStateError('owned Provider SQLite evidence differs from its finite contract')


def _rows(db, sql, params, limit=2):
    rows = [dict(row) for row in db.execute(sql, params).fetchmany(limit)]
    _require(len(json.dumps(rows, ensure_ascii=True)) <= 65_536)
    return rows


def _one(db, sql, params):
    rows = _rows(db, sql, params)
    _require(len(rows) == 1)
    return rows[0]


def _json(raw):
    _require(isinstance(raw, str) and len(raw.encode()) <= 65_536)
    return json.loads(raw, object_pairs_hook=unique_keys)


class OwnedLocalProviderStorage(OwnedSqliteDatabase):
    """Only fixed ID-filtered projections; no caller-supplied SQL or writes."""

    def fixture_snapshot(self, entries: dict, human: str) -> dict:
        _require(set(entries) == {'CEO', 'PM', 'ENG', 'QA'})
        result = {}
        with self._connection() as db:
            db.execute('BEGIN')
            for role in ('CEO', 'PM', 'ENG', 'QA'):
                bot, token = entries[role]
                identifier(bot, 'fixture Bot ID')
                row = _one(db, '''SELECT bot_uuid, name, visibility, created_by,
                    actor_kind, status, is_deleted, user_visibility, task_claim_mode,
                    task_dream_mode, friend_ext, friend_check_in_strategy, bot_info,
                    session_token = ? AS credential_matches FROM bcs_bots
                    WHERE env = ? AND bot_uuid = ? LIMIT 2''', (token, 'local', bot))
                _require(row['created_by'] == human and row['actor_kind'] == 'bot'
                         and row['is_deleted'] == 0 and row['credential_matches'] == 1)
                # Compare opaque configuration without persisting it (or its secrets).
                for field in ('bot_info', 'friend_ext'):
                    raw = row.pop(field)
                    _require(raw is None or isinstance(raw, str))
                    row[field + '_sha256'] = hashlib.sha256((raw or '').encode()).hexdigest()
                bindings = _rows(db, '''SELECT provider_id FROM bcs_provider_bot_bindings
                    WHERE env = ? AND bot_uuid = ? LIMIT 2''', ('local', bot))
                _require(not bindings)
                row['provider_bindings'] = 0
                result[role] = row
        return {'source': 'local-sqlite', 'roles': result}

    def fresh_namespace(self, organization_code: str) -> dict:
        identifier(organization_code, 'organization code')
        with self._connection() as db:
            db.execute('BEGIN')
            providers = _rows(db, 'SELECT provider_id FROM bcs_providers WHERE env = ? LIMIT 1',
                              ('local',), 1)
            organizations = _rows(db, '''SELECT code FROM bcs_organizations
                WHERE env = ? AND code = ? LIMIT 2''', ('local', organization_code))
            _require(not providers and not organizations)
        return {'source': 'local-sqlite', 'providers_present': False,
                'organization_code': organization_code, 'organization_present': False}

    def provider(self, provider_id: str) -> dict:
        identifier(provider_id, 'Provider ID')
        with self._connection() as db:
            db.execute('BEGIN')
            row = _one(db, '''SELECT provider_id, name, created_by, owners, disabled, config
                FROM bcs_providers WHERE env = ? AND provider_id = ? LIMIT 2''',
                       ('local', provider_id))
            config = _json(row.pop('config'))
            row['owners'] = _json(row['owners'])
            _require(isinstance(config, dict) and isinstance(config.get('downlink'), dict))
            downlink = config['downlink']
            url = downlink.get('webhook_url')
            _require(isinstance(url, str) and len(url) <= 2048)
            row.update(sink_url_sha256=hashlib.sha256(url.encode()).hexdigest(),
                       auth_mode=downlink.get('auth_mode'),
                       protocol_version=downlink.get('protocol_version'),
                       downlink_enabled=downlink.get('enabled'),
                       coordination_mode=config.get('coordination', {}).get('mode'),
                       admin_callback_configured=bool(config.get('admin_callback_url')))
            credentials = _rows(db, '''SELECT credential_kind, disabled
                FROM bcs_provider_credentials WHERE env = ? AND provider_id = ?
                ORDER BY credential_kind LIMIT 3''', ('local', provider_id), 3)
        return {'source': 'local-sqlite', 'provider': row, 'credentials': credentials}

    def bot(self, provider_id: str, bot_id: str, provider_ref: str, token: str) -> dict:
        for value in (provider_id, bot_id, provider_ref):
            identifier(value, 'owned Provider Bot identity')
        with self._connection() as db:
            db.execute('BEGIN')
            binding = _one(db, '''SELECT bot_uuid, provider_id, provider_bot_ref, disabled
                FROM bcs_provider_bot_bindings
                WHERE env = ? AND provider_id = ? AND provider_bot_ref = ? LIMIT 2''',
                           ('local', provider_id, provider_ref))
            _require(binding['bot_uuid'] == bot_id)
            bot = _one(db, '''SELECT bot_uuid, name, created_by, actor_kind, is_deleted,
                visibility, user_visibility, session_token = ? AS credential_matches
                FROM bcs_bots WHERE env = ? AND bot_uuid = ? LIMIT 2''',
                       (token, 'local', bot_id))
        return {'source': 'local-sqlite', 'binding': binding, 'bot': bot}

    def organization(self, provider_id: str, code: str, bot_id: str) -> dict:
        for value in (provider_id, code, bot_id):
            identifier(value, 'owned organization identity')
        with self._connection() as db:
            db.execute('BEGIN')
            organization = _one(db, '''SELECT code, name, managing_provider_id, disabled
                FROM bcs_organizations WHERE env = ? AND code = ? LIMIT 2''', ('local', code))
            _require(organization['managing_provider_id'] == provider_id)
            members = _rows(db, '''SELECT organization_code, bot_uuid, role, disabled
                FROM bcs_organization_members
                WHERE env = ? AND organization_code = ? AND bot_uuid = ? LIMIT 2''',
                            ('local', code, bot_id))
            _require(len(members) <= 1)
        return {'source': 'local-sqlite', 'organization': organization, 'members': members}
