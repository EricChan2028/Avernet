"""Source binding for the complete upstream caller-execution black-box Story.

The upstream story is a self-contained Python script: it owns the mocked
provider, the instrumented test-utils BCS process, its SQLite store and the
cache-loss restart. The runner treats it as one black-box child process and
freezes its source plus the coverage wrapper before any executor exists.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'caller_execution_story'
CASE_ID = 'LOCAL-CALLER-EXEC-01'
SUITE = 'owned-local-caller-execution-v1'
STATUS = 'LOCAL_CALLER_EXECUTION_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'caller_execution_story.py'
COVERAGE_WRAPPER = ROOT.parent / 'caller_e2e_coverage.sh'
REQUIREMENTS = tuple('LOCAL-CALLER-EXEC.' + name for name in (
    'provider-bot-binding', 'binding-mode-guard', 'ingress-rejection', 'queue-pause',
    'caller-context-token', 'downgrade-no-inherit', 'sse-token',
    'cache-loss-fail-closed', 'token-redaction'))
MARKER_KEYS = ('http-queue-provider', 'cache-loss-fail-closed', 'credential-redaction')
MARKER_PATTERNS = (
    re.compile(r'^Caller HTTP/queue/provider E2E: ([0-9]+) HTTP assertions passed$', re.M),
    re.compile(r'^Durable queue/cache-loss restart fails closed: PASS$', re.M),
    re.compile(r'^Caller credential persistence and log redaction: PASS$', re.M),
)
STORY_MARKERS = (
    'Caller HTTP/queue/provider E2E: {http_assertions} HTTP assertions passed',
    'Durable queue/cache-loss restart fails closed: PASS',
    'Caller credential persistence and log redaction: PASS',
)
MARKER1_PREFIX = 'Caller HTTP/queue/provider E2E: '
# Behavior literals frozen from the upstream story at 74dd751b6. Every
# obligation must keep its literals; story drift fails the binding instead of
# silently re-scoping an obligation.
ANCHORS = {
    'LOCAL-CALLER-EXEC.provider-bot-binding': (
        "'POST', '/me/ensure-human'", "'POST', '/providers'",
        "'/providers/' + self.registration['provider_id'] + '/bots'",
        "'PUT', '/bots/' + bot_id + '/visibility'", "'POST', '/channels/bindings'",
        "'POST', '/groups'"),
    'LOCAL-CALLER-EXEC.binding-mode-guard': (
        "target={'group': {'group_id': group['id']}}, status=400",
        "self.binding(bot_id, 'default', target={'group': {'group_id': group['id']}})",
        "{'config': {'execution_mode': 'caller'}}, status=400"),
    'LOCAL-CALLER-EXEC.ingress-rejection': (
        "credential=None, status=422", "credential='  ', status=422",
        "sender='', status=422", "'/channels/caller-e2e/callback', {}, status=401",
        "assert self.provider.received.empty(), 'invalid caller reached provider'"),
    'LOCAL-CALLER-EXEC.queue-pause': (
        "self.policy(True)", "time.sleep(0.5)",
        "assert self.provider.received.empty(), 'queue pause ignored'"),
    'LOCAL-CALLER-EXEC.caller-context-token': (
        "assert ext['caller_context']['subject']['kind'] == 'human'",
        "'test-token-' + user[-1]", "assert 'test-token-' not in json.dumps(body['message'])",
        "assert seen == {'usera', 'userb'}, 'caller contexts crossed'"),
    'LOCAL-CALLER-EXEC.downgrade-no-inherit': (
        "'caller_execution_token' not in body.get('extensions', {})",
        "credential='<test-token-default>'"),
    'LOCAL-CALLER-EXEC.sse-token': (
        'server_port}/sse', "credential='test-token-sse'",
        "assert streamed['extensions']['caller_execution_token'] == {'type': 'iam-token', "
        "'token': 'test-token-sse'}"),
    'LOCAL-CALLER-EXEC.cache-loss-fail-closed': (
        "credential='test-token-lost'", "process.send_signal(signal.SIGTERM)",
        "process = subprocess.Popen([binary, '-c', str(config_path)]",
        "status == 'failed'",
        "assert provider.received.empty(), 'cache loss fell back to provider identity'"),
    'LOCAL-CALLER-EXEC.token-redaction': (
        "'test-token' not in durable", "db.iterdump()", "root.rglob('*.log')",
        "'test-token' not in log_path.read_text()"),
}


def _require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def caller_execution_source_binding() -> dict:
    """Freeze and validate the upstream story, wrapper and obligation map."""
    try:
        value = read(ROOT / 'caller-execution-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'suite', 'case_id',
                           'environment', 'sources', 'test_utils_binary', 'requirements',
                           'story_markers', 'remote_validation', 'grants_release_credit',
                           'release_parent_denominator', 'independent_slice',
                           'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or value['environment'] != {'local': 'LOCAL_EXECUTOR_ONLY',
                                            'preprod': 'REMOTE_UNSUPPORTED'}
                or set(value['sources']) != {'caller_execution_story.py', 'caller_e2e_coverage.sh'}
                or value['story_markers'] != list(STORY_MARKERS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != 'SHARED_CHILD_RECEIPT'):
            raise ValueError
        binary = value['test_utils_binary']
        if (set(binary) != {'cargo_package', 'cargo_bin', 'cargo_features', 'build_command',
                            'auto_build', 'required_arguments', 'hash_algorithm'}
                or binary['cargo_package'] != 'bcs' or binary['cargo_bin'] != 'bcs-caller-e2e'
                or binary['cargo_features'] != ['test-utils']
                or binary['build_command'] != 'cargo build -p bcs --features test-utils --bin bcs-caller-e2e'
                or binary['auto_build'] is not False
                or binary['required_arguments'] != ['--binary', '--binary-sha256']
                or binary['hash_algorithm'] != 'sha256'):
            raise ValueError
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        for name, digest in value['sources'].items():
            path = STORY_SOURCE if name == 'caller_execution_story.py' else COVERAGE_WRAPPER
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError('caller-execution upstream source drift requires review')
        if MARKER1_PREFIX not in story_text:
            raise RunStateError('caller-execution story marker 1 disappeared')
        if any(marker not in story_text for marker in value['story_markers'][1:]):
            raise RunStateError('caller-execution story markers changed')
        rows = value['requirements']
        if [row['id'] for row in rows] != list(REQUIREMENTS):
            raise ValueError
        for row in rows:
            if (set(row) != {'id', 'expectation', 'story_lines', 'markers'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024
                    or (not isinstance(row['story_lines'], list) or len(row['story_lines']) != 2
                        or any(type(item) is not int for item in row['story_lines'])
                        or not 1 <= row['story_lines'][0] <= row['story_lines'][1]
                        or row['story_lines'][1] > len(story_text.splitlines()))
                    or not row['markers'] or any(marker not in MARKER_KEYS for marker in row['markers'])
                    or len(set(row['markers'])) != len(row['markers'])):
                raise ValueError
            for literal in ANCHORS[row['id']]:
                if literal not in story_text:
                    raise RunStateError('caller-execution obligation anchor changed: ' + row['id'])
            if row['id'] == 'LOCAL-CALLER-EXEC.cache-loss-fail-closed' \
                    and row['markers'] != ['cache-loss-fail-closed']:
                raise ValueError
            if row['id'] == 'LOCAL-CALLER-EXEC.token-redaction' \
                    and row['markers'] != ['credential-redaction']:
                raise ValueError
        if any(row['markers'] != ['http-queue-provider'] for row in rows[:7]):
            raise ValueError
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('caller-execution source binding changed') from None
