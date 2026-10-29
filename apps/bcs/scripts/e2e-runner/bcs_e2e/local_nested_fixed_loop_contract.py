"""Source binding for the upstream nested fixed-loop black-box Stories.

The upstream story drives two complete nested fixed-loop collaboration
workflows through public HTTP plus the product CLI. The Bot exercise runs a
two-level hierarchical state machine (outer max 2, inner max 3) through the
scripted Judge and Provider, verifies the exact 15-step delivery path, replays
a stale inner callback, and checks the 17/15/2 graph and history shape. The
Human exercise runs a three-level fixed loop (outer 3, middle 2, inner 2),
drives exactly one pending Human node at a time, proves iteration-context
isolation and stale-reply rejection, upholds the Human authentication
boundary for the graph API, checks the 31/21/10 shape and verifies machine
output equality. The runner treats the story as one black-box child beside an
owned BCS runtime and freezes the story, its fixed-loop base class and the
Provider/Judge mock bytes before any executor exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_nested_fixed_loop_workflows'
CASE_ID = 'LOCAL-NESTED-FIXED-LOOP-01'
SUITE = 'owned-local-nested-fixed-loop-v1'
STATUS = 'LOCAL_NESTED_FIXED_LOOP_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'nested_fixed_loop_story.py'
BASE_SOURCE = ROOT.parent / 'e2e-test' / 'fixed_loop_story.py'
PROVIDER_JUDGE_MOCK = ROOT.parent / 'e2e-test' / 'http_provider_judge_mock.py'
REQUIREMENTS = tuple('NESTED-LOOP.' + name for name in (
    'nested-bot-loop-definition', 'nested-human-loop-definition',
    'setup-runtime-identity-registration', 'definition-cli-validation-and-run-admission',
    'terminal-callback-replay-guard', 'bot-story-judge-outcomes', 'bot-story-routing-path-map',
    'bot-story-completion-marker', 'bot-story-graph-shape', 'human-story-judge-outcomes',
    'human-story-pending-loop-iteration', 'human-loop-context-isolation',
    'human-story-completion-marker', 'human-story-auth-boundary', 'history-output-equality',
    'main-required-args-and-two-story-orchestration'))
MARKER_KEYS = ('nested-bot-story', 'nested-human-story')
MARKER_PATTERNS = (
    re.compile(r'^  Nested Bot: 17/15/2 routing, paths, history and stale callback verified$', re.M),
    re.compile(r'^  Nested Human: three levels, parent isolation, stale reply and outer subtree skip verified$', re.M),
)
STORY_MARKERS = (
    '  Nested Bot: 17/15/2 routing, paths, history and stale callback verified',
    '  Nested Human: three levels, parent isolation, stale reply and outer subtree skip verified',
)
REQUIRED_STORY_ARGUMENTS = ('--base-url', '--mock-url', '--cli', '--driver', '--human')
# Behavior literals frozen from the upstream story at
# 71860eae99c62d7bf1349a0f46562f6395acbf1e. Every obligation keeps its
# literals; story drift fails the binding instead of silently re-scoping an
# obligation.
ANCHORS = {
    'NESTED-LOOP.nested-bot-loop-definition': (
        'def nested_bot_definition()', '"Nested Bot review E2E"',
        '"mode": "fixed", "max_iterations": 2', '"mode": "fixed", "max_iterations": 3',
    ),
    'NESTED-LOOP.nested-human-loop-definition': (
        'def nested_human_definition()', '"Three-level Human review E2E"',
        '"entry_node": "outer_gate"', '"entry_node": "middle_gate"',
        '"entry_node": "inner_review"',
    ),
    'NESTED-LOOP.setup-runtime-identity-registration': (
        'self.post("/control/reset", mock=True)', 'self.post("/me/ensure-human")',
        '"name": "Nested Loop E2E provider"', '"name": "Nested Loop E2E worker"',
        '/visibility", {"visibility": "public"}',
    ),
    'NESTED-LOOP.definition-cli-validation-and-run-admission': (
        'self.cli("validate", str(path))', 'validation["valid"] is True, validation',
        '"group_strategy": "state_machine"',
        'f"/groups/{segment(self.group_id)}/state-machine-runs"',
    ),
    'NESTED-LOOP.terminal-callback-replay-guard': (
        'def replay_terminal_callback', 'status=(200, 409, 410)',
        'assert [item["body"]["id"] for item in after] == [item["body"]["id"] for item in before]',
        'assert not response.get("created", False), response',
    ),
    'NESTED-LOOP.bot-story-judge-outcomes': (
        '"outcomes": ["revise", "approved", "repeat", "revise", "revise", "revise", "done"]',
        'self.post("/control/judge/outcomes"',
    ),
    'NESTED-LOOP.bot-story-routing-path-map': (
        '("outer-1-prepare", "nested-bot-story-input")',
        '("outer-2-inner-3-review", "outer-2-inner-3-draft")',
        'early_break_delivery = delivery_id',
        'self.replay_terminal_callback(delivery_id, "stale-inner-review")',
    ),
    'NESTED-LOOP.bot-story-completion-marker': (
        '"  Nested Bot: 17/15/2 routing, paths, history and stale callback verified"',
    ),
    'NESTED-LOOP.bot-story-graph-shape': (
        'assert set(graph["loops"]) == {"outer", "outer/inner"}',
        'assert len(graph["nodes"]) == 17',
        'assert len(completed) == 15 and len(skipped) == 2',
        '("outer", 1), ("inner", 3),',
    ),
    'NESTED-LOOP.human-story-judge-outcomes': (
        '"outcomes": ["repeat", "done"]',
        'self.start_definition(nested_human_definition(), "nested-human-story", directory)',
    ),
    'NESTED-LOOP.human-story-pending-loop-iteration': (
        '"/pending-human-nodes"', 'assert len(pending_nodes) == 1, pending_nodes',
        'assert 1 <= len(path) <= 3', 'assert context["loop_path"] == path',
    ),
    'NESTED-LOOP.human-loop-context-isolation': (
        'context.get("previous_result") is None',
        'previous["execution_node_id"] == first_inner[parent][0]',
        '{"content": "stale-inner-response"}, status=409',
        'assert len(first_inner) == 4',
    ),
    'NESTED-LOOP.human-story-completion-marker': (
        'self.finish_bot("nested-human-published", "human-result-19")',
        '"  Nested Human: three levels, parent isolation, stale reply and outer subtree skip verified"',
    ),
    'NESTED-LOOP.human-story-auth-boundary': (
        '# Runs containing HumanInput are deliberately Human-authenticated;',
        'assert set(graph["loops"]) == {"outer", "outer/middle", "outer/middle/inner"}',
        'assert len(completed) == 21 and len(skipped) == 10',
        'assert all(node["execution"]["loop_path"][0]["iteration"] == 3 for node in skipped)',
    ),
    'NESTED-LOOP.history-output-equality': (
        '"event") == "output"', 'assert len(matches) == 1',
        'matches[0]["metadata"]["state_machine"].get("execution") == node.get("execution")',
    ),
    'NESTED-LOOP.main-required-args-and-two-story-orchestration': (
        'for name in ("base-url", "mock-url", "cli", "driver", "human")',
        'story.exercise_bot_story(Path(directory))', 'story.exercise_human_story(Path(directory))',
        'with tempfile.TemporaryDirectory(prefix="bcs-nested-loop-e2e-") as directory',
    ),
}
DRIFT_ERROR = 'nested-fixed-loop upstream source drift requires review'


def _source_paths() -> dict:
    # Built per call so tests can patch the module path constants.
    return {
        'nested_fixed_loop_story.py': STORY_SOURCE,
        'fixed_loop_story.py': BASE_SOURCE,
        'http_provider_judge_mock.py': PROVIDER_JUDGE_MOCK,
    }


def _require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def nested_fixed_loop_source_binding() -> dict:
    """Freeze and validate the upstream story, base, mock and obligation map."""
    try:
        value = read(ROOT / 'nested-fixed-loop-migration-contract.json')
        source_paths = _source_paths()
        if (set(value) != {'schema_version', 'source_revision', 'source_file_revisions',
                           'parent', 'suite', 'case_id', 'environment', 'sources',
                           'required_story_arguments', 'source_marker_lines',
                           'owned_runtime_binary', 'mock_child', 'requirements',
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
                or list(value['required_story_arguments']) != list(REQUIRED_STORY_ARGUMENTS)
                or value['source_marker_lines'] != 2
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
                or runtime['required_arguments'] != ['--binary', '--binary-sha256',
                                                     '--cli-binary', '--driver']
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
        for marker in STORY_MARKERS:
            if marker not in story_text:
                raise RunStateError('nested-fixed-loop story marker disappeared')
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        from .local_fixed_loop_contract import fixed_loop_source_binding  # noqa: PLC0415
        fixed_loop_source_binding()
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
                    or not isinstance(row['markers'], list) or not row['markers']
                    or any(marker not in MARKER_KEYS for marker in row['markers'])
                    or len(set(row['markers'])) != len(row['markers'])):
                raise ValueError
            for literal in ANCHORS[row['id']]:
                if literal not in story_text:
                    raise RunStateError('nested-fixed-loop obligation anchor changed: ' + row['id'])
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('nested-fixed-loop source binding changed') from None
