"""Explicit native group-only contract. Never changes the V1 pilot adapter.

A short-lived source review is matched against the authenticated service build
stamp. This is deployment self-report + source review, not binary attestation.
No session-list, chat, Bot mutation or route fallback is exposed here.
"""
from __future__ import annotations

import copy
import re
import time
from urllib.parse import quote

from .identity_binding import NATIVE
from .profile import ConfigError, fingerprint, identifier, object_keys, validate, require_resolved
from .write_transport import WriteClient

CASE_ID = 'NATIVE-GROUP-01'
CONTRACT = 'native-sessionless-group-v1'


def validate_review(value: dict, profile: dict) -> dict:
    object_keys(value, {'schema_version', 'contract', 'profile_digest', 'health_version',
                       'ocb_commit', 'avernet_commit', 'reviewed_at', 'expires_at',
                       'source_review_sha256'}, 'sessionless review')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT or value['profile_digest'] != fingerprint(profile)):
        raise ConfigError('sessionless review does not match the selected contract/profile')
    for key, length in (('ocb_commit', 40), ('avernet_commit', 40), ('source_review_sha256', 64)):
        if not isinstance(value[key], str) or not re.fullmatch('[0-9a-f]{'+str(length)+'}', value[key]):
            raise ConfigError('sessionless review requires resolved source commits and review digest')
    version = value['health_version']
    match = re.fullmatch(r'[^\s]{1,32} \(ocb [^\s/;]{1,128}/([0-9a-f]{7,40}); avernet '
                         r'[^\s/;]{1,128}/([0-9a-f]{7,40}); \d{4}-\d{2}-\d{2}\)',
                         version) if isinstance(version, str) else None
    if (match is None or not value['ocb_commit'].startswith(match[1])
            or not value['avernet_commit'].startswith(match[2])):
        raise ConfigError('sessionless review build stamp does not identify the reviewed source')
    start, end = value['reviewed_at'], value['expires_at']
    if (type(start) is not int or type(end) is not int or not 0 < end-start <= 3600
            or not start <= int(time.time()) < end):
        raise ConfigError('sessionless source review is expired or not yet valid')
    return copy.deepcopy(value)


def require_profile(profile: dict) -> None:
    validate(copy.deepcopy(profile))
    require_resolved(profile)
    if (profile['environment']['tier'] != 'preprod'
            or profile['entrypoint']['kind'] != 'bcs-direct'
            or profile.get('identity_binding') != NATIVE
            or profile['entrypoint']['manifest_face'] != 'legacy'
            or profile['policy']['exclusive_use_confirmed'] is not True
            or set(profile['auth'].get('header_env', {})) != {'Cookie'}):
        raise ConfigError('sessionless slice requires an explicit native preprod Cookie identity and exclusive pool')


class NativeSessionlessClient:
    def __init__(self, profile: dict, review: dict):
        require_profile(profile)
        self.profile = copy.deepcopy(profile)
        self.review = validate_review(review, profile)
        # Private transport projection only. The input remains a discovery-only
        # native profile; global public-v1 validation/guard is not relaxed.
        wire = copy.deepcopy(profile)
        wire['entrypoint']['api_face'] = CONTRACT
        human = quote('human_' + profile['human']['user_id'], safe='')
        routes = {
            'identity': ('GET', '/auth/user'), 'manifest': ('GET', '/manifest'),
            'health': ('GET', '/health'),
            'create': ('POST', '/groups'), 'read': ('GET', '/groups/{group_id}'),
            'rename': ('PATCH', '/groups/{group_id}'),
            'delete': ('DELETE', '/groups/{group_id}?bot_id=' + human),
        }
        for role, actor in profile['actors'].items():
            routes['bot.'+role] = ('GET', '/bots/' + quote(actor['bot_id'], safe=''))
        wire['route_bindings'] = {k: {'method': m, 'path': p, 'traceparent': 'disabled'}
                                  for k, (m, p) in routes.items()}
        self._wire = WriteClient(wire)
        self.evidence = []

    def _call(self, operation, **kwargs):
        try:
            value, record = self._wire.call(operation, **kwargs)
        except Exception as error:
            for record in getattr(error, 'request_records', []):
                self.evidence.append(record)
            raise
        self.evidence.append(record)
        return value

    def verify_build(self):
        validate_review(self.review, self.profile)
        health = self._call('health')
        if (not isinstance(health, dict) or health.get('service') != 'bcs'
                or health.get('status') != 'ok' or health.get('version') != self.review['health_version']):
            raise ConfigError('native service build differs from the source-reviewed sessionless contract')

    def verify_identity(self):
        identity = self._call('identity')
        if not isinstance(identity, dict) or identity.get('user_id') != self.profile['human']['user_id']:
            raise ConfigError('native identity differs from the bound Human')

    def preflight(self):
        self.verify_identity()
        manifest = self._call('manifest')
        if not isinstance(manifest, dict) or manifest.get('env') != self.profile['environment']['expected_bcs_env']:
            raise ConfigError('native environment differs from the selected profile')
        self.verify_build()
        self.verify_actors()

    def verify_actors(self):
        for role, binding in self.profile['actors'].items():
            bot = self._call('bot.'+role)
            if not isinstance(bot, dict):
                raise ConfigError('native Bot response must be an object')
            expected = {'bot_uuid': binding['bot_id'], 'actor_kind': 'bot',
                        'created_by': self.profile['human']['user_id'],
                        'env': self.profile['environment']['expected_bcs_env'], 'status': 'online'}
            if (any(bot.get(k) != v for k, v in expected.items()) or not isinstance(bot.get('capabilities'), dict)
                    or bot['capabilities'].get('visibility') != 'protected'):
                raise ConfigError('native Bot must be owned, online, in the selected env and protected')

    def create(self, name: str, request_id: str):
        identifier(request_id, "request_id")
        self.verify_build()
        self.verify_actors()
        driver, member = (self.profile['actors'][r]['bot_id'] for r in ('driver', 'member'))
        return self._call('create', request_id=request_id, body={
            'topic': name, 'context': 'Isolated sessionless group CRUD; no Bot execution',
            'driver_bot': driver, 'originator': 'human_' + self.profile['human']['user_id'],
            'participants': [{'bot_uuid': driver, 'role': 'driver'},
                             {'bot_uuid': member, 'role': 'consultant'}], 'group_kind': 'normal',
            'group_strategy': 'chat', 'visibility': 'private', 'create_initial_session': False,
        })

    def read(self, group_id: str):
        identifier(group_id, 'group_id')
        return self._call('read', group_id=group_id)

    def rename(self, group_id: str, name: str):
        identifier(group_id, 'group_id'); self.verify_build()
        return self._call('rename', group_id=group_id, body={'name': name})

    def delete(self, group_id: str):
        identifier(group_id, 'group_id'); self.verify_identity(); self.verify_build()
        return self._call('delete', group_id=group_id)

    def verify_deleted(self, group_id: str):
        identifier(group_id, 'group_id')
        self._call('read', group_id=group_id, expected=(404,))
