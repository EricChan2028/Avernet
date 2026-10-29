"""Complete source map for the original Bot-search Edge case.

The case is not wholly read-only: it contains one embedded service-key fixture
mutation. That mutation stays represented instead of being silently dropped or
misreported as harmless. Source maps do not execute, admit credentials, or
grant preproduction authority.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'test_ep_bot_search'
REQUIREMENTS = tuple('EDGE-BOT-SEARCH.' + name for name in (
    'name-fuzzy', 'public-online-not-friend', 'friend-true', 'hidden-status',
    'private-visibility', 'empty-query', 'ensure-tc-fixture', 'tc-true',
    'tc-false', 'anonymous', 'bad-limit', 'bad-visibility', 'bad-status'))
_SOURCE_REVISION = '8559a7a5258a2cb6750befec187e50aa85060cc6'


def bot_search_source_binding() -> dict:
    try:
        value = read(ROOT / 'edge-bot-search-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'sources', 'requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['source_revision'] != _SOURCE_REVISION
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'edge_permission.sh'}
                or [row['id'] for row in value['requirements']] != list(REQUIREMENTS)):
            raise ValueError
        path = ROOT.parent / 'e2e-test' / 'edge_permission.sh'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['edge_permission.sh']:
            raise ValueError
        text = raw.decode('utf-8')
        if len(re.findall(r'^' + re.escape(PARENT) + r'\(\) \{$', text, re.M)) != 1:
            raise ValueError
        start = text.index(PARENT + '() {')
        body = text[start:text.index('\n}', start) + 2]
        read_calls = re.findall(
            r'^    _api_authed "GET" "([^"]+)" "" "([^"]*)"', body, re.M)
        if len(read_calls) != 12:
            raise ValueError
        fixture_literals = (
            'local tc_uuid="tc_cov_bot:85020"',
            '-H "X-BCS-Service-Key: e2e-test-key"',
            '''-X POST -d '{"name":"TCCov","staff_no":"85020"}' ''',
            '/admin/bots/$tc_uuid/ensure',
        )
        if any(body.count(literal) != 1 for literal in fixture_literals):
            raise ValueError('Bot-search fixture setup changed')

        actual = []
        for row in value['requirements']:
            actor = row['actor']
            token = '' if actor == 'UNAUTHENTICATED' else (
                '$ceo_token' if actor == 'CEO_BOT' else 'e2e-test-key')
            actual.append((row['method'], row['path'], token))
        expected = [('GET', path_param, token) for path_param, token in read_calls]
        expected.insert(6, ('POST', '/admin/bots/$tc_uuid/ensure', 'e2e-test-key'))
        if actual != expected:
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id', 'kind', 'effect', 'method', 'path', 'actor', 'expectation'}
                    or row['kind'] not in {'http', 'fixture'}
                    or row['effect'] not in {'READ', 'MUTATION'}
                    or row['effect'] != ('MUTATION' if row['method'] == 'POST' else 'READ')
                    or row['actor'] not in {'CEO_BOT', 'SERVICE_KEY', 'UNAUTHENTICATED'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 2048):
                raise ValueError
        if sum(row['effect'] == 'MUTATION' for row in value['requirements']) != 1:
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise RunStateError('Bot-search original source or complete13-operation map changed') from None
