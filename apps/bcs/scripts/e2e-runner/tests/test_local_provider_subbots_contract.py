"""Offline guards for the Provider subbots owned-local migration contract."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _runner_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if ((parent / 'provider-subbots-migration-contract.json').is_file()
                and (parent / 'bcs_e2e').is_dir()):
            return parent
    raise AssertionError('provider-subbots runner root not found')


ROOT = _runner_root()
sys.path.insert(0, str(ROOT))

from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, current_manifest,
                               reviewed_release_inventory)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.provider_subbots.contract import (CONFIG_FRAGMENT, CONFIG_REQUIRED,
                                               DOCUMENTED_GAPS, ENVIRONMENT,
                                               MARKER_PATTERNS, REDACTION_TOKENS,
                                               REQUIREMENTS, STORY_CHECKS,
                                               STORY_MARKERS, STORY_SOURCE,
                                               provider_subbots_source_binding)
from bcs_e2e.provider_subbots.story import EXPECTED_CHECKS

GOOD_MARKER = 'Provider subbots story: 131 live HTTP assertions passed'


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_binds_the_exact_assertion_count(self):
        match = MARKER_PATTERNS[0].match(GOOD_MARKER)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '131')
        self.assertEqual(MARKER_PATTERNS[0].match(
            'Provider subbots story: 130 live HTTP assertions passed').group(1), '130')
        self.assertIsNone(MARKER_PATTERNS[0].match(
            'Provider subbots story: 131 checks'))


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_map(self):
        value = provider_subbots_source_binding()
        self.assertEqual(value['parent'], 'story_provider_operator_manages_subbots')
        self.assertEqual(value['suite'], 'owned-local-provider-subbots-v1')
        self.assertEqual(value['case_id'], 'LOCAL-PROVIDER-SUBBOTS-01')
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(len(value['requirements']), 15)
        self.assertEqual(value['story_checks'], STORY_CHECKS)
        self.assertEqual(value['story_markers'], list(STORY_MARKERS))
        self.assertEqual(value['environment'], ENVIRONMENT)
        self.assertEqual(value['environment']['preprod'], 'REMOTE_UNSUPPORTED')
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertFalse(value['grants_release_credit'])
        self.assertFalse(value['release_parent_denominator'])
        self.assertTrue(value['independent_slice'])
        self.assertEqual(value['redaction_tokens'], list(REDACTION_TOKENS))
        self.assertEqual(set(value['documented_gaps']), set(DOCUMENTED_GAPS))
        self.assertEqual(value['config_fragment']['required'], CONFIG_REQUIRED)

    def test_upstream_anchors_are_real_lines_inside_each_obligation(self):
        text = STORY_SOURCE.read_text(encoding='utf-8').splitlines()
        rows = provider_subbots_source_binding()['requirements']
        for row in rows:
            start, end = row['story_lines']
            self.assertLessEqual(start, end)
            self.assertLessEqual(end, len(text))
            for anchor in row['anchors']:
                self.assertGreaterEqual(anchor['line'], start)
                self.assertLessEqual(anchor['line'], end)
                self.assertIn(anchor['literal'], text[anchor['line'] - 1], row['id'])

    def test_all_fifteen_obligations_have_independent_check_functions(self):
        from bcs_e2e.provider_subbots import checks
        expected = {
            'provider-registration-discovery',
            'admin-complete-ref-validation', 'admin-sibling-registration',
            'scoped-complete-ref-validation', 'scoped-sibling-distinct-identity',
            'duplicate-reference-conflicts', 'self-lookup-actor-binding',
            'legacy-api-actor-binding', 'metadata-update-parent-immutability',
            'onboard-parent-immutability', 'parent-discovery-fail-closed',
            'callback-actor-binding', 'signed-principal-live-state',
            'disable-enable-state', 'deletion-tombstone-retirement',
        }
        self.assertEqual({name.split('.', 1)[1] for name in REQUIREMENTS}, expected)
        declared = {value for name, value in vars(checks).items()
                    if name.startswith('OBLIGATION_') and isinstance(value, str)}
        self.assertEqual(declared, set(REQUIREMENTS))

    def test_contract_tamper_matrix_is_rejected(self):
        original = provider_subbots_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'story-checks': lambda value: value.__setitem__('story_checks', 130),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'denominator': lambda value: value.__setitem__(
                'release_parent_denominator', True),
            'independent': lambda value: value.__setitem__('independent_slice', False),
            'environment': lambda value: value['environment'].__setitem__(
                'preprod', 'SUPPORTED'),
            'marker': lambda value: value['story_markers'].__setitem__(
                0, 'Provider subbots story: {checks}'),
            'redaction': lambda value: value['redaction_tokens'].pop(),
            'requirement-marker': lambda value: value['requirements'][0].__setitem__(
                'markers', ['wrong']),
            'anchor-line': lambda value: value['requirements'][0]['anchors'][0].__setitem__(
                'line', 999),
            'anchor-literal': lambda value: value['requirements'][0]['anchors'][0].__setitem__(
                'literal', 'not-present'),
            'config-required': lambda value: value['config_fragment']['required'].__setitem__(
                'provider_stream_gray_enabled', True),
            'sources-drop': lambda value: value['sources'].pop('agentpass-subbots.md'),
            'documented-gap': lambda value: value['documented_gaps'].clear(),
            'runtime-auto-build': lambda value: value['owned_runtime_binary'].__setitem__(
                'auto_build', True),
            'source-revision': lambda value: value.__setitem__(
                'source_revision', '0' * 40),
            'parent': lambda value: value.__setitem__('parent', 'other-story'),
        }
        for name, mutate in mutations.items():
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(mutation=name), \
                    patch('bcs_e2e.provider_subbots.contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                provider_subbots_source_binding()

    def test_source_or_config_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drifted = root / 'drifted.py'
            drifted.write_text('#!/usr/bin/env python3\n# drifted\n', encoding='utf-8')
            config = root / 'config.json'
            config.write_text(json.dumps({
                'provider_stream_gray_enabled': True,
                'provider_stream_gray_created_by': [],
            }), encoding='utf-8')
            for name, attribute, replacement in (
                    ('story', 'STORY_SOURCE', drifted),
                    ('config', 'CONFIG_FRAGMENT', config)):
                with self.subTest(source=name), \
                        patch('bcs_e2e.provider_subbots.contract.' + attribute, replacement), \
                        self.assertRaises(RunStateError):
                    provider_subbots_source_binding()

    def test_story_checks_match_the_black_box_child_constant(self):
        self.assertEqual(STORY_CHECKS, EXPECTED_CHECKS)
        self.assertEqual(STORY_CHECKS, 131)

    def test_parent_is_known_backlog_outside_release_denominator(self):
        manifest = current_manifest()
        legacy_ids = [row['id'] for row in manifest['legacy_inventory']]
        self.assertEqual(len(legacy_ids), len(set(legacy_ids)))
        self.assertIn('story_provider_operator_manages_subbots', legacy_ids)
        self.assertIn('story_provider_operator_manages_subbots',
                      KNOWN_UNMAPPED_LEGACY_IDS)
        reviewed = {row['id'] for row in reviewed_release_inventory(manifest)}
        self.assertNotIn('story_provider_operator_manages_subbots', reviewed)
        self.assertEqual(len(reviewed), 31)

    def test_slice_owns_config_fragment_without_editing_shared_local_config(self):
        local_config = (ROOT / 'bcs_e2e' / 'local_config.py').read_text(encoding='utf-8')
        self.assertNotIn("'provider_stream_gray_enabled'", local_config)
        self.assertEqual(json.loads(CONFIG_FRAGMENT.read_text(encoding='utf-8')),
                         CONFIG_REQUIRED)
        note = provider_subbots_source_binding()['config_fragment']['integration_note']
        self.assertIn('provider_stream_gray_enabled=false', note)


if __name__ == '__main__':
    unittest.main()
