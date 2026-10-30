"""Offline guards for the Draft delivery owned-local slice contract."""
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
        if (parent / 'draft-delivery-migration-contract.json').is_file() and (parent / 'bcs_e2e').is_dir():
            return parent
    raise AssertionError('draft-delivery runner root not found')


ROOT = _runner_root()
sys.path.insert(0, str(ROOT))
from bcs_e2e.inventory import (KNOWN_UNMAPPED_LEGACY_IDS, current_manifest,
                               reviewed_release_inventory)
from bcs_e2e.run_state import RunStateError
from bcs_e2e.draft_delivery.checks import OBLIGATION_CHECKS
from bcs_e2e.draft_delivery.contract import (CONFIG_FRAGMENT, CONFIG_REQUIRED, ENVIRONMENT,
                                             FIXTURE_SOURCE, MARKER_PATTERNS,
                                             REDACTION_TOKENS, REQUIREMENTS, STORY_CHECKS,
                                             STORY_MARKERS, STORY_SOURCE,
                                             draft_delivery_source_binding)
from bcs_e2e.draft_delivery.story import EXPECTED_CHECKS

GOOD_MARKER = 'Draft delivery story: 65 live HTTP assertions passed'
ROW_KEYS = {'id', 'expectation', 'story_lines', 'anchors', 'markers'}


class MarkerTests(unittest.TestCase):
    def test_marker_pattern_binds_the_exact_assertion_count(self):
        match = MARKER_PATTERNS[0].match(GOOD_MARKER)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '65')
        self.assertIsNone(MARKER_PATTERNS[0].match(GOOD_MARKER[:-len(' passed')]))
        self.assertIsNone(MARKER_PATTERNS[0].match('Draft delivery story: 45 checks passed'))
        self.assertIsNone(MARKER_PATTERNS[0].match('Draft delivery story: 65 checks passed'))


