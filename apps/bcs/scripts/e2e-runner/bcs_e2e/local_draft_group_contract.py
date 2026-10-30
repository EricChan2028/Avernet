"""Source binding for the upstream collaboration Draft-group black-box Story.

The upstream story is a self-contained Python script driven against a live
BCS HTTP boundary: it bootstraps the local Human principal, registers a
disposable author Bot, registers a scripted coverage Provider plus its driver
and reviewer provider Bots, completes their friendship, authors an immutable
Draft revision, shares a V1 preview reference, runs the grant and browser
guard negative matrices, launches the edited collaboration through the
browser API, asserts persistence and Draft immutability, revokes the share
and tears every resource down. The runner treats it as one black-box child
process beside an owned coverage Provider mock child and freezes the story,
the incident fixture and the mock script bytes before any executor exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .run_state import RunStateError, read
from .local_fixed_loop_contract import _story_assertion_sites

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'collaboration_draft_group_story'
CASE_ID = 'LOCAL-DRAFT-GROUP-01'
SUITE = 'owned-local-draft-group-v1'
STATUS = 'LOCAL_DRAFT_GROUP_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'collaboration_draft_group_story.py'
FIXTURE_SOURCE = (ROOT.parents[1] / 'tests' / 'fixtures'
                  / 'collaboration-definition-draft' / 'indentation-regression.json')
PROVIDER_JUDGE_MOCK = ROOT.parent / 'e2e-test' / 'http_provider_judge_mock.py'
REQUIREMENTS = tuple('LOCAL-DRAFT-GROUP.' + name for name in (
    'human-registration', 'provider-collaborator-friendship', 'immutable-source-upload',
    'share-reference-issuance', 'v1-preview-byte-fidelity', 'shared-validation-binding',
    'grant-negative-matrix', 'revision-input-guards', 'browser-identity-chain',
    'originator-selection', 'group-launch-input', 'browser-context-submission-guards',
    'impersonation-rejection', 'group-launch-binding', 'draft-immutability',
    'share-revocation', 'cleanup-disposition'))
ASSERTION_IDS = _story_assertion_sites(STORY_SOURCE.read_text(encoding='utf-8'),
                                   'LOCAL-DRAFT-GROUP.', weighted=True)
REQUIRED_ASSERTION_COUNT = len(ASSERTION_IDS)

MARKER_KEYS = ('draft-group-launch',)
MARKER_PATTERNS = (
    re.compile(r'^Draft group launch story: ([0-9]+) live HTTP assertions passed '
               r'\(including cleanup\)$', re.M),
)
STORY_MARKERS = (
    'Draft group launch story: {checks} live HTTP assertions passed (including cleanup)',
)
MARKER_PREFIX = 'Draft group launch story: '
MARKER_SUFFIX = ' live HTTP assertions passed (including cleanup)'
# Behavior literals frozen from the upstream story at
# 48b23001e2edf63fbedc80acdf1474e5c105ad23. Every obligation keeps its
# literals; story drift fails the binding instead of silently re-scoping an
# obligation.
ANCHORS = {
    'LOCAL-DRAFT-GROUP.human-registration': (
        '"ensure Human"', '"/register/token"', '"/register?" + query', '"/bots/onboard"',
    ),
    'LOCAL-DRAFT-GROUP.provider-collaborator-friendship': (
        '"register scripted Provider"', 'self.mock_sink_url + "/provider/webhook"',
        '"register connected " + role', '"driver requests collaboration"',
        '"reviewer accepts collaboration"', '"from_bot": driver["bot_uuid"]',
    ),
    'LOCAL-DRAFT-GROUP.immutable-source-upload': (
        '"assistant uploads incident YAML"', 'len(source.encode()) == 2174',
        'hashlib.sha256(source.encode()).hexdigest()', '"saved immutable source"',
    ),
    'LOCAL-DRAFT-GROUP.share-reference-issuance': (
        '"assistant obtains share card"', '"/revisions/1/reference"',
        'set(reference) == {"schemaVersion", "draftId", "revision", "readToken"}',
        '"reference remains compatible with the deployed CLI"',
        'reference["readToken"] == "A" * 43',
        '"legacy field is a fixed non-secret placeholder"',
    ),
    'LOCAL-DRAFT-GROUP.v1-preview-byte-fidelity': (
        '"/api/v1" + self.draft + "/revisions/1"', '"V1 host opens shared card"',
        'opened["definition_yaml"].encode() == source.encode()',
        '"V1 preview pins hash and revision"',
    ),
    'LOCAL-DRAFT-GROUP.shared-validation-binding': (
        '"V1 host validates stored revision"', 'revision + "/validate"',
        'validated["validation"]["valid"] is True', '"validation binds the same revision"',
    ),
    'LOCAL-DRAFT-GROUP.grant-negative-matrix': (
        '("GET", ""), ("POST", "/validate")', '"missing revision"',
        'revision.replace("/revisions/1", "/revisions/2")', 'status=404',
        'DRAFT_REFERENCE_UNAVAILABLE',
    ),
    'LOCAL-DRAFT-GROUP.revision-input-guards': (
        '"invalid revision"', '"/invalid"', '"replacement"', 'DRAFT_INPUT',
    ),
    'LOCAL-DRAFT-GROUP.browser-identity-chain': (
        '"read current Human"', '"GET", "/me"', 'user["user_id"] == self.human',
        '"browser identity comes from the configured Human auth chain"',
        '"X-BCS-Expected-User"',
    ),
    'LOCAL-DRAFT-GROUP.originator-selection': (
        '"select an owned originator"', '"/bots/mine?" + query',
        "own Bot selector returns the user's registered Bots",
        '"load originator collaborators"', 'purpose=collaboration',
        '"friend appears as an eligible collaborator"',
    ),
    'LOCAL-DRAFT-GROUP.group-launch-input': (
        'edited = source.replace(', 'name: Human edited collaboration',
        '"group_kind": "normal"', '"originator": driver["bot_uuid"]',
        '"strategy": "state_machine"', '"content_yaml": edited',
        '"participant_bindings"',
    ),
    'LOCAL-DRAFT-GROUP.browser-context-submission-guards': (
        '"missing browser context"', '"cross-site submission"', '"account changed"',
        '"duplicate expected user"', '"Sec-Fetch-Site", "cross-site"',
        'browser_context_required', 'user_context_changed', 'status=403',
    ),
    'LOCAL-DRAFT-GROUP.impersonation-rejection': (
        '"Bot credentials cannot impersonate a Human"',
        'self.credentials(author)[0], ("Actor", driver["bot_uuid"])',
        '"X-Avernet-Principal", "invalid"', 'status=401', '"unauthenticated"',
    ),
    'LOCAL-DRAFT-GROUP.group-launch-binding': (
        '"Human creates edited collaboration"', 'status=201',
        '"creation supplies the group navigation Session"',
        '"chosen Bot leads the collaboration"',
        "group stores the user's edited YAML",
        'group persists the chosen role bindings, without substituting the author Bot',
    ),
    'LOCAL-DRAFT-GROUP.draft-immutability': (
        '"historical card remains unchanged"', '"editing the group does not mutate the Draft"',
        'reopened["definition_yaml"] == source',
    ),
    'LOCAL-DRAFT-GROUP.share-revocation': (
        '"author revokes the shared revision"', '"/revisions/1/share"',
        'status=204', '"revoked V1 reference is unavailable"',
    ),
    'LOCAL-DRAFT-GROUP.cleanup-disposition': (
        '"delete disposable " + label', 'status=204 if label == "Draft" else 200',
        '"disable disposable Provider"', '"Cleanup failed: " + "; ".join(errors)',
    ),
}
DRIFT_ERROR = 'draft-group upstream source drift requires review'


def _source_paths() -> dict:
    # Built per call so tests can patch the module path constants.
    return {
        'collaboration_draft_group_story.py': STORY_SOURCE,
        'indentation-regression.json': FIXTURE_SOURCE,
        'http_provider_judge_mock.py': PROVIDER_JUDGE_MOCK,
    }


def _require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def draft_group_source_binding() -> dict:
    """Freeze and validate the upstream story, fixture, mock and obligation map."""
    try:
        value = read(ROOT / 'draft-group-migration-contract.json')
        source_paths = _source_paths()
        if (set(value) != {'schema_version', 'source_revision', 'source_file_revisions',
                           'parent', 'suite', 'case_id', 'environment', 'sources',
                           'definition_yaml_bytes', 'owned_runtime_binary', 'mock_child',
                           'requirements', 'story_markers', 'remote_validation',
                           'grants_release_credit', 'release_parent_denominator',
                           'independent_slice', 'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['source_file_revisions']) != set(source_paths)
                or any(not re.fullmatch('[0-9a-f]{40}', digest)
                       for digest in value['source_file_revisions'].values())
                or value['environment'] != {'local': 'LOCAL_EXECUTOR_ONLY',
                                            'preprod': 'REMOTE_UNSUPPORTED'}
                or set(value['sources']) != set(source_paths)
                or value['definition_yaml_bytes'] != 2174
                or value['story_markers'] != list(STORY_MARKERS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != 'OWNED_CHILD_RECEIPT'):
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
        mock_child = value['mock_child']
        if (set(mock_child) != {'script', 'ready_protocol', 'expected_webhook_requests'}
                or mock_child['script'] != 'http_provider_judge_mock.py'
                or mock_child['ready_protocol'] != 'ready-file'
                or mock_child['expected_webhook_requests'] != 0):
            raise ValueError
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        for name, digest in value['sources'].items():
            path = source_paths[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError(DRIFT_ERROR)
        if MARKER_PREFIX not in story_text or MARKER_SUFFIX not in story_text:
            raise RunStateError('draft-group story marker disappeared')
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        fixture_json = json.loads(FIXTURE_SOURCE.read_text(encoding='utf-8'))
        definition_yaml = fixture_json['revision']['definition_yaml']
        if len(definition_yaml.encode()) != value['definition_yaml_bytes']:
            raise RunStateError('draft-group incident fixture byte size changed')
        rows = value['requirements']
        if [row['id'] for row in rows] != list(REQUIREMENTS):
            raise ValueError
        for row in rows:
            if (set(row) != {'id', 'expectation', 'story_lines', 'markers'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024
                    or (not isinstance(row['story_lines'], list)
                        or len(row['story_lines']) != 2
                        or any(type(item) is not int for item in row['story_lines'])
                        or not 1 <= row['story_lines'][0] <= row['story_lines'][1]
                        or row['story_lines'][1] > len(story_text.splitlines()))
                    or row['markers'] != list(MARKER_KEYS)):
                raise ValueError
            for literal in ANCHORS[row['id']]:
                if literal not in story_text:
                    raise RunStateError('draft-group obligation anchor changed: ' + row['id'])
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('draft-group source binding changed') from None
