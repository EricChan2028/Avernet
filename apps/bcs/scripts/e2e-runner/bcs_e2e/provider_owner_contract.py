"""Source binding for the upstream Provider owner-isolation black-box Story.

The upstream story (provider_scope_story.py, frozen at git tip f181ba44,
provider-scoped owner collaboration #2654) is one self-contained Python script
driven against one live BCS HTTP boundary plus its Workbench WebSocket: it
bootstraps two disjoint mock Humans through the loopback X-Mock-User-Id
admission header, registers one restricted collaboration-scope Provider and one
standard Provider, mints owner-owned and foreign-owned provider Bots under the
provider_admin_token, walks the owner boundary across public-visibility
writes, public/private Group transitions, the same_owner promotion with its
409/403 one-way gates, member add/deny, invite-link join, Session create/read
/chat and the Workbench 101 upgrade connect projection, then restores legacy
behavior by removing the restricted Bots and deletes every Group and provider
Bot on teardown. The runner treats it as one black-box child process beside one
owned BCS runtime and freezes the story, its shell entry, the per-obligation
anchor map and the 45-check accounting before any executor exists.

Truthful status: the parent is a long-unregistered upstream story registered
2026-10-29 into the known-unmapped backlog (not part of the reviewed 30-Story
release denominator), the preprod public-v1 gateway exposes no Provider
admin face and no provider_admin_token credential channel (REMOTE_UNSUPPORTED
by design), and the owned-local live replay currently blocks at story request
8 because provider-registered Bots receive no bcs-edge-permission owner edges
(the frozen story predates the #2625 bot-authority model). All three facts are
frozen in the contract as documented gaps; nothing here claims release credit.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from .inventory import KNOWN_UNMAPPED_LEGACY_IDS
from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_provider_owner_isolation'
CASE_ID = 'LOCAL-PROVIDER-OWNER-01'
SUITE = 'owned-local-provider-owner-v1'
STATUS = 'LOCAL_PROVIDER_OWNER_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'provider_scope_story.py'
SHELL_SOURCE = ROOT.parent / 'e2e-test' / 'provider_scope_story.sh'
REPO_ROOT = ROOT.parents[3]
SHELL_RELATIVE = 'apps/bcs/scripts/e2e-test/provider_scope_story.sh'
STORY_RELATIVE = 'apps/bcs/scripts/e2e-test/provider_scope_story.py'
SOURCE_REVISION = 'f181ba448a8bd5844c9517d8543ff3b6bb9d66bb'
REQUIREMENTS = tuple('PROVIDER-OWNER.' + name for name in (
    'mock-human-bootstrap', 'provider-registration', 'provider-bot-mint',
    'public-visibility-writes', 'public-group-create',
    'restricted-scope-downgrade-denied', 'group-private-transition',
    'same-owner-promotion', 'legacy-standard-downgrade-denied',
    'restricted-bot-attach', 'foreign-member-add-denied',
    'restricted-driver-foreign-member-denied', 'foreign-driver-group-denied',
    'cross-provider-member-denied', 'restricted-group-visibility-upgrade-denied',
    'private-bot-collaboration-without-friendship', 'invite-foreign-join-denied',
    'session-owner-boundary', 'session-messages-owner-boundary',
    'session-foreign-chat-denied', 'workbench-ws-owner-boundary',
    'legacy-restore-on-restricted-bot-removal', 'restricted-member-readd-denied',
    'cleanup-disposition'))
MARKER_KEYS = ('owned-local-provider-owner-compliance',)
MARKER_PATTERNS = (
    re.compile(r'^Provider scope story: ([0-9]+) live API assertions passed$', re.M),
)
STORY_MARKERS = (
    'Provider scope story: {checks} live API assertions passed',
)
MARKER_PREFIX = 'Provider scope story: '
MARKER_SUFFIX = ' live API assertions passed'
# Upstream Story.checks accounting, frozen from provider_scope_story.py at
# f181ba44. request() (L40-41) and ws_connect (L95-96) are the only two
# increment sites; execution multiplicity comes from the two literal-tuple
# loops in run(): the three-Bot visibility loop (L116-118) and the two-Bot
# member-removal loop (L152-153).
#   direct self.request executions: 33
#     straight-line sites L102,103,104,110,120,121,123,124,125,126,129,130,
#       131,135,137,139,140,141,142,143,144,146,147,148,154,155,156,158 (28)
#       plus visibility loop L117 x3 and member-removal loop L153 x2 (5)
#   self.bot executions (L109,114,115,128): 4
#   self.group executions (L119,132,133,134,138): 5
#   self.ws_connect executions (L149,150,157): 3
#   total: 33 + 4 + 5 + 3 = 45
# Separately frozen but NOT marker-counted: the two explicit metadata asserts
# (L122, L125) and the three Workbench 101-upgrade guards (L69, per connect).
STORY_CHECKS = 45
# Deterministic loopback principal the frozen story drives as its owner
# (foreign principal is minted by the story itself as scopeforeign<uuid4>).
OWNER_ID = 'provider-owner-story'
# Behavior literals frozen from the upstream story at
# f181ba448a8bd5844c9517d8543ff3b6bb9d66bb. Every obligation keeps its
# literals; story drift fails the binding instead of silently re-scoping an
# obligation.
ANCHORS = {
    'PROVIDER-OWNER.mock-human-bootstrap': (
        '"POST", "/me/ensure-human", {}',
        ', owner=self.foreign)',
    ),
    'PROVIDER-OWNER.provider-registration': (
        '"name": "Scope story", "auth": {"mode": "static_bearer"}',
        '"collaboration_scope": "standard"',
    ),
    'PROVIDER-OWNER.provider-bot-mint': (
        '"name": "Owner scope " + ref, "owners": [owner], "provider_bot_ref": ref',
        '"name": "Cloud scope story", "auth": {"mode": "static_bearer"}',
        '"cloud-" + uuid.uuid4().hex',
        '"foreign-" + uuid.uuid4().hex',
    ),
    'PROVIDER-OWNER.public-visibility-writes': (
        'f"/bots/{bot}/visibility", {"visibility": "public"}',
        'owner=self.foreign if bot == outsider else self.owner',
    ),
    'PROVIDER-OWNER.public-group-create': (
        '"driver_bot": driver, "visibility": visibility',
        '"participants": [{"bot_uuid": member} for member in members]',
    ),
    'PROVIDER-OWNER.restricted-scope-downgrade-denied': (
        'status=409',
        '"Rejected rename"',
        'metadata["name"] == "Scope story"',
    ),
    'PROVIDER-OWNER.group-private-transition': (
        'f"/groups/{group}", {"visibility": "private"}',),
    'PROVIDER-OWNER.same-owner-promotion': (
        '{"collaboration_scope": "same_owner"}, token=admin',
        '["collaboration_scope"] == "same_owner"',
    ),
    'PROVIDER-OWNER.legacy-standard-downgrade-denied': (
        '{"collaboration_scope": "standard"}, token=admin, status=403',),
    'PROVIDER-OWNER.restricted-bot-attach': (
        '"third-" + uuid.uuid4().hex',
        'f"/groups/{group}/members", {"bot_uuid": third}',
    ),
    'PROVIDER-OWNER.foreign-member-add-denied': (
        '{"bot_uuid": outsider}, status=403',),
    'PROVIDER-OWNER.restricted-driver-foreign-member-denied': (
        'self.group(local, [outsider], status=403)',),
    'PROVIDER-OWNER.foreign-driver-group-denied': (
        'self.group(outsider, [local], status=403)',),
    'PROVIDER-OWNER.cross-provider-member-denied': (
        'self.group(local, [cloud], "public", status=403)',),
    'PROVIDER-OWNER.restricted-group-visibility-upgrade-denied': (
        'f"/groups/{group}", {"visibility": "public"}, status=403',),
    'PROVIDER-OWNER.private-bot-collaboration-without-friendship': (
        'must not need a friendship',
        'self.group(local, [cloud])',
    ),
    'PROVIDER-OWNER.invite-foreign-join-denied': (
        'f"/groups/{group}/invite-link", {}',
        'f"/groups/join/{invite}", {}, owner=self.foreign, status=403',
    ),
    'PROVIDER-OWNER.session-owner-boundary': (
        '["session_id"]',
        'f"/groups/{group}/sessions", {}, owner=self.foreign, status=403',
    ),
    'PROVIDER-OWNER.session-messages-owner-boundary': (
        'f"/sessions/{session}/messages"',
        'owner=self.foreign, status=400',
    ),
    'PROVIDER-OWNER.session-foreign-chat-denied': (
        '"must not be delivered"}, owner=self.foreign, status=403',),
    'PROVIDER-OWNER.workbench-ws-owner-boundary': (
        'self.ws_connect(group, session, self.owner, True)',
        'self.ws_connect(group, session, self.foreign, False)',
    ),
    'PROVIDER-OWNER.legacy-restore-on-restricted-bot-removal': (
        'Removing all restricted Bots restores legacy behavior',
        'self.ws_connect(group, session, self.foreign, True)',
        'f"/groups/{group}/members/{bot}"',
    ),
    'PROVIDER-OWNER.restricted-member-readd-denied': (
        '{"bot_uuid": local}, status=403',),
    'PROVIDER-OWNER.cleanup-disposition': (
        'f"/groups/{group}?bot_id={driver}"',
        'token=provider["provider_admin_token"]',
    ),
}
SHELL_ANCHORS = (
    'E2E_TESTS_STORIES+=(story_provider_owner_isolation)',
    'provider_scope_story.py" --base-url "$BCS_API_BASE_URL" \\',
    'assert_eq "Provider owner boundary survives group/session/Workbench transitions" "$result" "0"',
)
DOCUMENTED_GAPS = (
    'PREPROD_PROVIDER_ADMIN_FACE_ABSENT',
    'OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT',
    'WORKBENCH_WS_MOCK_ADMISSION',
)
DRIFT_ERROR = 'provider-owner upstream source drift requires review'


def _git_tip(relative: str) -> str:
    """Freeze the per-file git tip revision; unwritable history fails closed."""
    try:
        result = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'log', '-1', '--format=%H', '--', relative],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        raise RunStateError('provider-owner git tip revision is unavailable; drift gate cannot run') from None
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise RunStateError('provider-owner git tip revision is not a full commit digest')
    return value


def provider_owner_adaptability() -> dict:
    """Declare the dual-environment adaptability row without release credit."""
    return {
        'case_id': CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'selected_obligations': [name.split('.', 1)[1] for name in REQUIREMENTS],
        'public_paths': [],
        'environments': ['owned-local-full-story'],
        'local_executor_status': 'LOCAL_EXECUTOR_ONLY',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'The Provider registration/admin face is absent from the preprod public-v1 route '
            'bindings and no provider_admin_token credential channel exists there '
            '(PREPROD_PROVIDER_ADMIN_FACE_ABSENT), and the owned-local live replay currently '
            'blocks because provider-registered Bots receive no authority owner edges '
            '(OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT). Neither environment grants release credit.'),
    }


def provider_owner_source_binding() -> dict:
    """Freeze and validate the upstream story, its shell entry and the map."""
    try:
        value = read(ROOT / 'provider-owner-migration-contract.json')
        source_paths = {
            'provider_scope_story.py': STORY_SOURCE,
            'provider_scope_story.sh': SHELL_SOURCE,
        }
        if (set(value) != {'schema_version', 'source_revision', 'source_file_revisions',
                           'parent', 'suite', 'case_id', 'environment', 'sources',
                           'story_checks', 'owned_runtime_binary', 'requirements',
                           'story_markers', 'documented_gaps', 'adaptability',
                           'remote_validation', 'grants_release_credit',
                           'release_parent_denominator', 'independent_slice',
                           'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or value['source_revision'] != SOURCE_REVISION
                or set(value['source_file_revisions']) != set(source_paths)
                or any(not re.fullmatch('[0-9a-f]{40}', digest)
                       for digest in value['source_file_revisions'].values())
                or value['source_revision'] != _git_tip(STORY_RELATIVE)
                or value['source_file_revisions']['provider_scope_story.py'] != _git_tip(STORY_RELATIVE)
                or value['source_file_revisions']['provider_scope_story.sh'] != _git_tip(SHELL_RELATIVE)
                or value['environment'] != {'local': 'LOCAL_EXECUTOR_ONLY',
                                            'preprod': 'REMOTE_UNSUPPORTED'}
                or set(value['sources']) != set(source_paths)
                or type(value['story_checks']) is not int or value['story_checks'] != STORY_CHECKS
                or value['story_markers'] != list(STORY_MARKERS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != 'OWNED_CHILD_RECEIPT'
                or value['adaptability'] != provider_owner_adaptability()):
            raise ValueError
        runtime = value['owned_runtime_binary']
        if (set(runtime) != {'package', 'opt_in_env', 'build_command', 'auto_build',
                             'required_arguments', 'hash_algorithm'}
                or runtime['package'] != 'bcs'
                or runtime['opt_in_env'] != ['BCS_E2E_TEST_BINARY',
                                             'BCS_E2E_TEST_BINARY_SHA256']
                or runtime['build_command'] != 'cargo build -p bcs --bin bcs'
                or runtime['auto_build'] is not False
                or runtime['required_arguments'] != ['--binary', '--binary-sha256']
                or runtime['hash_algorithm'] != 'sha256'):
            raise ValueError
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        shell_text = SHELL_SOURCE.read_text(encoding='utf-8')
        for name, digest in value['sources'].items():
            path = source_paths[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError(DRIFT_ERROR)
        if MARKER_PREFIX not in story_text or MARKER_SUFFIX not in story_text:
            raise RunStateError('provider-owner story marker disappeared')
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        for anchor in SHELL_ANCHORS:
            if anchor not in shell_text:
                raise RunStateError('provider-owner shell entry anchor changed')
        # Parent fence: this story is a named known-unmapped backlog parent
        # registered 2026-10-29 for migration; it is outside the reviewed
        # release denominator until a promotion review moves it.
        if PARENT not in KNOWN_UNMAPPED_LEGACY_IDS:
            raise RunStateError('provider-owner parent left the known-unmapped backlog')
        rows = value['requirements']
        if [row['id'] for row in rows] != list(REQUIREMENTS):
            raise ValueError
        previous_end = 0
        for row in rows:
            if (set(row) != {'id', 'expectation', 'story_lines', 'markers'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024
                    or (not isinstance(row['story_lines'], list)
                        or len(row['story_lines']) != 2
                        or any(type(item) is not int for item in row['story_lines'])
                        or not 1 <= row['story_lines'][0] <= row['story_lines'][1]
                        or row['story_lines'][1] > len(story_text.splitlines()))
                    or row['story_lines'][0] <= previous_end
                    or row['markers'] != list(MARKER_KEYS)):
                raise ValueError
            previous_end = row['story_lines'][1]
            for literal in ANCHORS[row['id']]:
                if literal not in story_text:
                    raise RunStateError('provider-owner obligation anchor changed: ' + row['id'])
        gaps = value['documented_gaps']
        if (not isinstance(gaps, dict) or set(gaps) != set(DOCUMENTED_GAPS)
                or any(not isinstance(text, str) or not 1 <= len(text) <= 2048
                       for text in gaps.values())):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('provider-owner source binding changed') from None
