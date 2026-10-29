"""Source binding for the upstream retired service policy black-box Story.

The upstream story (#3173 retirement regression) is a self-contained Python
script driven against one live BCS HTTP boundary: it bootstraps the local
Human principal through the loopback mock-admission headers, registers one
disposable driver Bot with a one-time token, creates two legacy Groups whose
``service_spec`` payloads (a disabled policy object and a null compatibility
shim) must persist nothing, asserts the retired PATCH settings surface is
Gone with the exact ``service_policy_retired`` error, proves two distinct
service_invocation Sessions still run with persisted inputs, keeps the
running-reactivation and completed-chat conversion guards intact, and deletes
every resource on teardown. The runner treats it as one black-box child
process beside one owned BCS runtime and freezes the story and its shell
entry before any executor exists. This slice is promoted into the reviewed
release denominator (the 30th parent); it is not an independent slice.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .inventory import reviewed_release_inventory
from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_retired_service_policy'
CASE_ID = 'LOCAL-SERVICE-POLICY-01'
SUITE = 'owned-local-service-policy-v1'
STATUS = 'LOCAL_SERVICE_POLICY_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'service_policy_story.py'
SHELL_SOURCE = ROOT.parent / 'e2e-test' / 'service_policy_story.sh'
REQUIREMENTS = tuple('SERVICE-POLICY.' + name for name in (
    'mock-human-bootstrap', 'provider-free-bot-registration', 'legacy-encoding-ignored',
    'retired-settings-gone', 'invocation-lifecycle', 'running-reactivation-denied',
    'chat-session-guard', 'cleanup-disposition'))
MARKER_KEYS = ('retired-service-policy-compliance',)
MARKER_PATTERNS = (
    re.compile(r'^Retired service policy story: ([0-9]+) HTTP/response checks passed$', re.M),
)
STORY_MARKERS = (
    'Retired service policy story: {checks} HTTP/response checks passed',
)
MARKER_PREFIX = 'Retired service policy story: '
MARKER_SUFFIX = ' HTTP/response checks passed'
STORY_CHECKS = 23
# Behavior literals frozen from the upstream story at
# 31bbfdc933ebf5e9361a273ea16b668326092b48. Every obligation keeps its
# literals; story drift fails the binding instead of silently re-scoping an
# obligation.
ANCHORS = {
    'SERVICE-POLICY.mock-human-bootstrap': (
        '"POST", "/me/ensure-human", {}, bot_auth=False',
        '"GET", "/register/token"',
    ),
    'SERVICE-POLICY.provider-free-bot-registration': (
        'f"/register?token={segment(token)}&bot-name=Policy-story-driver"',
        'registered["bot_uuid"], registered["bot_token"]',
        '"/bots/onboard"',
        '"domains": ["testing"], "skills": ["review"], "scopes": ["local"]',
    ),
    'SERVICE-POLICY.legacy-encoding-ignored': (
        'for policy in ({"enabled": False, "max_concurrency": 1}, None):',
        '"service_spec": policy',
        'assert_policy_absent(created)',
        'assert "service_spec" not in group',
        'assert "retired_service_spec" not in group',
    ),
    'SERVICE-POLICY.retired-settings-gone': (
        '"PATCH", f"/groups/{group}/settings"',
        '"service_spec": {"enabled": True}}, status=410',
        'retired["error"] == "service_policy_retired"',
    ),
    'SERVICE-POLICY.invocation-lifecycle': (
        'status=202',
        'invoked["session_kind"] == "service_invocation"',
        'invoked["status"] == "running"',
        'stored["input"] == {"run": index}',
        'self.sessions[0] != self.sessions[1]',
    ),
    'SERVICE-POLICY.running-reactivation-denied': (
        'status=409',
        '"running" in conflict["error"]',
        '["input"] == {"run": 0}',
    ),
    'SERVICE-POLICY.chat-session-guard': (
        '"Chat lifecycle guard"',
        'status=201',
        'completed["status"] == "completed"',
        '"not service_invocation" in invalid["error"]',
        'stored["status"] == "completed" and stored["output"] == {"ok": True}',
    ),
    'SERVICE-POLICY.cleanup-disposition': (
        'f"/sessions/{sid}?bot_id={segment(self.bot)}"',
        'f"/groups/{gid}?bot_id={segment(self.bot)}"',
        'f"/bots/{self.bot}"',
        'f"Story cleanup failed: {failures}"',
    ),
}
SHELL_ANCHORS = (
    'E2E_TESTS_STORIES+=(story_retired_service_policy)',
    'service_policy_story.py" --base-url "$BCS_API_BASE_URL"',
    'assert_eq "Legacy service_spec is ignored and Session lifecycle remains valid" "$result" "0"',
)
DRIFT_ERROR = 'service-policy upstream source drift requires review'


def _source_paths() -> dict:
    # Built per call so tests can patch the module path constants.
    return {
        'service_policy_story.py': STORY_SOURCE,
        'service_policy_story.sh': SHELL_SOURCE,
    }


def _require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def service_policy_source_binding() -> dict:
    """Freeze and validate the upstream story, its shell entry and the map."""
    try:
        value = read(ROOT / 'service-policy-migration-contract.json')
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
                or value['environment'] != {'local': 'LOCAL_EXECUTOR_ONLY',
                                            'preprod': 'REMOTE_UNSUPPORTED'}
                or set(value['sources']) != set(source_paths)
                or type(value['story_checks']) is not int or value['story_checks'] != STORY_CHECKS
                or value['story_markers'] != list(STORY_MARKERS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not True
                or value['independent_slice'] is not False
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
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        shell_text = SHELL_SOURCE.read_text(encoding='utf-8')
        for name, digest in value['sources'].items():
            path = source_paths[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError(DRIFT_ERROR)
        if MARKER_PREFIX not in story_text or MARKER_SUFFIX not in story_text:
            raise RunStateError('service-policy story marker disappeared')
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        for anchor in SHELL_ANCHORS:
            if anchor not in shell_text:
                raise RunStateError('service-policy shell entry anchor changed')
        if PARENT not in {row['id'] for row in reviewed_release_inventory()}:
            raise RunStateError('service-policy parent left the reviewed release denominator')
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
                    raise RunStateError('service-policy obligation anchor changed: ' + row['id'])
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('service-policy source binding changed') from None
