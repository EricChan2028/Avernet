"""Complete original Provider/Organization journey; no remote or execution credit."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = 'LOCAL-PROVIDER-01'
SUITE = 'owned-local-provider-v1'
PARENT = 'story_provider_operator_publishes_agent'
HELPER = '_story_provider_manages_organization'
REQUIREMENTS = tuple('PROVIDER.'+name for name in (
    'register', 'duplicate-slug-conflict', 'read', 'update', 'bot-publish', 'bot-update',
    'attributes-read-denied', 'attributes-write-denied', 'org-create', 'org-list',
    'org-read', 'org-update', 'candidate-list-before', 'candidate-before',
    'member-add', 'candidate-list-after', 'candidate-after', 'member-profile',
    'member-read', 'member-list', 'admin-start', 'admin-read', 'member-remove',
    'bot-list', 'roster-denied', 'agentpass-negative', 'unknown-run-callback',
    'takeover-denied', 'disable', 'enable', 'bot-retire', 'bot-list-empty'))


def provider_source_binding():
    """Verify both complete functions and nested call order, never update hashes."""
    try:
        value = read(ROOT/'provider-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'sources', 'requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'stories.sh'}
                or [r['id'] for r in value['requirements']] != list(REQUIREMENTS)):
            raise ValueError
        path = ROOT.parent/'e2e-test/stories.sh'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['stories.sh']:
            raise ValueError
        text = raw.decode('utf-8')
        def body(function):
            if len(re.findall(r'^'+function+r'\(\) \{$', text, re.M)) != 1:
                raise ValueError
            start = text.index(function+'() {')
            return text[start:text.index('\n}', start)+2]
        main, nested = body(PARENT), body(HELPER)
        if main.count('    '+HELPER+' ') != 1:
            raise ValueError
        pattern = r'^    (api_post|api_request_headers)\s+(?:([A-Z]+)\s+)?"([^"]+)"'
        before, after = main.split('    '+HELPER+' ', 1)
        actual = []
        for function, part in ((PARENT, before), (HELPER, nested), (PARENT, after)):
            actual.extend((function, method or 'POST', route) for _, method, route in re.findall(pattern, part, re.M))
        if actual != [(r['function'], r['method'], r['path']) for r in value['requirements']]:
            raise ValueError
        for item in value['requirements']:
            if (set(item) != {'id', 'function', 'source', 'method', 'path', 'expectation'}
                    or item['source'] != 'stories.sh' or not isinstance(item['expectation'], str)
                    or not 1 <= len(item['expectation']) <= 2048):
                raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('Provider original source or complete32-operation map changed') from None
