"""Offline guards for the owned-local Draft-group black-box slice."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.run_state import RunStateError
from bcs_e2e.local_draft_group_contract import (ANCHORS, FIXTURE_SOURCE, MARKER_KEYS,
                                                MARKER_PATTERNS, MARKER_PREFIX, MARKER_SUFFIX,
                                                PROVIDER_JUDGE_MOCK, REQUIREMENTS,
                                                STORY_SOURCE, draft_group_source_binding)

GOOD_MARKER_LINE = ('Draft group launch story: 41 live HTTP assertions passed '
                    '(including cleanup)')


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_parses_the_checks_count(self):
        match = MARKER_PATTERNS[0].match(GOOD_MARKER_LINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '41')
        self.assertIsNone(MARKER_PATTERNS[0].match(GOOD_MARKER_LINE[:-len(' (including cleanup)')]))
        self.assertIsNone(MARKER_PATTERNS[0].match('Draft group story: 41 checks passed'))

    def test_marker_literals_remain_in_the_upstream_story(self):
        text = STORY_SOURCE.read_text(encoding='utf-8')
        self.assertIn(MARKER_PREFIX, text)
        self.assertIn(MARKER_SUFFIX, text)


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_upstream_map(self):
        value = draft_group_source_binding()
        self.assertEqual(value['parent'], 'collaboration_draft_group_story')
        self.assertEqual(value['suite'], 'owned-local-draft-group-v1')
        self.assertEqual(value['case_id'], 'LOCAL-DRAFT-GROUP-01')
        self.assertEqual(len(value['requirements']), 17)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['story_markers'][0],
                         'Draft group launch story: {checks} live HTTP assertions passed '
                         '(including cleanup)')
        self.assertEqual(set(value['sources']),
                         {'collaboration_draft_group_story.py', 'indentation-regression.json',
                          'http_provider_judge_mock.py'})
        self.assertEqual(value['definition_yaml_bytes'], 2174)
        self.assertFalse(value['owned_runtime_binary']['auto_build'])
        self.assertEqual(value['mock_child']['expected_webhook_requests'], 0)
        self.assertFalse(value['grants_release_credit'])
        self.assertTrue(value['independent_slice'])
        self.assertEqual(value['evidence_granularity'], 'OWNED_CHILD_RECEIPT')

    def test_contract_tamper_matrix_is_rejected(self):
        original = draft_group_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'markers': lambda value: value['story_markers'].__setitem__(0, 'Draft group story: {checks}'),
            'runtime-binary-auto-build': lambda value: value['owned_runtime_binary'].__setitem__('auto_build', True),
            'runtime-binary-arguments': lambda value: value['owned_runtime_binary'].__setitem__('required_arguments', ['--binary']),
            'revision': lambda value: value.__setitem__('source_revision', 'z' * 40),
            'file-revision': lambda value: value['source_file_revisions'].pop('indentation-regression.json'),
            'definition-yaml-bytes': lambda value: value.__setitem__('definition_yaml_bytes', 2175),
            'mock-child-script': lambda value: value['mock_child'].__setitem__('script', 'other_mock.py'),
            'mock-child-traffic': lambda value: value['mock_child'].__setitem__('expected_webhook_requests', 5),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'granularity': lambda value: value.__setitem__('evidence_granularity', 'PER_OBLIGATION_RECEIPT'),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'independent': lambda value: value.__setitem__('independent_slice', False),
            'sources-drop': lambda value: value['sources'].pop('http_provider_judge_mock.py'),
            'sources-extra': lambda value: value['sources'].__setitem__('rogue.py', '0' * 64),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.local_draft_group_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                draft_group_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'drifted.bin'
            drifted.write_bytes(b'drifted upstream source\n')
            for name, attribute in (('story', 'STORY_SOURCE'), ('fixture', 'FIXTURE_SOURCE'),
                                    ('judge-mock', 'PROVIDER_JUDGE_MOCK')):
                with self.subTest(source=name), \
                        patch('bcs_e2e.local_draft_group_contract.' + attribute, drifted), \
                        self.assertRaises(RunStateError):
                    draft_group_source_binding()


class ObligationTests(unittest.TestCase):
    def setUp(self):
        self.text = STORY_SOURCE.read_text(encoding='utf-8')
        self.rows = draft_group_source_binding()['requirements']

    def test_anchors_and_disjoint_line_ranges_cover_the_frozen_story(self):
        self.assertEqual([row['id'] for row in self.rows], list(REQUIREMENTS))
        previous_end = 0
        for row in self.rows:
            start, end = row['story_lines']
            self.assertGreaterEqual(start, 1)
            self.assertGreaterEqual(start, previous_end + 1)
            self.assertLessEqual(end, len(self.text.splitlines()))
            previous_end = end
            anchors = ANCHORS[row['id']]
            self.assertTrue(anchors)
            for literal in anchors:
                self.assertIn(literal, self.text, row['id'])

    def test_v1_reference_is_no_longer_an_authority_grant(self):
        self.assertNotIn('X-BCS-Draft-Read-Token', self.text)
        self.assertIn('reference["readToken"] == "A" * 43', self.text)
        self.assertIn('set(reference) == {"schemaVersion", "draftId", "revision", "readToken"}', self.text)
        self.assertEqual(self.text.count('"missing revision"'), 1)
        self.assertIn('revision.replace("/revisions/1", "/revisions/2")', self.text)

    def test_independent_slice_never_claims_release_credit(self):
        value = draft_group_source_binding()
        self.assertFalse(value['grants_release_credit'])
        self.assertFalse(value['release_parent_denominator'])
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertEqual(value['environment'],
                         {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'})


class FixtureTests(unittest.TestCase):
    def test_incident_sample_bytes_and_marker_stay_frozen(self):
        value = draft_group_source_binding()
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        fixture_json = json.loads(FIXTURE_SOURCE.read_text(encoding='utf-8'))
        definition_yaml = fixture_json['revision']['definition_yaml']
        self.assertEqual(len(definition_yaml.encode()), 2174)
        self.assertEqual(value['definition_yaml_bytes'], 2174)
        self.assertIn('len(source.encode()) == 2174', story_text)
        self.assertIn('"saved immutable source"', story_text)
        self.assertIn('"editing the group does not mutate the Draft"', story_text)


if __name__ == '__main__':
    unittest.main()