class ContractTests(unittest.TestCase):
    def test_binding_freezes_the_complete_map(self):
        value = draft_delivery_source_binding()
        self.assertEqual(value['parent'], 'story_collaboration_draft_delivery')
        self.assertEqual(value['suite'], 'owned-local-draft-delivery-v1')
        self.assertEqual(value['case_id'], 'LOCAL-DRAFT-DELIVERY-01')
        self.assertEqual([row['id'] for row in value['requirements']], list(REQUIREMENTS))
        self.assertEqual(len(value['requirements']), 32)
        self.assertEqual(value['story_checks'], STORY_CHECKS)
        self.assertEqual(value['story_markers'], list(STORY_MARKERS))
        self.assertEqual(value['environment'], ENVIRONMENT)
        self.assertEqual(value['remote_validation'], 'NOT_RUN')
        self.assertFalse(value['grants_release_credit'])
        self.assertFalse(value['release_parent_denominator'])
        self.assertTrue(value['independent_slice'])
        self.assertEqual(value['redaction_tokens'], list(REDACTION_TOKENS))
        self.assertEqual(value['config_fragment']['required'], CONFIG_REQUIRED)

    def test_requirement_rows_carry_anchors_and_markers(self):
        for row in draft_delivery_source_binding()['requirements']:
            self.assertEqual(set(row), ROW_KEYS, row['id'])
            self.assertTrue(row['expectation'])
            self.assertGreaterEqual(row['story_lines'][1], row['story_lines'][0])
            self.assertTrue(row['anchors'])
            self.assertEqual(row['markers'], ['draft-delivery-compliance'])

    def test_upstream_anchors_are_real_lines_inside_each_obligation(self):
        text = STORY_SOURCE.read_text(encoding='utf-8').splitlines()
        rows = draft_delivery_source_binding()['requirements']
        for row in rows:
            start, end = row['story_lines']
            self.assertLessEqual(start, end)
            self.assertLessEqual(end, len(text))
            for anchor in row['anchors']:
                self.assertGreaterEqual(anchor['line'], start)
                self.assertLessEqual(anchor['line'], end)
                self.assertIn(anchor['literal'], text[anchor['line'] - 1], row['id'])

    def test_all_32_obligations_bind_one_check_function_each(self):
        registered = {(obligation, function) for obligation, function in OBLIGATION_CHECKS}
        self.assertEqual(len(registered), 32)
        self.assertEqual({obligation for obligation, _ in registered}, set(REQUIREMENTS))
        for name in REQUIREMENTS:
            self.assertTrue(name.startswith('DRAFT-DELIVERY.'))

    def test_contract_tamper_matrix_is_rejected(self):
        original = draft_delivery_source_binding()
        mutations = {
            'drop-requirement': lambda value: value['requirements'].pop(),
            'requirement-id': lambda value: value['requirements'][0].__setitem__(
                'id', 'DRAFT-DELIVERY.offline-preview-wrong-id'),
            'story-checks': lambda value: value.__setitem__('story_checks', 64),
            'credit': lambda value: value.__setitem__('grants_release_credit', True),
            'denominator': lambda value: value.__setitem__('release_parent_denominator', True),
            'independent': lambda value: value.__setitem__('independent_slice', False),
            'environment': lambda value: value['environment'].__setitem__('preprod', 'SUPPORTED'),
            'marker': lambda value: value['story_markers'].__setitem__(0, 'Draft delivery story: {checks}'),
            'redaction': lambda value: value['redaction_tokens'].pop(),
            'requirement-marker': lambda value: value['requirements'][0].__setitem__('markers', ['wrong']),
            'anchor-line': lambda value: value['requirements'][0]['anchors'][0].__setitem__('line', 999),
            'anchor-literal': lambda value: value['requirements'][0]['anchors'][0].__setitem__('literal', 'not-present'),
            'config-required': lambda value: value['config_fragment']['required']['collaboration_drafts'].__setitem__('enabled', False),
            'sources-drop': lambda value: value['sources'].pop('indentation-regression.json'),
            'fixture-bytes': lambda value: value['fixture'].__setitem__('definition_yaml_bytes', 1),
            'runtime-auto-build': lambda value: value['owned_runtime_binary'].__setitem__('auto_build', True),
            'cli-binary-package': lambda value: value['cli_runtime_binary'].__setitem__('package', 'bcs'),
        }
        for name, mutate in mutations.items():
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(mutation=name), \
                    patch('bcs_e2e.draft_delivery.contract.read', return_value=value), \
                    self.assertRaises(RunStateError):
                draft_delivery_source_binding()

    def test_source_or_config_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drifted = root / 'drifted.sh'
            drifted.write_text('#!/usr/bin/env bash\n# drifted\n', encoding='utf-8')
            fixture = root / 'fixture.json'
            fixture.write_text('{}', encoding='utf-8')
            config = root / 'config.json'
            config.write_text(json.dumps({'collaboration_drafts': {'enabled': False}}), encoding='utf-8')
            for name, attribute, replacement in (
                    ('story', 'STORY_SOURCE', drifted),
                    ('fixture', 'FIXTURE_SOURCE', fixture),
                    ('config', 'CONFIG_FRAGMENT', config)):
                with self.subTest(source=name), \
                        patch('bcs_e2e.draft_delivery.contract.' + attribute, replacement), \
                        self.assertRaises(RunStateError):
                    draft_delivery_source_binding()

    def test_story_checks_match_the_black_box_child_constant(self):
        self.assertEqual(STORY_CHECKS, EXPECTED_CHECKS)
        self.assertEqual(STORY_CHECKS, 65)

    def test_parent_is_known_backlog_outside_release_denominator(self):
        manifest = current_manifest()
        legacy_ids = [row['id'] for row in manifest['legacy_inventory']]
        self.assertEqual(len(legacy_ids), 41)
        self.assertEqual(len(set(legacy_ids)), len(legacy_ids))
        self.assertIn('story_collaboration_draft_delivery', legacy_ids)
        self.assertIn('story_collaboration_draft_delivery', KNOWN_UNMAPPED_LEGACY_IDS)
        reviewed = {row['id'] for row in reviewed_release_inventory(manifest)}
        self.assertNotIn('story_collaboration_draft_delivery', reviewed)
        self.assertEqual(len(reviewed), 31)

    def test_slice_owns_its_config_fragment_without_editing_shared_local_config(self):
        local_config = (ROOT / 'bcs_e2e' / 'local_config.py').read_text(encoding='utf-8')
        self.assertNotIn("'collaboration_drafts'", local_config)
        self.assertEqual(json.loads(CONFIG_FRAGMENT.read_text(encoding='utf-8')),
                         {'collaboration_drafts': {'enabled': True}})
        note = draft_delivery_source_binding()['config_fragment']['integration_note']
        self.assertIn('collaboration_drafts.enabled=true', note)

    def test_fixture_and_shell_hashes_are_frozen(self):
        value = draft_delivery_source_binding()
        self.assertEqual(value['sources']['collaboration_draft_story.sh'],
                         '7c081dfb84b04549572c173f5da51125f5ff5475648ce471664c83cf3c77f4d2')
        self.assertEqual(value['sources']['indentation-regression.json'],
                         '23e0a22ea7f14f632b1af54a34aaab5bb6679f48580cd99240d36dd6943569e5')
        self.assertTrue(STORY_SOURCE.is_file())
        self.assertTrue(FIXTURE_SOURCE.is_file())
        self.assertTrue(CONFIG_FRAGMENT.is_file())


    def test_cli_runtime_binary_block_is_frozen_with_the_separation_note(self):
        cli_runtime = draft_delivery_source_binding()['cli_runtime_binary']
        self.assertEqual(cli_runtime['package'], 'bcs-cli')
        self.assertEqual(cli_runtime['build_command'], 'cargo build -p bcs-cli')
        self.assertEqual(cli_runtime['hash_algorithm'], 'sha256')
        self.assertEqual(cli_runtime['opt_in_env'],
                         ['BCS_E2E_TEST_CLI_BINARY', 'BCS_E2E_TEST_CLI_BINARY_SHA256'])
        self.assertFalse(cli_runtime['auto_build'])
        self.assertEqual(cli_runtime['separation_note'],
                         'The frozen journey splits runtime roles: bcs owns the server leg; '
                         'bcs-cli owns every author-side Draft CLI step.')
        self.assertEqual(cli_runtime['required_arguments'],
                         ['--cli-binary', '--cli-binary-sha256'])


if __name__ == '__main__':
    unittest.main()
