"""Source binding for the upstream one-shot result-queue black-box Story.

The upstream story publishes one final state-machine result through the real
delivery scheduler: it guarantees the Human principal, registers a disposable
native-tool Provider with static bearer auth, registers the release-domain
driver and observer Bots, makes them public, creates the driver-to-observer
friendship, snapshots the real message-delivery policy and installs an
enforce-only replacement, starts one state-machine run, observes the scheduled
answer-node callback, publishes the final result, proves owner Send plus
observer Inject admission rows, completes and consumes the durable result,
re-reads the published history and restores every owned scope on teardown.
The runner treats it as one black-box child beside an owned BCS runtime and
freezes the story, its fixed-loop base class and the Provider mock bytes
before any executor exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .run_state import RunStateError, read
from .local_fixed_loop_contract import _story_assertion_sites

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_state_machine_result_queue'
CASE_ID = 'LOCAL-RESULT-QUEUE-01'
SUITE = 'owned-local-result-queue-v1'
STATUS = 'LOCAL_RESULT_QUEUE_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'result_queue_story.py'
BASE_SOURCE = ROOT.parent / 'e2e-test' / 'fixed_loop_story.py'
PROVIDER_JUDGE_MOCK = ROOT.parent / 'e2e-test' / 'http_provider_judge_mock.py'
REQUIREMENTS = tuple('RESULT-QUEUE.' + name for name in (
    'human-registration', 'provider-registration', 'recipient-bot-pair',
    'recipient-visibility-harness', 'delivery-policy-baseline',
    'delivery-policy-replacement', 'policy-update-cas', 'provider-callout-harness',
    'final-callback-admission', 'result-delivery-ledger', 'group-and-session',
    'state-machine-definition', 'state-machine-run-admission', 'one-shot-node-delivery',
    'durable-result-rows', 'scheduled-result-delivery', 'bound-context-and-unique-history',
    'run-completion-and-cleanup-disposition'))
ASSERTION_IDS = _story_assertion_sites(STORY_SOURCE.read_text(encoding='utf-8'),
                                   'RESULT-QUEUE.', weighted=False)
REQUIRED_ASSERTION_COUNT = len(ASSERTION_IDS)

MARKER_KEYS = ('result-queue-delivery',)
MARKER_PATTERNS = (
    re.compile(r'^Queued result: owner Send, observer Inject, bound context '
               r'and unique history verified$', re.M),
)
STORY_MARKERS = (
    'Queued result: owner Send, observer Inject, bound context and unique history verified',
)
MARKER_PREFIX = 'Queued result: '
MARKER_SUFFIX = ' bound context and unique history verified'
REQUIRED_STORY_ARGUMENTS = ('--base-url', '--mock-url', '--human')
# Behavior literals frozen from the upstream story at
# 71860eae99c62d7bf1349a0f46562f6395acbf1e. Every obligation keeps its
# literals; story drift fails the binding instead of silently re-scoping an
# obligation.
ANCHORS = {
    'RESULT-QUEUE.human-registration': (
        'self.post("/control/reset", mock=True)', 'self.post("/me/ensure-human")',
    ),
    'RESULT-QUEUE.provider-registration': (
        '"Result queue E2E"',
        'self.args.mock_sink_url or self.args.mock_url) + "/provider/webhook"',
        '"mode": "static_bearer"', '"mode": "native_tool"',
    ),
    'RESULT-QUEUE.recipient-bot-pair': (
        '("driver", "observer")', '"One-shot result recipient"',
        '"domains": ["release"], "skills": ["review"]',
    ),
    'RESULT-QUEUE.recipient-visibility-harness': (
        '/visibility", {"visibility": "public"}', 'self.post("/friends/request"',
    ),
    'RESULT-QUEUE.delivery-policy-baseline': (
        'self.request("/admin/message-delivery/policy")["policy"]',
        'policy = copy.deepcopy(self.original_policy)',
    ),
    'RESULT-QUEUE.delivery-policy-replacement': (
        'policy["flow_enabled"].update(group=True, system=True)', '"max_running": 1',
        '"max_queued": 20',
    ),
    'RESULT-QUEUE.policy-update-cas': (
        '"expected_version": current["version"]', '"policy": policy',
    ),
    'RESULT-QUEUE.provider-callout-harness': (
        'self.request("/control/provider/requests", mock=True)["requests"]',
    ),
    'RESULT-QUEUE.final-callback-admission': (
        'self.post("/bot/events"', '"state": "final"',
        'body.get("error") != "transport_conflict"',
    ),
    'RESULT-QUEUE.result-delivery-ledger': (
        '"/openapi/v1/collaboration/messages/"', '"/deliveries?session_id="',
    ),
    'RESULT-QUEUE.group-and-session': (
        'self.post("/groups"', '"Queued one-shot publication"',
        '"session_title": "Result admission"',
    ),
    'RESULT-QUEUE.state-machine-definition': (
        '"kind": "state_machine"', '"final_output": True',
    ),
    'RESULT-QUEUE.state-machine-run-admission': (
        '}/state-machine-runs"', '"input": {"question": "Prepare the result"}',
        '"Authorization": "Bearer " + self.driver["bot_runtime_token"]',
    ),
    'RESULT-QUEUE.one-shot-node-delivery': (
        '"one-shot node delivery"', 'row["body"].get("method") == "chat.send" for row in rows',
        'self.callback(node, self.driver, "queued-final-result")',
    ),
    'RESULT-QUEUE.durable-result-rows': (
        '"durable result admission"', 'len(rows) == 2', '["kind"] == "send"',
        '["kind"] == "inject"',
    ),
    'RESULT-QUEUE.scheduled-result-delivery': (
        '"scheduled result delivery"',
        'assert "queued-final-result" in json.dumps(result["body"]["message"])',
        'self.provider["bcs_to_provider_token"]', '"completed result delivery"',
    ),
    'RESULT-QUEUE.bound-context-and-unique-history': (
        '"observer receives the result as context"', '"Bound result context was lost"',
        '"consumed result context"',
    ),
    'RESULT-QUEUE.run-completion-and-cleanup-disposition': (
        'for history in (self.request(history_path), self.request(history_path))',
        'view["run"]["status"] == "completed"', 'self.replace_policy(self.original_policy)',
        '"/control/reset", mock=True',
    ),
}
DRIFT_ERROR = 'result-queue upstream source drift requires review'


def _source_paths() -> dict:
    # Built per call so tests can patch the module path constants.
    return {
        'result_queue_story.py': STORY_SOURCE,
        'fixed_loop_story.py': BASE_SOURCE,
        'http_provider_judge_mock.py': PROVIDER_JUDGE_MOCK,
    }


def _require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def result_queue_source_binding() -> dict:
    """Freeze and validate the upstream story, base, mock and obligation map."""
    try:
        value = read(ROOT / 'result-queue-migration-contract.json')
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
                or value['source_marker_lines'] != 1
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
                or mock_child['expected_webhook_requests'] != 3):
            raise ValueError
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        for name, digest in value['sources'].items():
            path = source_paths[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError(DRIFT_ERROR)
        if MARKER_PREFIX not in story_text or MARKER_SUFFIX not in story_text:
            raise RunStateError('result-queue story marker disappeared')
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
                    or row['markers'] != list(MARKER_KEYS)):
                raise ValueError
            for literal in ANCHORS[row['id']]:
                if literal not in story_text:
                    raise RunStateError('result-queue obligation anchor changed: ' + row['id'])
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('result-queue source binding changed') from None
