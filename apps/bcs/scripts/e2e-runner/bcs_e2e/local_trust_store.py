"""Fixed absence checks for legacy trust state in a verified owned SQLite DB.

No arbitrary path, SQL, remote target or repair operation is accepted. This is
not evidence about the separate edge-permission graph or a future time interval.
"""
from __future__ import annotations

from .local_fixtures import OwnedLocalFixtures
from .local_sqlite import OwnedSqliteDatabase
from .trust_baseline import ROLES, TrustBaselineError

_COLUMNS = {'bcs_friendships': {'env', 'left_bot', 'right_bot'},
            'bcs_friend_requests': {'env', 'from_bot', 'to_bot', 'status'}}
_SQL = '''WITH actors(role, bot_id) AS (VALUES (?, ?), (?, ?), (?, ?), (?, ?), (?, ?))
SELECT role,
 EXISTS(SELECT 1 FROM bcs_friendships f WHERE f.env='local'
        AND (f.left_bot=actors.bot_id OR f.right_bot=actors.bot_id)) AS friendship_present,
 EXISTS(SELECT 1 FROM bcs_friend_requests r WHERE r.env='local'
        AND r.from_bot=actors.bot_id) AS sent_request_present,
 EXISTS(SELECT 1 FROM bcs_friend_requests r WHERE r.env='local'
        AND r.to_bot=actors.bot_id) AS received_request_present
FROM actors'''


def observe_legacy_baseline(fixtures, bindings: dict) -> dict:
    if type(fixtures) is not OwnedLocalFixtures or fixtures.fixture_set != 'trusted-team':
        raise TrustBaselineError('legacy trust persistence requires owned trusted-team fixtures')
    before = fixtures.check()
    if (set(bindings) != set(ROLES)
            or any(bindings[role] != before['roles'][role]['bot_id'] for role in ROLES)):
        raise TrustBaselineError('legacy trust query IDs differ from the verified fixture roles')
    database = OwnedSqliteDatabase(fixtures.runtime)
    with database._connection() as db:
        db.execute('BEGIN')
        for table, columns in _COLUMNS.items():
            objects = db.execute('SELECT type FROM sqlite_master WHERE name=? LIMIT 2', (table,)).fetchall()
            if len(objects) != 1 or objects[0]['type'] != 'table':
                raise TrustBaselineError('legacy trust table is missing or not a table')
            schema = db.execute(f'PRAGMA table_info("{table}")').fetchmany(101)
            if len(schema) > 100 or not columns <= {row['name'] for row in schema}:
                raise TrustBaselineError('legacy trust schema is incompatible')
        parameters = tuple(value for role in ROLES for value in (role, bindings[role]))
        rows = [dict(row) for row in db.execute(_SQL, parameters).fetchmany(6)]
        if len(rows) != 5 or {row['role'] for row in rows} != set(ROLES):
            raise TrustBaselineError('legacy trust snapshot has an invalid role inventory')
        rows.sort(key=lambda row: ROLES.index(row['role']))
        for row in rows:
            if any(type(row[key]) is not int or row[key] != 0
                   for key in ('friendship_present', 'sent_request_present', 'received_request_present')):
                # Include all request statuses, deleted/missing peers and either
                # endpoint, not only rows still visible through API enrichment.
                raise TrustBaselineError('persisted legacy relationship or request history conflicts with fresh fixtures')
    if fixtures.check() != before:
        raise TrustBaselineError('trusted-team fixtures changed during storage observation')
    return {'status': 'OBSERVED', 'source': 'owned-local-sqlite', 'environment': 'local',
            'scope': 'all incident legacy friendship and request rows for five exact created Bots',
            'snapshot': 'READ_TRANSACTION', 'rows': rows, 'business_queries': 1,
            'edge_permission_graph': 'NOT_CHECKED', 'storage_writes': 0}
