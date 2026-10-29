"""Source and obligation contract for the Draft delivery owned-local slice.

The upstream entry is a shell story: it selects the Draft UUID on the client,
creates immutable revisions through the product CLI, exercises public fixed
revision reads/validation, appends through CAS, rejects a stale writer, revokes
one revision and deletes the Draft tombstone. This module freezes that source
and a stricter eight-obligation semantic map before any owned runtime starts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from ..run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[2]
PARENT = 'story_collaboration_draft_delivery'
CASE_ID = 'LOCAL-DRAFT-DELIVERY-01'
SUITE = 'owned-local-draft-delivery-v1'
STATUS = 'LOCAL_DRAFT_DELIVERY_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'collaboration_draft_story.sh'
FIXTURE_SOURCE = ROOT.parents[1] / 'tests' / 'fixtures' / 'collaboration-definition-draft' / 'indentation-regression.json'
CONFIG_FRAGMENT = Path(__file__).with_name('collaboration-drafts.enabled.json')
REQUIREMENTS = tuple(
    'DRAFT-DELIVERY.' + name for name in (
        'client-selected-idempotent-create',
        'immutable-revision-append',
        'stale-update-conflict',
        'read-token-single-revision',
        'revoked-or-deleted-unavailable',
        'read-token-read-only',
        'no-store-every-response',
        'token-redaction',
    )
)
MARKER_KEYS = ('draft-delivery-compliance',)
MARKER_PATTERNS = (
    re.compile(r'^Draft delivery story: ([0-9]+) live HTTP assertions passed$', re.M),
)
STORY_MARKERS = ('Draft delivery story: {checks} live HTTP assertions passed',)
MARKER_PREFIX = 'Draft delivery story: '
MARKER_SUFFIX = ' live HTTP assertions passed'
STORY_CHECKS = 57
CONFIG_REQUIRED = {'collaboration_drafts': {'enabled': True}}
REDACTION_TOKENS = ('bot_token', 'readToken', 'read_token', 'Bearer ')
ENVIRONMENT = {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'}


def _source_paths() -> dict:
    # Built per call so tests can patch the module path constants.
    return {
        'collaboration_draft_story.sh': STORY_SOURCE,
        'indentation-regression.json': FIXTURE_SOURCE,
    }


def _require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def _line_text(path: Path, line_number: int) -> str:
    lines = path.read_text(encoding='utf-8').splitlines()
    if line_number < 1 or line_number > len(lines):
        raise ValueError('anchor line is outside the upstream source')
    return lines[line_number - 1]


def _validate_anchors(rows: list, source: Path) -> None:
    if [row['id'] for row in rows] != list(REQUIREMENTS):
        raise ValueError('requirement order differs from the frozen map')
    for row in rows:
        if set(row) != {'id', 'expectation', 'story_lines', 'anchors', 'markers'}:
            raise ValueError('requirement shape changed')
        if not isinstance(row['expectation'], str) or not 1 <= len(row['expectation']) <= 1024:
            raise ValueError('requirement expectation is invalid')
        lines = row['story_lines']
        if (not isinstance(lines, list) or len(lines) != 2
                or any(type(item) is not int for item in lines)
                or not 1 <= lines[0] <= lines[1]
                or lines[1] > len(source.read_text(encoding='utf-8').splitlines())):
            raise ValueError('requirement story_lines are invalid')
        if not isinstance(row['anchors'], list) or not row['anchors']:
            raise ValueError('requirement anchors are missing')
        for anchor in row['anchors']:
            if set(anchor) != {'line', 'literal'} or type(anchor['line']) is not int:
                raise ValueError('anchor shape changed')
            if anchor['line'] < lines[0] or anchor['line'] > lines[1]:
                raise ValueError('anchor line is outside its requirement range')
            if anchor['literal'] not in _line_text(source, anchor['line']):
                raise RunStateError('draft-delivery obligation anchor changed: ' + row['id'])
        if row['markers'] != list(MARKER_KEYS):
            raise ValueError('requirement marker changed')


def draft_delivery_source_binding() -> dict:
    """Freeze and validate the shell story, fixture and eight obligations."""
    try:
        value = read(ROOT / 'draft-delivery-migration-contract.json')
        source_paths = _source_paths()
        if (set(value) != {'schema_version', 'parent', 'suite', 'case_id', 'environment',
                           'sources', 'source_revision', 'source_file_revisions',
                           'story_checks', 'story_markers', 'owned_runtime_binary',
                           'fixture', 'config_fragment', 'redaction_tokens',
                           'requirements', 'remote_validation', 'grants_release_credit',
                           'release_parent_denominator', 'independent_slice',
                           'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['source_file_revisions']) != {'collaboration_draft_story.sh'}
                or any(not re.fullmatch('[0-9a-f]{40}', digest)
                       for digest in value['source_file_revisions'].values())
                or value['environment'] != ENVIRONMENT
                or set(value['sources']) != set(source_paths)
                or type(value['story_checks']) is not int or value['story_checks'] != STORY_CHECKS
                or value['story_markers'] != list(STORY_MARKERS)
                or value['redaction_tokens'] != list(REDACTION_TOKENS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != 'OWNED_CHILD_RECEIPT'):
            raise ValueError
        runtime = value['owned_runtime_binary']
        if (set(runtime) != {'package', 'build_command', 'hash_algorithm', 'opt_in_env',
                             'auto_build', 'required_arguments'}
                or runtime['package'] != 'bcs'
                or runtime['build_command'] != 'cargo build -p bcs --bin bcs'
                or runtime['hash_algorithm'] != 'sha256'
                or runtime['opt_in_env'] != ['BCS_E2E_TEST_BINARY',
                                             'BCS_E2E_TEST_BINARY_SHA256']
                or runtime['auto_build'] is not False
                or runtime['required_arguments'] != ['--binary', '--binary-sha256']):
            raise ValueError
        for name, digest in value['sources'].items():
            path = source_paths[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError('draft-delivery upstream source drift requires review')
        if not STORY_SOURCE.read_text(encoding='utf-8').startswith('#!/usr/bin/env bash'):
            raise ValueError
        fixture = json.loads(FIXTURE_SOURCE.read_text(encoding='utf-8'))
        definition_yaml = fixture['revision']['definition_yaml']
        expected_fixture = value['fixture']
        if (set(expected_fixture) != {'path', 'definition_yaml_bytes', 'content_hash'}
                or expected_fixture['path'] != 'indentation-regression.json'
                or expected_fixture['definition_yaml_bytes'] != len(definition_yaml.encode())
                or expected_fixture['definition_yaml_bytes'] != 2174
                or expected_fixture['content_hash'] != hashlib.sha256(
                    definition_yaml.encode()).hexdigest()
                or expected_fixture['content_hash'] != fixture['revision']['content_hash']):
            raise RunStateError('draft-delivery incident fixture changed')
        config = value['config_fragment']
        if (set(config) != {'path', 'required', 'integration_note'}
                or config['path'] != 'bcs_e2e/draft_delivery/collaboration-drafts.enabled.json'
                or config['required'] != CONFIG_REQUIRED
                or not isinstance(config['integration_note'], str)
                or 'collaboration_drafts.enabled=true' not in config['integration_note']):
            raise ValueError
        if CONFIG_FRAGMENT.is_symlink() or not CONFIG_FRAGMENT.is_file():
            raise ValueError
        if json.loads(CONFIG_FRAGMENT.read_text(encoding='utf-8')) != CONFIG_REQUIRED:
            raise RunStateError('draft-delivery config fragment changed')
        _validate_anchors(value['requirements'], STORY_SOURCE)
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, json.JSONDecodeError):
        raise RunStateError('draft-delivery source binding changed') from None
