#!/usr/bin/env python3
"""Black-box Provider subbots journey against one owned BCS runtime.

This is the owned-local migration of the upstream
``agentpass_subbots_story.py``.  It preserves the upstream request order and
its 131 live-HTTP assertion count, but emits a non-secret semantic observation
file instead of retaining registration credentials.  Tokens and signed
principals live only in this child process's memory.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

EXPECTED_CHECKS = 131
MARKER_PREFIX = 'Provider subbots story: '
MARKER_SUFFIX = ' live HTTP assertions passed'
PRINCIPAL_ENV = 'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'
REDACTION_CANARIES = (
    'bot' + '_token', 'bot_runtime' + '_token', 'provider_admin' + '_token',
    'X-Avernet-' + 'Principal', 'Bear' + 'er ', 'coo' + 'kie',
)


class StoryFailure(AssertionError):
    pass


class Story:
    def __init__(self, base_url: str, human: str):
        self.base_url = base_url.rstrip('/')
        self.human = human
        self.checks = 0
        self.responses: list[dict] = []
        self.views: list[dict] = []
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, label: str, method: str, path: str, status: int = 200,
                body=None, token: str | None = None, headers=None):
        request_headers = {
            'Content-Type': 'application/json',
            'X-Mock-User-Id': self.human,
            'X-Mock-Nick-Name': 'Subbots E2E operator',
        }
        if token is not None:
            request_headers['Authorization'] = 'Bear' + 'er ' + token
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body).encode('utf-8'),
            headers=request_headers,
            method=method,
        )
        try:
            try:
                response = self._opener.open(request, timeout=30)
            except urllib.error.HTTPError as error:
                response = error
        except (OSError, urllib.error.URLError):
            # Never include the request target: registration paths can carry
            # one-use credentials and signed principals in headers.
            raise StoryFailure(label + ': HTTP transport failed') from None
        with response:
            observed = response.status
            cache_control = response.headers.get('Cache-Control')
            self.responses.append({'label': label, 'status': observed,
                                   'cache_control': cache_control})
            self.check(observed == status,
                       label + ': HTTP ' + str(observed) + ', expected ' + str(status))
            try:
                payload = json.load(response)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise StoryFailure(label + ': response was not JSON') from None
        return payload, response.headers

    def check(self, condition: object, label: str) -> None:
        if not condition:
            raise StoryFailure(label)
        self.checks += 1

    def self_view(self, bot: dict, parent: str, actor: str | None = None,
                  status: int = 200):
        headers = {} if actor is None else {'X-BCN-Actor-Bot-Id': actor}
        payload, response_headers = self.request(
            'Bot self lookup', 'GET', '/api/v1/collaboration/bots/me',
            status=status, token=bot['token'], headers=headers)
        self.check(response_headers.get('Cache-Control') == 'no-store',
                   'self lookup disables caching')
        entry = {
            'status': status,
            'cache_control': response_headers.get('Cache-Control'),
            'actor_kind': ('none' if actor is None else
                           'matching' if actor == bot['id'] else 'sibling'),
        }
        if status == 200:
            data = payload['data']
            entry.update({
                'registered': data['registration_status'] == 'registered',
                'parent_matches': data['identity']['agent_code'] == parent,
                'bot_id_matches': data['bot']['bot_id'] == bot['id'],
                'ref_matches': data['bot']['provider_bot_ref'] == bot['ref'],
                'token_absent': bot['token'] not in json.dumps(payload),
            })
            self.check(entry['registered'], 'Bot remains registered')
            self.check(entry['parent_matches'],
                       'metadata update preserves parent identity')
            self.check(entry['bot_id_matches'],
                       "self lookup selects the credential's Bot")
            self.check(entry['ref_matches'], 'self lookup preserves composed ref')
            self.check(entry['token_absent'],
                       'self projection contains no runtime token')
        self.views.append(entry)

    def signed_bot(self, bot: dict, parent: str) -> str:
        key = os.environ.get(PRINCIPAL_ENV)
        if not isinstance(key, str) or not key:
            raise StoryFailure('owned runtime principal signing key is missing')
        now = int(time.time())
        claims = {
            'iss': 'gateway', 'aud': 'bcs', 'iat': now - 1, 'exp': now + 300,
            'principals': [{
                'type': 'bot', 'tenant': 'e2e',
                'bot': {
                    'bot_uuid': bot['id'], 'owner_id': self.human,
                    'app_id': 0, 'agent_code': parent, 'tenant': 'e2e',
                },
            }],
        }

        def encode(value) -> bytes:
            return base64.urlsafe_b64encode(
                json.dumps(value, separators=(',', ':')).encode('utf-8')).rstrip(b'=')

        message = (encode({'alg': 'HS256', 'typ': 'JWT', 'kid': 'bare'})
                   + b'.' + encode(claims))
        signature = base64.urlsafe_b64encode(
            hmac.new(key.encode('utf-8'), message, hashlib.sha256).digest()).rstrip(b'=')
        return (message + b'.' + signature).decode('ascii')

    def _statuses(self, label: str) -> list[int]:
        return [row['status'] for row in self.responses if row['label'] == label]

    def _status(self, label: str) -> int:
        statuses = self._statuses(label)
        if len(statuses) != 1:
            raise StoryFailure('observation count mismatch for ' + label)
        return statuses[0]

    def run(self) -> dict:
        suffix = uuid.uuid4().hex[:12]
        parent = 'poolab:e2e:' + suffix
        mode = 'agentpass-subbots'
        provider, _ = self.request(
            'create subbots Provider', 'POST', '/providers', body={
                'name': 'E2E sibling Bots',
                'slug': 'e2e-subbots-' + suffix,
                'webhook_url': 'https://provider.example.com/bcs/webhook',
                'auth': {'mode': mode},
                'protocol_version': '2.0',
                'coordination': {'mode': 'native_tool'},
            })
        provider_id, admin = provider['provider_id'], provider['provider_admin_token']
        path = '/providers/' + provider_id
        public, _ = self.request(
            'discover subbots mode', 'GET', '/providers/by-slug/e2e-subbots-' + suffix)
        self.check(public['auth_mode'] == mode,
                   'Provider discovery reports subbots mode')

        registration = {
            'name': 'E2E Reviewer', 'summary': 'Review changes',
            'owners': [self.human], 'provider_bot_ref': parent + ':reviewer',
        }
        for invalid in ('parent-without-slug', ':a', parent + ':',
                        parent + ':Uppercase', 'bad/parent:a', parent + ':' + 'x' * 33):
            self.request(
                'reject invalid complete ref before registration', 'POST',
                path + '/bots', 400,
                body={**registration, 'provider_bot_ref': invalid}, token=admin)
        first, _ = self.request(
            'register reviewer with Provider credential', 'POST', path + '/bots',
            body=registration, token=admin)
        self.check(first['agent_code'] == parent and first['bot_slug'] == 'reviewer',
                   'registration returns parent and slug')
        self.check(first['auth_mode'] == mode
                   and first['provider_bot_ref'] == parent + ':reviewer',
                   'server preserves client-composed ref')
        a = {
            'id': first['bot_uuid'],
            'token': first['bot_runtime_token'],
            'ref': first['provider_bot_ref'],
        }
        self.request(
            'reject duplicate reviewer', 'POST', path + '/bots', 409,
            body=registration, token=admin)

        scoped, _ = self.request(
            'owner issues scoped registration credential', 'GET',
            '/register/token?' + urllib.parse.urlencode({'provider_id': provider_id}))
        query = {
            'token': scoped['token'], 'bot-name': 'E2E Coder', 'mode': 'plugin',
            'provider_bot_ref': parent + ':coder',
        }
        self.request(
            'removed bot_slug cannot complete a malformed ref', 'POST',
            '/register?' + urllib.parse.urlencode(
                {**query, 'provider_bot_ref': 'parent-without-slug',
                 'bot_slug': 'coder'}), 400)
        for invalid in (':a', parent + ':', parent + ':Uppercase', 'bad/parent:a'):
            self.request(
                'scoped registration rejects malformed complete ref', 'POST',
                '/register?' + urllib.parse.urlencode(
                    {**query, 'provider_bot_ref': invalid}), 400)
        second, _ = self.request(
            'register coder with scoped credential', 'POST',
            '/register?' + urllib.parse.urlencode(query))
        identity = second['registration']
        self.check(identity['agent_code'] == parent and identity['bot_slug'] == 'coder',
                   'scoped registration returns child identity')
        self.check(identity['auth_mode'] == mode
                   and identity['provider_bot_ref'] == parent + ':coder',
                   'both paths parse the same identity')
        b = {
            'id': second['bot_uuid'],
            'token': second['bot' + '_token'],
            'ref': identity['provider_bot_ref'],
        }
        self.check(a['id'] != b['id'] and a['token'] != b['token'],
                   'siblings receive distinct UUIDs and tokens')
        self.request(
            'scoped duplicate fails at registration', 'POST',
            '/register?' + urllib.parse.urlencode(query), 409)

        for bot, sibling in ((a, b), (b, a)):
            self.self_view(bot, parent)
            self.self_view(bot, parent, actor=bot['id'])
            self.self_view(bot, parent, actor=sibling['id'], status=401)
            self.request(
                'legacy auth accepts matching Actor', 'GET', '/groups/my',
                token=bot['token'], headers={'X-BCN-Actor-Bot-Id': bot['id']})
            self.request(
                'legacy auth rejects sibling Actor despite mock user', 'GET',
                '/groups/my', 401, token=bot['token'],
                headers={'X-BCN-Actor-Bot-Id': sibling['id']})
        self.request(
            'Actor alone cannot fall back to human', 'GET', '/groups/my', 401,
            headers={'X-BCN-Actor-Bot-Id': a['id']})
        self.request(
            'malformed Actor is rejected', 'GET', '/groups/my', 401,
            token=a['token'], headers={'X-BCN-Actor-Bot-Id': ''})
        changed, _ = self.request(
            'update reviewer metadata', 'PATCH',
            path + '/bots/' + urllib.parse.quote(a['ref'], safe=''),
            body={'name': 'E2E Senior Reviewer', 'domains': ['audit'], 'scopes': []},
            token=admin)
        self.check(changed['bot_uuid'] == a['id']
                   and changed['name'] == 'E2E Senior Reviewer',
                   'update retains Bot UUID')
        self.self_view(a, parent)
        metadata_view = dict(self.views[-1])

        self.request(
            'onboarding metadata preserves parent identity', 'POST', '/bots/onboard',
            body={'name': 'E2E Reviewer', 'summary': 'Review changes'},
            token=a['token'], headers={'X-BCN-Actor-Bot-Id': a['id']})
        self.self_view(a, parent)
        onboarding_view = dict(self.views[-1])

        self.request(
            'parent discovery requires credentials', 'GET',
            '/api/v1/collaboration/agents/me', 401)
        for query_string in ('limit=0', 'limit=101', 'cursor=', 'unknown=value'):
            self.request(
                'parent discovery rejects invalid query', 'GET',
                '/api/v1/collaboration/agents/me?' + query_string, 400,
                token=a['token'])
        unavailable, _ = self.request(
            'public build fails closed without AgentPass verifier', 'GET',
            '/api/v1/collaboration/agents/me', 502, token=a['token'])
        self.check(unavailable['data']['error_code'] == 'agent_identity_unavailable',
                   'missing verifier cannot authenticate a parent')

        event = {
            'run_id': 'missing-subbots-run-' + suffix,
            'state': 'final', 'message': {'text': 'finished'},
        }
        callback_headers = {
            'X-BCN-Provider-Id': provider_id,
            'X-BCN-Actor-Bot-Id': a['id'],
        }
        result, _ = self.request(
            'authenticated callback reaches run lookup', 'POST', '/bot/events', 404,
            body=event, token=a['token'], headers=callback_headers)
        self.check(result['error'] == 'run_not_found',
                   'callback validates identity before resolving run')
        self.request(
            'callback rejects sibling Actor', 'POST', '/bot/events', 401,
            body=event, token=a['token'],
            headers={**callback_headers, 'X-BCN-Actor-Bot-Id': b['id']})
        self.request(
            'callback accepts Bot token without Actor', 'POST', '/bot/events', 404,
            body=event, token=a['token'],
            headers={'X-BCN-Provider-Id': provider_id})

        principal = {
            'X-Avernet-Principal': self.signed_bot(a, parent),
            'X-BCN-Actor-Bot-Id': b['id'],
        }
        openapi_path = '/openapi/v1/collaboration/public-groups'
        self.request('Gateway principal uses signed Bot', 'GET', openapi_path,
                     headers=principal)
        self.request('disable Provider', 'POST', path + '/disable', token=admin)
        self.self_view(a, parent, status=401)
        disabled_view_a = self.views[-1]['status']
        self.self_view(b, parent, status=401)
        disabled_view_b = self.views[-1]['status']
        self.request(
            'disabled Provider invalidates signed Bot', 'GET', openapi_path, 401,
            headers=principal)
        self.request(
            'disabled Provider rejects callbacks', 'POST', '/bot/events', 401,
            body=event, token=a['token'], headers=callback_headers)
        self.request('enable Provider', 'POST', path + '/enable', token=admin)
        self.self_view(a, parent)
        enabled_view_a = self.views[-1]['status']
        self.self_view(b, parent)
        enabled_view_b = self.views[-1]['status']
        self.request('enabled Provider restores signed Bot', 'GET', openapi_path,
                     headers=principal)

        self.request(
            'delete reviewer', 'DELETE',
            path + '/bots/' + urllib.parse.quote(a['ref'], safe=''), token=admin)
        self.self_view(a, parent, status=401)
        reviewer_view_status = self.views[-1]['status']
        self.self_view(b, parent)
        sibling_view_status = self.views[-1]['status']
        self.request(
            'deleted Bot invalidates signed principal', 'GET', openapi_path, 401,
            headers=principal)
        self.request(
            'deleted slug cannot be reclaimed', 'POST', path + '/bots', 409,
            body=registration, token=admin)
        self.request(
            'scoped path cannot reclaim deleted slug', 'POST',
            '/register?' + urllib.parse.urlencode(
                {**query, 'provider_bot_ref': parent + ':reviewer'}), 409)
        self.request(
            'delete coder', 'DELETE',
            path + '/bots/' + urllib.parse.quote(b['ref'], safe=''), token=admin)
        self.self_view(b, parent, status=401)
        coder_view_status = self.views[-1]['status']
        self.request('retire test Provider', 'POST', path + '/disable', token=admin)

        successes = [row for row in self.views if row['status'] == 200]
        rejections = [row for row in self.views if row['status'] == 401]
        return {
            'schema_version': 1,
            'parent': parent,
            'provider': {
                'registration_status': self._status('create subbots Provider'),
                'provider_id_present': bool(provider_id),
                'provider_slug': 'e2e-subbots-' + suffix,
                'admin_credential_present': bool(admin),
                'discovery_status': self._status('discover subbots mode'),
                'auth_mode': public.get('auth_mode'),
            },
            'admin_ref_validation': {
                'malformed_count': 6,
                'statuses': self._statuses(
                    'reject invalid complete ref before registration'),
            },
            'admin_registration': {
                'status': self._status('register reviewer with Provider credential'),
                'agent_code': first.get('agent_code'),
                'bot_slug': first.get('bot_slug'),
                'auth_mode': first.get('auth_mode'),
                'provider_bot_ref': first.get('provider_bot_ref'),
                'bot_id': a['id'],
                'token_present': bool(a['token']),
            },
            'scoped_ref_validation': {
                'removed_slug_status': self._status(
                    'removed bot_slug cannot complete a malformed ref'),
                'malformed_statuses': self._statuses(
                    'scoped registration rejects malformed complete ref'),
            },
            'scoped_registration': {
                'status': self._status('register coder with scoped credential'),
                'agent_code': identity.get('agent_code'),
                'bot_slug': identity.get('bot_slug'),
                'auth_mode': identity.get('auth_mode'),
                'provider_bot_ref': identity.get('provider_bot_ref'),
                'bot_id': b['id'],
                'token_present': bool(b['token']),
                'distinct_from_admin_uuid': a['id'] != b['id'],
                'distinct_from_admin_token': a['token'] != b['token'],
            },
            'duplicates': {
                'admin_status': self._status('reject duplicate reviewer'),
                'scoped_status': self._status('scoped duplicate fails at registration'),
            },
            'self_lookup': {
                'successes': successes,
                'rejections': rejections,
                'matching_actor_successes': sum(
                    row['actor_kind'] == 'matching' for row in successes),
                'actor_only_status': self._status('Actor alone cannot fall back to human'),
                'malformed_actor_status': self._status('malformed Actor is rejected'),
            },
            'legacy_api': {
                'matching_statuses': self._statuses('legacy auth accepts matching Actor'),
                'sibling_statuses': self._statuses(
                    'legacy auth rejects sibling Actor despite mock user'),
            },
            'metadata_update': {
                'status': self._status('update reviewer metadata'),
                'requested_bot_id': a['id'],
                'returned_bot_id': changed.get('bot_uuid'),
                'name': changed.get('name'),
                'authorization_scopes': changed.get('scopes'),
                'readback_parent_matches': metadata_view.get('parent_matches'),
                'readback_ref_matches': metadata_view.get('ref_matches'),
            },
            'onboarding': {
                'status': self._status('onboarding metadata preserves parent identity'),
                'readback_status': onboarding_view.get('status'),
                'readback_parent_matches': onboarding_view.get('parent_matches'),
                'readback_ref_matches': onboarding_view.get('ref_matches'),
            },
            'parent_discovery': {
                'unauthenticated_status': self._status(
                    'parent discovery requires credentials'),
                'invalid_query_statuses': self._statuses(
                    'parent discovery rejects invalid query'),
                'unavailable_status': self._status(
                    'public build fails closed without AgentPass verifier'),
                'error_code': unavailable.get('data', {}).get('error_code'),
            },
            'callback': {
                'authenticated_status': self._status(
                    'authenticated callback reaches run lookup'),
                'authenticated_error': result.get('error'),
                'sibling_status': self._status('callback rejects sibling Actor'),
                'token_only_status': self._status(
                    'callback accepts Bot token without Actor'),
            },
            'signed_principal': {
                'accepted_status': self._status('Gateway principal uses signed Bot'),
                'conflicting_actor_sent': principal['X-BCN-Actor-Bot-Id'] == b['id'],
                'disabled_status': self._status(
                    'disabled Provider invalidates signed Bot'),
                'enabled_status': self._status('enabled Provider restores signed Bot'),
                'deleted_status': self._status('deleted Bot invalidates signed principal'),
            },
            'disable_enable': {
                'disable_status': self._status('disable Provider'),
                'disabled_view_statuses': [disabled_view_a, disabled_view_b],
                'disabled_callback_status': self._status(
                    'disabled Provider rejects callbacks'),
                'disabled_signed_status': self._status(
                    'disabled Provider invalidates signed Bot'),
                'enable_status': self._status('enable Provider'),
                'enabled_view_statuses': [enabled_view_a, enabled_view_b],
                'enabled_signed_status': self._status(
                    'enabled Provider restores signed Bot'),
            },
            'deletion': {
                'delete_reviewer_status': self._status('delete reviewer'),
                'reviewer_view_status': reviewer_view_status,
                'sibling_view_status': sibling_view_status,
                'deleted_signed_status': self._status(
                    'deleted Bot invalidates signed principal'),
                'admin_reclaim_status': self._status('deleted slug cannot be reclaimed'),
                'scoped_reclaim_status': self._status(
                    'scoped path cannot reclaim deleted slug'),
                'delete_coder_status': self._status('delete coder'),
                'coder_view_status': coder_view_status,
                'retire_status': self._status('retire test Provider'),
            },
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--human', required=True)
    parser.add_argument('--evidence-file', required=True)
    args = parser.parse_args(argv)
    story = Story(args.base_url, args.human)
    evidence = story.run()
    if story.checks != EXPECTED_CHECKS:
        raise StoryFailure('assertion count mismatch: expected ' + str(EXPECTED_CHECKS)
                           + ', observed ' + str(story.checks))
    payload = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    if any(canary.encode('utf-8') in payload for canary in REDACTION_CANARIES):
        raise StoryFailure('evidence redaction gate rejected credential-shaped content')
    evidence_path = Path(args.evidence_file)
    if evidence_path.exists() or evidence_path.is_symlink():
        raise StoryFailure('evidence file must be fresh')
    fd = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print(MARKER_PREFIX + str(story.checks) + MARKER_SUFFIX, flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except StoryFailure as error:
        print('Provider subbots story failed: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
