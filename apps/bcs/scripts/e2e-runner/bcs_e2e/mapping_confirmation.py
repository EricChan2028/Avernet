"""Reusable environment facts, deliberately not a query permission or credential."""
from __future__ import annotations

import copy
from pathlib import Path
import re
import time

from .metadata_probe import _server_name
from .profile import ConfigError, fingerprint, identifier, object_keys, require_resolved, validate
from .run_state import read

CONTRACT = 'environment-database-mapping-v1'


def target(profile: dict) -> dict:
    return {'environment_tier': profile['environment']['tier'], 'environment_id': profile['environment']['id'],
            'api_origin': profile['entrypoint']['origin'], 'server': profile['observer']['server'],
            'database': profile['observer']['database'], 'env': profile['environment']['expected_bcs_env']}


def validate_confirmation(value: dict, profile: dict) -> dict:
    validate(copy.deepcopy(profile))
    expected = target(profile)
    require_resolved(expected)
    if (profile['environment']['tier'] != 'preprod'
            or profile['entrypoint']['kind'] not in {'gateway', 'bcs-direct'}
            or profile['observer']['kind'] != 'alisddbquery'
            or profile['observer']['environment_binding_confirmed'] is not True
            or profile['policy']['max_response_bytes'] < 1024):
        raise ConfigError('mapping confirmation requires a resolved confirmed preprod MCP target')
    object_keys(value, {'schema_version', 'contract', 'mapping_id', 'revision', 'status',
                       'confirmed_at', 'confirmed_by', 'target', 'database_sharing', 'evidence'}, 'mapping confirmation')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT or value['status'] != 'ACTIVE'):
        raise ConfigError('environment mapping is unsupported, revoked or not confirmed')
    identifier(value['mapping_id'], 'environment mapping ID')
    identifier(value['confirmed_by'], 'mapping confirmer')
    if (type(value['revision']) is not int or not 1 <= value['revision'] <= 2**31-1
            or type(value['confirmed_at']) is not int or not 0 < value['confirmed_at'] <= int(time.time())):
        raise ConfigError('mapping revision or confirmation time is invalid')
    if value['target'] != expected:
        raise ConfigError('environment mapping target changed; explicit reconfirmation required')
    _server_name(expected['server'])
    if value['database_sharing'] not in ('preprod-only', 'preprod-and-production'):
        raise ConfigError('mapping must state whether the database is shared with production')
    evidence = object_keys(value['evidence'], {'reference', 'sha256'}, 'mapping evidence')
    identifier(evidence['reference'], 'mapping evidence reference')
    if not isinstance(evidence['sha256'], str) or re.fullmatch('[0-9a-f]{64}', evidence['sha256']) is None:
        raise ConfigError('mapping requires actual confirmation evidence digest')
    return copy.deepcopy(value)


class EnvironmentMapping:
    def __init__(self, profile: dict, path: Path):
        self.profile, self.path = copy.deepcopy(profile), path
        value = validate_confirmation(read(path), self.profile)
        self.digest = fingerprint(value)
        self.summary = {'verification_mode': 'OWNER_CONFIRMED_ENVIRONMENT_MAPPING',
                        'receipt_digest': self.digest, 'mapping_id': value['mapping_id'],
                        'revision': value['revision'], 'confirmed_at': value['confirmed_at'],
                        'validity': 'UNTIL_REVOKED_OR_TARGET_CHANGED',
                        'query_authorization': 'NONE', 'live_verification': 'NOT_PERFORMED',
                        'deployment_version_source': 'UNVERIFIED',
                        'shared_database': value['database_sharing'] == 'preprod-and-production'}

    def validate(self) -> dict:
        value = validate_confirmation(read(self.path), self.profile)
        if fingerprint(value) != self.digest:
            raise ConfigError('environment mapping changed during this command; no implicit rebind')
        return value
