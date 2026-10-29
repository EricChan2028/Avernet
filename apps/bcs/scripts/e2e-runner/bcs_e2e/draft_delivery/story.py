#!/usr/bin/env python3
"""Black-box Draft delivery journey against one owned BCS runtime.

Credentials stay in this process's memory. The child emits only a fixed marker
on stdout and writes a non-secret semantic observation file for the runner.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import urllib.parse
import uuid

EXPECTED_CHECKS = 57
MARKER_PREFIX = 'Draft delivery story: '
MARKER_SUFFIX = ' live HTTP assertions passed'
READ_TOKEN_CANARIES = ('bot_token', 'readToken', 'read_token', 'Bearer ')


class StoryFailure(AssertionError):
    pass


class Story:
    def __init__(self, base_url: str, human: str, fixture: Path):
        self.base = urllib.parse.urlsplit(base_url.rstrip('/'))
        self.human = human
        self.fixture = fixture
        self.checks = 0
        self.responses: list[dict] = []
        self.bot_token = ''
        self.read_token_header_used = False

    def check(self, condition: object, label: str) -> None:
        if not condition:
            raise StoryFailure(label)
        self.checks += 1

    def request(self, label: str, method: str, path: str, *, body=None,
                token: str = '', expected_status=200, register_query: str = ''):
        connection = (http.client.HTTPSConnection if self.base.scheme == 'https'
                      else http.client.HTTPConnection)(self.base.netloc, timeout=30)
        payload = None if body is None else json.dumps(body).encode('utf-8')
        try:
            connection.putrequest(method, self.base.path + path)
            connection.putheader('X-Mock-User-Id', self.human)
            connection.putheader('X-Mock-Nick-Name', 'Draft delivery operator')
            if token:
                connection.putheader('Authorization', 'Bearer ' + token)
            if payload is not None:
                connection.putheader('Content-Type', 'application/json')
                connection.putheader('Content-Length', str(len(payload)))
            connection.endheaders(payload)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
            cache_control = response.getheader('Cache-Control')
        except (OSError, http.client.HTTPException) as error:
            # Never include the request path: registration carries a one-use token.
            raise StoryFailure(label + ': HTTP transport failed: ' + type(error).__name__) from None
        finally:
            connection.close()
        if path.startswith('/collaboration/drafts/'):
            self.responses.append({'method': method, 'path': path, 'status': status,
                                   'cache_control': cache_control})
        acceptable = expected_status if isinstance(expected_status, (set, tuple, frozenset)) else {expected_status}
        self.check(status in acceptable, label + ': unexpected HTTP status ' + str(status))
        try:
            value = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = {}
        return status, value, cache_control

    @staticmethod
    def credentials(token: str) -> str:
        return token

    @staticmethod
    def error_code(value: dict) -> str:
        return value.get('error') if isinstance(value, dict) else ''

    def authenticate(self) -> None:
        self.request('ensure Human', 'POST', '/me/ensure-human', body={})
        _, issued, _ = self.request('issue registration token', 'GET', '/register/token')
        registration_token = issued.get('token')
        self.check(isinstance(registration_token, str) and bool(registration_token),
                   'registration token is missing')
        name = 'Draft delivery author ' + uuid.uuid4().hex[:10]
        query = urllib.parse.urlencode({'token': registration_token, 'bot-name': name})
        _, bot, _ = self.request('register author', 'POST', '/register?' + query)
        self.bot_token = bot.get('bot_token', '')
        self.check(isinstance(bot.get('bot_uuid'), str) and bool(self.bot_token),
                   'registered author identity is incomplete')
        self.request('onboard author', 'POST', '/bots/onboard', token=self.bot_token,
                     body={'name': name, 'summary': 'Draft delivery author'})

    def run(self) -> dict:
        fixture_json = json.loads(self.fixture.read_text(encoding='utf-8'))
        source = fixture_json['revision']['definition_yaml']
        original_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
        updated = source + '\n# second revision\n'
        updated_hash = hashlib.sha256(updated.encode('utf-8')).hexdigest()
        draft_id = str(uuid.uuid4())
        draft_path = '/collaboration/drafts/' + draft_id
        title = 'Draft delivery regression'

        self.authenticate()
        _, created, _ = self.request('first create', 'PUT', draft_path, token=self.bot_token,
                                     expected_status=201,
                                     body={'definition_yaml': source, 'title': title})
        self.check(created.get('revision') == 1 and created.get('size_bytes') == 2174
                   and created.get('content_hash') == original_hash,
                   'first create did not bind revision 1 and incident bytes')
        self.check('readToken' not in json.dumps(created, sort_keys=True),
                   'create response exposed readToken')
        _, replay, _ = self.request('create replay', 'PUT', draft_path, token=self.bot_token,
                                    body={'definition_yaml': source, 'title': title})
        self.check(replay.get('revision') == 1 and replay.get('content_hash') == original_hash,
                   'idempotent create replay changed revision 1')
        _, conflict, _ = self.request('different create for selected ID', 'PUT', draft_path,
                                      token=self.bot_token, expected_status=409,
                                      body={'definition_yaml': 'different', 'title': title})
        self.check(self.error_code(conflict) == 'DRAFT_CONFLICT',
                   'different content did not report DRAFT_CONFLICT')
        _, metadata, _ = self.request('author reads metadata', 'GET', draft_path, token=self.bot_token)
        self.check(metadata.get('head_revision') == 1, 'metadata did not point to revision 1')
        _, public_read, public_cache = self.request(
            'public reads revision 1', 'GET', draft_path + '/revisions/1')
        self.check(public_read.get('content_hash') == original_hash
                   and public_read.get('size_bytes') == 2174,
                   'public read did not preserve the incident bytes')
        self.check(public_cache is not None and 'no-store' in public_cache.lower(),
                   'public read was cacheable')
        _, validated, _ = self.request(
            'public validates revision 1', 'POST', draft_path + '/revisions/1/validate')
        self.check(validated.get('revision') == 1
                   and validated.get('validation', {}).get('valid') is True,
                   'validation did not bind revision 1')
        public_delete_status = self.request('public cannot delete', 'DELETE', draft_path,
                                            expected_status=401)[0]
        token_delete_status = self.request('readToken cannot delete', 'DELETE', draft_path,
                                           token='A' * 43, expected_status={401, 403})[0]
        self.read_token_header_used = True
        token_update_status = self.request(
            'readToken cannot update', 'POST', draft_path + '/revisions',
            token='A' * 43, expected_status={401, 403},
            body={'expected_revision': 1, 'definition_yaml': 'unauthorized'})[0]
        _, metadata, _ = self.request('head survives management rejection', 'GET', draft_path,
                                      token=self.bot_token)
        self.check(metadata.get('head_revision') == 1 and metadata.get('deleted') is False,
                   'rejected management operation changed the Draft')
        _, appended, _ = self.request(
            'author appends revision 2', 'POST', draft_path + '/revisions',
            token=self.bot_token,
            body={'expected_revision': 1, 'definition_yaml': updated, 'title': title})
        self.check(appended.get('revision') == 2 and appended.get('content_hash') == updated_hash,
                   'append did not produce exactly revision 2')
        _, metadata, _ = self.request('head after append', 'GET', draft_path, token=self.bot_token)
        self.check(metadata.get('head_revision') == 2, 'append did not advance head to 2')
        _, original, _ = self.request('historical revision remains readable', 'GET',
                                      draft_path + '/revisions/1')
        self.check(original.get('revision') == 1 and original.get('size_bytes') == 2174
                   and original.get('content_hash') == original_hash,
                   'append mutated or renumbered revision 1')
        _, latest, _ = self.request('revision 2 readable', 'GET', draft_path + '/revisions/2')
        self.check(latest.get('revision') == 2 and latest.get('content_hash') == updated_hash,
                   'revision 2 does not bind the appended content')
        _, stale, _ = self.request(
            'stale writer is rejected', 'POST', draft_path + '/revisions', token=self.bot_token,
            expected_status=409,
            body={'expected_revision': 1, 'definition_yaml': 'different'})
        self.check(self.error_code(stale) == 'DRAFT_CONFLICT',
                   'stale writer did not report DRAFT_CONFLICT')
        _, metadata, _ = self.request('head unchanged after stale', 'GET', draft_path,
                                      token=self.bot_token)
        self.check(metadata.get('head_revision') == 2, 'stale writer advanced the head')
        _, latest, _ = self.request('latest unchanged after stale', 'GET',
                                    draft_path + '/revisions/2')
        self.check(latest.get('content_hash') == updated_hash,
                   'stale writer replaced revision 2')
        _, card_one, _ = self.request('historical card revision 1', 'GET',
                                      draft_path + '/revisions/1/reference', token=self.bot_token)
        self.check(card_one.get('draftId') == draft_id and card_one.get('revision') == 1
                   and isinstance(card_one.get('readToken'), str)
                   and len(card_one.get('readToken')) == 43,
                   'historical card is not pinned to revision 1')
        _, card_two, _ = self.request('card revision 2', 'GET',
                                      draft_path + '/revisions/2/reference', token=self.bot_token)
        self.check(card_two.get('revision') == 2 and card_two.get('draftId') == draft_id,
                   'revision 2 card is not pinned to revision 2')
        _, shared, _ = self.request('ID and revision read without token', 'GET',
                                    draft_path + '/revisions/1')
        self.check(shared.get('revision') == 1 and shared.get('content_hash') == original_hash,
                   'tokenless fixed read did not resolve the historical card')
        self.request('revoke revision 2', 'DELETE', draft_path + '/revisions/2/share',
                     token=self.bot_token, expected_status=204)
        self.request('repeat revoke revision 2', 'DELETE', draft_path + '/revisions/2/share',
                     token=self.bot_token, expected_status=204)
        _, revoked, _ = self.request('revoked card unavailable', 'GET',
                                     draft_path + '/revisions/2', expected_status=404)
        self.check(self.error_code(revoked) == 'DRAFT_REFERENCE_UNAVAILABLE',
                   'revoked card leaked a different error')
        _, sibling, _ = self.request('historical sibling remains readable', 'GET',
                                     draft_path + '/revisions/1')
        self.check(sibling.get('content_hash') == original_hash,
                   'revoking revision 2 changed revision 1')
        self.request('delete Draft', 'DELETE', draft_path, token=self.bot_token,
                     expected_status=204)
        _, deleted, _ = self.request('deleted Draft unavailable', 'GET',
                                     draft_path + '/revisions/1', expected_status=404)
        self.check(self.error_code(deleted) == 'DRAFT_REFERENCE_UNAVAILABLE',
                   'deleted Draft leaked a different error')
        self.request('late create cannot revive', 'PUT', draft_path, token=self.bot_token,
                     expected_status=404,
                     body={'definition_yaml': source, 'title': title})
        self.check(all(row.get('cache_control') == 'no-store' for row in self.responses),
                   'a Draft response omitted Cache-Control: no-store')
        self.check(all('token' not in json.dumps(row, sort_keys=True).lower()
                       for row in self.responses),
                   'response observation captured token material')
        evidence = {
            'schema_version': 1,
            'draft_id': draft_id,
            'fixture': {'content_hash': original_hash, 'updated_content_hash': updated_hash},
            'create': {
                'draft_id': draft_id,
                'content_hash': original_hash,
                'attempts': [
                    {'status': 201, 'draft_id': draft_id, 'revision': 1,
                     'size_bytes': 2174, 'content_hash': original_hash,
                     'valid': validated.get('validation', {}).get('valid') is True},
                    {'status': 200, 'draft_id': draft_id, 'revision': 1,
                     'size_bytes': 2174, 'content_hash': original_hash,
                     'valid': validated.get('validation', {}).get('valid') is True},
                ],
                'different_content': {'status': 409, 'error': self.error_code(conflict)},
            },
            'append': {
                'head_before': 1,
                'expected_revision': 1,
                'status': 200,
                'revision': 2,
                'head_after': 2,
                'new_content_hash': updated_hash,
                'original_after': {'status': 200, 'revision': original.get('revision'),
                                   'size_bytes': original.get('size_bytes'),
                                   'content_hash': original.get('content_hash')},
                'updated_after': {'status': 200, 'revision': latest.get('revision'),
                                  'content_hash': latest.get('content_hash')},
            },
            'stale': {
                'status': 409,
                'expected_revision': 1,
                'head_after': 2,
                'new_content_hash': updated_hash,
                'original_content_hash': original_hash,
                'latest_after': {'status': 200, 'revision': latest.get('revision'),
                                 'content_hash': latest.get('content_hash')},
            },
            'cards': {
                'head_revision': 2,
                'historical': {'draft_id': card_one.get('draftId'),
                               'revision': card_one.get('revision'),
                               'content_hash': original_hash,
                               'compat_field_present': isinstance(card_one.get('readToken'), str),
                               'compat_revision': card_one.get('revision'),
                               'read_with_token_as_header': False,
                               'read_without_token': {'status': 200,
                                                      'revision': shared.get('revision'),
                                                      'size_bytes': shared.get('size_bytes'),
                                                      'content_hash': shared.get('content_hash')}},
                'other_revision': {'revision': card_two.get('revision'),
                                   'compat_revision': card_two.get('revision')},
            },
            'availability': {
                'revoked': {'status': 404, 'error': self.error_code(revoked)},
                'historical_sibling': {'status': 200,
                                       'content_hash': sibling.get('content_hash')},
                'deleted': {'status': 404, 'error': self.error_code(deleted)},
                'late_create': {'method': 'PUT', 'status': 404},
            },
            'management': {
                'public_delete': {'status': public_delete_status},
                'token_delete': {'status': token_delete_status},
                'token_update': {'status': token_update_status},
                'head_after': 2,
                'deleted_after': False,
            },
            'responses': self.responses,
        }
        return evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--human', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--evidence-file', required=True)
    args = parser.parse_args(argv)
    story = Story(args.base_url, args.human, Path(args.fixture))
    evidence = story.run()
    if story.checks != EXPECTED_CHECKS:
        raise StoryFailure('assertion count mismatch: expected ' + str(EXPECTED_CHECKS)
                           + ', observed ' + str(story.checks))
    payload = json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
    if any(canary.encode('utf-8') in payload for canary in READ_TOKEN_CANARIES):
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
        print('Draft delivery story failed: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
