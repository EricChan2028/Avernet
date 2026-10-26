"""Read-only reviewed HTTP + MCP admission before reserving a fixture pool.

No resource IDs, business-row query, Run directory, lease or fixture writes.
Metadata readiness is not proof of write permission, BaaS health or cleanup.
"""
from __future__ import annotations

from pathlib import Path

from .alisddbquery import AlisdDbQueryObserver
from .errors import primary_error
from .evidence import EvidenceError
from .observation_gate import REQUIRED_OBSERVATIONS
from .preflight import check
from .profile import ConfigError
from .remote_observer import remote_check_dependencies
from .transport import ReadClient


class MetadataOnlyClient:
    """Refuse the db_query operation even if a provider accidentally requests it."""
    def __init__(self, client):
        self._client = client

    def db_list(self):
        return self._client.db_list()

    def table_list(self, database):
        return self._client.table_list(database)

    def table_describe(self, database, table):
        return self._client.table_describe(database, table)

    def db_query(self, plan):
        raise EvidenceError('business-row queries are forbidden during remote preflight')


def check_remote(profile: dict, *, receipt: Path, executable: Path,
                 allow_http_read: bool, allow_observer_read: bool) -> dict:
    if allow_http_read is not True:
        raise ConfigError('remote check requires --allow-read for authenticated HTTP discovery')
    client, binding = remote_check_dependencies(profile, receipt, executable=executable,
                                                allow_read=allow_observer_read)
    # Refuse the wrong API environment or identity before querying MCP metadata.
    result = check(profile, ReadClient(profile))
    result['observer_binding'] = binding.summary
    result['observer_preflight'] = {'status': 'NOT_RUN', 'scope': 'METADATA_ADMISSION_ONLY',
                                    'business_queries': 0}
    if result['status'] != 'DISCOVERY_PASSED':
        return result
    assertions = tuple(dict.fromkeys(a for ids in REQUIRED_OBSERVATIONS.values() for a in ids))
    observer = AlisdDbQueryObserver(client=MetadataOnlyClient(client),
        database=profile['observer']['database'], environment=profile['environment']['expected_bcs_env'],
        environment_binding_confirmed=True, physical_binding_probe=binding)
    try:
        admission = observer.admit(assertions)
        expected = {'status': 'READY', 'source': 'alisddbquery', 'database': profile['observer']['database'],
                    'environment': profile['environment']['expected_bcs_env'], 'assertion_ids': list(assertions)}
        if admission != expected:
            raise EvidenceError('observer preflight returned an unexpected admission')
        # Reject a receipt changed/expired during the final metadata call too.
        binding.validate()
        result['observer_preflight'].update(status='METADATA_ADMITTED', assertion_ids=list(assertions))
        result['not_verified'] = [
            'OB business-row access and values' if item == 'live OB schema/access' else item
            for item in result['not_verified']]
        result['checks'].append({'id': 'observer.reviewed_schema', 'status': 'PASS',
                                'reason': 'required columns present under operator-attested mapping; no rows queried'})
    except Exception as error:
        cause = primary_error(error)
        if isinstance(cause, KeyboardInterrupt):
            raise cause from None
        result['status'] = 'BLOCKED'
        result['observer_preflight'].update(status='BLOCKED', error_type=type(error).__name__)
        result['checks'].append({'id': 'observer.reviewed_schema', 'status': 'BLOCKED',
                                'reason': 'required observer metadata admission failed'})
    return result
