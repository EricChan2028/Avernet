"""Source binding for the upstream Bot-authority user journey Story.

The upstream shell story drives the owned-registration surface and the
unified Bot-authority surface end to end: it registers the owner human's
fixture Bot and the transfer target human's own Bot (which seeds the
target's ``human_`` actor row), reads ownership, exercises the manager
single-item surface with the ``owner_not_manager`` polarity, proves the
mine-ensured Human (created only by the public ``bots/mine`` read) is a
valid strict-edit target while a never-seen user stays unknown, walks the
ownership-transfer chain (initiate, refresh, target read, accept), and
reads both transfer inboxes. This module freezes that map: the shell
source, its git tip revision, per-obligation anchors and the replayed
call matrix. Drift in any of them fails closed before any executor,
binding or runtime exists; migration review is the only path forward.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from .inventory import reviewed_release_inventory
from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_authority_user_journey'
CASE_ID = 'LOCAL-AUTHORITY-JOURNEY-01'
SUITE = 'owned-local-authority-v1'
STATUS = 'LOCAL_AUTHORITY_PASS'
JOURNAL = 'local-authority.json'
BINDING = 'authority-binding.json'
FLOW = 'authority-flow-report.json'
COMPLETION = 'owned-local-authority-completion-v1'
INTERNAL_PREFIX = '/api/v1/collaboration'
PUBLIC_PREFIX = '/openapi/v1/collaboration'
STAFF_B = '002'
STAFF_MINE = '137'
SHELL_SOURCE = ROOT.parent / 'e2e-test' / 'authority.sh'
REPO_ROOT = ROOT.parents[3]
SHELL_RELATIVE = 'apps/bcs/scripts/e2e-test/authority.sh'
SOURCE_REVISION = 'c6f00120949078f14cb0d3602fb8301f2ccb1803'
EXPECTED_HTTP_CALLS = 24

REQUIREMENTS = tuple('AUTHORITY.' + name for name in (
    'trusted-registration', 'ownership-read', 'empty-manager-page',
    'owner-not-manager-polarity', 'manager-add-list-remove-idempotent',
    'manager-restore', 'mine-ensured-human-read', 'mine-ensured-manager-add',
    'never-seen-manager-unknown', 'mine-ensured-manager-remove',
    'transfer-initiate-refresh', 'transfer-read-accept',
    'transferred-ownership', 'transferred-manager-ledger',
    'transfer-inboxes', 'story-scoped-disposition'))

# Primary method/path/actor of every obligation, mirroring the first
# replayed call of each obligation in the frozen shell story. The
# disposition obligation owns no HTTP call; its lifecycle evidence is the
# owned runtime record itself.
PRIMARY = {
    'AUTHORITY.trusted-registration': ('GET', '/register/token', 'register'),
    'AUTHORITY.ownership-read': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 'owner'),
    'AUTHORITY.empty-manager-page': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 'owner'),
    'AUTHORITY.owner-not-manager-polarity': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{owner_user_id}', 'owner'),
    'AUTHORITY.manager-add-list-remove-idempotent': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 'owner'),
    'AUTHORITY.manager-restore': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 'owner'),
    'AUTHORITY.mine-ensured-human-read': ('GET', PUBLIC_PREFIX + '/bots/mine', 'mine'),
    'AUTHORITY.mine-ensured-manager-add': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 'owner'),
    'AUTHORITY.never-seen-manager-unknown': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{never_user_id}', 'owner'),
    'AUTHORITY.mine-ensured-manager-remove': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 'owner'),
    'AUTHORITY.transfer-initiate-refresh': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 'owner'),
    'AUTHORITY.transfer-read-accept': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 'target'),
    'AUTHORITY.transferred-ownership': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 'target'),
    'AUTHORITY.transferred-manager-ledger': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 'target'),
    'AUTHORITY.transfer-inboxes': ('GET', INTERNAL_PREFIX + '/ownership-transfers', 'owner'),
    'AUTHORITY.story-scoped-disposition': (None, None, 'runtime'),
}

# Ordered replayed call matrix per obligation: (method, path template,
# expected HTTP status, actor label, query). The registration calls carry
# no business principal (mock-header admission mints the one-time token
# and registers the Bot); every business call carries the Gateway
# Principal JWT in X-Avernet-Principal only.
CALLS = {
    'AUTHORITY.trusted-registration': (
        ('GET', '/register/token', 200, 'register_owner', None),
        ('POST', '/register', 200, 'register_owner', None),
        ('GET', '/register/token', 200, 'register_target', None),
        ('POST', '/register', 200, 'register_target', None),
    ),
    'AUTHORITY.ownership-read': (
        ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'owner', None),
    ),
    'AUTHORITY.empty-manager-page': (
        ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
    ),
    'AUTHORITY.owner-not-manager-polarity': (
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{owner_user_id}', 409, 'owner', None),
    ),
    'AUTHORITY.manager-add-list-remove-idempotent': (
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
        ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
        ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
        ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    ),
    'AUTHORITY.manager-restore': (
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    ),
    'AUTHORITY.mine-ensured-human-read': (
        ('GET', PUBLIC_PREFIX + '/bots/mine', 200, 'mine', {'limit': '20'}),
    ),
    'AUTHORITY.mine-ensured-manager-add': (
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None),
    ),
    'AUTHORITY.never-seen-manager-unknown': (
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{never_user_id}', 400, 'owner', None),
    ),
    'AUTHORITY.mine-ensured-manager-remove': (
        ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None),
    ),
    'AUTHORITY.transfer-initiate-refresh': (
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None),
        ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'owner', None),
    ),
    'AUTHORITY.transfer-read-accept': (
        ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'target', None),
        ('POST', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept', 200, 'target', None),
    ),
    'AUTHORITY.transferred-ownership': (
        ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'target', None),
    ),
    'AUTHORITY.transferred-manager-ledger': (
        ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'target', None),
    ),
    'AUTHORITY.transfer-inboxes': (
        ('GET', INTERNAL_PREFIX + '/ownership-transfers', 200, 'target', {'direction': 'received'}),
        ('GET', INTERNAL_PREFIX + '/ownership-transfers', 200, 'owner', {'direction': 'sent'}),
    ),
    'AUTHORITY.story-scoped-disposition': (),
}

# Behavior literals frozen from the upstream authority.sh at git tip
# SOURCE_REVISION. Every obligation keeps its anchors; story drift fails
# the binding instead of silently re-scoping an obligation.
ANCHORS = {
    'AUTHORITY.trusted-registration': (
        '_au_register_fixture_bot "$BCS_MOCK_USER_ID" "authority-story-$$-$(date +%s)"',
        '_au_register_fixture_bot "$_AU_STAFF_B" "authority-story-b-$$-$(date +%s)"',
        '_AU_STAFF_B="${BCS_E2E_AUTHORITY_STAFF_B:-002}"',
        'X-Mock-User-Id: $staff',
    ),
    'AUTHORITY.ownership-read': (
        '_au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_a"',
        'assert_json_eq "ownership keeps the bot id" "$RESPONSE" "data.bot_id" "$owner_bot"',
        'assert_json_eq "ownership identifies the registered owner"',
    ),
    'AUTHORITY.empty-manager-page': (
        'require_status "owner reads an empty manager page" "200"',
        'assert_json_eq "manager page starts empty" "$RESPONSE" "data.total" "0"',
    ),
    'AUTHORITY.owner-not-manager-polarity': (
        '_au_request PUT "${prefix}/bots/${owner_bot}/managers/${BCS_MOCK_USER_ID}" "$principal_a"',
        'require_status "adding the owner as a manager is rejected" "409"',
        'assert_json_eq "owner add uses the stable error code"',
    ),
    'AUTHORITY.manager-add-list-remove-idempotent': (
        'assert_json_eq "manager add reports one added" "$RESPONSE" "data.added" "1"',
        'assert_json_array_contains "manager page exposes the added manager"',
        'assert_json_eq "manager removal reports one removed" "$RESPONSE" "data.removed" "1"',
        'require_status "manager removal is idempotent for absent" "200"',
        'assert_json_eq "absent manager removal reports no change" "$RESPONSE" "data.removed" "0"',
    ),
    'AUTHORITY.manager-restore': (
        '# Re-add so the transfer has a live manager ledger to update.',
        'require_status "manager is restored before the transfer" "200"',
    ),
    'AUTHORITY.mine-ensured-human-read': (
        'public_prefix="/openapi/v1/collaboration"',
        'mine_only_staff="${BCS_E2E_AUTHORITY_STAFF_MINE:-137}"',
        'api_request_headers GET "${public_prefix}/bots/mine?limit=20" ""',
        'require_status "mine-ensured human reads its own mine" "200"',
    ),
    'AUTHORITY.mine-ensured-manager-add': (
        '_au_request PUT "${prefix}/bots/${owner_bot}/managers/${mine_only_staff}" "$principal_a"',
        'require_status "mine-ensured human is a valid manager add target" "200"',
        'assert_json_eq "mine-ensured manager add reports one added"',
    ),
    'AUTHORITY.never-seen-manager-unknown': (
        '_au_request PUT "${prefix}/bots/${owner_bot}/managers/never-exists-$$" "$principal_a"',
        'require_status "never-seen human is not a manager add target" "400"',
        'assert_json_eq "unknown manager add uses the stable error code"',
    ),
    'AUTHORITY.mine-ensured-manager-remove': (
        'require_status "mine-ensured manager is removed before the transfer" "200"',
        'assert_json_eq "mine-ensured manager removal reports one removed"',
    ),
    'AUTHORITY.transfer-initiate-refresh': (
        'local transfer_path="${prefix}/bots/${owner_bot}/ownership-transfers/human_${_AU_STAFF_B}"',
        'require_status "owner initiates the ownership transfer" "201"',
        'assert_json_eq "initiated transfer names the target"',
        'assert new - old < 300_000, "renewal stays within the re-stamp gap"',
    ),
    'AUTHORITY.transfer-read-accept': (
        '_au_request GET "$transfer_path" "$principal_b"',
        'require_status "target human reads the pending transfer" "200"',
        '_au_request POST "${transfer_path}/accept" "$principal_b"',
        'assert_json_eq "accept records the accepted status" "$RESPONSE" "data.status" "accepted"',
    ),
    'AUTHORITY.transferred-ownership': (
        '_au_request GET "${prefix}/bots/${owner_bot}/ownership" "$principal_b"',
        'require_status "the new owner reads the collaboration ownership view" "200"',
        'assert_json_eq "ownership transfer moved the owner"',
    ),
    'AUTHORITY.transferred-manager-ledger': (
        '_au_request GET "${prefix}/bots/${owner_bot}/managers" "$principal_b"',
        'require_status "the new owner reads the transferred manager ledger" "200"',
        'assert_json_eq "the transferred owner is in the ledger"',
        'assert_json_array_contains "the original owner is now a plain manager"',
    ),
    'AUTHORITY.transfer-inboxes': (
        '_au_request GET "${prefix}/ownership-transfers?direction=received" "$principal_b"',
        'require_status "the new owner lists received transfers" "200"',
        'assert_contains "received inbox contains the accepted bot" "$RESPONSE" "$owner_bot"',
        '_au_request GET "${prefix}/ownership-transfers?direction=sent" "$principal_a"',
    ),
    'AUTHORITY.story-scoped-disposition': (
        'story_authority_user_journey() {',
        'E2E_TESTS_STORIES+=("story_authority_user_journey")',
    ),
}

DISPOSITION_ID = REQUIREMENTS[-1]
DISPOSITION_STORY_LINES = (1, 2)
_ROW_KEYS = {'id', 'method', 'path', 'actor', 'kind', 'expectation',
             'story_lines', 'anchors', 'calls'}
_CALL_KEYS = {'method', 'path', 'expect_status', 'actor', 'query'}


def _shell_text() -> str:
    try:
        path = SHELL_SOURCE
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError
        return path.read_bytes().decode('utf-8')
    except (OSError, ValueError, UnicodeError):
        raise RunStateError('authority shell story is unreadable or unbounded') from None


def _git_tip_revision() -> str:
    try:
        result = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'log', '-1', '--format=%H', '--', SHELL_RELATIVE],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        raise RunStateError('authority git tip revision is unavailable; drift gate cannot run') from None
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise RunStateError('authority git tip revision is not a full commit digest')
    return value


def authority_source_binding() -> dict:
    """Freeze and validate the upstream story, its call matrix and the map."""
    try:
        value = read(ROOT / 'authority-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'source_file_revisions',
                           'parent', 'suite', 'case_id', 'environment', 'sources',
                           'requirements', 'remote_validation', 'grants_release_credit',
                           'release_parent_denominator', 'independent_slice',
                           'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or value['source_revision'] != SOURCE_REVISION
                or set(value['source_file_revisions']) != {'authority.sh'}
                or any(not re.fullmatch('[0-9a-f]{40}', digest)
                       for digest in value['source_file_revisions'].values())
                or value['source_file_revisions']['authority.sh'] != SOURCE_REVISION
                or value['environment'] != {'local': 'LOCAL_EXECUTOR_ONLY',
                                            'preprod': 'REMOTE_UNSUPPORTED'}
                or set(value['sources']) != {'authority.sh'}):
            raise ValueError
        text = _shell_text()
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != value['sources']['authority.sh']:
            raise RunStateError('authority upstream source drift requires review')
        if _git_tip_revision() != SOURCE_REVISION:
            raise RunStateError('authority upstream git tip drift requires review')
        if len(re.findall(r'^' + re.escape(PARENT) + r'\(\) \{$', text, re.M)) != 1:
            raise RunStateError('authority parent story function disappeared')
        lines = text.splitlines()
        if [row['id'] for row in value['requirements']] != list(REQUIREMENTS):
            raise ValueError
        previous_end = 0
        for index, row in enumerate(value['requirements']):
            if set(row) != _ROW_KEYS:
                raise ValueError
            requirement = row['id']
            method, path, actor = PRIMARY[requirement]
            if row['method'] != method or row['path'] != path or row['actor'] != actor:
                raise ValueError
            if row['kind'] != ('lifecycle' if requirement == DISPOSITION_ID else 'http'):
                raise ValueError
            story_lines = row['story_lines']
            if (not isinstance(story_lines, list) or len(story_lines) != 2
                    or any(type(item) is not int for item in story_lines)
                    or not 1 <= story_lines[0] <= story_lines[1] <= len(lines)):
                raise ValueError
            if requirement != DISPOSITION_ID:
                # The first fifteen obligations replay strictly increasing,
                # disjoint story line ranges; the disposition obligation is
                # runner-lifecycle metadata the upstream story never pinned.
                if story_lines[0] <= previous_end:
                    raise ValueError
                previous_end = story_lines[1]
            anchors = row['anchors']
            if anchors != list(ANCHORS[requirement]):
                raise ValueError
            if (not isinstance(anchors, list) or not 2 <= len(anchors) <= 5
                    or len(set(anchors)) != len(anchors)
                    or any(not isinstance(item, str) or not 1 <= len(item) <= 1024 for item in anchors)):
                raise ValueError
            for literal in anchors:
                if literal not in text:
                    raise RunStateError('authority obligation anchor changed: ' + requirement)
            calls = row['calls']
            if calls != [{'method': call[0], 'path': call[1], 'expect_status': call[2],
                          'actor': call[3], 'query': call[4]} for call in CALLS[requirement]]:
                raise ValueError
            for call in calls:
                if set(call) != _CALL_KEYS or call['method'] not in ('GET', 'POST', 'PUT', 'DELETE'):
                    raise ValueError
            expectation = row['expectation']
            if not isinstance(expectation, str) or not 1 <= len(expectation) <= 1024:
                raise ValueError
        if sum(len(row['calls']) for row in value['requirements']) != EXPECTED_HTTP_CALLS:
            raise ValueError
        if (value['remote_validation'] != 'NOT_RUN' or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not True
                or value['independent_slice'] is not False
                or value['evidence_granularity'] != 'OWNED_PARENT_JOURNAL'):
            raise ValueError
        if PARENT not in {row['id'] for row in reviewed_release_inventory()}:
            raise RunStateError('authority parent left the reviewed release denominator')
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('authority source binding changed') from None
