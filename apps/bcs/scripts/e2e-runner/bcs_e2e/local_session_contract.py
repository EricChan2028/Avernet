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

# Parsed assertion points from the original shell story (87 total). These are
# field-level obligations: each is evaluated against one observed response,
# operation receipt, or cleanup proof, never a declaration-only count.
PUBLIC_ASSERTION_IDS = tuple('SESSION.public-jwt-ws.' + name for name in (
    'missing-principal-rejected', 'missing-principal-envelope',
    'unknown-field-rejected', 'unknown-field-envelope',
    'bots-mine', 'bots-query', 'bot-detail', 'bot-detail-product-manager',
    'group-create', 'group-create-id', 'group-list', 'group-detail',
    'group-detail-id', 'group-patch', 'group-patch-name',
    'session-create', 'session-create-id', 'session-list',
    'session-detail', 'session-detail-id', 'session-patch',
    'session-patch-title', 'session-messages', 'session-token',
    'session-token-no-store', 'session-token-present',
    'session-websocket-101', 'session-delete', 'session-delete-ack',
    'group-delete', 'group-delete-ack'))
SHARING_ASSERTION_IDS = (
    'SESSION.cli-create-list-get', 'SESSION.group-invite',
    'SESSION.cli-session-invite', 'SESSION.bot-groups',
    'SESSION.bot-groups-contains', 'SESSION.invite-join')
COMPLETION_ASSERTION_IDS = tuple(
    ['SESSION.lifecycle.' + name for name in (
        'session-create', 'session-patch', 'member-add', 'member-mute',
        'chat', 'messages', 'member-remove', 'session-delete',
        'session-delete-verified')]
    + ['SESSION.complete-invoke.' + name for name in (
        'service-group-create', 'service-group-id', 'signoff-session-create',
        'unmanaged-message-inspect', 'unmanaged-message-no-deliveries',
        'delivery-batch-query', 'delivery-batch-no-rows',
        'cancel-missing-delivery', 'cancel-missing-message',
        'manual-resolve-forbidden', 'chat-complete',
        'chat-status-completed', 'chat-output-stored', 'chat-readback',
        'chat-readback-status', 'chat-readback-output', 'service-start',
        'service-session-id', 'service-kind', 'service-caller-read',
        'service-belongs-group', 'service-title', 'service-foreign-forbidden',
        'service-isolation', 'service-wrong-complete',
        'service-completion-guard', 'service-guard-endpoint',
        'chat-cleanup', 'service-cleanup', 'service-group-cleanup')]
    + ['SESSION.collection.' + name for name in (
        'session-create', 'initial-empty', 'missing-participant-rejected',
        'collect', 'collect-idempotent', 'pm-contains', 'eng-isolation',
        'qa-nonparticipant', 'uncollect', 'uncollect-idempotent',
        'final-empty')])
SESSION_ASSERTION_IDS = (PUBLIC_ASSERTION_IDS + SHARING_ASSERTION_IDS
                         + COMPLETION_ASSERTION_IDS)


def session_assertion_ids() -> tuple[str, ...]:
    if (len(PUBLIC_ASSERTION_IDS), len(SHARING_ASSERTION_IDS),
            len(COMPLETION_ASSERTION_IDS), len(SESSION_ASSERTION_IDS)) != (31, 6, 50, 87):
        raise RunStateError('Session parsed assertion inventory changed')
    if len(set(SESSION_ASSERTION_IDS)) != 87:
        raise RunStateError('Session parsed assertion IDs are not unique')
    return SESSION_ASSERTION_IDS


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
