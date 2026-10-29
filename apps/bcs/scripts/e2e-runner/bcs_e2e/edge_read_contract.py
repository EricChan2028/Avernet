"""Source maps for read-only Edge parents and slices.

These contracts identify environment-neutral migration candidates. They do not
execute an HTTP request, resolve Bot credentials, admit a route, or make an
original warning-only branch count as a passing assertion.  The friend
request-and-list parent contains a write, so its repeated list GETs are frozen
as an independent read slice without granting parent completion.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
ADMISSION_PARENT = 'test_ep_admission'
FRIEND_REQUESTS_PARENT = 'test_ep_friend_connections_friend_requests_list'
FRIEND_CONNECTIONS_PARENT = 'test_ep_friend_connections_friend_request_and_list'
ADMISSION_REQUIREMENTS = tuple('EDGE-ADMISSION.' + name for name in ('human-actor', 'actor-kind'))
FRIEND_REQUESTS_REQUIREMENTS = tuple('EDGE-FRIEND-REQUESTS.' + name for name in ('received', 'sent', 'all'))
FRIEND_CONNECTIONS_REQUIREMENTS = tuple('EDGE-FRIEND-CONNECTIONS.' + name for name in ('list', 'list-repeat'))
_SOURCE_REVISION = '71860eae99c62d7bf1349a0f46562f6395acbf1e'


def _body(text: str, parent: str) -> str:
    if len(re.findall(r'^' + re.escape(parent) + r'\(\) \{$', text, re.M)) != 1:
        raise ValueError('parent function is missing or duplicated')
    start = text.index(parent + '() {')
    return text[start:text.index('\n}', start) + 2]


def _calls(body: str) -> list[tuple[str, str, str]]:
    pattern = re.compile(
        r'^    _api_authed "GET" "([^"]+)" "" "([^"]*)"', re.M)
    rows = []
    for path, token in pattern.findall(body):
        rows.append(('GET', path, token))
    return rows


def _binding(filename: str, parent: str, requirements: tuple[str, ...]) -> dict:
    try:
        value = read(ROOT / filename)
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'sources', 'requirements'}
                or value['schema_version'] != 1
                or value['parent'] != parent
                or value['source_revision'] != _SOURCE_REVISION
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'edge_permission.sh'}
                or [row['id'] for row in value['requirements']] != list(requirements)):
            raise ValueError
        path = ROOT.parent / 'e2e-test' / 'edge_permission.sh'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['edge_permission.sh']:
            raise ValueError
        text = raw.decode('utf-8')
        body = _body(text, parent)
        actual = _calls(body)
        expected = [(row['method'], row['path'], ('' if row['actor'] == 'UNAUTHENTICATED' else '$ceo_token'))
                    for row in value['requirements']]
        if parent == ADMISSION_PARENT:
            if 'get_bot_token' in body:
                raise ValueError('admission source unexpectedly resolved credentials')
        elif parent == FRIEND_REQUESTS_PARENT:
            if len(re.findall(r'get_bot_token CEO ', body)) != 1:
                raise ValueError('friend-request list source lacks the exact CEO credential boundary')
        elif parent == FRIEND_CONNECTIONS_PARENT:
            if len(re.findall(r'get_bot_token CEO ', body)) != 1:
                raise ValueError('friend-connection list source lacks the exact CEO credential boundary')
            if len(re.findall(r'get_bot_token ENG ', body)) != 1:
                raise ValueError('friend-connection list slice did not preserve the ENG credential boundary')
        else:
            raise ValueError('unsupported Edge read source parent')
        if actual != expected:
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id', 'kind', 'method', 'path', 'actor', 'expectation'}
                    or row['kind'] != 'http' or row['method'] != 'GET'
                    or row['actor'] not in {'UNAUTHENTICATED', 'CEO_BOT'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 2048):
                raise ValueError
        return value
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise RunStateError(f'{parent} original read-only source map changed') from None


def admission_source_binding() -> dict:
    return _binding(
        'edge-admission-migration-contract.json',
        ADMISSION_PARENT,
        ADMISSION_REQUIREMENTS,
    )


def friend_requests_list_source_binding() -> dict:
    return _binding(
        'edge-friend-requests-list-migration-contract.json',
        FRIEND_REQUESTS_PARENT,
        FRIEND_REQUESTS_REQUIREMENTS,
    )


def friend_connections_list_source_binding() -> dict:
    return _binding(
        'edge-friend-connections-list-migration-contract.json',
        FRIEND_CONNECTIONS_PARENT,
        FRIEND_CONNECTIONS_REQUIREMENTS,
    )


def edge_read_adaptability() -> dict:
    """Expose the next read candidates; this is not execution proof."""
    return {
        'cases': [
            {
                'legacy_id': ADMISSION_PARENT,
                'effect_scope': 'READ_ONLY',
                'requirements': list(ADMISSION_REQUIREMENTS),
                'environments': ['owned-local', 'singlebox', 'preprod'],
                'status': 'PENDING',
                'blocker': 'Admit exact admission route, dedicated Bot identity, and explicit 200 contract in each environment.',
                'grants_release_credit': False,
            },
            {
                'legacy_id': FRIEND_REQUESTS_PARENT,
                'effect_scope': 'READ_ONLY',
                'requirements': list(FRIEND_REQUESTS_REQUIREMENTS),
                'environments': ['owned-local', 'singlebox', 'preprod'],
                'status': 'PENDING',
                'blocker': 'Admit authenticated CEO Bot identity, exact request-list routes, fixture isolation, and bounded response semantics.',
                'grants_release_credit': False,
            },
            {
                'legacy_id': FRIEND_CONNECTIONS_PARENT,
                'effect_scope': 'READ_ONLY_SLICE_PARENT_CONTAINS_WRITES',
                'requirements': list(FRIEND_CONNECTIONS_REQUIREMENTS),
                'environments': ['owned-local', 'singlebox', 'preprod'],
                'status': 'PENDING',
                'blocker': "Admit the repeated list reads and native/public route mapping; the parent's friend-request POST remains a separate write obligation.",
                'grants_release_credit': False,
            },
        ],
        'ready_to_execute': False,
    }
