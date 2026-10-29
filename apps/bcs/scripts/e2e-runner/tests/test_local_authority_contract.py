"""Offline guards for the promoted Bot-authority user journey release slice."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.local_authority_contract import (
    ANCHORS, CALLS, EXPECTED_HTTP_CALLS, PRIMARY, REQUIREMENTS, SHELL_SOURCE,
    SOURCE_REVISION, authority_source_binding)
from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, REVIEWED_RELEASE_PARENT_COUNT,
                               current_manifest, reviewed_release_inventory)
from bcs_e2e.run_state import RunStateError


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_upstream_map(self):
        value = authority_source_binding()
        self.assertEqual(set(value), {'schema_version', 'source_revision', 'source_file_revisions',
                                      'parent', 'suite', 'case_id', 'environment', 'sources',
                                      'requirements', 'remote_validation', 'grants_release_credit',
                                      'release_parent_denominator', 'independent_slice',
                                      'evidence_granularity'})
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['parent'], 'story_authority_user_journey')
        self.assertEqual(value['suite'], 'owned-local-authority-v1')
        self.assertEqual(value['case_id'], 'LOCAL-AUTHORITY-JOURNEY-01')
        self.assertEqual(len(value['requirements']), 16)
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(value['source_revision'], SOURCE_REVISION)
        self.assertEqual(set(value['sources']), {'authority.sh'})
        self.assertEqual(value['source_file_revisions'], {'authority.sh': SOURCE_REVISION})
        self.assertEqual(value['environment'], {'local': 'LOCAL_EXECUTOR_ONLY',
                                                'preprod': 'REMOTE_UNSUPPORTED'})
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertFalse(value['grants_release_credit'])
        self.assertTrue(value['release_parent_denominator'])
        self.assertFalse(value['independent_slice'])
        self.assertEqual(value['evidence_granularity'], 'OWNED_PARENT_JOURNAL')

    def test_call_matrix_primary_and_anchors_match_the_frozen_story(self):
        value = authority_source_binding()
        text = SHELL_SOURCE.read_text(encoding='utf-8')
        lines_count = len(text.splitlines())
        self.assertEqual(sum(len(row['calls']) for row in value['requirements']), EXPECTED_HTTP_CALLS)
        previous_end = 0
        for row in value['requirements']:
            requirement = row['id']
            method, path, actor = PRIMARY[requirement]
            self.assertEqual((row['method'], row['path'], row['actor']), (method, path, actor))
            self.assertEqual(row['kind'], 'lifecycle' if requirement == REQUIREMENTS[-1] else 'http')
            self.assertEqual(row['anchors'], list(ANCHORS[requirement]))
            self.assertEqual(row['calls'],
                             [{'method': call[0], 'path': call[1], 'expect_status': call[2],
                               'actor': call[3], 'query': call[4]} for call in CALLS[requirement]])
            start, end = row['story_lines']
            self.assertIsInstance(start, int)
            self.assertIsInstance(end, int)
            self.assertGreaterEqual(start, 1)
            self.assertLessEqual(end, lines_count)
            if requirement != REQUIREMENTS[-1]:
                self.assertGreater(start, previous_end)
                previous_end = end
            for literal in row['anchors']:
                self.assertIn(literal, text, requirement)
        # The first fifteen obligations replay disjoint, strictly increasing story lines.

    def test_contract_tamper_matrix_is_rejected(self):
        original = authority_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'revision': lambda value: value.__setitem__('source_revision', 'z' * 40),
            'file-revision': lambda value: value['source_file_revisions'].pop('authority.sh'),
            'denominator': lambda value: value.__setitem__('release_parent_denominator', False),
            'independent': lambda value: value.__setitem__('independent_slice', True),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'granularity': lambda value: value.__setitem__('evidence_granularity', 'PER_OBLIGATION_RECEIPT'),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'sources-drop': lambda value: value['sources'].pop('authority.sh'),
            'sources-extra': lambda value: value['sources'].__setitem__('rogue.sh', '0' * 64),
            'suite': lambda value: value.__setitem__('suite', 'owned-local-rogue-v1'),
            'case-id': lambda value: value.__setitem__('case_id', 'LOCAL-ROGUE-01'),
            'parent': lambda value: value.__setitem__('parent', 'story_rogue_user_journey'),
            'requirement-method': lambda value: value['requirements'][1].__setitem__('method', 'PATCH'),
            'requirement-anchors': lambda value: value['requirements'][1]['anchors'].__setitem__(0, 'not-an-anchor'),
            'requirement-lines': lambda value: value['requirements'][0].__setitem__('story_lines', [1, 999]),
            'requirement-call-status': lambda value: value['requirements'][1]['calls'][0].__setitem__('expect_status', 404),
            'requirement-call-method': lambda value: value['requirements'][0]['calls'][0].__setitem__('method', 'HEAD'),
            'requirement-calls-dropped': lambda value: value['requirements'][0]['calls'].pop(),
        }
        for mutation, apply in mutations.items():
            value = copy.deepcopy(original)
            apply(value)
            with self.subTest(mutation=mutation), \
                    patch('bcs_e2e.local_authority_contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                authority_source_binding()

    def test_upstream_drift_is_rejected_before_any_executor_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / 'drifted.sh'
            drifted.write_text('# drifted upstream authority story\n', encoding='utf-8')
            with self.subTest(source='shell-entry'), \
                    patch('bcs_e2e.local_authority_contract.SHELL_SOURCE', drifted), \
                    self.assertRaises(RunStateError):
                authority_source_binding()
        with patch('bcs_e2e.local_authority_contract.SOURCE_REVISION', '0' * 40), \
                self.assertRaises(RunStateError):
            authority_source_binding()

    def test_promotion_fence_requires_the_reviewed_release_inventory(self):
        reviewed = {row['id'] for row in reviewed_release_inventory()}
        self.assertIn('story_authority_user_journey', reviewed)
        self.assertNotIn('story_authority_user_journey', KNOWN_UNMAPPED_LEGACY_IDS)
        self.assertEqual(REVIEWED_RELEASE_PARENT_COUNT, 31)
        self.assertEqual(len(reviewed), 31)
        self.assertEqual(len(current_manifest()['legacy_inventory']), 41)


if __name__ == '__main__':
    unittest.main()
