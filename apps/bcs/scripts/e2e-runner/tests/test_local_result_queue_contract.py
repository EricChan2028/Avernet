"""Offline guards for the owned-local result-queue black-box slice."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.run_state import RunStateError
from bcs_e2e.local_result_queue_contract import (ANCHORS, BASE_SOURCE, MARKER_KEYS,
                                                  MARKER_PATTERNS, MARKER_PREFIX,
                                                  MARKER_SUFFIX, PROVIDER_JUDGE_MOCK,
                                                  REQUIRED_STORY_ARGUMENTS, REQUIREMENTS,
                                                  STORY_SOURCE, result_queue_source_binding)

GOOD_MARKER_LINE = ('Queued result: owner Send, observer Inject, bound context '
                    'and unique history verified')


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_requires_the_complete_result_line(self):
        self.assertIsNotNone(MARKER_PATTERNS[0].match(GOOD_MARKER_LINE))
        self.assertIsNone(MARKER_PATTERNS[0].match(GOOD_MARKER_LINE + '.'))
        self.assertIsNone(MARKER_PATTERNS[0].match('Queued result: partial marker'))
        self.assertIsNone(MARKER_PATTERNS[0].match('Queued result:\n' + GOOD_MARKER_LINE))

    def test_marker_literals_remain_in_the_upstream_story(self):
        text = STORY_SOURCE.read_text(encoding='utf-8')
        self.assertIn(MARKER_PREFIX, text)
        self.assertIn(MARKER_SUFFIX, text)
        self.assertIn(GOOD_MARKER_LINE, text)


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_upstream_map(self):
        value = result_queue_source_binding()
        self.assertEqual(value['parent'], 'story_state_machine_result_queue')
        self.assertEqual(value['suite'], 'owned-local-result-queue-v1')
        self.assertEqual(value['case_id'], 'LOCAL-RESULT-QUEUE-01')
        self.assertEqual(len(value['requirements']), 18)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['story_markers'], [GOOD_MARKER_LINE])
        self.assertEqual(value['required_story_arguments'], list(REQUIRED_STORY_ARGUMENTS))
        self.assertEqual(set(value['sources']),
                         {'result_queue_story.py', 'fixed_loop_story.py',
                          'http_provider_judge_mock.py'})
        self.assertFalse(value['owned_runtime_binary']['auto_build'])
        self.assertEqual(value['mock_child']['expected_webhook_requests'], 3)
        self.assertFalse(value['grants_release_credit'])
        self.assertTrue(value['independent_slice'])
        self.assertEqual(value['evidence_granularity'], 'OWNED_CHILD_RECEIPT')

    def test_raw_tamper_matrix_is_rejected(self):
        original = result_queue_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'revision': lambda value: value.__setitem__('source_revision', 'z' * 40),
            'file-revision': lambda value: value['source_file_revisions'].pop('fixed_loop_story.py'),
            'argument': lambda value: value.__setitem__(
                'required_story_arguments', ['--base-url', '--human']),
            'marker-lines': lambda value: value.__setitem__('source_marker_lines', 2),
            'runtime-auto-build': lambda value: value['owned_runtime_binary'].__setitem__(
                'auto_build', True),
            'mock-webhooks': lambda value: value['mock_child'].__setitem__(
                'expected_webhook_requests', 0),
            'mock-script': lambda value: value['mock_child'].__setitem__(
                'script', 'other_mock.py'),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'granularity': lambda value: value.__setitem__(
                'evidence_granularity', 'PER_OBLIGATION_RECEIPT'),
            'environment': lambda value: value['environment'].__setitem__(
                'preprod', 'SUPPORTED'),
            'independent': lambda value: value.__setitem__('independent_slice', False),
            'sources-drop': lambda value: value['sources'].pop('fixed_loop_story.py'),
            'sources-extra': lambda value: value['sources'].__setitem__('rogue.py', '0' * 64),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.local_result_queue_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                result_queue_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'drifted.py'
            drifted.write_bytes(b'#!/usr/bin/env python3\nprint("drifted")\n')
            for name, attribute in (('story', 'STORY_SOURCE'), ('base', 'BASE_SOURCE'),
                                    ('mock', 'PROVIDER_JUDGE_MOCK')):
                with self.subTest(source=name), \
                        patch('bcs_e2e.local_result_queue_contract.' + attribute, drifted), \
                        self.assertRaises(RunStateError):
                    result_queue_source_binding()


class ObligationTests(unittest.TestCase):
    def setUp(self):
        self.text = STORY_SOURCE.read_text(encoding='utf-8')
        self.lines = self.text.splitlines()
        self.rows = result_queue_source_binding()['requirements']

    def test_anchors_and_disjoint_line_ranges_cover_the_frozen_story(self):
        self.assertEqual([row['id'] for row in self.rows], list(REQUIREMENTS))
        previous_end = 0
        for row in self.rows:
            start, end = row['story_lines']
            self.assertGreaterEqual(start, 1)
            self.assertGreaterEqual(start, previous_end + 1)
            self.assertLessEqual(end, len(self.lines))
            previous_end = end
            anchors = ANCHORS[row['id']]
            self.assertTrue(anchors)
            for literal in anchors:
                self.assertIn(literal, self.text, row['id'])
            for line in self.lines[start - 1:end]:
                if any(literal in line for literal in anchors):
                    break
            else:
                self.fail('obligation anchors fall outside the declared range: ' + row['id'])

    def test_independent_slice_never_claims_release_credit(self):
        value = result_queue_source_binding()
        self.assertFalse(value['grants_release_credit'])
        self.assertFalse(value['release_parent_denominator'])
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertEqual(value['environment'],
                         {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})


class BaseContractTests(unittest.TestCase):
    def test_result_queue_dependencies_stay_valid_and_bounded(self):
        from bcs_e2e.local_fixed_loop_contract import fixed_loop_source_binding
        base = fixed_loop_source_binding()
        self.assertEqual(base['parent'], 'story_fixed_loop_human_editor_workflow')
        self.assertIn('fixed_loop_story.py', base['sources'])

    def test_base_story_and_mock_stay_regular_bounded_files(self):
        for path in (BASE_SOURCE, PROVIDER_JUDGE_MOCK):
            self.assertFalse(path.is_symlink())
            self.assertTrue(path.is_file())
            self.assertLessEqual(path.stat().st_size, 1_048_576)


if __name__ == '__main__':
    unittest.main()
