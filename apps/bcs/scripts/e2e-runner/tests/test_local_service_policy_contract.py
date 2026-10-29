"""Offline guards for the promoted retired service policy release slice."""
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
from bcs_e2e.local_service_policy_contract import (ANCHORS, MARKER_KEYS, MARKER_PATTERNS,
                                                    MARKER_PREFIX, MARKER_SUFFIX, REQUIREMENTS,
                                                    STORY_CHECKS, STORY_SOURCE, SHELL_SOURCE,
                                                    SHELL_ANCHORS, service_policy_source_binding)
from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, REVIEWED_RELEASE_PARENT_COUNT,
                               current_manifest, reviewed_release_inventory)

GOOD_MARKER_LINE = 'Retired service policy story: 23 HTTP/response checks passed'


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_parses_the_checks_count(self):
        match = MARKER_PATTERNS[0].match(GOOD_MARKER_LINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '23')
        self.assertIsNone(MARKER_PATTERNS[0].match(GOOD_MARKER_LINE[:-len(' passed')]))
        self.assertIsNone(MARKER_PATTERNS[0].match('Retired service policy: 23 checks passed'))

    def test_marker_literals_remain_in_the_upstream_story(self):
        text = STORY_SOURCE.read_text(encoding='utf-8')
        self.assertIn(MARKER_PREFIX, text)
        self.assertIn(MARKER_SUFFIX, text)

    def test_shell_anchors_remain_in_the_frozen_shell_entry(self):
        shell = SHELL_SOURCE.read_text(encoding='utf-8')
        for anchor in SHELL_ANCHORS:
            self.assertIn(anchor, shell)


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_upstream_map(self):
        value = service_policy_source_binding()
        self.assertEqual(set(value), {'schema_version', 'source_revision', 'source_file_revisions',
                                      'parent', 'suite', 'case_id', 'environment', 'sources',
                                      'story_checks', 'owned_runtime_binary', 'requirements',
                                      'story_markers', 'remote_validation', 'grants_release_credit',
                                      'release_parent_denominator', 'independent_slice',
                                      'evidence_granularity'})
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['parent'], 'story_retired_service_policy')
        self.assertEqual(value['suite'], 'owned-local-service-policy-v1')
        self.assertEqual(value['case_id'], 'LOCAL-SERVICE-POLICY-01')
        self.assertEqual(len(value['requirements']), 8)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['story_checks'], STORY_CHECKS)
        self.assertEqual(value['story_markers'],
                         ['Retired service policy story: {checks} HTTP/response checks passed'])
        self.assertEqual(set(value['sources']), {'service_policy_story.py', 'service_policy_story.sh'})
        for digest in value['sources'].values():
            self.assertEqual(len(digest), 64)
        for digest in value['source_file_revisions'].values():
            self.assertEqual(len(digest), 40)
        runtime = value['owned_runtime_binary']
        self.assertEqual(runtime['package'], 'bcs')
        self.assertFalse(runtime['auto_build'])
        self.assertEqual(runtime['required_arguments'], ['--binary', '--binary-sha256'])
        self.assertEqual(runtime['opt_in_env'], ['BCS_E2E_TEST_BINARY', 'BCS_E2E_TEST_BINARY_SHA256'])

    def test_promoted_slice_joins_the_reviewed_release_denominator(self):
        value = service_policy_source_binding()
        self.assertTrue(value['release_parent_denominator'])
        self.assertFalse(value['independent_slice'])
        self.assertFalse(value['grants_release_credit'])
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertEqual(value['evidence_granularity'], 'OWNED_CHILD_RECEIPT')
        self.assertEqual(value['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertNotIn('mock_child', value)
        self.assertNotIn('human_id', value)

    def test_contract_tamper_matrix_is_rejected(self):
        original = service_policy_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'markers': lambda value: value['story_markers'].__setitem__(0, 'Retired service policy story: {checks}'),
            'runtime-binary-auto-build': lambda value: value['owned_runtime_binary'].__setitem__('auto_build', True),
            'runtime-binary-arguments': lambda value: value['owned_runtime_binary'].__setitem__('required_arguments', ['--binary']),
            'revision': lambda value: value.__setitem__('source_revision', 'z' * 40),
            'file-revision': lambda value: value['source_file_revisions'].pop('service_policy_story.sh'),
            'denominator': lambda value: value.__setitem__('release_parent_denominator', False),
            'independent': lambda value: value.__setitem__('independent_slice', True),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'granularity': lambda value: value.__setitem__('evidence_granularity', 'PER_OBLIGATION_RECEIPT'),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'sources-drop': lambda value: value['sources'].pop('service_policy_story.py'),
            'sources-extra': lambda value: value['sources'].__setitem__('rogue.py', '0' * 64),
            'story-checks': lambda value: value.__setitem__('story_checks', 24),
            'requirement-markers': lambda value: value['requirements'][0].__setitem__('markers', ['wrong-key']),
            'requirement-lines': lambda value: value['requirements'][0].__setitem__('story_lines', [1, 999]),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.local_service_policy_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                service_policy_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'drifted.bin'
            drifted.write_bytes(b'drifted upstream source\n')
            for name, attribute in (('story', 'STORY_SOURCE'), ('shell-entry', 'SHELL_SOURCE')):
                with self.subTest(source=name), \
                        patch('bcs_e2e.local_service_policy_contract.' + attribute, drifted), \
                        self.assertRaises(RunStateError):
                    service_policy_source_binding()


class ObligationTests(unittest.TestCase):
    def setUp(self):
        self.text = STORY_SOURCE.read_text(encoding='utf-8')
        self.rows = service_policy_source_binding()['requirements']

    def test_anchors_and_disjoint_line_ranges_cover_the_frozen_story(self):
        self.assertEqual([row['id'] for row in self.rows], list(REQUIREMENTS))
        previous_end = 0
        covered = 0
        for row in self.rows:
            start, end = row['story_lines']
            self.assertGreaterEqual(start, 1)
            self.assertGreaterEqual(start, previous_end + 1)
            self.assertLessEqual(end, len(self.text.splitlines()))
            covered += end - start + 1
            previous_end = end
            anchors = ANCHORS[row['id']]
            self.assertTrue(anchors)
            for literal in anchors:
                self.assertIn(literal, self.text, row['id'])
        self.assertGreaterEqual(covered, 23)

    def test_promotion_fence_requires_the_reviewed_release_inventory(self):
        reviewed = {row['id'] for row in reviewed_release_inventory()}
        self.assertIn('story_retired_service_policy', reviewed)
        self.assertNotIn('story_retired_service_policy', KNOWN_UNMAPPED_LEGACY_IDS)
        self.assertEqual(REVIEWED_RELEASE_PARENT_COUNT, 30)
        self.assertEqual(len(reviewed), 30)
        self.assertEqual(len(current_manifest()['legacy_inventory']), 41)


if __name__ == '__main__':
    unittest.main()
