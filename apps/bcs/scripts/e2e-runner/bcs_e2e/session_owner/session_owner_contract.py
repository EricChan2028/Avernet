"""Source binding for the upstream session owner permissions Story.

The upstream story is a self-contained Python script driven against one live
BCS deployment with two HTTP faces: the station legacy face admits per-request
X-Mock-User-Id headers and bootstraps three distinct Human principals, ensures
each disposable Bot through the one-time register token flow, and the public
openapi/v1 face authenticates minted gateway Principal tokens. The story freezes
the complete ownership matrix: a manager creates a private Group and one
Session, invites the owner's Bot as a Session-only participant (invisible in
the Group participant projection), the owner controls only its own Bot mode
while every foreign mutation is refused with the exact error codes, Human
self-service scope joins are ownership-bound, collection and history stay
actor-scoped, and Bot revocation keeps the Human relation until both are
revoked. Every HTTP operation counts toward the frozen 60-assertion marker;
teardown disposes the Session, Group and both Bots. This slice is an
independent migration candidate: its parent stays in the known unmapped
backlog and never joins the reviewed release denominator.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from ..inventory import KNOWN_UNMAPPED_LEGACY_IDS, reviewed_release_inventory
from ..run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[2]
PARENT = 'story_session_owner_permissions'
CASE_ID = 'LOCAL-SESSION-OWNER-01'
SUITE = 'owned-local-session-owner-v1'
STATUS = 'LOCAL_SESSION_OWNER_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'session_owner_story.py'
SHELL_SOURCE = ROOT.parent / 'e2e-test' / 'session_owner_story.sh'
REQUIREMENTS = tuple('SESSION-OWNER.' + name for name in (
    'bootstrap-ownership', 'group-session-creation', 'group-read-boundary',
    'bot-mode-ownership', 'foreign-mutation-denied', 'human-self-service-scope',
    'collection-history-actor-scope', 'membership-revocation-isolation',
    'cleanup-disposition'))
MARKER_KEYS = ('session-owner-permissions-compliance',)
MARKER_PATTERNS = (
    re.compile(r'^Session owner story: ([0-9]+) HTTP assertions passed$', re.M),
)
STORY_MARKERS = ('Session owner story: {checks} HTTP assertions passed',)
MARKER_PREFIX = 'Session owner story: '
MARKER_SUFFIX = ' HTTP assertions passed'
STORY_CHECKS = 60
CLEANUP_CHECKS = 4
EVIDENCE_GRANULARITY = 'OWNED_SESSION_OWNER_JOURNAL'
ENVIRONMENT = {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'}
# The upstream run() sequence executes exactly 60 HTTP assertions (the frozen
# story marker value, lines 96-187) and cleanup() executes 4 DELETE
# dispositions (lines 192-197); thirteen structural Python asserts anchor the
# write-echo and read-back obligations.
UPSTREAM_ASSERTIONS = {'run_http': STORY_CHECKS, 'cleanup_http': CLEANUP_CHECKS,
                       'structural_python': 13, 'shell': 1}
ANCHORS = {
    'SESSION-OWNER.bootstrap-ownership': (
        '"POST", "/me/ensure-human", {}, public=False',
        'token = self.request(user, "GET", "/register/token", public=False)["token"]',
        'f"/register?token={segment(token)}&bot-name={segment(name)}"',
        '"/bots/onboard"',
        '{"visibility": "public"}, public=False',
    ),
    'SESSION-OWNER.group-session-creation': (
        '"group_kind": "normal", "name": "Session owner authorization E2E"',
        '"driver_bot_uuid": driver',
        'status=201)["group_id"]',
        '{"visibility": "private"}',
        '"title": "Session-only participation"}, status=201)["session_id"]',
    ),
    'SESSION-OWNER.group-read-boundary': (
        'status=403, error="forbidden"',
        'next(item for item in detail["participants"] if item["actor_id"] == actor)',
        'assert all(p["actor_id"] != owned for p in detail["participants"]), detail',
    ),
    'SESSION-OWNER.bot-mode-ownership': (
        'for mode in ("muted", "auto"):',
        'assert updated["mode"] == mode, updated',
        '"invalid_participant_mode"',
        '"message_view_scope_forbidden"',
        'assert self.participant(session, owned)["mode"] == "auto", "Denied patch partially wrote mode"',
    ),
    'SESSION-OWNER.foreign-mutation-denied': (
        '"PATCH", f"/sessions/{session}", {"title": "denied"}',
        '"participant_not_found"',
        'self.patch(self.manager, session, "missing-bot", {"mode": "auto"}',
        'self.patch(self.owner, session, "missing-bot", {"mode": "auto"}',
    ),
    'SESSION-OWNER.human-self-service-scope': (
        'human = "human_" + self.owner',
        'stranger = "human_" + self.outsider',
        'for scope, mode in (("participant", "absent"), ("full", "present")):',
        'assert (stored["mode"], stored["message_view_scope"]) == (mode, scope), stored',
        'self.patch(self.manager, session, human, {"message_view_scope": "participant"})',
    ),
    'SESSION-OWNER.collection-history-actor-scope': (
        'f"/groups/{group}/sessions?view_bot_id={owned}&limit=20"',
        'view_bot_id={driver}&limit=20"',
        'f"/sessions/{session}/collect"',
        '{"participant": owned}',
    ),
    'SESSION-OWNER.membership-revocation-isolation': (
        '# Revoking the Bot leaves the Human relation; revoking both denies reads.',
        'f"/sessions/{session}/participants/{owned}"',
        'f"/sessions/{session}/participants/{human}"',
        'self.request(self.owner, "GET", f"/sessions/{session}", status=403, error="forbidden")',
    ),
    'SESSION-OWNER.cleanup-disposition': (
        'resources = [(self.manager, f"/sessions/{sid}", True) for sid in self.sessions]',
        'resources += [(user, f"/bots/{bot}", False) for user, bot in self.bots]',
        'assert not failures, f"Story cleanup failed: {failures}"',
    ),
}
SHELL_ANCHORS = (
    'E2E_TESTS_STORIES+=(story_session_owner_permissions)',
    'session_owner_story.py" --base-url "$BCS_API_BASE_URL"',
    'assert_eq "Session ownership, Human scope, and membership revocation remain isolated" "$result" "0"',
)
DRIFT_ERROR = 'session-owner upstream source drift requires review'

DOCUMENTED_GAPS = {
    'PREPROD_STATION_BOOTSTRAP_FACE_ABSENT': (
        'ensure-human, register-token, register, onboard, visibility PUT and station Bot delete are '
        'legacy station products with per-request X-Mock-User-Id admission on the owned-local runtime '
        'only; the preprod public-v1 gateway face exposes none of them and mock-header admission does '
        'not exist there.'),
    'PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE': (
        'The story needs three distinct Human principals (manager/owner/outsider) plus per-Human Bot '
        'registration; the preprod pilot credential family is one Human Cookie plus its dedicated '
        'registered Bots (profile actors driver/member only), so the three-Human ownership isolation '
        'matrix cannot be expressed.'),
    'PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY': (
        'public-v1 X-Avernet-Principal tokens are minted from AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE; '
        'the owned-local Singlebox runtime mints from its per-run key while a gateway profile needs an '
        'operator-provisioned auth.principal_signing_key_env reference the preprod pilot profile does '
        'not declare.'),
    'PREPROD_PARTICIPANT_ROUTES_UNBOUND': (
        'participants PATCH/POST/DELETE, session collect add/remove, view_bot_id-scoped session listing, '
        'message history and Bot visibility are outside the reviewed public-v1 route bindings '
        '(route_bindings.py binds group/session CRUD, messages, token and the register trio only).'),
}


def session_owner_dual_adaptability() -> dict:
    """Declare the dual-environment face honestly: local full, preprod documented gaps."""
    return {
        'case_id': CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'selected_obligations': [
            'bootstrap-ownership', 'group-session-creation', 'group-read-boundary',
            'bot-mode-ownership', 'foreign-mutation-denied', 'human-self-service-scope',
            'collection-history-actor-scope', 'membership-revocation-isolation',
            'cleanup-disposition',
        ],
        'api_face': 'public-v1',
        'local_face': 'station-mock-admission+gateway-principal (owned-local runtime)',
        'environments': ['owned-local-executor', 'preprod-public-v1'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_execution_status': 'NOT_RUN_DOCUMENTED_GAPS',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'Owned-local executor ports all 60 run-phase HTTP assertions plus 4 cleanup '
            'dispositions in process; the preprod public-v1 gateway face lacks the station '
            'bootstrap legs, per-request mock admission, a three-Human pilot credential family, '
            'participants/collect/visibility routes and operator principal minting.'),
    }


def _source_paths() -> dict:
    # Built per call so tests can patch the module path constants.
    return {
        'session_owner_story.py': STORY_SOURCE,
        'session_owner_story.sh': SHELL_SOURCE,
    }


def session_owner_source_binding() -> dict:
    """Freeze and validate the upstream story, its shell entry and the map."""
    try:
        value = read(ROOT / 'session-owner-migration-contract.json')
        source_paths = _source_paths()
        if (set(value) != {'schema_version', 'source_revision', 'source_file_revisions',
                           'parent', 'suite', 'case_id', 'environment', 'sources',
                           'story_checks', 'owned_runtime_binary', 'requirements',
                           'story_markers', 'remote_validation', 'grants_release_credit',
                           'release_parent_denominator', 'independent_slice',
                           'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['source_file_revisions']) != set(source_paths)
                or any(not re.fullmatch('[0-9a-f]{40}', digest)
                       for digest in value['source_file_revisions'].values())
                or set(value['sources']) != set(source_paths)
                or any(not re.fullmatch('[0-9a-f]{64}', digest)
                       for digest in value['sources'].values())
                or value['environment'] != ENVIRONMENT
                or type(value['story_checks']) is not int or value['story_checks'] != STORY_CHECKS
                or value['story_markers'] != list(STORY_MARKERS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != EVIDENCE_GRANULARITY):
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
            raise RunStateError('session-owner story marker disappeared')
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        if not shell_text.startswith('#!/usr/bin/env bash'):
            raise ValueError
        for anchor in SHELL_ANCHORS:
            if anchor not in shell_text:
                raise RunStateError('session-owner shell entry anchor changed')
        # Independent-slice fence: the parent stays a known unmapped backlog story
        # and never joins the reviewed release denominator.
        if PARENT not in KNOWN_UNMAPPED_LEGACY_IDS:
            raise RunStateError('session-owner parent left the known unmapped backlog')
        reviewed = {row['id'] for row in reviewed_release_inventory()}
        if PARENT in reviewed:
            raise RunStateError('session-owner slice must stay outside the reviewed release denominator')
        rows = value['requirements']
        if [row['id'] for row in rows] != list(REQUIREMENTS):
            raise ValueError
        total_lines = len(story_text.splitlines())
        for row in rows:
            if (set(row) != {'id', 'expectation', 'story_lines', 'markers'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024
                    or (not isinstance(row['story_lines'], list)
                        or len(row['story_lines']) != 2
                        or any(type(item) is not int for item in row['story_lines'])
                        or not 1 <= row['story_lines'][0] <= row['story_lines'][1]
                        or row['story_lines'][1] > total_lines)
                    or row['markers'] != list(MARKER_KEYS)):
                raise ValueError
            for literal in ANCHORS[row['id']]:
                if literal not in story_text:
                    raise RunStateError('session-owner obligation anchor changed: ' + row['id'])
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('session-owner source binding changed') from None
