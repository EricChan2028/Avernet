"""Offline guards for the independent session owner permissions slice."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.run_state import RunStateError
from bcs_e2e.session_owner.session_owner_contract import (ANCHORS, CASE_ID, DOCUMENTED_GAPS,
                                                           ENVIRONMENT, MARKER_PATTERNS,
                                                           MARKER_PREFIX, MARKER_SUFFIX, PARENT,
                                                           REQUIREMENTS, SHELL_ANCHORS,
                                                           SHELL_SOURCE, STORY_CHECKS, STORY_SOURCE,
                                                           SUITE, session_owner_dual_adaptability,
                                                           session_owner_source_binding)
from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, REVIEWED_RELEASE_PARENT_COUNT,
                               current_manifest, reviewed_release_inventory)

GOOD_MARKER_LINE = 'Session owner story: 60 HTTP assertions passed'


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_parses_the_checks_count(self):
        match = MARKER_PATTERNS[0].match(GOOD_MARKER_LINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '60')
        self.assertIsNone(MARKER_PATTERNS[0].match(GOOD_MARKER_LINE[:-len(' passed')]))
        self.assertIsNone(MARKER_PATTERNS[0].match('Session owner story: 60 checks passed'))

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
        value = session_owner_source_binding()
        self.assertEqual(set(value), {'schema_version', 'source_revision', 'source_file_revisions',
                                      'parent', 'suite', 'case_id', 'environment', 'sources',
                                      'story_checks', 'owned_runtime_binary', 'requirements',
                                      'story_markers', 'remote_validation', 'grants_release_credit',
                                      'release_parent_denominator', 'independent_slice',
                                      'evidence_granularity'})
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['parent'], 'story_session_owner_permissions')
        self.assertEqual(value['suite'], 'owned-local-session-owner-v1')
        self.assertEqual(value['case_id'], 'LOCAL-SESSION-OWNER-01')
        self.assertEqual(len(value['requirements']), 9)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['story_checks'], STORY_CHECKS)
        self.assertEqual(value['story_markers'], ['Session owner story: {checks} HTTP assertions passed'])
        self.assertEqual(set(value['sources']), {'session_owner_story.py', 'session_owner_story.sh'})
        for digest in value['sources'].values():
            self.assertEqual(len(digest), 64)
        for digest in value['source_file_revisions'].values():
            self.assertEqual(len(digest), 40)
        runtime = value['owned_runtime_binary']
        self.assertEqual(runtime['package'], 'bcs')
        self.assertFalse(runtime['auto_build'])
        self.assertEqual(runtime['required_arguments'], ['--binary', '--binary-sha256'])
        self.assertEqual(runtime['opt_in_env'], ['BCS_E2E_TEST_BINARY', 'BCS_E2E_TEST_BINARY_SHA256'])

    def test_independent_slice_stays_outside_the_reviewed_release_denominator(self):
        value = session_owner_source_binding()
        self.assertTrue(value['independent_slice'])
        self.assertFalse(value['release_parent_denominator'])
        self.assertFalse(value['grants_release_credit'])
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertEqual(value['evidence_granularity'], 'OWNED_SESSION_OWNER_JOURNAL')
        self.assertEqual(value['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertNotIn('human_id', value)
        self.assertNotIn('mock_child', value)

    def test_contract_tamper_matrix_is_rejected(self):
        original = session_owner_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'markers': lambda value: value['story_markers'].__setitem__(0, 'Session owner story: {checks}'),
            'runtime-binary-auto-build': lambda value: value['owned_runtime_binary'].__setitem__('auto_build', True),
            'runtime-binary-arguments': lambda value: value['owned_runtime_binary'].__setitem__('required_arguments', ['--binary']),
            'revision': lambda value: value.__setitem__('source_revision', 'z' * 40),
            'file-revision': lambda value: value['source_file_revisions'].pop('session_owner_story.sh'),
            'denominator': lambda value: value.__setitem__('release_parent_denominator', True),
            'independent': lambda value: value.__setitem__('independent_slice', False),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'granularity': lambda value: value.__setitem__('evidence_granularity', 'PER_OBLIGATION_RECEIPT'),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'sources-drop': lambda value: value['sources'].pop('session_owner_story.py'),
            'sources-extra': lambda value: value['sources'].__setitem__('rogue.py', '0' * 64),
            'story-checks': lambda value: value.__setitem__('story_checks', 61),
            'requirement-markers': lambda value: value['requirements'][0].__setitem__('markers', ['wrong-key']),
            'requirement-lines': lambda value: value['requirements'][0].__setitem__('story_lines', [1, 999]),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.session_owner.session_owner_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                session_owner_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'drifted.bin'
            drifted.write_bytes(b'drifted upstream source\n')
            for name, attribute in (('story', 'STORY_SOURCE'), ('shell-entry', 'SHELL_SOURCE')):
                with self.subTest(source=name), \
                        patch('bcs_e2e.session_owner.session_owner_contract.' + attribute, drifted), \
                        self.assertRaises(RunStateError):
                    session_owner_source_binding()


class ObligationTests(unittest.TestCase):
    def setUp(self):
        self.text = STORY_SOURCE.read_text(encoding='utf-8')
        self.rows = session_owner_source_binding()['requirements']

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
        self.assertEqual(covered, 123)

    def test_independent_slice_fence_requires_the_unmapped_backlog(self):
        self.assertIn('story_session_owner_permissions', KNOWN_UNMAPPED_LEGACY_IDS)
        reviewed = {row['id'] for row in reviewed_release_inventory()}
        self.assertNotIn('story_session_owner_permissions', reviewed)
        self.assertEqual(REVIEWED_RELEASE_PARENT_COUNT, len(reviewed_release_inventory()))
        self.assertGreaterEqual(REVIEWED_RELEASE_PARENT_COUNT, 30)
        self.assertEqual(len(current_manifest()['legacy_inventory']), 41)
        case_ids = {row['id'] for row in current_manifest()['cases']}
        self.assertNotIn(CASE_ID, case_ids)
        self.assertNotIn('LOCAL-SESSION-OWNER-01', case_ids)


class AdaptabilityTests(unittest.TestCase):
    def test_dual_adaptability_declares_local_full_and_preprod_gaps(self):
        value = session_owner_dual_adaptability()
        self.assertEqual(value['case_id'], CASE_ID)
        self.assertEqual(value['legacy_id'], PARENT)
        self.assertEqual(value['requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(len(value['selected_obligations']), 9)
        self.assertEqual(value['api_face'], 'public-v1')
        self.assertEqual(value['local_executor_status'], 'LOCAL_FULL_EXECUTOR')
        self.assertEqual(value['preprod_execution_status'], 'NOT_RUN_DOCUMENTED_GAPS')
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertFalse(value['grants_release_credit'])

    def test_documented_gaps_name_the_four_preprod_faces(self):
        self.assertEqual(set(DOCUMENTED_GAPS), {
            'PREPROD_STATION_BOOTSTRAP_FACE_ABSENT', 'PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE',
            'PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY', 'PREPROD_PARTICIPANT_ROUTES_UNBOUND'})
        for gap_id, explanation in DOCUMENTED_GAPS.items():
            self.assertIsInstance(explanation, str)
            self.assertTrue(explanation.strip(), gap_id)


if __name__ == '__main__':
    unittest.main()
