"""Complete original Session parent mapping; partial execution never drops steps."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_user_runs_and_shares_sessions'
REQUIREMENTS = tuple('SESSION.'+name for name in (
    'public-jwt-ws', 'cli-create-list-get', 'group-invite', 'cli-session-invite',
    'bot-groups', 'invite-join', 'lifecycle', 'complete-invoke', 'collection'))
FUNCTIONS = ('_story_connect_with_group_session_jwt', 'test_group_session_via_cli',
             'test_group_invite_link', 'test_session_invite_link', 'test_bot_groups_of_bot',
             'test_session_invite_join', 'test_session_lifecycle',
             '_story_complete_and_invoke_sessions', 'test_session_collection')
PUBLIC_CASE = 'LOCAL-SESSION-PUBLIC-01'
PUBLIC_SUITE = 'owned-local-session-public-v1'


def session_source_binding():
    try:
        value = read(ROOT/'session-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'sources', 'requirements'}
                or value['parent'] != PARENT or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'stories.sh', 'group.sh', 'common.sh', 'group_session_ws_probe.py'}
                or [row['id'] for row in value['requirements']] != list(REQUIREMENTS)
                or [row['function'] for row in value['requirements']] != list(FUNCTIONS)):
            raise ValueError
        texts = {}
        for name, digest in value['sources'].items():
            path = ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError
            texts[name] = raw.decode('utf-8')
        for row in value['requirements']:
            if (set(row) != {'id', 'function', 'source', 'expectation'}
                    or row['source'] not in texts or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 2048
                    or len(re.findall(r'^'+re.escape(row['function'])+r'\(\) \{$', texts[row['source']], re.M)) != 1):
                raise ValueError
        start = texts['stories.sh'].index(PARENT+'() {')
        end = texts['stories.sh'].index('\n}', start)
        functions = re.findall(r'^    ((?:test_|_story_)[A-Za-z0-9_]+)(?:\s|$)', texts['stories.sh'][start:end], re.M)
        if functions != list(FUNCTIONS):
            raise ValueError
        return value
    except (ValueError, OSError, TypeError, KeyError):
        raise RunStateError('Complete Session migration map or original source changed') from None
